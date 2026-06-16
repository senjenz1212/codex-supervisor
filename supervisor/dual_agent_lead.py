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
from .agent_mailbox import critical_review_prompt


HANDOFF_PACKET_SCHEMA_VERSION = "dual-agent-handoff/v1"
CLAUDE_OPUS_ULTIMATE_MODEL = "opus"
CLAUDE_OPUS_UNDERLYING_MODEL = "claude-opus-4-8"
CLAUDE_OPUS_LEGACY_FABLE_MODEL = "claude-fable-5"
CLAUDE_OPUS_SAFE_OVERRIDE_MODEL = "claude-opus-4-6"
CLAUDE_BALANCED_MODEL = "sonnet"
CLAUDE_CHEAP_MODEL = "haiku"
CLAUDE_OPUS_ULTIMATE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "xhigh"},
}
CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "max"},
}
REPORT_ONLY_EXECUTION_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "LS",
    "Edit",
    "MultiEdit",
    "Write",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(*.venv/bin/python -m pytest*)",
    "Bash(python -m pytest*)",
    "Bash(python3 -m pytest*)",
    "Bash(*.venv/bin/python -m cortex.vela_eval.runner*)",
    "Bash(python -m cortex.vela_eval.runner*)",
    "Bash(python3 -m cortex.vela_eval.runner*)",
    "Bash(curl http://127.0.0.1:5173*)",
    "Bash(curl http://localhost:5173*)",
)
REPORT_ONLY_EXECUTION_PERMISSION_MODE = "dontAsk"

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
AgenticLeadPolicyMode = Literal["off", "allowed", "required"]
EvidenceGrade = Literal["self_reported", "lead_captured", "runtime_native"]
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
    agentic_lead_policy: AgenticLeadPolicyMode = "off"
    min_subagents: int = 0
    required_roles: tuple[str, ...] = ()
    solo_exception_for_artifact_only_gates: bool = False
    required_evidence_grade: EvidenceGrade = "self_reported"
    explicit_env: dict[str, str] = field(default_factory=dict)
    handoff_packet_path: str | Path | None = None
    injected_lesson_block: str = ""
    injected_lesson_block_sha256: str = ""
    injected_lesson_ids: tuple[str, ...] = ()
    policy_overlay_block: str = ""
    policy_overlay_block_sha256: str = ""
    policy_overlay_hash: str = ""
    policy_proposal_id: str = ""


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


class AgenticLeadPolicyConfig(BaseModel):
    policy: AgenticLeadPolicyMode = "off"
    min_subagents: int = 0
    required_roles: list[str] = Field(default_factory=list)
    solo_exception_for_artifact_only_gates: bool = False
    required_evidence_grade: EvidenceGrade = "self_reported"


