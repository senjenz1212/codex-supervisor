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
from hashlib import sha256
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity


HANDOFF_PACKET_SCHEMA_VERSION = "dual-agent-handoff/v1"
CLAUDE_OPUS_ULTIMATE_MODEL = "opus"
CLAUDE_OPUS_UNDERLYING_MODEL = "claude-opus-4-8"
CLAUDE_BALANCED_MODEL = "sonnet"
CLAUDE_CHEAP_MODEL = "haiku"
CLAUDE_OPUS_ULTIMATE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "xhigh"},
}

GateName = Literal[
    "intent",
    "prd_review",
    "issues_review",
    "tdd_review",
    "implementation_plan",
    "execution",
    "outcome_review",
    "blocked",
    "unknown",
]
ModelQuality = Literal["best", "balanced", "cheap"]
PlanningArtifactKind = Literal[
    "decision_brief",
    "prd",
    "tdd_plan",
    "grill_findings",
    "issues",
    "implementation_plan",
    "outcome",
    "other",
]
OutcomeValidationAction = Literal[
    "retry_once_with_corrective_packet",
    "abort_to_operator",
]
ClaudeEffort = Literal["low", "medium", "high", "xhigh", "max"]
ExecutionLayerMode = Literal["lead_direct", "dynamic_workflow_preview"]
DynamicWorkflowTaskClass = Literal[
    "codebase_audit",
    "large_migration",
    "cortex_pod_four_reviewer_fanout",
    "eval_fixture_population",
    "other_fanout",
]

DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES: tuple[str, ...] = (
    "codex_and_lead_remain_supervision_layer",
    "per_subagent_budget_caps_verified",
    "permission_mode_and_tool_pins_verified",
    "machine_readable_output_verified",
    "headless_no_session_persistence_verified",
    "replay_or_ci_determinism_verified",
    "throwaway_worktree_comparison_recorded",
)


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
    permission_mode: str = "bypassPermissions"
    tools: str = "default"
    effort: ClaudeEffort = "max"
    execution_layer_mode: ExecutionLayerMode = "lead_direct"
    dynamic_workflow_task_class: DynamicWorkflowTaskClass | None = None
    explicit_env: dict[str, str] = field(default_factory=dict)
    handoff_packet_path: str | Path | None = None


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
    tokens_in: int | None = None
    tokens_out: int | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)


Runner = Callable[..., subprocess.CompletedProcess[str]]


class PlanningArtifact(BaseModel):
    path: str | Path
    kind: PlanningArtifactKind
    mutable_by_worker: bool = False


class HandoffPlanningArtifact(BaseModel):
    kind: PlanningArtifactKind
    path: str
    sha256: str
    mutable_by_worker: bool = False


class LeadSkillPin(BaseModel):
    path: str
    sha256: str
    version: str = "unversioned"


class OutcomeValidationPolicy(BaseModel):
    malformed_outcome: OutcomeValidationAction = "retry_once_with_corrective_packet"
    fidelity_failure: OutcomeValidationAction = "abort_to_operator"
    subprocess_failure: OutcomeValidationAction = "abort_to_operator"
    timeout: OutcomeValidationAction = "abort_to_operator"


class ExecutionLayerPolicy(BaseModel):
    mode: ExecutionLayerMode = "lead_direct"
    dynamic_workflow_task_class: DynamicWorkflowTaskClass | None = None
    supervision_layer: str = "codex_plus_lead"
    lead_execution_layer: str = "single_lead_worker"
    codex_supervises_final_artifact: bool = True
    preview_required_gates: list[str] = Field(default_factory=list)
    allowed_dynamic_workflow_task_classes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dynamic_workflow_requires_task_class(self) -> "ExecutionLayerPolicy":
        if self.mode == "dynamic_workflow_preview" and self.dynamic_workflow_task_class is None:
            raise ValueError("dynamic_workflow_preview requires dynamic_workflow_task_class")
        return self


