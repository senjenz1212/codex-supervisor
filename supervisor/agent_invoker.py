"""Invokes the Claude Agent SDK with the appropriate skill per decision kind.

Each invocation is bounded (max_turns), runs to completion, and terminates.
This is what gives us predictable cost — the agent doesn't loop forever.
"""
from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from pathlib import Path

from .agent_runtime import AgentRuntime, AgentTask
from .config import Config
from .runtime_cleanup import cancel_runtime_after_failure
from .state import State, Decision

log = logging.getLogger(__name__)

SKILL_FOR_DECISION = {
    "adjudicate_drift": "drift-watch",
    "evaluate_run":     "evaluate-run",
    "plan_recovery":    "plan-recovery",
    "review_updates":   "review-updates",
}


class AgentInvoker:
    def __init__(self, cfg: Config, state: State, skills_dir: Path,
                 codex_mcp_server, telegram_mcp_server,
                 *,
                 agent_runtime: AgentRuntime,
                 agent_environment: Mapping[str, str] | None = None,
                 inherit_agent_environment: bool = True):
        self.cfg = cfg
        self.state = state
        self.skills_dir = skills_dir
        self.codex_mcp = codex_mcp_server
        self.telegram_mcp = telegram_mcp_server
        self.agent_runtime = agent_runtime
        self.agent_environment = dict(agent_environment or {})
        self.inherit_agent_environment = bool(inherit_agent_environment)

    async def run(self) -> None:
        log.info("AgentInvoker: starting decision loop")
        while True:
            decision = await self.state.next_decision()
            try:
                await self._handle(decision)
            except Exception as e:
                log.exception("decision %s failed: %s", decision.kind, e)

    async def _handle(self, d: Decision) -> None:
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
            timeout_s=900,
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
            self.state.write_verdict(
                run_id=d.run_id, phase=d.kind,
                layer="L4" if d.kind == "adjudicate_drift" else None,
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