class ExecutionLayerPolicy(BaseModel):
    mode: ExecutionLayerMode = "lead_direct"
    dynamic_workflow_task_class: DynamicWorkflowTaskClass | None = None
    supervision_layer: str = "codex_plus_lead"
    lead_execution_layer: str = "single_lead_worker"
    codex_supervises_final_artifact: bool = True
    preview_required_gates: list[str] = Field(default_factory=list)
    allowed_dynamic_workflow_task_classes: list[str] = Field(default_factory=list)
    agentic_lead_policy: AgenticLeadPolicyConfig = Field(default_factory=AgenticLeadPolicyConfig)

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
    injected_lesson_block: str = ""
    injected_lesson_block_sha256: str = ""
    injected_lesson_ids: list[str] = Field(default_factory=list)
    policy_overlay_block: str = ""
    policy_overlay_block_sha256: str = ""
    policy_overlay_hash: str = ""
    policy_proposal_id: str = ""

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
    implementation_contract = ""
    if request.gate == "execution":
        implementation_contract = (
            "\nIMPLEMENTATION CONTRACT (execution gate): You are the IMPLEMENTER, not a reviewer. "
            "Edit real worktree files to satisfy the accepted PRD / issues / TDD / "
            "implementation-plan referenced in the handoff. Do NOT merely review, validate, or summarize "
            "this gate. Work RED-first: confirm the planned tests fail, then implement until they pass. "
            "For code tasks, you MUST produce a non-empty implementation diff in the task-relevant source "
            "and/or test files. For explicit docs/report-only tasks, edit the requested docs/report "
            "artifacts. Incidental docs-only or handoff-only changes do not count as execution. If you "
            "cannot edit the required deliverable files, STOP and report the blocker instead of returning "
            "an accept. In the outcome, changed_files MUST list the files you actually changed and "
            "test_status MUST reflect tests you actually ran.\n"
        )
    lesson_block = ""
    if request.injected_lesson_block and request.injected_lesson_block not in request.instruction:
        lesson_block = "\n\n" + request.injected_lesson_block.strip()
    policy_overlay_block = ""
    if request.policy_overlay_block and request.policy_overlay_block not in request.instruction:
        policy_overlay_block = "\n\n" + request.policy_overlay_block.strip()
    return (
        f"/lead Gate mode: {request.gate}. Task id: {request.task_id}.\n"
        f"{request.instruction.strip()}\n\n"
        f"{policy_overlay_block}"
        f"{lesson_block}"
        f"{handoff}"
        f"{execution_layer}"
        f"{implementation_contract}"
        f"{expected}\n\n"
        "Use the strongest available reasoning for this gate. Keep routine progress concise. "
        f"{critical_review_prompt('gate handoff')} "
        f"{_outcome_block_contract()}"
    )


def _outcome_block_contract() -> str:
    return (
        "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome>. "
        "The JSON must include: task_id string, summary string, specialists array, "
        "decisions array, objections array, changed_files array, tests array, "
        "test_status string, confidence number from 0 to 1, confidence_rationale string, "
        "confidence_criteria array, claims array, and critical_review object. "
        "critical_review must include strongest_objection string, missing_evidence array, "
        "contradictions_checked array, assumptions_to_verify array, "
        "what_would_change_my_mind string, decision string, and severity string. "
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
        injected_lesson_block=request.injected_lesson_block,
        injected_lesson_block_sha256=request.injected_lesson_block_sha256,
        injected_lesson_ids=list(request.injected_lesson_ids),
        policy_overlay_block=request.policy_overlay_block,
        policy_overlay_block_sha256=request.policy_overlay_block_sha256,
        policy_overlay_hash=request.policy_overlay_hash,
        policy_proposal_id=request.policy_proposal_id,
    )


def _execution_layer_policy(request: LeadInvocationRequest) -> ExecutionLayerPolicy:
    agentic_policy = AgenticLeadPolicyConfig(
        policy=request.agentic_lead_policy,
        min_subagents=max(0, int(request.min_subagents)),
        required_roles=[str(role) for role in request.required_roles if str(role).strip()],
        solo_exception_for_artifact_only_gates=bool(request.solo_exception_for_artifact_only_gates),
        required_evidence_grade=request.required_evidence_grade,
    )
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
                "other_fanout",
            ],
            agentic_lead_policy=agentic_policy,
        )
    return ExecutionLayerPolicy(agentic_lead_policy=agentic_policy)


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
        _permission_mode_for_request(request),
    ]
    if not _uses_adaptive_opus_effort(model):
        command.extend(["--effort", request.effort])
    command.extend(["--tools", request.tools])
    allowed_tools = _allowed_tools_for_request(request)
    if allowed_tools:
        command.extend(["--allowedTools", *allowed_tools])
    return command


def _allowed_tools_for_request(request: LeadInvocationRequest) -> tuple[str, ...]:
    if _is_report_only_execution_request(request):
        return REPORT_ONLY_EXECUTION_ALLOWED_TOOLS
    return ()


def _permission_mode_for_request(request: LeadInvocationRequest) -> str:
    if _is_report_only_execution_request(request):
        return REPORT_ONLY_EXECUTION_PERMISSION_MODE
    return request.permission_mode


