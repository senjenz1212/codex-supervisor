"""Invokes the Claude Agent SDK with the appropriate skill per decision kind.

Each invocation is bounded (max_turns), runs to completion, and terminates.
This is what gives us predictable cost — the agent doesn't loop forever.
"""
from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .agent_runtime import AgentRuntime, AgentTask
from .config import Config
from .runtime_cleanup import cancel_runtime_after_failure
from .state import State, Decision

log = logging.getLogger(__name__)

DECISION_TASK_TIMEOUT_S = 900
DECISION_LEASE_S = DECISION_TASK_TIMEOUT_S + 120

SKILL_FOR_DECISION = {
    "adjudicate_drift": "drift-watch",
    "evaluate_run":     "evaluate-run",
    "plan_recovery":    "plan-recovery",
    "review_updates":   "review-updates",
}


@dataclass(frozen=True)
class _DecisionVerdict:
    model: str
    output: dict[str, object]
    latency_ms: int = 0


class AgentInvoker:
    def __init__(self, cfg: Config, state: State, skills_dir: Path,
                 codex_mcp_server, telegram_mcp_server,
                 *,
                 agent_runtime: AgentRuntime,
                 agent_environment: Mapping[str, str] | None = None,
                 inherit_agent_environment: bool = False,
                 max_decision_attempts: int = 5,
                 retry_base_delay_s: float = 5.0):
        self.cfg = cfg
        self.state = state
        self.skills_dir = skills_dir
        self.codex_mcp = codex_mcp_server
        self.telegram_mcp = telegram_mcp_server
        self.agent_runtime = agent_runtime
        self.agent_environment = dict(agent_environment or {})
        self.inherit_agent_environment = bool(inherit_agent_environment)
        self.max_decision_attempts = max(1, int(max_decision_attempts))
        self.retry_base_delay_s = max(0.0, float(retry_base_delay_s))

    async def run(self) -> None:
        log.info("AgentInvoker: starting decision loop")
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "decision loop iteration failed; continuing",
                )
                await asyncio.sleep(self.retry_base_delay_s)

    async def run_once(self) -> None:
        """Dispatch one leased decision and durably settle its outbox row."""

        decision = await self.state.next_decision(lease_s=DECISION_LEASE_S)
        try:
            verdict = await self._handle(decision)
        except asyncio.CancelledError:
            self.state.retry_decision(
                decision,
                error="dispatcher_cancelled",
                delay_s=0,
            )
            raise
        except Exception as exc:
            if decision.attempt_count >= self.max_decision_attempts:
                self.state.dead_letter_decision(
                    decision,
                    error=f"{type(exc).__name__}: {exc}",
                )
                log.exception(
                    "decision %s dead-lettered after %s attempts: %s",
                    decision.kind,
                    decision.attempt_count,
                    exc,
                )
                return
            delay_s = min(
                300.0,
                self.retry_base_delay_s
                * (2 ** max(0, decision.attempt_count - 1)),
            )
            self.state.retry_decision(
                decision,
                error=f"{type(exc).__name__}: {exc}",
                delay_s=delay_s,
            )
            log.exception(
                "decision %s attempt %s failed; retrying in %.3fs: %s",
                decision.kind,
                decision.attempt_count,
                delay_s,
                exc,
            )
            return
        settled = (
            self.state.ack_decision(decision)
            if verdict is None
            else self.state.commit_decision_verdict(
                decision,
                model=verdict.model,
                output=verdict.output,
                latency_ms=verdict.latency_ms,
            )
            is not None
        )
        if not settled:
            raise RuntimeError(
                "decision completed but its durable lease could not be acked: "
                f"{decision.decision_id}"
            )

    async def _handle(self, d: Decision) -> _DecisionVerdict | None:
        skill_name = SKILL_FOR_DECISION.get(d.kind)
        if not skill_name:
            log.warning("unknown decision kind: %s", d.kind)
            return
        skill_text = (self.skills_dir / f"{skill_name}.md").read_text()

        model = self._model_for(d.kind)
        user_message = self._format_decision(d)
        log.info("invoking agent: kind=%s run=%s skill=%s",
                 d.kind, d.run_id, skill_name)
        handle = await self.agent_runtime.start(AgentTask(
            task_id=f"{d.run_id}:{d.kind}",
            instruction=user_message,
            cwd=Path.cwd(),
            model=model,
            timeout_s=DECISION_TASK_TIMEOUT_S,
            env=self.agent_environment,
            inherit_env=self.inherit_agent_environment,
            metadata={
                "system_prompt": skill_text,
                "max_turns": 12,
                "mcp_servers": {
                    "codex": self.codex_mcp,
                    "telegram": self.telegram_mcp,
                },
                "allowed_tools": self._allowed_tools_for(d.kind),
                "effort": self._effort_for(d.kind),
                "result_metadata": {
                    "decision_kind": d.kind,
                    "source_run_id": d.run_id,
                },
            },
        ))
        try:
            outputs: list[str] = []
            async for event in self.agent_runtime.stream(handle):
                if event.kind != "agent.message":
                    continue
                text = event.payload.get("message")
                if text:
                    outputs.append(str(text))
            result = await self.agent_runtime.collect(handle)
            if result.status != "completed":
                raise RuntimeError(
                    f"decision runtime ended {result.status}: "
                    f"{result.metadata.get('stderr') or result.output}"
                )
            if not outputs and result.output:
                outputs.append(result.output)

            verdict_model = result.resolved_model or model
            return _DecisionVerdict(
                model=verdict_model,
                output={
                    "agent_outputs": outputs,
                    "runtime": result.runtime,
                    "runtime_run_id": result.run_id,
                    "result_hash": result.result_hash,
                    "requested_model": model,
                    "resolved_model": result.resolved_model or None,
                    "model_provenance": (
                        result.model_provenance or "requested_model_fallback"
                    ),
                },
                latency_ms=0,
            )
        except BaseException:
            await cancel_runtime_after_failure(
                self.agent_runtime,
                handle,
                logger=log,
            )
            raise

    def _model_for(self, kind: str) -> str:
        if kind == "adjudicate_drift":
            return self.cfg.models.drift_l4_model
        if kind in ("evaluate_run", "review_updates"):
            return self.cfg.models.post_run_eval_model
        return self.cfg.models.drift_l4_model

    @staticmethod
    def _effort_for(kind: str) -> str:
        if kind in {"evaluate_run", "plan_recovery", "review_updates"}:
            return "high"
        return "medium"

    @staticmethod
    def _allowed_tools_for(kind: str) -> list[str]:
        if kind == "review_updates":
            return [
                "mcp__codex__read_rollout",
                "mcp__codex__get_run_metadata",
                "mcp__codex__read_workspace_snapshot",
                "mcp__codex__read_workspace_file",
                "mcp__telegram__send_message",
            ]
        return [
            "mcp__codex__read_rollout",
            "mcp__codex__get_run_metadata",
            "mcp__codex__inject_steering",
            "mcp__codex__list_active_runs",
            "mcp__telegram__send_message",
            "mcp__telegram__ask_user",
        ]

    def _format_decision(self, d: Decision) -> str:
        return (
            f"Decision request:\n\n"
            f"kind: {d.kind}\n"
            f"run_id: {d.run_id}\n"
            f"context:\n{json.dumps(d.payload, indent=2, default=str)[:8000]}\n\n"
            f"Follow the procedure in your system prompt. Use your MCP tools as needed. "
            f"End your response with a single JSON block summarizing the action you took."
        )