class HandoffPacket(BaseModel):
    packet_schema_version: str = HANDOFF_PACKET_SCHEMA_VERSION
    task_id: str
    gate: GateName
    cwd: str
    instruction: str
    expected_specialists: list[str] = Field(default_factory=list)
    expected_decisions: list[str] = Field(default_factory=list)
    expected_objections: list[str] = Field(default_factory=list)
    suggested_skills: list[str] = Field(default_factory=lambda: ["/lead"])
    planning_artifacts: list[HandoffPlanningArtifact] = Field(default_factory=list)
    lead_skill: LeadSkillPin | None = None
    execution_layer_policy: ExecutionLayerPolicy = Field(default_factory=ExecutionLayerPolicy)
    outcome_validation_policy: OutcomeValidationPolicy = Field(default_factory=OutcomeValidationPolicy)

    @field_validator("packet_schema_version")
    @classmethod
    def _known_schema(cls, value: str) -> str:
        if value != HANDOFF_PACKET_SCHEMA_VERSION:
            raise ValueError(f"unsupported handoff packet schema: {value}")
        return value


def select_lead_model(
    gate: GateName,
    *,
    quality: ModelQuality,
    explicit_model: str | None = None,
) -> str:
    if explicit_model:
        return explicit_model
    if quality == "cheap":
        return CLAUDE_CHEAP_MODEL
    if quality == "balanced":
        return CLAUDE_BALANCED_MODEL
    return CLAUDE_OPUS_ULTIMATE_MODEL


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
    handoff = ""
    if request.handoff_packet_path is not None:
        handoff_path = Path(request.handoff_packet_path)
        handoff = (
            f"\nHandoff packet: {handoff_path}\n"
            "Treat the handoff packet as the bounded context source. "
            "Do not rewrite planning artifacts unless explicitly instructed.\n"
        )
    execution_layer = ""
    if request.execution_layer_mode == "dynamic_workflow_preview":
        execution_layer = (
            "\nExecution layer: dynamic workflow preview is allowed only behind the lead worker. "
            "Codex plus the lead remain the supervision layer; native workflow fan-out must not "
            "replace gate review, outcome validation, receipts, or the final Codex-supervised artifact. "
            "Use it only for fan-out execution work and report the preview gates in the outcome claims.\n"
        )
    return (
        f"/lead Gate mode: {request.gate}. Task id: {request.task_id}.\n"
        f"{request.instruction.strip()}\n\n"
        f"{handoff}"
        f"{execution_layer}"
        f"{expected}\n\n"
        "Use the strongest available reasoning for this gate. Keep routine progress concise. "
        f"{_outcome_block_contract()}"
    )


def _outcome_block_contract() -> str:
    return (
        "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome>. "
        "The JSON must include: task_id string, summary string, specialists array, "
        "decisions array, objections array, changed_files array, tests array, "
        "test_status string, confidence number from 0 to 1, confidence_rationale string, "
        "confidence_criteria array, and claims array. "
        "Every specialist object must include a string name and a string decision; "
        "do not use null for specialist decisions. Repeat each required decision in "
        "the top-level decisions array."
    )


def build_handoff_packet(
    request: LeadInvocationRequest,
    *,
    planning_artifacts: tuple[PlanningArtifact, ...] = (),
    lead_skill_path: str | Path | None = None,
    outcome_validation_policy: OutcomeValidationPolicy | None = None,
    suggested_skills: tuple[str, ...] = ("/lead",),
) -> HandoffPacket:
    cwd = Path(request.cwd).resolve()
    artifacts = [
        _handoff_artifact(artifact, cwd)
        for artifact in planning_artifacts
    ]
    lead_skill = _lead_skill_pin(lead_skill_path) if lead_skill_path is not None else None
    return HandoffPacket(
        task_id=request.task_id,
        gate=request.gate,
        cwd=str(cwd),
        instruction=request.instruction,
        expected_specialists=list(request.expected_specialists),
        expected_decisions=list(request.expected_decisions),
        expected_objections=list(request.expected_objections),
        suggested_skills=list(suggested_skills),
        planning_artifacts=artifacts,
        lead_skill=lead_skill,
        execution_layer_policy=_execution_layer_policy(request),
        outcome_validation_policy=outcome_validation_policy or OutcomeValidationPolicy(),
    )


def _execution_layer_policy(request: LeadInvocationRequest) -> ExecutionLayerPolicy:
    if request.execution_layer_mode == "dynamic_workflow_preview":
        return ExecutionLayerPolicy(
            mode="dynamic_workflow_preview",
            dynamic_workflow_task_class=request.dynamic_workflow_task_class,
            lead_execution_layer="lead_worker_may_use_dynamic_workflow",
            preview_required_gates=list(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES),
            allowed_dynamic_workflow_task_classes=[
                "codebase_audit",
                "large_migration",
                "cortex_pod_four_reviewer_fanout",
                "eval_fixture_population",
            ],
        )
    return ExecutionLayerPolicy()