def _is_report_only_execution_request(request: LeadInvocationRequest) -> bool:
    if request.gate != "execution":
        return False
    text = " ".join((
        request.instruction,
        request.task_id,
        str(request.handoff_packet_path or ""),
    )).lower().replace("_", "-")
    report_markers = (
        "report-only",
        "report only",
        "docs-only",
        "docs only",
        "documentation-only",
        "documentation only",
        "artifact-only",
        "artifact only",
        "pilot report",
        "report artifact",
        "benchmark artifact",
    )
    deliverable_markers = (
        "docs/dual-agent/",
        "autoresearch-report",
        "report.md",
        "outcome-review.md",
    )
    return any(marker in text for marker in report_markers) and any(
        marker in text for marker in deliverable_markers
    )


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
        # r-2026-06-10 (write-canary evidence matrix; through-stack event
        # 660691): the pinned claude-opus-4-8 underlying route breaks headless
        # bypassPermissions — Edit/Write/Bash fall into the interactive prompt
        # flow and auto-deny ("you haven't granted it yet"). Direct A/B
        # canaries: the default opus route writes fine, with or without the
        # extra body; ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-8 denies
        # either way. Execution gates must write, so by default they drop the
        # pin and use the CLI's default opus route (we POP any inherited pin —
        # the parent daemon/shell or secrets.env may carry a stale value);
        # read-only planning/review gates keep a pin for quality. Both pins
        # are operator-overridable via env (verify any new pin with the
        # direct write canary BEFORE trusting it — that is how 4-8 was
        # caught): CODEX_SUPERVISOR_EXECUTION_OPUS_MODEL (empty = default
        # route) and CODEX_SUPERVISOR_PLANNING_OPUS_MODEL (default
        # claude-opus-4-8; e.g. claude-opus-4-6 after live canary).
        pin = _underlying_opus_model_for_gate(request.gate)
        if pin is None:
            env.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        else:
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = pin
        env["CLAUDE_CODE_EXTRA_BODY"] = json.dumps(_opus_extra_body_for_pin(pin))
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
    except (OSError, subprocess.SubprocessError) as e:
        return LeadInvocationResult(
            probe=ProbeResult(
                "P2",
                "red",
                "lead_invocation_failed",
                {
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            ),
            outcome=None,
            command=command,
            stdout="",
            stderr="",
            stdout_bytes=0,
            stderr_bytes=0,
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
                {
                    "returncode": proc.returncode,
                    "stdout_tail": stdout[-2000:],
                    "stderr_tail": stderr[-2000:],
                },
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


def _underlying_opus_model_for_gate(gate: str) -> str | None:
    """Resolve the ANTHROPIC_DEFAULT_OPUS_MODEL pin for one gate.

    Execution gates default to None (no pin: the CLI's default opus route is
    the only one verified to honor headless bypassPermissions, 2026-06-10).
    Planning/review gates default to CLAUDE_OPUS_UNDERLYING_MODEL. Operators
    may override either via env; an empty override means "no pin".
    """
    if gate == "execution":
        override = os.environ.get("CODEX_SUPERVISOR_EXECUTION_OPUS_MODEL", "").strip()
        return _canonical_underlying_opus_pin(override) or None
    override = os.environ.get("CODEX_SUPERVISOR_PLANNING_OPUS_MODEL", "").strip()
    return _canonical_underlying_opus_pin(override) or CLAUDE_OPUS_UNDERLYING_MODEL


def _canonical_underlying_opus_pin(value: str) -> str:
    if value == CLAUDE_OPUS_LEGACY_FABLE_MODEL:
        return CLAUDE_OPUS_SAFE_OVERRIDE_MODEL
    return value


def _opus_extra_body_for_pin(pin: str | None) -> dict[str, Any]:
    """Return a Claude extra body compatible with the selected Opus route."""
    if pin and pin.startswith(CLAUDE_OPUS_SAFE_OVERRIDE_MODEL):
        return CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY
    return CLAUDE_OPUS_ULTIMATE_EXTRA_BODY


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
