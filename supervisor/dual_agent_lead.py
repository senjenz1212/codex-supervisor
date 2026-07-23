"""Provider-neutral lead invocation boundary for dual-agent gates.

The deterministic Slice 0 validators live in `supervisor.dual_agent`. This
module turns a gate request into an ``AgentTask`` and adapts the resulting
``AgentRunResult`` back into those validators. A legacy Claude subprocess edge
remains available for existing fake-runner tests and unmigrated callers.
"""
from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity
from .agent_mailbox import critical_review_prompt
from .agent_runtime import (
    AgentRunResult,
    AgentTask,
    RuntimeExecutionMode,
    normalize_runtime_execution_mode,
)
from .dual_agent_legacy_claude import (
    CLAUDE_CHEAP_MODEL as CLAUDE_CHEAP_MODEL,
    CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY as CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY,
    CLAUDE_OPUS_ULTIMATE_EXTRA_BODY as CLAUDE_OPUS_ULTIMATE_EXTRA_BODY,
    CLAUDE_OPUS_ULTIMATE_MODEL as CLAUDE_OPUS_ULTIMATE_MODEL,
    CLAUDE_OPUS_UNDERLYING_MODEL as CLAUDE_OPUS_UNDERLYING_MODEL,
    CLAUDE_PRIMARY_MODEL as CLAUDE_PRIMARY_MODEL,
    REPORT_ONLY_EXECUTION_ALLOWED_TOOLS,
    REPORT_ONLY_EXECUTION_PERMISSION_MODE,
    Runner,
    build_legacy_claude_command,
    build_legacy_claude_environment,
    run_legacy_claude,
    uses_adaptive_opus_effort,
)
from .provider_routing import (
    DEFAULT_ANTHROPIC_EFFORT,
    XHIGH_ANTHROPIC_EFFORT,
)
from .runtime_execution import RuntimeTaskRunner


HANDOFF_PACKET_SCHEMA_VERSION = "dual-agent-handoff/v1"


class _GateInvocationCancelled(RuntimeError):
    """Internal marker for a synchronous gate unwinding after cancellation."""

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

HIGH_EFFORT_GATES: frozenset[str] = frozenset({
    "prd_review",
    "issues_review",
    "tdd_review",
    "implementation_plan",
    "execution",
    "outcome_review",
})


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
    effort: ClaudeEffort | None = None
    execution_mode: RuntimeExecutionMode = "compatible"
    execution_layer_mode: ExecutionLayerMode = "lead_direct"
    corrective_retry: bool = False
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
    runtime: str | None = None
    runtime_run_id: str | None = None
    runtime_session_id: str | None = None
    runtime_result_hash: str | None = None
    runtime_duration_ms: int | None = None
    model_provenance: str = ""
    cost_provenance: str = ""
    token_provenance: str = ""
    agent_run_result: AgentRunResult | None = None


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
    return CLAUDE_PRIMARY_MODEL


