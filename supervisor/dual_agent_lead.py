"""Claude Code `/lead` invocation boundary for dual-agent gates.

The deterministic Slice 0 validators live in `supervisor.dual_agent`. This
module is the thin process boundary that turns a gate request into a Claude
Code command, captures the result, and adapts the transcript back into those
validators. Tests inject a fake runner; live probes can use the default runner.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity


GateName = Literal[
    "intent",
    "prd_review",
    "tdd_review",
    "implementation_plan",
    "execution",
    "outcome_review",
    "blocked",
    "unknown",
]
ModelQuality = Literal["best", "balanced", "cheap"]


@dataclass(frozen=True)
class LeadInvocationRequest:
    task_id: str
    gate: GateName
    instruction: str
    cwd: str | Path
    expected_specialists: tuple[str, ...] = ()
    expected_decisions: tuple[str, ...] = ()
    expected_objections: tuple[str, ...] = ()
    quality: ModelQuality = "best"
    model: str | None = None
    budget_usd: float = 5.0
    timeout_s: int = 600
    cli_command: str = "claude"
    permission_mode: str = "dontAsk"
    tools: str = ""
    explicit_env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LeadInvocationResult:
    probe: ProbeResult
    outcome: Outcome | None
    command: list[str]
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    transcript: str
    model: str | None = None
    cost_usd: float | None = None


Runner = Callable[..., subprocess.CompletedProcess[str]]


def select_lead_model(
    gate: GateName,
    *,
    quality: ModelQuality,
    explicit_model: str | None = None,
) -> str:
    if explicit_model:
        return explicit_model
    if quality == "cheap":
        return "haiku"
    if quality == "balanced":
        return "sonnet"
    if gate == "execution":
        return "sonnet"
    return "opus"


def build_lead_prompt(request: LeadInvocationRequest) -> str:
    expected_bits: list[str] = []
    if request.expected_specialists:
        expected_bits.append(
            "Expected specialists: " + ", ".join(request.expected_specialists) + "."
        )
    if request.expected_decisions:
        expected_bits.append(
            "Expected decisions: " + "; ".join(request.expected_decisions) + "."
        )
    if request.expected_objections:
        expected_bits.append(
            "Expected objections: " + "; ".join(request.expected_objections) + "."
        )
    expected = "\n".join(expected_bits) if expected_bits else "No expected worker signal was provided."
    return (
        f"/lead Gate mode: {request.gate}. Task id: {request.task_id}.\n"
        f"{request.instruction.strip()}\n\n"
        f"{expected}\n\n"
        "Use the strongest available reasoning for this gate. Keep routine progress concise. "
        "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome> "
        "including task_id, summary, specialists, decisions, objections, changed_files, "
        "tests, test_status, confidence, evidence, claims, blockers, and escalations."
    )


def build_claude_lead_command(request: LeadInvocationRequest) -> list[str]:
    model = select_lead_model(
        request.gate,
        quality=request.quality,
        explicit_model=request.model,
    )
    return [
        request.cli_command,
        "--no-session-persistence",
        "-p",
        build_lead_prompt(request),
        "--output-format",
        "json",
        "--model",
        model,
        "--max-budget-usd",
        _format_budget(request.budget_usd),
        "--permission-mode",
        request.permission_mode,
        "--tools",
        request.tools,
    ]


def invoke_claude_lead(
    request: LeadInvocationRequest,
    *,
    runner: Runner = subprocess.run,
) -> LeadInvocationResult:
    command = build_claude_lead_command(request)
    env = dict(os.environ)
    env.update(request.explicit_env)
    try:
        proc = runner(
            command,
            cwd=str(Path(request.cwd)),
            env=env,
            capture_output=True,
            text=True,
            timeout=request.timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        stdout = _coerce_text(e.stdout)
        stderr = _coerce_text(e.stderr)
        return LeadInvocationResult(
            probe=ProbeResult(
                "P2",
                "red",
                "lead_invocation_timeout",
                {"timeout_s": request.timeout_s},
            ),
            outcome=None,
            command=command,
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=len(stdout.encode()),
            stderr_bytes=len(stderr.encode()),
            transcript="",
        )

    stdout = _coerce_text(proc.stdout)
    stderr = _coerce_text(proc.stderr)
    stdout_bytes = len(stdout.encode())
    stderr_bytes = len(stderr.encode())
    if proc.returncode != 0:
        return LeadInvocationResult(
            probe=ProbeResult(
                "P2",
                "red",
                "lead_invocation_failed",
                {"returncode": proc.returncode},
            ),
            outcome=None,
            command=command,
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            transcript="",
        )

    transcript, model, cost_usd = _extract_claude_json_payload(stdout)
    probe, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=request.expected_specialists,
        expected_decisions=request.expected_decisions,
        expected_objections=request.expected_objections,
    )
    return LeadInvocationResult(
        probe=probe,
        outcome=outcome,
        command=command,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        transcript=transcript,
        model=model,
        cost_usd=cost_usd,
    )


def _extract_claude_json_payload(stdout: str) -> tuple[str, str | None, float | None]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, None, None
    if not isinstance(payload, dict):
        return stdout, None, None
    transcript = payload.get("result")
    if not isinstance(transcript, str):
        transcript = stdout
    model = payload.get("model")
    if not isinstance(model, str):
        model = None
    cost = payload.get("total_cost_usd", payload.get("cost_usd"))
    cost_usd = float(cost) if isinstance(cost, (int, float)) else None
    return transcript, model, cost_usd


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _format_budget(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)
