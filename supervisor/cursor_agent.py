"""Cursor SDK invocation boundary for tri-agent workflow review.

The supervisor treats Cursor as an optional independent reviewer. Cursor must
return the same typed outcome block as Claude so the existing validators remain
the acceptance boundary.
"""
from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity
from .dual_agent_lead import GateName, ModelQuality, PlanningArtifact


@dataclass(frozen=True)
class CursorInvocationRequest:
    task_id: str
    gate: GateName
    instruction: str
    cwd: str | Path
    claude_outcome: dict[str, Any] | None = None
    planning_artifacts: tuple[PlanningArtifact, ...] = ()
    quality: ModelQuality = "best"
    model: str | None = None
    timeout_s: int = 600
    api_key: str | None = None
    mode: str = "plan"
    tool_receipts: tuple[dict[str, Any], ...] = ()
    expected_specialists: tuple[str, ...] = ("Cursor Reviewer",)
    expected_decisions: tuple[str, ...] = ()
    expected_objections: tuple[str, ...] = ()


@dataclass(frozen=True)
class CursorInvocationResult:
    probe: ProbeResult
    outcome: Outcome | None
    transcript: str
    agent_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    model: str | None = None
    duration_ms: int | None = None


CursorRunner = Callable[[CursorInvocationRequest], CursorInvocationResult]
StatusRunner = Callable[..., subprocess.CompletedProcess[str]]


def select_cursor_model(
    *,
    quality: ModelQuality,
    explicit_model: str | None = None,
) -> str:
    if explicit_model:
        return explicit_model
    # Cursor's current documented SDK examples use composer-2.5 for both local
    # and cloud agents. Keep one default until model discovery is wired.
    return "composer-2.5"


def build_cursor_prompt(request: CursorInvocationRequest) -> str:
    artifact_lines = [
        f"- {artifact.kind}: {Path(artifact.path)}"
        for artifact in request.planning_artifacts
    ]
    artifacts = "\n".join(artifact_lines) if artifact_lines else "- none"
    receipt_lines = [
        f"- {receipt.get('receipt_id') or receipt.get('id') or 'receipt'}: "
        f"{_receipt_prompt_payload(receipt)}"
        for receipt in request.tool_receipts
    ]
    receipts = "\n".join(receipt_lines) if receipt_lines else "- none"
    claude_outcome = request.claude_outcome or {}
    return "\n".join([
        f"Tri-agent review gate: {request.gate}.",
        f"Task id: {request.task_id}.",
        "",
        "Role: Cursor is an independent reviewer/challenger, not the implementer.",
        "Do not edit files. Inspect the worktree and artifacts, then judge the gate.",
        "",
        "Instruction:",
        request.instruction.strip(),
        "",
        "Planning artifacts:",
        artifacts,
        "",
        "Evidence receipts:",
        receipts,
        "",
        "Claude outcome JSON:",
        str(claude_outcome),
        "",
        "Return a specialist named Cursor Reviewer. Use decision accept only if the gate should advance.",
        _outcome_block_contract(),
    ])


def cursor_accepts(result: CursorInvocationResult | None) -> bool:
    if result is None or not result.probe.ok or result.outcome is None:
        return False
    decisions = [value.lower() for value in result.outcome.decisions]
    decisions.extend(specialist.decision.lower() for specialist in result.outcome.specialists)
    return any("accept" in decision for decision in decisions)


def invoke_cursor_agent(
    request: CursorInvocationRequest,
    *,
    status_runner: StatusRunner = subprocess.run,
) -> CursorInvocationResult:
    before_status = _git_status(request.cwd, status_runner=status_runner)
    try:
        transcript, metadata = _run_cursor_sdk(request)
    except ModuleNotFoundError as e:
        if e.name == "cursor_sdk":
            return CursorInvocationResult(
                probe=ProbeResult("CURSOR", "red", "cursor_sdk_missing"),
                outcome=None,
                transcript="",
            )
        raise
    except Exception as e:  # pragma: no cover - exact SDK exception classes vary.
        return CursorInvocationResult(
            probe=ProbeResult("CURSOR", "red", "cursor_invocation_failed", {"error": str(e)}),
            outcome=None,
            transcript="",
        )

    probe, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=request.expected_specialists,
        expected_decisions=request.expected_decisions,
        expected_objections=request.expected_objections,
    )
    after_status = _git_status(request.cwd, status_runner=status_runner)
    if before_status is not None and after_status is not None and before_status != after_status:
        probe = ProbeResult(
            "CURSOR",
            "red",
            "cursor_modified_worktree",
            {"before": before_status, "after": after_status},
        )
    elif probe.ok:
        probe = ProbeResult("CURSOR", "green", "cursor_review_ok", probe.details)

    return CursorInvocationResult(
        probe=probe,
        outcome=outcome,
        transcript=transcript,
        agent_id=metadata.get("agent_id"),
        run_id=metadata.get("run_id"),
        status=metadata.get("status"),
        model=metadata.get("model"),
        duration_ms=metadata.get("duration_ms"),
    )


def _run_cursor_sdk(request: CursorInvocationRequest) -> tuple[str, dict[str, Any]]:
    from cursor_sdk import Agent, LocalAgentOptions

    api_key = request.api_key or os.environ.get("CURSOR_API_KEY")
    kwargs: dict[str, Any] = {
        "model": select_cursor_model(quality=request.quality, explicit_model=request.model),
        "local": LocalAgentOptions(cwd=str(Path(request.cwd).expanduser())),
    }
    if api_key:
        kwargs["api_key"] = api_key

    with Agent.create(**kwargs) as agent:
        run = agent.send(build_cursor_prompt(request), {"mode": request.mode})
        transcript = run.text()
        metadata = {
            "agent_id": getattr(agent, "agent_id", None),
            "run_id": getattr(run, "id", None),
            "status": getattr(run, "status", None),
            "model": _model_id(getattr(run, "model", None) or getattr(agent, "model", None)),
            "duration_ms": getattr(run, "duration_ms", None),
        }
    return transcript, metadata


def _git_status(cwd: str | Path, *, status_runner: StatusRunner) -> str | None:
    try:
        completed = status_runner(
            ["git", "status", "--porcelain=v1"],
            cwd=str(Path(cwd).expanduser()),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def _model_id(model: Any) -> str | None:
    if model is None:
        return None
    if isinstance(model, str):
        return model
    value = getattr(model, "id", None)
    return str(value) if value is not None else str(model)


def _outcome_block_contract() -> str:
    return (
        "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome>. "
        "The JSON must include: task_id string, summary string, specialists array, "
        "decisions array, objections array, changed_files array, tests array, "
        "test_status string, confidence number from 0 to 1, confidence_rationale string, "
        "confidence_criteria array, and claims array. "
        "Every specialist object must include a string name and a string decision."
    )


def _receipt_prompt_payload(receipt: dict[str, Any]) -> str:
    allowed = {
        key: receipt.get(key)
        for key in (
            "receipt_id",
            "id",
            "kind",
            "type",
            "status",
            "result",
            "claims",
            "command",
            "changed_files",
            "remote",
            "branch",
            "commit",
            "path",
            "label",
        )
        if receipt.get(key) not in (None, "", [], {})
    }
    return json.dumps(allowed or receipt, sort_keys=True, default=str)