def write_handoff_packet(
    request: LeadInvocationRequest,
    *,
    planning_artifacts: tuple[PlanningArtifact, ...] = (),
    lead_skill_path: str | Path | None = None,
    outcome_validation_policy: OutcomeValidationPolicy | None = None,
    suggested_skills: tuple[str, ...] = ("/lead",),
) -> Path:
    packet = build_handoff_packet(
        request,
        planning_artifacts=planning_artifacts,
        lead_skill_path=lead_skill_path,
        outcome_validation_policy=outcome_validation_policy,
        suggested_skills=suggested_skills,
    )
    path = handoff_packet_path(request.cwd, request.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet.model_dump(), indent=2, sort_keys=True) + "\n")
    return path


def handoff_packet_path(cwd: str | Path, task_id: str) -> Path:
    return Path(cwd).resolve() / ".handoff" / f"{_safe_task_id(task_id)}.json"


def read_handoff_packet(path: str | Path) -> HandoffPacket:
    return HandoffPacket(**json.loads(Path(path).read_text()))


def verify_planning_artifact_boundaries(packet_path: str | Path) -> ProbeResult:
    packet = read_handoff_packet(packet_path)
    cwd = Path(packet.cwd)
    changed: list[str] = []
    missing: list[str] = []
    for artifact in packet.planning_artifacts:
        path = cwd / artifact.path
        if not path.exists() or not path.is_file():
            missing.append(artifact.path)
            continue
        if compute_file_sha256(path) != artifact.sha256:
            changed.append(artifact.path)
    if missing:
        return ProbeResult(
            "P1",
            "red",
            "planning_artifact_missing",
            {"paths": missing},
        )
    if changed:
        return ProbeResult(
            "P1",
            "red",
            "planning_artifact_checksum_changed",
            {"paths": changed},
        )
    return ProbeResult("P1", "green", "planning_artifact_boundaries_ok")


def build_claude_lead_command(request: LeadInvocationRequest) -> list[str]:
    model = select_lead_model(
        request.gate,
        quality=request.quality,
        explicit_model=request.model,
    )
    command = [
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
    ]
    if not _uses_adaptive_opus_effort(model):
        command.extend(["--effort", request.effort])
    command.extend(["--tools", request.tools])
    return command


def invoke_claude_lead(
    request: LeadInvocationRequest,
    *,
    runner: Runner = subprocess.run,
) -> LeadInvocationResult:
    command = build_claude_lead_command(request)
    requested_model = _command_value(command, "--model")
    env = dict(os.environ)
    env.update(request.explicit_env)
    if requested_model is not None and _uses_adaptive_opus_effort(requested_model):
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = CLAUDE_OPUS_UNDERLYING_MODEL
        env["CLAUDE_CODE_EXTRA_BODY"] = json.dumps(CLAUDE_OPUS_ULTIMATE_EXTRA_BODY)
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
            model=requested_model,
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
            model=requested_model,
        )

    payload_probe, transcript, model, cost_usd, token_usage = _extract_claude_json_payload(stdout)
    if payload_probe is not None:
        return LeadInvocationResult(
            probe=payload_probe,
            outcome=None,
            command=command,
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            transcript="",
            model=model or requested_model,
            cost_usd=cost_usd,
            tokens_in=_int_from_mapping(token_usage, "tokens_in"),
            tokens_out=_int_from_mapping(token_usage, "tokens_out"),
            token_usage=token_usage,
        )
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
        model=model or requested_model,
        cost_usd=cost_usd,
        tokens_in=_int_from_mapping(token_usage, "tokens_in"),
        tokens_out=_int_from_mapping(token_usage, "tokens_out"),
        token_usage=token_usage,
    )


def _command_value(command: list[str], flag: str) -> str | None:
    try:
        index = command.index(flag)
    except ValueError:
        return None
    try:
        return command[index + 1]
    except IndexError:
        return None


def _uses_adaptive_opus_effort(model: str) -> bool:
    return (
        model == CLAUDE_OPUS_ULTIMATE_MODEL
        or model == CLAUDE_OPUS_UNDERLYING_MODEL
        or model.startswith(f"{CLAUDE_OPUS_UNDERLYING_MODEL}-")
    )


