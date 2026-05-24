"""Codex-facing stdio MCP server for supervisor control.

This module is intentionally independent of the Claude Agent SDK wrappers in
`mcp_tools.codex_tools` and `mcp_tools.supervisor_tools`. Codex loads this
server through its external MCP configuration and receives ordinary MCP tools.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from supervisor.config import Config
from supervisor.dual_agent import GateRound, evaluate_deadlock_budget
from supervisor.dual_agent_artifacts import (
    ScreenshotArtifact,
    default_dual_agent_artifact_dir,
    export_dual_agent_run_artifacts,
)
from supervisor.dual_agent_runner import (
    DualAgentGateResult,
    DualAgentGateSpec,
    request_deadlock_escalation,
    resume_pending_gates,
    run_dual_agent_gate,
    run_dual_agent_gate_with_escalation,
)
from supervisor.dual_agent_lead import GateName, PlanningArtifact
from supervisor.redaction import redact
from supervisor.state import State
from supervisor.telegram import TelegramNotifier, telegram_enabled


Runner = Callable[..., subprocess.CompletedProcess[str]]
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "xhigh"


class CodexSupervisorMcpAPI:
    def __init__(
        self,
        cfg: Config,
        state: State,
        *,
        runner: Runner = subprocess.run,
        codex_runner: Runner = subprocess.run,
        notifier: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.runner = runner
        self.codex_runner = codex_runner
        self.notifier = notifier

    async def start_dual_agent_gate(
        self,
        *,
        task_id: str,
        run_id: str,
        gate: GateName,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        planning_artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        spec = self._gate_spec(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            planning_artifacts=planning_artifacts,
        )
        notifier = self._notifier()
        if notifier is not None:
            result = await run_dual_agent_gate_with_escalation(
                spec,
                state=self.state,
                notifier=notifier,
                runner=self.runner,
            )
        else:
            result = run_dual_agent_gate(spec, runner=self.runner)
        payload = _gate_result_payload(result)
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload=payload,
        )
        return payload

    def poll_resume_signal(
        self,
        *,
        task_id: str,
        run_id: str,
        gate: GateName,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        planning_artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        spec = self._gate_spec(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            planning_artifacts=planning_artifacts,
        )
        results = resume_pending_gates([spec], state=self.state, runner=self.runner)
        if not results:
            return {"status": "no_signal", "task_id": task_id, "run_id": run_id}
        payload = _gate_result_payload(results[0])
        self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload=payload,
        )
        return payload

    def record_gate_round(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: GateName,
        round_index: int,
        codex_decision: str,
        claude_decision: str,
        codex_confidence: float,
        claude_confidence: float,
        objection: str | None = None,
    ) -> dict[str, Any]:
        round_payload = _gate_round_payload(
            round_index=round_index,
            codex_decision=codex_decision,
            claude_decision=claude_decision,
            codex_confidence=codex_confidence,
            claude_confidence=claude_confidence,
            objection=objection,
        )
        event_id = self.state.write_event(
            run_id=run_id,
            source="dual_agent",
            kind="dual_agent_gate_round",
            payload={
                "task_id": task_id,
                "gate": gate,
                "round": round_payload,
            },
        )
        return {
            "status": "recorded",
            "event_id": event_id,
            "run_id": run_id,
            "task_id": task_id,
            "gate": gate,
            "round": round_payload,
        }

    def check_budget(
        self,
        *,
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        probe = evaluate_deadlock_budget(
            [_gate_round_from_payload(r) for r in rounds],
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )
        return {"probe": asdict(probe)}

    async def escalate_deadlock(
        self,
        *,
        run_id: str,
        task_id: str,
        gate: GateName,
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        notifier = self._notifier()
        if notifier is None:
            return {
                "status": "telegram_disabled",
                "reason": "deadlock_escalation_requires_telegram",
            }
        escalation = await request_deadlock_escalation(
            state=self.state,
            notifier=notifier,
            run_id=run_id,
            task_id=task_id,
            gate=gate,
            rounds=[_gate_round_from_payload(r) for r in rounds],
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )
        return redact(asdict(escalation))

    def read_outcome(self, *, run_id: str, task_id: str) -> dict[str, Any]:
        rows = self.state._conn.execute(
            """SELECT * FROM events
               WHERE run_id=? AND source='dual_agent'
                 AND kind='dual_agent_gate_result'
               ORDER BY event_id DESC
               LIMIT 50""",
            (run_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if str(payload.get("task_id") or "") == task_id:
                return {
                    "status": "ok",
                    "run_id": run_id,
                    "task_id": task_id,
                    "event_id": row["event_id"],
                    "result": redact(payload),
                }
        return {"status": "not_found", "run_id": run_id, "task_id": task_id}

    def read_gate_transcript(self, *, run_id: str, task_id: str) -> dict[str, Any]:
        rows = self.state._conn.execute(
            """SELECT * FROM events
               WHERE run_id=? AND source='dual_agent'
                 AND kind IN ('dual_agent_gate_round', 'dual_agent_gate_result')
               ORDER BY event_id ASC""",
            (run_id,),
        ).fetchall()
        rounds: list[dict[str, Any]] = []
        latest_result: dict[str, Any] | None = None
        latest_result_event_id: int | None = None
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if str(payload.get("task_id") or "") != task_id:
                continue
            if row["kind"] == "dual_agent_gate_round":
                round_payload = dict(payload.get("round") or {})
                rounds.append(redact({
                    "event_id": row["event_id"],
                    "occurred_at": row["ts"],
                    "gate": payload.get("gate"),
                    **round_payload,
                }))
            elif row["kind"] == "dual_agent_gate_result":
                latest_result = redact(payload)
                latest_result_event_id = int(row["event_id"])

        if not rounds and latest_result is None:
            return {
                "status": "not_found",
                "run_id": run_id,
                "task_id": task_id,
                "rounds": [],
                "result": None,
                "handoff_packet_path": None,
            }
        return {
            "status": "ok",
            "run_id": run_id,
            "task_id": task_id,
            "rounds": rounds,
            "result_event_id": latest_result_event_id,
            "result": latest_result,
            "handoff_packet_path": (
                latest_result.get("handoff_packet_path")
                if latest_result is not None else None
            ),
        }

    def export_gate_artifacts(
        self,
        *,
        run_id: str,
        task_id: str,
        cwd: str,
        output_dir: str | None = None,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        target_dir = Path(output_dir).expanduser() if output_dir else default_dual_agent_artifact_dir(cwd, task_id)
        result = export_dual_agent_run_artifacts(
            self.state,
            run_id=run_id,
            task_id=task_id,
            output_dir=target_dir,
            screenshots=tuple(
                artifact
                for artifact in (
                    _maybe_screenshot(item)
                    for item in (screenshots or [])
                )
                if artifact is not None
            ),
        )
        cwd_path = Path(cwd).resolve()
        return {
            "status": result.status,
            "run_id": run_id,
            "task_id": task_id,
            "output_dir": _display_path(result.output_dir, cwd_path),
            "files": [_display_path(path, cwd_path) for path in result.files],
        }

    def start_codex_session(
        self,
        *,
        prompt: str,
        cwd: str,
        model: str | None = None,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        execute: bool = False,
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        codex_cfg = self.cfg.target.codex if self.cfg.target and self.cfg.target.codex else None
        cli = codex_cfg.cli_command if codex_cfg is not None else "codex"
        argv = [cli, "exec", "--json", "-C", str(Path(cwd).expanduser())]
        argv.extend(["-m", model or DEFAULT_CODEX_MODEL])
        if reasoning_effort:
            argv.extend(["-c", f'reasoning_effort="{reasoning_effort}"'])
        argv.append(prompt)
        if not execute:
            return {"status": "dry_run", "argv": _redacted_prompt_argv(argv)}
        try:
            completed = self.codex_runner(
                argv,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_s)),
                cwd=str(Path(cwd).expanduser()),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "argv": _redacted_prompt_argv(argv),
                "timeout_s": timeout_s,
            }
        except FileNotFoundError:
            return {"status": "failed", "reason": "codex_binary_not_found", "argv": _redacted_prompt_argv(argv)}
        status = "completed" if completed.returncode == 0 else "failed"
        return redact({
            "status": status,
            "returncode": completed.returncode,
            "argv": _redacted_prompt_argv(argv),
            "stdout_tail": (completed.stdout or "")[-4000:],
            "stderr_tail": (completed.stderr or "")[-4000:],
        })

    def _gate_spec(
        self,
        *,
        task_id: str,
        run_id: str,
        gate: GateName,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None,
        expected_decisions: list[str] | None,
        expected_objections: list[str] | None,
        quality: str,
        model: str | None,
        budget_usd: float,
        timeout_s: int,
        planning_artifacts: list[dict[str, Any]] | None,
    ) -> DualAgentGateSpec:
        return DualAgentGateSpec(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            instruction=instruction,
            cwd=cwd,
            expected_specialists=tuple(expected_specialists or ()),
            expected_decisions=tuple(expected_decisions or ()),
            expected_objections=tuple(expected_objections or ()),
            quality=quality,  # type: ignore[arg-type]
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            planning_artifacts=tuple(
                artifact
                for artifact in (
                    _maybe_artifact(item)
                    for item in (planning_artifacts or [])
                )
                if artifact is not None
            ),
        )

    def _notifier(self) -> Any | None:
        if self.notifier is not None:
            return self.notifier
        if not telegram_enabled(self.cfg):
            return None
        return TelegramNotifier(self.cfg)


def build_codex_supervisor_mcp_server(
    cfg: Config,
    state: State,
    *,
    api: CodexSupervisorMcpAPI | None = None,
    mcp_cls: Any | None = None,
    runner: Runner = subprocess.run,
    codex_runner: Runner = subprocess.run,
    notifier: Any | None = None,
) -> Any:
    if mcp_cls is None:
        from mcp.server.fastmcp import FastMCP
        mcp_cls = FastMCP

    tool_api = api or CodexSupervisorMcpAPI(
        cfg,
        state,
        runner=runner,
        codex_runner=codex_runner,
        notifier=notifier,
    )
    mcp = mcp_cls("codex_supervisor")

    @mcp.tool()
    async def start_dual_agent_gate(
        task_id: str,
        run_id: str,
        gate: str,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        planning_artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await tool_api.start_dual_agent_gate(
            task_id=task_id,
            run_id=run_id,
            gate=gate,  # type: ignore[arg-type]
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            planning_artifacts=planning_artifacts,
        )

    @mcp.tool()
    def poll_resume_signal(
        task_id: str,
        run_id: str,
        gate: str,
        instruction: str,
        cwd: str,
        expected_specialists: list[str] | None = None,
        expected_decisions: list[str] | None = None,
        expected_objections: list[str] | None = None,
        quality: str = "best",
        model: str | None = None,
        budget_usd: float = 5.0,
        timeout_s: int = 600,
        planning_artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return tool_api.poll_resume_signal(
            task_id=task_id,
            run_id=run_id,
            gate=gate,  # type: ignore[arg-type]
            instruction=instruction,
            cwd=cwd,
            expected_specialists=expected_specialists,
            expected_decisions=expected_decisions,
            expected_objections=expected_objections,
            quality=quality,
            model=model,
            budget_usd=budget_usd,
            timeout_s=timeout_s,
            planning_artifacts=planning_artifacts,
        )

    @mcp.tool()
    def record_gate_round(
        run_id: str,
        task_id: str,
        gate: str,
        round_index: int,
        codex_decision: str,
        claude_decision: str,
        codex_confidence: float,
        claude_confidence: float,
        objection: str | None = None,
    ) -> dict[str, Any]:
        return tool_api.record_gate_round(
            run_id=run_id,
            task_id=task_id,
            gate=gate,  # type: ignore[arg-type]
            round_index=round_index,
            codex_decision=codex_decision,
            claude_decision=claude_decision,
            codex_confidence=codex_confidence,
            claude_confidence=claude_confidence,
            objection=objection,
        )

    @mcp.tool()
    def check_budget(
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        return tool_api.check_budget(
            rounds=rounds,
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )

    @mcp.tool()
    async def escalate_deadlock(
        run_id: str,
        task_id: str,
        gate: str,
        rounds: list[dict[str, Any]],
        per_gate_cap: int,
        task_budget: int,
    ) -> dict[str, Any]:
        return await tool_api.escalate_deadlock(
            run_id=run_id,
            task_id=task_id,
            gate=gate,  # type: ignore[arg-type]
            rounds=rounds,
            per_gate_cap=per_gate_cap,
            task_budget=task_budget,
        )

    @mcp.tool()
    def read_outcome(run_id: str, task_id: str) -> dict[str, Any]:
        return tool_api.read_outcome(run_id=run_id, task_id=task_id)

    @mcp.tool()
    def read_gate_transcript(run_id: str, task_id: str) -> dict[str, Any]:
        return tool_api.read_gate_transcript(run_id=run_id, task_id=task_id)

    @mcp.tool()
    def export_gate_artifacts(
        run_id: str,
        task_id: str,
        cwd: str,
        output_dir: str | None = None,
        screenshots: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return tool_api.export_gate_artifacts(
            run_id=run_id,
            task_id=task_id,
            cwd=cwd,
            output_dir=output_dir,
            screenshots=screenshots,
        )

    @mcp.tool()
    def start_codex_session(
        prompt: str,
        cwd: str,
        model: str | None = None,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        execute: bool = False,
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        return tool_api.start_codex_session(
            prompt=prompt,
            cwd=cwd,
            model=model,
            reasoning_effort=reasoning_effort,
            execute=execute,
            timeout_s=timeout_s,
        )

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Codex supervisor MCP server over stdio.")
    parser.add_argument(
        "--config",
        default=str(Path.home() / ".codex-supervisor" / "config.yaml"),
        help="Path to supervisor YAML config.",
    )
    args = parser.parse_args(argv)
    cfg = Config.load(args.config)
    state = State(cfg.supervisor.state_db)
    server = build_codex_supervisor_mcp_server(cfg, state)
    server.run(transport="stdio")


def _gate_result_payload(result: DualAgentGateResult) -> dict[str, Any]:
    payload = {
        "task_id": result.task_id,
        "gate": result.gate,
        "status": result.status,
        "attempts": result.attempts,
        "handoff_packet_path": str(result.handoff_packet_path),
        "probes": {
            key: asdict(value)
            for key, value in result.probes.items()
        },
        "outcome": result.outcome.model_dump() if result.outcome is not None else None,
        "escalation": asdict(result.escalation) if result.escalation is not None else None,
    }
    return redact(payload)


def _gate_round_payload(
    *,
    round_index: int,
    codex_decision: str,
    claude_decision: str,
    codex_confidence: float,
    claude_confidence: float,
    objection: str | None,
) -> dict[str, Any]:
    return asdict(GateRound(
        round_index=round_index,
        codex_decision=codex_decision,  # type: ignore[arg-type]
        claude_decision=claude_decision,  # type: ignore[arg-type]
        codex_confidence=codex_confidence,
        claude_confidence=claude_confidence,
        objection=objection,
    ))


def _gate_round_from_payload(payload: dict[str, Any]) -> GateRound:
    return GateRound(
        round_index=int(payload["round_index"]),
        codex_decision=str(payload["codex_decision"]),  # type: ignore[arg-type]
        claude_decision=str(payload["claude_decision"]),  # type: ignore[arg-type]
        codex_confidence=float(payload["codex_confidence"]),
        claude_confidence=float(payload["claude_confidence"]),
        objection=payload.get("objection"),
    )


def _maybe_artifact(payload: dict[str, Any]) -> PlanningArtifact | None:
    path = payload.get("path")
    kind = payload.get("kind")
    if not path or not kind:
        return None
    return PlanningArtifact(
        path=Path(str(path)).expanduser(),
        kind=str(kind),  # type: ignore[arg-type]
        mutable_by_worker=bool(payload.get("mutable_by_worker", False)),
    )


def _maybe_screenshot(payload: dict[str, Any]) -> ScreenshotArtifact | None:
    path = payload.get("path")
    if not path:
        return None
    return ScreenshotArtifact(
        path=Path(str(path)).expanduser(),
        label=str(payload.get("label") or Path(str(path)).stem),
        note=str(payload.get("note") or ""),
    )


def _display_path(path: Path, cwd: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(cwd))
    except ValueError:
        return str(resolved)


def _redacted_prompt_argv(argv: list[str]) -> list[str]:
    if not argv:
        return []
    return [*argv[:-1], "[PROMPT_REDACTED]"]


if __name__ == "__main__":
    main()