def select_lead_effort(
    gate: GateName,
    *,
    quality: ModelQuality,
    explicit_effort: ClaudeEffort | None = None,
) -> ClaudeEffort:
    if explicit_effort:
        return explicit_effort
    if quality == "cheap":
        return "low"
    if gate in HIGH_EFFORT_GATES:
        return XHIGH_ANTHROPIC_EFFORT
    return DEFAULT_ANTHROPIC_EFFORT


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
    agentic_receipt_note = ""
    if request.agentic_lead_policy in {"allowed", "required"}:
        agentic_receipt_note = (
            "\nAgentic worker receipt discipline: Codex enforces "
            "agentic_lead_policy/min_subagents using supervisor-owned "
            "dynamic_subagent_result receipts outside the Claude lead. Do not "
            "spawn internal Claude subagents or block solely because the handoff "
            "packet mentions min_subagents or solo_exception_for_artifact_only_gates. "
            "Judge the gate artifacts and outcome; Codex will enforce external "
            "agentic receipt availability separately.\n"
        )
    execution_layer = ""
    if request.execution_layer_mode == "dynamic_workflow_preview":
        execution_layer = (
            "\nExecution layer: dynamic workflow preview is allowed only behind the lead worker. "
            "Codex plus the lead remain the supervision layer; native workflow fan-out must not "
            "replace gate review, outcome validation, receipts, or the final Codex-supervised artifact. "
            "Use it only for fan-out execution work and report the preview gates in the outcome claims.\n"
        )
    corrective_retry = request.corrective_retry
    implementation_contract = ""
    if request.gate == "execution" and corrective_retry:
        implementation_contract = (
            "\nCORRECTIVE REPORT CONTRACT (execution retry): The prior execution "
            "attempt already performed the implementation work. Do not make further "
            "file changes, rerun mutation steps, or repeat completed implementation. "
            "Inspect the current worktree only if needed to reconstruct the truthful "
            "outcome, then return the corrected required outcome block.\n"
        )
    elif request.gate == "execution":
        implementation_contract = (
            "\nIMPLEMENTATION CONTRACT (execution gate): You are the IMPLEMENTER, not a reviewer. "
            "Edit real worktree files to satisfy the accepted PRD / issues / TDD / "
            "implementation-plan referenced in the handoff. Do NOT merely review, validate, or summarize "
            "this gate. Work RED-first: confirm the planned tests fail, then implement until they pass. "
            "Keep execution context bounded: do not read workflow transcript artifacts such as "
            "transcript.jsonl, transcript.md, interactions.md, replay snapshots, or agentic-worker "
            "transcripts unless a specific line reference is required to resolve a blocker. Prefer the "
            "handoff packet, source planning artifacts, rg, and small file chunks under 200 lines; large "
            "tool outputs can trip the Claude Code rapid-refill breaker and do not count as product "
            "evidence. "
            "For code tasks, you MUST produce a non-empty implementation diff in the task-relevant source "
            "and/or test files. For explicit docs/report-only tasks, edit the requested docs/report "
            "artifacts. Incidental docs-only or handoff-only changes do not count as execution. If you "
            "cannot edit the required deliverable files, STOP and report the blocker instead of returning "
            "an accept. In the outcome, changed_files MUST list the files you actually changed. "
            "If you ran tests, test_status MUST reflect the tests you actually ran. If your Bash/test "
            "tooling is unavailable but the implementation diff is complete, do not block solely on that "
            "tooling outage: return accept with test_status unknown, list exact pytest commands/nodeids "
            "in tests, and make no tests-passed claim. The supervisor runtime floor will rerun those "
            "tests and block the gate if they fail or are missing.\n"
        )
    lesson_block = ""
    if request.injected_lesson_block and request.injected_lesson_block not in request.instruction:
        lesson_block = "\n\n" + request.injected_lesson_block.strip()
    policy_overlay_block = ""
    if request.policy_overlay_block and request.policy_overlay_block not in request.instruction:
        policy_overlay_block = "\n\n" + request.policy_overlay_block.strip()
    instruction_block = request.instruction.strip()
    if request.gate == "execution" and request.handoff_packet_path is not None:
        instruction_block = (
            "Execution request: implement the accepted task described in the handoff packet. "
            "Use the compact handoff packet and its source planning artifact paths as the "
            "authoritative context; do not rely on this inline prompt to restate the full "
            "operator request."
        )
        if corrective_retry:
            corrective_start = request.instruction.find("Corrective retry:")
            corrective_text = (
                request.instruction[corrective_start:]
                if corrective_start >= 0
                else request.instruction
            ).strip()
            if corrective_text:
                instruction_block += "\n\n" + corrective_text
    return (
        f"/lead Gate mode: {request.gate}. Task id: {request.task_id}.\n"
        f"{instruction_block}\n\n"
        f"{policy_overlay_block}"
        f"{lesson_block}"
        f"{handoff}"
        f"{agentic_receipt_note}"
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
    effort = (
        None
        if uses_adaptive_opus_effort(model)
        else select_lead_effort(
            request.gate,
            quality=request.quality,
            explicit_effort=request.effort,
        )
    )
    return build_legacy_claude_command(
        cli_command=request.cli_command,
        prompt=build_lead_prompt(request),
        model=model,
        budget_usd=request.budget_usd,
        permission_mode=_permission_mode_for_request(request),
        effort=effort,
        tools=request.tools,
        allowed_tools=_allowed_tools_for_request(request),
    )


def build_lead_agent_task(request: LeadInvocationRequest) -> AgentTask:
    """Translate one lead request into the provider-neutral runtime contract."""

    execution_mode = normalize_runtime_execution_mode(
        request.execution_mode,
        context="lead",
    )
    model = select_lead_model(
        request.gate,
        quality=request.quality,
        explicit_model=request.model,
    )
    metadata: dict[str, Any] = {
        "execution_mode": execution_mode,
        "lead_invocation": {
            "task_id": request.task_id,
            "gate": request.gate,
            "quality": request.quality,
            "execution_layer_mode": request.execution_layer_mode,
        },
        "permission_mode": _permission_mode_for_request(request),
        "max_budget_usd": float(request.budget_usd),
        "result_metadata": {
            "lead_task_id": request.task_id,
            "lead_gate": request.gate,
        },
    }
    if not uses_adaptive_opus_effort(model):
        metadata["effort"] = select_lead_effort(
            request.gate,
            quality=request.quality,
            explicit_effort=request.effort,
        )
    allowed_tools = _allowed_tools_for_request(request)
    if allowed_tools:
        metadata["allowed_tools"] = list(allowed_tools)
    return AgentTask(
        task_id=request.task_id,
        instruction=build_lead_prompt(request),
        cwd=Path(request.cwd),
        model=model,
        timeout_s=request.timeout_s,
        env=dict(request.explicit_env),
        metadata=metadata,
    )


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


def invoke_lead(
    request: LeadInvocationRequest,
    *,
    runner: Runner = subprocess.run,
    runtime_runner: RuntimeTaskRunner | None = None,
) -> LeadInvocationResult:
    """Invoke a lead without allowing operational fallback to legacy Claude."""

    execution_mode = normalize_runtime_execution_mode(
        request.execution_mode,
        context="lead",
    )
    if execution_mode == "operational":
        if runtime_runner is None:
            raise ValueError(
                "operational lead execution requires an injected "
                "RuntimeTaskRunner"
            )
        return _invoke_runtime_lead(request, runtime_runner=runtime_runner)
    if execution_mode in {"legacy", "replay", "fixture_replay", "test"}:
        return _invoke_legacy_claude_lead(request, runner=runner)
    if runtime_runner is not None:
        return _invoke_runtime_lead(request, runtime_runner=runtime_runner)
    return _invoke_legacy_claude_lead(request, runner=runner)


def invoke_claude_lead(
    request: LeadInvocationRequest,
    *,
    runner: Runner = subprocess.run,
    runtime_runner: RuntimeTaskRunner | None = None,
) -> LeadInvocationResult:
    """Backward-compatible alias for the provider-neutral lead invoker."""

    return invoke_lead(
        request,
        runner=runner,
        runtime_runner=runtime_runner,
    )


def _invoke_runtime_lead(
    request: LeadInvocationRequest,
    *,
    runtime_runner: RuntimeTaskRunner,
) -> LeadInvocationResult:
    task = build_lead_agent_task(request)
    try:
        execution = runtime_runner(task)
        result = execution.result
        if not isinstance(result, AgentRunResult):
            raise TypeError(
                "runtime runner must return RuntimeExecution with AgentRunResult"
            )
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        stdout = _coerce_text(getattr(exc, "stdout", None))
        stderr = _coerce_text(getattr(exc, "stderr", None))
        return LeadInvocationResult(
            probe=ProbeResult(
                "P2",
                "red",
                "lead_invocation_timeout",
                {"timeout_s": request.timeout_s},
            ),
            outcome=None,
            command=[],
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=len(stdout.encode()),
            stderr_bytes=len(stderr.encode()),
            transcript="",
            model=task.model,
        )
    except _GateInvocationCancelled:
        raise
    except Exception as exc:
        return LeadInvocationResult(
            probe=ProbeResult(
                "P2",
                "red",
                "lead_invocation_failed",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            ),
            outcome=None,
            command=[],
            stdout="",
            stderr="",
            stdout_bytes=0,
            stderr_bytes=0,
            transcript="",
            model=task.model,
        )

    stdout = _coerce_text(result.output)
    stderr_value = result.metadata.get("stderr")
    stderr = _coerce_text(
        stderr_value if isinstance(stderr_value, (str, bytes)) else None
    )
    stdout_bytes = len(stdout.encode())
    stderr_bytes = len(stderr.encode())
    token_usage = dict(result.token_usage)
    common = {
        "command": [],
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "model": result.resolved_model or task.model,
        "cost_usd": result.cost_usd,
        "tokens_in": _int_from_mapping(token_usage, "tokens_in"),
        "tokens_out": _int_from_mapping(token_usage, "tokens_out"),
        "token_usage": token_usage,
        "runtime": result.runtime,
        "runtime_run_id": result.run_id,
        "runtime_session_id": result.session_id,
        "runtime_result_hash": result.result_hash,
        "runtime_duration_ms": result.duration_ms,
        "model_provenance": result.model_provenance,
        "cost_provenance": result.cost_provenance,
        "token_provenance": result.token_provenance,
        "agent_run_result": result,
    }
    if result.status != "completed":
        returncode = _int_token(result.metadata.get("returncode"))
        details: dict[str, Any] = {
            "runtime": result.runtime,
            "runtime_status": result.status,
            "run_id": result.run_id,
        }
        if returncode is not None:
            details["returncode"] = returncode
        if stderr:
            details["stderr_tail"] = stderr[-2000:]
        if stdout:
            details["stdout_tail"] = stdout[-2000:]
        if result.status == "cancelled":
            reason = "lead_invocation_cancelled"
        elif result.status in {"timeout", "timed_out"} or returncode == 124:
            reason = "lead_invocation_timeout"
            details["timeout_s"] = request.timeout_s
        else:
            reason = "lead_invocation_failed"
        return LeadInvocationResult(
            probe=ProbeResult("P2", "red", reason, details),
            outcome=None,
            transcript="",
            **common,
        )

    probe, outcome = evaluate_outcome_fidelity(
        stdout,
        expected_specialists=request.expected_specialists,
        expected_decisions=request.expected_decisions,
        expected_objections=request.expected_objections,
    )
    return LeadInvocationResult(
        probe=probe,
        outcome=outcome,
        transcript=stdout,
        **common,
    )


def _invoke_legacy_claude_lead(
    request: LeadInvocationRequest,
    *,
    runner: Runner,
) -> LeadInvocationResult:
    command = build_claude_lead_command(request)
    requested_model = select_lead_model(
        request.gate,
        quality=request.quality,
        explicit_model=request.model,
    )
    env = build_legacy_claude_environment(
        explicit_env=request.explicit_env,
        requested_model=requested_model,
        gate=request.gate,
    )
    try:
        proc = run_legacy_claude(
            command,
            cwd=request.cwd,
            env=env,
            timeout_s=request.timeout_s,
            runner=runner,
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
    model_provenance = (
        "legacy_claude_json.model"
        if model is not None
        else "lead_request.model"
    )
    cost_provenance = (
        "legacy_claude_json.cost"
        if cost_usd is not None
        else ""
    )
    token_provenance = (
        "legacy_claude_json.usage"
        if token_usage
        else ""
    )
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
            model_provenance=model_provenance,
            cost_provenance=cost_provenance,
            token_provenance=token_provenance,
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
        model_provenance=model_provenance,
        cost_provenance=cost_provenance,
        token_provenance=token_provenance,
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