def _extract_claude_json_payload(
    stdout: str,
) -> tuple[ProbeResult | None, str, str | None, float | None, dict[str, Any]]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None, stdout, None, None, {}
    if not isinstance(payload, dict):
        return (
            ProbeResult(
                "P2",
                "red",
                "claude_json_schema_drift",
                {"missing_or_invalid": "object"},
            ),
            "",
            None,
            None,
            {},
        )
    transcript = payload.get("result")
    if not isinstance(transcript, str):
        token_usage = _token_usage_from_payload(payload)
        return (
            ProbeResult(
                "P2",
                "red",
                "claude_json_schema_drift",
                {"missing_or_invalid": "result"},
            ),
            "",
            _model_from_payload(payload),
            _cost_from_payload(payload),
            token_usage,
        )
    model = _model_from_payload(payload)
    cost_usd = _cost_from_payload(payload)
    token_usage = _token_usage_from_payload(payload)
    return None, transcript, model, cost_usd, token_usage


def _model_from_payload(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if not isinstance(model, str):
        return None
    return model


def _cost_from_payload(payload: dict[str, Any]) -> float | None:
    cost = payload.get("total_cost_usd", payload.get("cost_usd"))
    return float(cost) if isinstance(cost, (int, float)) else None


def _token_usage_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    model_usage = _model_usage_from_payload(payload)
    input_tokens = _int_token(usage.get("input_tokens"), model_usage.get("inputTokens"))
    cache_creation = _int_token(
        usage.get("cache_creation_input_tokens"),
        model_usage.get("cacheCreationInputTokens"),
    )
    cache_read = _int_token(
        usage.get("cache_read_input_tokens"),
        model_usage.get("cacheReadInputTokens"),
    )
    output_tokens = _int_token(usage.get("output_tokens"), model_usage.get("outputTokens"))
    token_parts = [
        value for value in (input_tokens, cache_creation, cache_read)
        if value is not None
    ]
    tokens_in = sum(token_parts) if token_parts else None
    out: dict[str, Any] = {}
    for key, value in (
        ("input_tokens", input_tokens),
        ("cache_creation_input_tokens", cache_creation),
        ("cache_read_input_tokens", cache_read),
        ("output_tokens", output_tokens),
        ("tokens_in", tokens_in),
        ("tokens_out", output_tokens),
        ("context_window", _int_token(model_usage.get("contextWindow"))),
        ("max_output_tokens", _int_token(model_usage.get("maxOutputTokens"))),
    ):
        if value is not None:
            out[key] = value
    return out


def _model_usage_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    model_usage = payload.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return {}
    model = _model_from_payload(payload)
    if model and isinstance(model_usage.get(model), dict):
        return model_usage[model]
    first = next(iter(model_usage.values()))
    return first if isinstance(first, dict) else {}


def _int_token(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(str(value))
        except (TypeError, ValueError):
            continue
    return None


def _int_from_mapping(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _handoff_artifact(
    artifact: PlanningArtifact,
    cwd: Path,
) -> HandoffPlanningArtifact:
    path = Path(artifact.path).resolve()
    try:
        relative = path.relative_to(cwd)
    except ValueError as e:
        raise ValueError(f"planning artifact outside cwd: {path}") from e
    if not path.exists() or not path.is_file():
        raise ValueError(f"planning artifact missing: {path}")
    return HandoffPlanningArtifact(
        kind=artifact.kind,
        path=relative.as_posix(),
        sha256=compute_file_sha256(path),
        mutable_by_worker=artifact.mutable_by_worker,
    )


def _lead_skill_pin(path: str | Path) -> LeadSkillPin:
    skill_path = Path(path).resolve()
    if not skill_path.exists() or not skill_path.is_file():
        raise ValueError(f"lead skill missing: {skill_path}")
    return LeadSkillPin(
        path=str(skill_path),
        sha256=compute_file_sha256(skill_path),
        version=_read_skill_version(skill_path),
    )


def compute_file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_skill_version(path: Path) -> str:
    text = path.read_text()
    if not text.startswith("---"):
        return "unversioned"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "unversioned"
    try:
        front_matter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return "unversioned"
    version = front_matter.get("version") if isinstance(front_matter, dict) else None
    return str(version) if version else "unversioned"


def _safe_task_id(task_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in task_id)


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
