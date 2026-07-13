"""Hermetic end-to-end composition tracer for Harness v1 public seams.

This module deliberately does not invoke provider CLIs, SWE-bench, or the
Unity Test Framework.  It exercises the real local composition boundaries with
deterministic fake runtime transports and a hidden local fixture verifier.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_runtime import (
    ClaudeCodeRuntime,
    CodexRuntime,
    RuntimeTransportResult,
)
from .claim_gate import ClaimGate, ClaimLevel, UnsupportedClaimError
from .evidence_committer import (
    EvidenceArtifact,
    EvidenceCommitRequest,
    EvidenceCommitter,
    EvidenceGradeHistory,
    HmacCheckpointAuthority,
)
from .evidence_ledger import LedgerVerification, canonical_json_bytes
from .ledger_checkpoints import FilesystemTrustedCheckpointPinStore
from .experiment_kernel import (
    Arm,
    ArmBudget,
    ArmExecution,
    ArmExecutionError,
    ArmOutcome,
    ExperimentKernel,
    ExperimentSpec,
    SqliteExperimentStore,
    TreatmentDescriptor,
)
from .grade_revisions import (
    DecisionGradeCitation,
    GradeBook,
    GradeInvalidation,
    GradeRevision,
    RunEnvelopeRef,
)
from .arm_executor import RepositoryArmExecutor, RuntimeArmPlan
from .run_registry import (
    PENDING_SESSION_SOURCE,
    bind_workflow_target_session,
    load_session_registration,
    register_submitted_workflow,
)
from .state import State
from .target.types import ScopeContract
from .task_environment import (
    FrozenTaskResult,
    GenericRepositoryTask,
    Grade,
    TaskSpec,
    UnityRepositoryTask,
    default_task_platform,
)
from .trace_graph import (
    ClosureResult,
    EdgeType,
    NodeType,
    TraceEdge,
    TraceGraph,
    TraceIdentity,
    TraceNode,
    canonical_revision_hash,
    trace_instance_id_from_hash,
)


HERMETIC_MODE = "hermetic"
_MARKER_FILE = "TRACER_RESULT.txt"
_NOT_EXECUTED = (
    "Claude Code CLI",
    "Codex CLI",
    "SWE-bench",
    "Unity Test Framework",
)


def _neutral_marker_content() -> str:
    return (
        "hermetic Harness v1 tracer\n"
        "network=disabled\n"
        "external_provider_calls=0\n"
    )


@dataclass(frozen=True)
class ExecutionCoordinate:
    task_family: str
    runtime_kind: str
    arm: Arm

    def to_dict(self) -> dict[str, str]:
        return {
            "task_family": self.task_family,
            "runtime_kind": self.runtime_kind,
            "arm": self.arm.value,
        }


@dataclass(frozen=True)
class HermeticTransportInvocation:
    run_id: str
    session_id: str
    runtime_kind: str
    workspace: Path
    argv: tuple[str, ...]
    env: Mapping[str, str]
    arm_adapter: str
    entrypoint: str
    treatment_hash: str
    marker_existed_before: bool
    hidden_read_blocked: bool
    network_used: bool = False
    external_process_started: bool = False


@dataclass(frozen=True)
class HermeticVerifierReceipt:
    frozen_result: FrozenTaskResult
    grade: Grade
    workspace: Path
    workspace_absent_at_verification: bool
    treatment_blind: bool
    hidden_content_absent: bool
    hidden_fixture_present: bool


@dataclass(frozen=True)
class HermeticGradeHistory:
    run: RunEnvelopeRef
    revisions: tuple[GradeRevision, ...]
    invalidations: tuple[GradeInvalidation, ...]


@dataclass(frozen=True)
class HermeticTracerExecution:
    execution_id: str
    coordinate: ExecutionCoordinate
    experiment_id: str
    assignment_id: str
    task_spec: TaskSpec
    transport: HermeticTransportInvocation
    arm_execution: ArmExecution
    outcome: ArmOutcome
    blinded_result: FrozenTaskResult
    verifier_receipts: tuple[HermeticVerifierReceipt, HermeticVerifierReceipt]
    grade_history: HermeticGradeHistory
    workspace_removed: bool


@dataclass(frozen=True)
class HermeticTracerReport:
    mode: str
    operational_efficacy_evidence: bool
    external_provider_calls: int
    not_executed: tuple[str, ...]
    aggregate_run_id: str
    executions: tuple[HermeticTracerExecution, ...]
    registered_run_ids: tuple[str, ...]
    aggregate_events: tuple[dict[str, Any], ...]
    ledger_verifications: Mapping[str, LedgerVerification]
    trace_graph: TraceGraph
    trace_closure: ClosureResult
    promotion_trace: tuple[TraceNode, ...]
    claim_evidence_bundle: Mapping[str, Any]
    claim_report: Mapping[str, Any]
    claim_level: ClaimLevel
    l2_refusal: str
    l3_refusal: str
    evidence_root: Path
    evidence_commit_status: str
    evidence_commit_phases: tuple[str, ...]
    artifact_manifest: Mapping[str, Any]
    manifest_event_id: int
    checkpoint_refs: Mapping[str, str]
    projection: Mapping[str, Any]
    projection_sha256: str
    trace_store_path: Path
    run_registry_path: Path


@dataclass(frozen=True)
class TreatmentWireCutReport:
    b_failed: bool
    b_failure: str
    c_completed: bool
    c_treatment_hash: str
    supervisor_orchestration_calls: int
    adapter_invocations: Mapping[str, int]


@dataclass(frozen=True)
class _RecordedArmExecution:
    arm: Arm
    budget: ArmBudget
    assignment_id: str
    execution: ArmExecution


@dataclass(frozen=True)
class _TaskFixture:
    task_spec: TaskSpec
    adapter_type: type[GenericRepositoryTask] | type[UnityRepositoryTask]
    verifier: "_HermeticHiddenVerifier"


RuntimeStartCallback = Callable[
    [HermeticTransportInvocation, Mapping[str, Any]],
    None,
]


class _SupervisorOrchestrationWire:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.calls = 0

    def invoke(self) -> None:
        self.calls += 1
        if not self.enabled:
            raise RuntimeError("supervisor orchestration wire is disabled")


class _ProductionBaselineTreatmentAdapter:
    arm_adapter = "production-baseline"
    entrypoint = "baseline.execute"

    def execute(
        self,
        *,
        marker: Path,
        workspace: Path,
        write_text: Callable[..., None],
    ) -> None:
        write_text(
            marker,
            _neutral_marker_content(),
            workspace=workspace,
        )


class _SupervisorOrchestrationTreatmentAdapter:
    arm_adapter = "supervisor-orchestration"
    entrypoint = "supervisor.execute"

    def __init__(self, wire: _SupervisorOrchestrationWire) -> None:
        self.wire = wire

    def execute(
        self,
        *,
        marker: Path,
        workspace: Path,
        write_text: Callable[..., None],
    ) -> None:
        self.wire.invoke()
        write_text(
            marker,
            _neutral_marker_content(),
            workspace=workspace,
        )


class _ComputeMatchedDirectTreatmentAdapter:
    arm_adapter = "compute-matched-direct"
    entrypoint = "direct.execute"

    def execute(
        self,
        *,
        marker: Path,
        workspace: Path,
        write_text: Callable[..., None],
    ) -> None:
        write_text(
            marker,
            _neutral_marker_content(),
            workspace=workspace,
        )


class _DeterministicFakeTransport:
    """In-process transport that permits reads/writes only in its workspace."""

    def __init__(
        self,
        *,
        runtime_kind: str,
        workspace_root: Path,
        hidden_root: Path,
        on_start: RuntimeStartCallback,
        supervisor_orchestration_enabled: bool = True,
    ) -> None:
        self.runtime_kind = runtime_kind
        self.workspace_root = workspace_root.resolve()
        self.hidden_root = hidden_root.resolve()
        self.on_start = on_start
        self.supervisor_wire = _SupervisorOrchestrationWire(
            enabled=supervisor_orchestration_enabled
        )
        adapters = (
            _ProductionBaselineTreatmentAdapter(),
            _SupervisorOrchestrationTreatmentAdapter(
                self.supervisor_wire
            ),
            _ComputeMatchedDirectTreatmentAdapter(),
        )
        self.treatment_adapters = {
            adapter.arm_adapter: adapter
            for adapter in adapters
        }
        self.adapter_invocations = {
            adapter.arm_adapter: 0
            for adapter in adapters
        }
        self._results: dict[str, RuntimeTransportResult] = {}
        self._invocations: dict[str, HermeticTransportInvocation] = {}

    def supports_filesystem_isolation(self, mode: str) -> bool:
        return mode == "workspace_only"

    async def start(
        self,
        *,
        run_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout_s: int,
        metadata: Mapping[str, Any],
    ) -> str:
        del timeout_s
        workspace = cwd.resolve()
        try:
            workspace.relative_to(self.workspace_root)
        except ValueError as exc:
            raise RuntimeError(
                "hermetic transport workspace escaped work root"
            ) from exc

        isolation = metadata.get("filesystem_isolation")
        if not isinstance(isolation, Mapping):
            raise RuntimeError("hermetic transport requires filesystem isolation")
        if isolation.get("mode") != "workspace_only":
            raise RuntimeError("hermetic transport requires workspace_only mode")
        deny_paths = tuple(
            Path(value).expanduser().resolve()
            for value in isolation.get("deny_paths") or ()
        )
        if not any(
            self.hidden_root == denied
            or self.hidden_root.is_relative_to(denied)
            for denied in deny_paths
        ):
            raise RuntimeError("hidden verifier root is not denied to the runtime")

        experiment_metadata = metadata.get("experiment")
        if not isinstance(experiment_metadata, Mapping):
            raise RuntimeError(
                "hermetic transport requires experiment launch metadata"
            )
        raw_descriptor = experiment_metadata.get("treatment_descriptor")
        if not isinstance(raw_descriptor, Mapping):
            raise RuntimeError(
                "hermetic transport requires a treatment descriptor"
            )
        descriptor = TreatmentDescriptor.from_mapping(raw_descriptor)
        treatment_hash = str(
            experiment_metadata.get("treatment_hash") or ""
        )
        if descriptor.treatment_hash != treatment_hash:
            raise RuntimeError(
                "hermetic transport treatment hash does not match descriptor"
            )
        adapter = self.treatment_adapters.get(descriptor.arm_adapter)
        if adapter is None:
            raise RuntimeError(
                "hermetic transport treatment adapter is not registered"
            )
        if adapter.entrypoint != descriptor.entrypoint:
            raise RuntimeError(
                "hermetic transport treatment entrypoint does not match adapter"
            )

        marker = workspace / _MARKER_FILE
        marker_existed_before = marker.exists()
        if marker_existed_before:
            raise RuntimeError("fresh workspace already contained tracer marker")

        hidden_fixture = self.hidden_root / "expected.txt"
        try:
            self._read_workspace_bytes(hidden_fixture, workspace=workspace)
        except PermissionError:
            hidden_read_blocked = True
        else:
            raise RuntimeError("hermetic transport read hidden verifier material")

        sequence = len(self._invocations) + 1
        started_at_ms = 1_720_000_000_000 + sequence * 10
        session_id = f"hermetic-session-{run_id}"
        invocation = HermeticTransportInvocation(
            run_id=run_id,
            session_id=session_id,
            runtime_kind=self.runtime_kind,
            workspace=workspace,
            argv=tuple(argv),
            env=dict(env),
            arm_adapter=descriptor.arm_adapter,
            entrypoint=descriptor.entrypoint,
            treatment_hash=descriptor.treatment_hash,
            marker_existed_before=marker_existed_before,
            hidden_read_blocked=hidden_read_blocked,
        )
        self.on_start(invocation, metadata)

        self.adapter_invocations[descriptor.arm_adapter] += 1
        adapter.execute(
            marker=marker,
            workspace=workspace,
            write_text=self._write_workspace_text,
        )
        message = (
            "hermetic transport completed a local fixture edit"
        )
        raw_events: tuple[Mapping[str, Any], ...] = (
            {
                "type": "run.started",
                "session_id": session_id,
                "ts_ms": started_at_ms,
            },
            {
                "type": "agent_message",
                "session_id": session_id,
                "message": message,
                "ts_ms": started_at_ms + 1,
            },
            {
                "type": "run.completed",
                "session_id": session_id,
                "ts_ms": started_at_ms + 2,
            },
        )
        self._invocations[run_id] = invocation
        self._results[run_id] = RuntimeTransportResult(
            returncode=0,
            stdout=message,
            stderr="",
            raw_events=raw_events,
            started_at_ms=started_at_ms,
            ended_at_ms=started_at_ms + 2,
            cost_usd=0.0,
            resolved_model=_argument_after_any(argv, "--model", "-m"),
            token_usage={"tokens_in": 0, "tokens_out": 0},
            model_provenance="deterministic_fake_transport.config",
            cost_provenance="deterministic_fake_transport.fixed_zero",
            token_provenance="deterministic_fake_transport.fixed_zero",
            metadata={
                "hermetic": True,
                "runtime_kind": self.runtime_kind,
                "network_used": False,
                "external_process_started": False,
            },
        )
        return run_id

    async def resume(
        self,
        token: str,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout_s: int,
        metadata: Mapping[str, Any],
    ) -> None:
        del token, argv, cwd, env, timeout_s, metadata
        raise RuntimeError("the hermetic tracer does not resume executions")

    async def cancel(self, token: str) -> None:
        if token not in self._results:
            raise KeyError(f"unknown hermetic runtime token: {token}")

    async def stream(self, token: str) -> AsyncIterator[Mapping[str, Any]]:
        result = self._result(token)
        for event in result.raw_events:
            await asyncio.sleep(0)
            yield event

    async def collect(self, token: str) -> RuntimeTransportResult:
        return self._result(token)

    def invocation(self, run_id: str) -> HermeticTransportInvocation:
        try:
            return self._invocations[run_id]
        except KeyError as exc:
            raise KeyError(f"unknown hermetic runtime invocation: {run_id}") from exc

    def _result(self, token: str) -> RuntimeTransportResult:
        try:
            return self._results[token]
        except KeyError as exc:
            raise KeyError(f"unknown hermetic runtime token: {token}") from exc

    @staticmethod
    def _guard_workspace_path(path: Path, *, workspace: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise PermissionError(
                "hermetic transport denied access outside its workspace"
            ) from exc
        return resolved

    @classmethod
    def _read_workspace_bytes(cls, path: Path, *, workspace: Path) -> bytes:
        return cls._guard_workspace_path(path, workspace=workspace).read_bytes()

    @classmethod
    def _write_workspace_text(
        cls,
        path: Path,
        value: str,
        *,
        workspace: Path,
    ) -> None:
        target = cls._guard_workspace_path(path, workspace=workspace)
        target.write_text(value, encoding="utf-8")


class _HermeticHiddenVerifier:
    """Verifier adapter with a root unavailable to the fake runtime."""

    verifier_version = "hermetic-hidden-fixture/v1"

    def __init__(
        self,
        *,
        task_family: str,
        work_root: Path,
        hidden_root: Path,
    ) -> None:
        self.task_family = task_family
        self.work_root = work_root.resolve()
        self.hidden_root = hidden_root.resolve()
        fixture = self.hidden_root / "expected.txt"
        self.verifier_id = f"tracer-001-hidden-{task_family}/v1"
        self.verifier_hash = _sha256_json(
            {
                "adapter": type(self).__name__,
                "version": self.verifier_version,
                "fixture_sha256": _sha256_bytes(fixture.read_bytes()),
            }
        )
        self.receipts: list[HermeticVerifierReceipt] = []

    @property
    def protected_paths(self) -> tuple[str, ...]:
        return (str(self.hidden_root),)

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        fixture = self.hidden_root / "expected.txt"
        hidden_fixture_present = fixture.is_file()
        hidden_secret = fixture.read_text(encoding="utf-8") if fixture.is_file() else ""
        materialization_id = str(
            frozen_result.metadata.get("materialization_id") or ""
        )
        workspace = self.work_root / materialization_id / "workspace"
        workspace_absent = bool(materialization_id) and not workspace.exists()
        packet = frozen_result.to_dict()
        serialized = json.dumps(
            packet,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        treatment_blind = not _contains_treatment_identity(packet)
        hidden_content_absent = (
            bool(hidden_secret)
            and hidden_secret not in serialized
            and str(self.hidden_root) not in serialized
        )
        marker_present = (
            f"diff --git a/{_MARKER_FILE} b/{_MARKER_FILE}"
            in frozen_result.patch
        )
        passed = all(
            (
                hidden_fixture_present,
                workspace_absent,
                treatment_blind,
                hidden_content_absent,
                marker_present,
                frozen_result.task_family == self.task_family,
            )
        )
        if not passed:
            raise RuntimeError(
                "hermetic hidden verifier boundary failed: "
                f"fixture={hidden_fixture_present}, "
                f"workspace_absent={workspace_absent}, "
                f"treatment_blind={treatment_blind}, "
                f"hidden_content_absent={hidden_content_absent}, "
                f"marker_present={marker_present}"
            )
        grade = Grade(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            verifier_hash=self.verifier_hash,
            frozen_result_hash=frozen_result.result_hash,
            passed=True,
            score=1.0,
            evidence={
                "mode": HERMETIC_MODE,
                "fixture_sha256": _sha256_bytes(fixture.read_bytes()),
                "patch_sha256": frozen_result.patch_hash,
                "workspace_absent_at_verification": workspace_absent,
                "treatment_blind": treatment_blind,
                "hidden_content_absent": hidden_content_absent,
                "operational_verifier": False,
            },
            flake_classification="deterministic_fixture",
        )
        self.receipts.append(
            HermeticVerifierReceipt(
                frozen_result=frozen_result,
                grade=grade,
                workspace=workspace,
                workspace_absent_at_verification=workspace_absent,
                treatment_blind=treatment_blind,
                hidden_content_absent=hidden_content_absent,
                hidden_fixture_present=hidden_fixture_present,
            )
        )
        return grade


class _RecordingExecutor:
    """Record the already treatment-bound RepositoryArmExecutor output."""

    def __init__(self, delegate: RepositoryArmExecutor) -> None:
        self.delegate = delegate
        self.records: list[_RecordedArmExecution] = []

    async def execute(
        self,
        *,
        arm: Arm,
        task: TaskSpec,
        budget: ArmBudget,
        assignment_id: str,
    ) -> ArmExecution:
        execution = await self.delegate.execute(
            arm=arm,
            task=task,
            budget=budget,
            assignment_id=assignment_id,
        )
        self.records.append(
            _RecordedArmExecution(
                arm=arm,
                budget=budget,
                assignment_id=assignment_id,
                execution=execution,
            )
        )
        return execution


async def run_hermetic_harness_tracer(
    root: str | Path,
) -> HermeticTracerReport:
    """Run the twelve-coordinate local tracer without provider or test spend."""
    tracer_root = Path(root).expanduser().resolve()
    tracer_root.mkdir(parents=True, exist_ok=False)
    repo_root = tracer_root / "repos"
    hidden_root = tracer_root / "hidden"
    work_root = tracer_root / "workspaces"
    evidence_root = tracer_root / "evidence"
    run_registry_path = tracer_root / "run-registry"
    for directory in (repo_root, hidden_root, work_root, evidence_root):
        directory.mkdir(parents=True, exist_ok=True)

    fixtures = _build_task_fixtures(
        repo_root=repo_root,
        hidden_root=hidden_root,
        work_root=work_root,
    )
    matrix = tuple(
        ExecutionCoordinate(
            task_family=fixture.task_spec.task_family,
            runtime_kind=runtime_kind,
            arm=arm,
        )
        for fixture in fixtures
        for runtime_kind in ("claude_code", "codex")
        for arm in Arm
    )
    if len(matrix) != 12 or len(set(
        (item.task_family, item.runtime_kind, item.arm)
        for item in matrix
    )) != 12:
        raise RuntimeError("hermetic tracer matrix is not exactly twelve coordinates")

    state = State(str(tracer_root / "state.db"))
    aggregate_run_id = "tracer-001-hermetic"
    registered_run_ids = [aggregate_run_id]
    event_clock = 1_720_100_000

    def emit(
        run_id: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> int:
        nonlocal event_clock
        event_clock += 1
        return state.write_event(
            run_id=run_id,
            source="harness_tracer",
            kind=kind,
            payload=dict(payload),
            ts=event_clock,
        )

    register_submitted_workflow(
        state=state,
        registry_dir=run_registry_path,
        workflow_run_id=aggregate_run_id,
        target_session_id="tracer-001-hermetic-session",
        task_id="tracer-001-hermetic",
        task="Hermetic Harness v1 composition tracer",
        target_kind=HERMETIC_MODE,
        cwd=tracer_root,
        session_id_source="hermetic_fixture",
        scope_contract=ScopeContract(
            allowed_paths=(str(tracer_root),),
            protected_paths=(str(hidden_root),),
        ),
    )
    emit(
        aggregate_run_id,
        "tracer.submitted",
        {
            "mode": HERMETIC_MODE,
            "operational_efficacy_evidence": False,
            "not_executed": list(_NOT_EXECUTED),
        },
    )
    emit(
        aggregate_run_id,
        "tracer.matrix.frozen",
        {
            "execution_count": len(matrix),
            "coordinates": [coordinate.to_dict() for coordinate in matrix],
        },
    )

    experiment_db_path = tracer_root / "experiments.db"
    gradebook_path = tracer_root / "grades.db"
    trace_store_path = tracer_root / "trace.db"
    experiment_store = SqliteExperimentStore(experiment_db_path)
    executions: list[HermeticTracerExecution] = []
    scenario_results: list[dict[str, Any]] = []

    with GradeBook(gradebook_path) as gradebook:
        for fixture in fixtures:
            task = fixture.task_spec
            for runtime_kind in ("claude_code", "codex"):
                experiment = _experiment_spec(
                    task=task,
                    runtime_kind=runtime_kind,
                )

                def on_runtime_start(
                    invocation: HermeticTransportInvocation,
                    metadata: Mapping[str, Any],
                    *,
                    task: TaskSpec = task,
                    runtime_kind: str = runtime_kind,
                    experiment_id: str = experiment.experiment_id,
                ) -> None:
                    experiment_metadata = metadata.get("experiment")
                    if not isinstance(experiment_metadata, Mapping):
                        raise RuntimeError(
                            "runtime start lacks experiment join metadata"
                        )
                    arm = Arm(str(experiment_metadata.get("arm") or ""))
                    assignment_id = str(
                        experiment_metadata.get("assignment_id") or ""
                    )
                    execution_id = _execution_id(
                        task_id=task.task_id,
                        runtime_kind=runtime_kind,
                        arm=arm,
                        assignment_id=assignment_id,
                    )
                    register_submitted_workflow(
                        state=state,
                        registry_dir=run_registry_path,
                        workflow_run_id=invocation.run_id,
                        target_session_id="",
                        task_id=task.task_id,
                        task=task.problem_statement,
                        target_kind=runtime_kind,
                        cwd=invocation.workspace,
                        session_id_source=PENDING_SESSION_SOURCE,
                        scope_contract=ScopeContract(
                            allowed_paths=(str(invocation.workspace),),
                            protected_paths=(str(hidden_root),),
                        ),
                    )
                    bound_registration = bind_workflow_target_session(
                        state=state,
                        registry_dir=run_registry_path,
                        workflow_run_id=invocation.run_id,
                        target_session_id=invocation.session_id,
                        source="hermetic_runtime_receipt",
                        rollout_path=str(
                            tracer_root / "no-provider-rollout.jsonl"
                        ),
                    )
                    registered_run_ids.append(invocation.run_id)
                    join = {
                        "aggregate_run_id": aggregate_run_id,
                        "execution_id": execution_id,
                        "experiment_id": experiment_id,
                        "task_id": task.task_id,
                        "task_family": task.task_family,
                        "runtime_kind": runtime_kind,
                        "arm": arm.value,
                        "assignment_id": assignment_id,
                        "treatment_hash": invocation.treatment_hash,
                        "arm_adapter": invocation.arm_adapter,
                        "entrypoint": invocation.entrypoint,
                        "runtime_run_id": str(
                            bound_registration["workflow_run_id"]
                        ),
                        "runtime_session_id": str(
                            bound_registration["target_session_id"]
                        ),
                        "session_id_source": str(
                            bound_registration["session_id_source"]
                        ),
                        "session_registration_ref": str(
                            bound_registration["registry_path"]
                        ),
                    }
                    emit(
                        invocation.run_id,
                        "tracer.execution.registered",
                        join,
                    )
                    emit(
                        aggregate_run_id,
                        "tracer.runtime.started",
                        join,
                    )

                transport = _DeterministicFakeTransport(
                    runtime_kind=runtime_kind,
                    workspace_root=work_root,
                    hidden_root=hidden_root,
                    on_start=on_runtime_start,
                )
                runtime = (
                    ClaudeCodeRuntime(
                        transport=transport,
                        binary="hermetic-claude-not-invoked",
                    )
                    if runtime_kind == "claude_code"
                    else CodexRuntime(
                        transport=transport,
                        binary="hermetic-codex-not-invoked",
                    )
                )
                plans = {
                    arm: _runtime_arm_plan(
                        arm=arm,
                        task=task,
                        runtime_kind=runtime_kind,
                        experiment_id=experiment.experiment_id,
                        hidden_root=hidden_root,
                        treatment=experiment.treatments[arm],
                    )
                    for arm in Arm
                }
                repository_executor = RepositoryArmExecutor(
                    task_environment=fixture.adapter_type(work_root=work_root),
                    runtimes={arm: runtime for arm in Arm},
                    plans=plans,
                )
                recording_executor = _RecordingExecutor(
                    repository_executor
                )
                kernel = ExperimentKernel(
                    store=experiment_store,
                    executor=recording_executor,
                )
                receipt_start = len(fixture.verifier.receipts)
                task_result = await kernel.run_task(
                    experiment,
                    task,
                    fixture.verifier,
                )
                initial_receipts = tuple(
                    fixture.verifier.receipts[
                        receipt_start:receipt_start + len(Arm)
                    ]
                )
                if (
                    len(recording_executor.records) != len(Arm)
                    or len(initial_receipts) != len(Arm)
                    or len(task_result.outcomes) != len(Arm)
                ):
                    raise RuntimeError(
                        "scenario did not produce one record per arm: "
                        f"records={len(recording_executor.records)}, "
                        f"receipts={len(initial_receipts)}, "
                        f"outcomes={len(task_result.outcomes)}, "
                        "errors="
                        + repr([
                            outcome.error
                            for outcome in task_result.outcomes
                            if outcome.error
                        ])
                    )

                emit(
                    aggregate_run_id,
                    "tracer.assignment.persisted",
                    {
                        "experiment_id": experiment.experiment_id,
                        "task_id": task.task_id,
                        "task_family": task.task_family,
                        "runtime_kind": runtime_kind,
                        "assignment_id": task_result.assignment.assignment_id,
                        "order": [
                            arm.value for arm in task_result.assignment.order
                        ],
                        "treatment_hashes": {
                            arm.value: task_result.assignment.treatment_hashes[
                                arm
                            ]
                            for arm in Arm
                        },
                    },
                )
                scenario_results.append(task_result.to_dict())

                for recorded, outcome, initial_receipt in zip(
                    recording_executor.records,
                    task_result.outcomes,
                    initial_receipts,
                    strict=True,
                ):
                    if recorded.arm is not outcome.arm:
                        raise RuntimeError("executor record did not join to outcome")
                    arm_execution = recorded.execution
                    runtime_run_id = str(
                        arm_execution.metadata.get("run_id") or ""
                    )
                    runtime_session_id = str(
                        arm_execution.metadata.get("session_id") or ""
                    )
                    invocation = transport.invocation(runtime_run_id)
                    execution_id = _execution_id(
                        task_id=task.task_id,
                        runtime_kind=runtime_kind,
                        arm=outcome.arm,
                        assignment_id=task_result.assignment.assignment_id,
                    )
                    if (
                        initial_receipt.frozen_result.result_hash
                        != outcome.blinded_frozen_result_hash
                    ):
                        raise RuntimeError(
                            "hidden verifier receipt did not join to blinded result"
                        )
                    run_ref = RunEnvelopeRef.from_frozen_result(
                        run_id=runtime_run_id,
                        run_envelope_hash=_sha256_json(
                            {
                                "schema_version": (
                                    "supervisor-hermetic-run-envelope/v1"
                                ),
                                "execution_id": execution_id,
                                "experiment_id": experiment.experiment_id,
                                "assignment_id": (
                                    task_result.assignment.assignment_id
                                ),
                                "runtime_kind": runtime_kind,
                                "runtime_session_id": runtime_session_id,
                                "task_spec_hash": task.spec_hash,
                                "original_frozen_result_hash": (
                                    outcome.original_frozen_result_hash
                                ),
                                "blinded_frozen_result_hash": (
                                    outcome.blinded_frozen_result_hash
                                ),
                            }
                        ),
                        frozen_result=initial_receipt.frozen_result,
                    )
                    first_revision = gradebook.append_grade(
                        run=run_ref,
                        grade=outcome.grade,
                        verifier_config_hash=_sha256_json(
                            {
                                "verifier_id": task.verifier_id,
                                "phase": "initial",
                                "mode": HERMETIC_MODE,
                            }
                        ),
                    )
                    recheck_grade = await fixture.verifier.verify(
                        initial_receipt.frozen_result
                    )
                    recheck_receipt = fixture.verifier.receipts[-1]
                    second_revision = gradebook.append_grade(
                        run=run_ref,
                        grade=recheck_grade,
                        verifier_config_hash=_sha256_json(
                            {
                                "verifier_id": task.verifier_id,
                                "phase": "deterministic_recheck",
                                "mode": HERMETIC_MODE,
                            }
                        ),
                        supersedes_grade_id=first_revision.grade_id,
                    )
                    revisions = gradebook.list_revisions(run_ref)
                    invalidations = gradebook.list_invalidations(
                        first_revision.grade_id
                    )
                    grade_history = HermeticGradeHistory(
                        run=run_ref,
                        revisions=revisions,
                        invalidations=invalidations,
                    )
                    execution = HermeticTracerExecution(
                        execution_id=execution_id,
                        coordinate=ExecutionCoordinate(
                            task_family=task.task_family,
                            runtime_kind=runtime_kind,
                            arm=outcome.arm,
                        ),
                        experiment_id=experiment.experiment_id,
                        assignment_id=task_result.assignment.assignment_id,
                        task_spec=task,
                        transport=invocation,
                        arm_execution=arm_execution,
                        outcome=outcome,
                        blinded_result=initial_receipt.frozen_result,
                        verifier_receipts=(
                            initial_receipt,
                            recheck_receipt,
                        ),
                        grade_history=grade_history,
                        workspace_removed=not invocation.workspace.exists(),
                    )
                    executions.append(execution)

                    bound_registration = load_session_registration(
                        run_registry_path,
                        runtime_session_id,
                    )
                    if (
                        bound_registration is None
                        or str(
                            bound_registration.get("workflow_run_id") or ""
                        )
                        != runtime_run_id
                    ):
                        raise RuntimeError(
                            "runtime completion did not resolve through the "
                            "public session-binding registry"
                        )
                    join = {
                        "aggregate_run_id": aggregate_run_id,
                        "execution_id": execution_id,
                        "experiment_id": experiment.experiment_id,
                        "task_id": task.task_id,
                        "task_family": task.task_family,
                        "runtime_kind": runtime_kind,
                        "arm": outcome.arm.value,
                        "assignment_id": task_result.assignment.assignment_id,
                        "treatment_hash": invocation.treatment_hash,
                        "arm_adapter": invocation.arm_adapter,
                        "entrypoint": invocation.entrypoint,
                        "runtime_run_id": str(
                            bound_registration["workflow_run_id"]
                        ),
                        "runtime_session_id": str(
                            bound_registration["target_session_id"]
                        ),
                        "session_id_source": str(
                            bound_registration["session_id_source"]
                        ),
                        "session_registration_ref": str(
                            bound_registration["registry_path"]
                        ),
                    }
                    emit(
                        runtime_run_id,
                        "tracer.runtime.completed",
                        {
                            **join,
                            "status": outcome.status,
                            "cost_usd": outcome.cost_usd,
                            "runtime_event_count": (
                                arm_execution.metadata.get(
                                    "runtime_event_count"
                                )
                            ),
                        },
                    )
                    emit(
                        runtime_run_id,
                        "tracer.result.blinded",
                        {
                            **join,
                            "original_frozen_result_hash": (
                                outcome.original_frozen_result_hash
                            ),
                            "blinded_frozen_result_hash": (
                                outcome.blinded_frozen_result_hash
                            ),
                            "blinding_removed_paths": list(
                                outcome.blinding_removed_paths
                            ),
                        },
                    )
                    emit(
                        runtime_run_id,
                        "tracer.grade.revised",
                        {
                            **join,
                            "grade_id": second_revision.grade_id,
                            "grade_revision_hash": (
                                second_revision.revision_hash
                            ),
                            "revision_count": len(revisions),
                            "superseded_grade_id": first_revision.grade_id,
                            "grade_revision_hashes": [
                                revision.revision_hash
                                for revision in revisions
                            ],
                            "grade_invalidation_hashes": [
                                invalidation.invalidation_hash
                                for invalidation in invalidations
                            ],
                        },
                    )
                    emit(
                        runtime_run_id,
                        "tracer.execution.completed",
                        {
                            **join,
                            "terminal": True,
                            "workspace_removed": execution.workspace_removed,
                            "hidden_verifier_passed": outcome.grade.passed,
                        },
                    )
                    state.end_run(runtime_run_id, "completed")
                    emit(
                        aggregate_run_id,
                        "tracer.execution.joined",
                        {
                            **join,
                            "original_frozen_result_hash": (
                                outcome.original_frozen_result_hash
                            ),
                            "blinded_frozen_result_hash": (
                                outcome.blinded_frozen_result_hash
                            ),
                            "grade_revision_hash": (
                                second_revision.revision_hash
                            ),
                            "grade_revision_hashes": [
                                revision.revision_hash
                                for revision in revisions
                            ],
                            "grade_invalidation_hashes": [
                                invalidation.invalidation_hash
                                for invalidation in invalidations
                            ],
                        },
                    )

    if len(executions) != 12:
        raise RuntimeError(
            f"hermetic tracer expected 12 executions, observed {len(executions)}"
        )
    observed_matrix = {
        (
            execution.coordinate.task_family,
            execution.coordinate.runtime_kind,
            execution.coordinate.arm,
        )
        for execution in executions
    }
    expected_matrix = {
        (item.task_family, item.runtime_kind, item.arm) for item in matrix
    }
    if observed_matrix != expected_matrix:
        raise RuntimeError("hermetic tracer execution matrix is incomplete")

    trace_graph, promotion = _build_trace_graph(executions)
    with GradeBook(gradebook_path) as gradebook:
        trace_closure = trace_graph.validate_closure(
            now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
            decision_grade_validator=gradebook,
        )
    if not trace_closure.ok:
        raise RuntimeError(
            "hermetic tracer trace graph did not close: "
            + json.dumps(trace_closure.to_dict(), sort_keys=True)
        )
    promotion_trace = trace_graph.promotion_trace(promotion)
    emit(
        aggregate_run_id,
        "tracer.trace.closed",
        {
            "status": trace_closure.to_dict()["status"],
            "node_count": len(trace_graph.nodes),
            "edge_count": len(trace_graph.edges),
            "promotion_trace": [
                node.identity.canonical_key for node in promotion_trace
            ],
        },
    )

    claim_bundle, evidence_artifacts, evidence_bytes = _build_claim_evidence(
        fixtures=fixtures,
        scenario_results=scenario_results,
        executions=executions,
        trace_graph=trace_graph,
        trace_closure=trace_closure,
    )
    claim_level = ClaimGate.max_claim_level(
        claim_bundle,
        evidence_resolver=evidence_bytes.get,
    )
    if claim_level is not ClaimLevel.L1:
        raise RuntimeError(
            "hermetic tracer ClaimGate must stop at L1, observed "
            f"{claim_level.value if claim_level else None}"
        )
    claim_report = ClaimGate.derive_report(
        {
            "schema_version": "supervisor-hermetic-tracer-report/v1",
            "mode": HERMETIC_MODE,
            "asserted_claim_level": "L1",
            "claims": [
                "CLAIM-HARNESS-L0-INTEGRITY",
                "CLAIM-HARNESS-L1-PROCESS",
            ],
            "operational_efficacy_evidence": False,
            "external_provider_calls": 0,
            "not_executed": list(_NOT_EXECUTED),
        },
        claim_bundle,
        evidence_resolver=evidence_bytes.get,
    )
    try:
        ClaimGate.validate_report(
            {"asserted_claim_level": "L2"},
            claim_bundle,
            evidence_resolver=evidence_bytes.get,
        )
    except UnsupportedClaimError as exc:
        l2_refusal = str(exc)
    else:
        raise RuntimeError("hermetic tracer evidence unexpectedly authorized L2")
    try:
        ClaimGate.validate_report(
            {"asserted_claim_level": "L3"},
            claim_bundle,
            evidence_resolver=evidence_bytes.get,
        )
    except UnsupportedClaimError as exc:
        l3_refusal = str(exc)
    else:
        raise RuntimeError("hermetic tracer evidence unexpectedly authorized L3")
    emit(
        aggregate_run_id,
        "tracer.claim.authorized",
        {
            "max_claim_level": claim_level.value,
            "l2_refusal": l2_refusal,
            "l3_refusal": l3_refusal,
            "operational_efficacy_evidence": False,
            "improvement_claim_allowed": claim_report[
                "improvement_claim_allowed"
            ],
        },
    )
    emit(
        aggregate_run_id,
        "tracer.completed",
        {
            "execution_count": len(executions),
            "claim_cap": claim_level.value,
            "mode": HERMETIC_MODE,
            "external_provider_calls": 0,
        },
    )
    state.end_run(aggregate_run_id, "completed")

    unique_registered = tuple(dict.fromkeys(registered_run_ids))
    if len(unique_registered) != 13:
        raise RuntimeError(
            "hermetic tracer must register one aggregate and twelve runtime runs"
        )

    evidence_artifacts = (
        *evidence_artifacts,
        EvidenceArtifact(
            role="claim_evidence_bundle",
            relative_path="artifacts/claim-evidence-bundle.json",
            content=canonical_json_bytes(claim_bundle),
        ),
        EvidenceArtifact(
            role="claim_report",
            relative_path="artifacts/claim-report.json",
            content=canonical_json_bytes(claim_report),
        ),
    )
    evidence_grade_histories = tuple(
        EvidenceGradeHistory(
            execution_id=execution.execution_id,
            run=execution.grade_history.run,
            revisions=execution.grade_history.revisions,
            invalidations=execution.grade_history.invalidations,
        )
        for execution in executions
    )
    checkpoint_authority = HmacCheckpointAuthority(
        key_id="tracer-001-hermetic-local-key",
        key=b"tracer-001-hermetic-local-checkpoint-key",
    )
    evidence_committer = EvidenceCommitter(
        root=evidence_root,
        state=state,
        experiment_db_path=experiment_db_path,
        gradebook_path=gradebook_path,
        trace_store_path=trace_store_path,
        signer=checkpoint_authority,
        verifier=checkpoint_authority,
        trusted_checkpoint_pins=FilesystemTrustedCheckpointPinStore(
            evidence_root.parent
            / f"{evidence_root.name}-trusted-checkpoint-pins"
        ),
    )
    evidence_commit = evidence_committer.commit(
        EvidenceCommitRequest(
            commit_id="tracer-001-hermetic-evidence",
            aggregate_run_id=aggregate_run_id,
            registered_run_ids=unique_registered,
            mode=HERMETIC_MODE,
            claim_cap=claim_level.value,
            operational_efficacy_evidence=False,
            subject={
                "schema_version": (
                    "supervisor-hermetic-tracer-evidence-subject/v1"
                ),
                "execution_ids": [
                    execution.execution_id for execution in executions
                ],
                "scenario_results_sha256": _sha256_json(
                    scenario_results
                ),
                "claim_evidence_bundle_sha256": _sha256_json(
                    claim_bundle
                ),
                "external_provider_calls": 0,
                "operational_efficacy_evidence": False,
            },
            grade_histories=evidence_grade_histories,
            trace_graph=trace_graph,
            promotion=promotion,
            closure_time=datetime(
                2026,
                7,
                12,
                12,
                0,
                tzinfo=timezone.utc,
            ),
            artifacts=evidence_artifacts,
            manifest_event_ts=event_clock + 1,
            checkpoint_created_at=event_clock + 2,
        )
    )
    if evidence_commit.status != "complete":
        raise RuntimeError("hermetic tracer evidence commit did not complete")

    aggregate_events = tuple(
        state.read_events_since(
            aggregate_run_id,
            after_event_id=0,
            limit=1000,
        )
    )
    return HermeticTracerReport(
        mode=HERMETIC_MODE,
        operational_efficacy_evidence=False,
        external_provider_calls=0,
        not_executed=_NOT_EXECUTED,
        aggregate_run_id=aggregate_run_id,
        executions=tuple(executions),
        registered_run_ids=unique_registered,
        aggregate_events=aggregate_events,
        ledger_verifications=evidence_commit.ledger_verifications,
        trace_graph=evidence_commit.trace_graph,
        trace_closure=evidence_commit.trace_closure,
        promotion_trace=evidence_commit.promotion_trace,
        claim_evidence_bundle=claim_bundle,
        claim_report=claim_report,
        claim_level=claim_level,
        l2_refusal=l2_refusal,
        l3_refusal=l3_refusal,
        evidence_root=evidence_root,
        evidence_commit_status=evidence_commit.status,
        evidence_commit_phases=evidence_commit.phases,
        artifact_manifest=evidence_commit.artifact_manifest,
        manifest_event_id=evidence_commit.manifest_event_id,
        checkpoint_refs=evidence_commit.checkpoint_refs,
        projection=evidence_commit.projection,
        projection_sha256=evidence_commit.projection_sha256,
        trace_store_path=trace_store_path,
        run_registry_path=run_registry_path,
    )


async def run_hermetic_treatment_wire_cut(
    root: str | Path,
) -> TreatmentWireCutReport:
    """Disable only the supervisor wire and prove B fails while C still runs."""

    tracer_root = Path(root).expanduser().resolve()
    tracer_root.mkdir(parents=True, exist_ok=False)
    repo_root = tracer_root / "repos"
    hidden_root = tracer_root / "hidden"
    work_root = tracer_root / "workspaces"
    for directory in (repo_root, hidden_root, work_root):
        directory.mkdir(parents=True, exist_ok=True)
    fixture = _build_task_fixtures(
        repo_root=repo_root,
        hidden_root=hidden_root,
        work_root=work_root,
    )[0]
    task = fixture.task_spec
    runtime_kind = "codex"
    experiment = _experiment_spec(
        task=task,
        runtime_kind=runtime_kind,
    )

    def ignore_runtime_start(
        _invocation: HermeticTransportInvocation,
        _metadata: Mapping[str, Any],
    ) -> None:
        return None

    transport = _DeterministicFakeTransport(
        runtime_kind=runtime_kind,
        workspace_root=work_root,
        hidden_root=hidden_root,
        on_start=ignore_runtime_start,
        supervisor_orchestration_enabled=False,
    )
    runtime = CodexRuntime(
        transport=transport,
        binary="hermetic-codex-not-invoked",
    )
    plans = {
        arm: _runtime_arm_plan(
            arm=arm,
            task=task,
            runtime_kind=runtime_kind,
            experiment_id=experiment.experiment_id,
            hidden_root=hidden_root,
            treatment=experiment.treatments[arm],
        )
        for arm in Arm
    }
    executor = RepositoryArmExecutor(
        task_environment=fixture.adapter_type(work_root=work_root),
        runtimes={arm: runtime for arm in Arm},
        plans=plans,
    )
    assignment_id = "tracer-001-supervisor-wire-cut"
    try:
        await executor.execute(
            arm=Arm.B,
            task=task,
            budget=experiment.arm_budgets[Arm.B],
            assignment_id=assignment_id,
        )
    except ArmExecutionError as exc:
        b_failure = f"{type(exc).__name__}: {exc}"
    else:
        raise RuntimeError(
            "disabling supervisor orchestration did not break arm B"
        )

    c_execution = await executor.execute(
        arm=Arm.C,
        task=task,
        budget=experiment.arm_budgets[Arm.C],
        assignment_id=assignment_id,
    )
    if c_execution.receipt is None:
        raise RuntimeError("wire-cut arm C did not produce a receipt")
    return TreatmentWireCutReport(
        b_failed=True,
        b_failure=b_failure,
        c_completed=True,
        c_treatment_hash=c_execution.receipt.treatment_hash,
        supervisor_orchestration_calls=transport.supervisor_wire.calls,
        adapter_invocations=dict(transport.adapter_invocations),
    )


def _build_task_fixtures(
    *,
    repo_root: Path,
    hidden_root: Path,
    work_root: Path,
) -> tuple[_TaskFixture, _TaskFixture]:
    architecture, os_name = default_task_platform()
    fixtures: list[_TaskFixture] = []
    for task_family, adapter_type in (
        ("generic", GenericRepositoryTask),
        ("unity", UnityRepositoryTask),
    ):
        repository = repo_root / task_family
        revision = _create_repository(
            repository,
            unity=task_family == "unity",
        )
        task_hidden_root = hidden_root / task_family
        task_hidden_root.mkdir()
        (task_hidden_root / "expected.txt").write_text(
            f"TRACER-001-HIDDEN-{task_family.upper()}-FIXTURE\n",
            encoding="utf-8",
        )
        verifier = _HermeticHiddenVerifier(
            task_family=task_family,
            work_root=work_root,
            hidden_root=task_hidden_root,
        )
        task_spec = TaskSpec(
            task_id=f"tracer-001-{task_family}",
            task_family=task_family,
            repo=str(repository),
            revision=revision,
            dataset_hash=_sha256_text(f"tracer-001-{task_family}-dataset"),
            split_hash=_sha256_text(f"tracer-001-{task_family}-split"),
            canonical_task_key=f"tracer-001-{task_family}",
            problem_statement=(
                f"Create {_MARKER_FILE} to complete the hermetic tracer fixture."
            ),
            image_digest="sha256:" + _sha256_text("tracer-001-local-image"),
            architecture=architecture,
            os_name=os_name,
            network_policy="disabled",
            resource_limits={
                "timeout_s": 30,
                "max_tokens": 256,
                "max_cost_usd": 0.0,
            },
            verifier_id=verifier.verifier_id,
            verifier_hash=verifier.verifier_hash,
            metadata={
                "tracer_mode": HERMETIC_MODE,
                "evaluation_scope": "composition_only",
                "reserved_from": ["pilot", "confirm"],
            },
        )
        fixtures.append(
            _TaskFixture(
                task_spec=task_spec,
                adapter_type=adapter_type,
                verifier=verifier,
            )
        )
    generic, unity = fixtures
    return generic, unity


def _experiment_spec(
    *,
    task: TaskSpec,
    runtime_kind: str,
) -> ExperimentSpec:
    budget = ArmBudget(
        max_tokens=256,
        max_cost_usd=0.0,
        timeout_s=30,
        max_retries=0,
    )
    return ExperimentSpec(
        experiment_id=f"tracer-001-{task.task_family}-{runtime_kind}",
        assignment_version="tracer-001-hermetic/v1",
        hmac_key=b"tracer-001-hermetic-assignment-key",
        arm_budgets={arm: budget for arm in Arm},
        treatments=_treatment_descriptors(runtime_kind=runtime_kind),
        metadata={
            "model": f"hermetic-{runtime_kind}-model/v1",
            "mode": HERMETIC_MODE,
        },
    )


def _treatment_descriptors(
    *,
    runtime_kind: str,
) -> dict[Arm, TreatmentDescriptor]:
    common_config = {
        "mode": HERMETIC_MODE,
        "runtime_kind": runtime_kind,
        "external_provider_calls": 0,
        "evidence_level": "fixture/L1",
    }
    return {
        Arm.A: TreatmentDescriptor(
            arm_adapter="production-baseline",
            entrypoint="baseline.execute",
            instruction_template=(
                "Use the production-baseline adapter in the hermetic "
                "composition fixture.\n\n{problem_statement}"
            ),
            treatment_config={
                **common_config,
                "orchestration": "none",
                "baseline_contract": "production",
            },
        ),
        Arm.B: TreatmentDescriptor(
            arm_adapter="supervisor-orchestration",
            entrypoint="supervisor.execute",
            instruction_template=(
                "Use the supervisor orchestration adapter in the hermetic "
                "composition fixture.\n\n{problem_statement}"
            ),
            treatment_config={
                **common_config,
                "orchestration": "supervisor",
                "supervisor_wire_required": True,
            },
        ),
        Arm.C: TreatmentDescriptor(
            arm_adapter="compute-matched-direct",
            entrypoint="direct.execute",
            instruction_template=(
                "Use the compute-matched direct adapter in the hermetic "
                "composition fixture.\n\n{problem_statement}"
            ),
            treatment_config={
                **common_config,
                "orchestration": "none",
                "compute_match_target": "arm-b",
            },
        ),
    }


def _runtime_arm_plan(
    *,
    arm: Arm,
    task: TaskSpec,
    runtime_kind: str,
    experiment_id: str,
    hidden_root: Path,
    treatment: TreatmentDescriptor,
) -> RuntimeArmPlan:
    expected_adapter = {
        Arm.A: "production-baseline",
        Arm.B: "supervisor-orchestration",
        Arm.C: "compute-matched-direct",
    }[arm]
    if treatment.arm_adapter != expected_adapter:
        raise ValueError("tracer arm treatment adapter is misbound")
    return RuntimeArmPlan(
        model=f"hermetic-{runtime_kind}-model/v1",
        treatment=treatment,
        environment={"TRACER_MODE": HERMETIC_MODE},
        runtime_metadata={
            "tracer_mode": HERMETIC_MODE,
            "task_id": task.task_id,
            "task_family": task.task_family,
            "experiment_id": experiment_id,
            "runtime_kind": runtime_kind,
            "external_provider_calls": 0,
        },
        denied_paths=(str(hidden_root),),
        inherit_env=False,
        container_digest=task.image_digest,
        network_policy=task.network_policy,
        resource_limits=dict(task.resource_limits),
    )


def _build_trace_graph(
    executions: list[HermeticTracerExecution],
) -> tuple[TraceGraph, TraceIdentity]:
    objective = _trace_node(
        NodeType.OBJ,
        "OBJ-TRACER-001-HERMETIC-COMPOSITION",
        {
            "goal": "compose Harness v1 seams without operational claims",
        },
    )
    requirement = _trace_node(
        NodeType.REQ,
        "REQ-TRACER-001-TWELVE-HERMETIC-EXECUTIONS",
        {
            "execution_count": 12,
            "task_families": ["generic", "unity"],
            "runtime_kinds": ["claude_code", "codex"],
        },
    )
    test = _trace_node(
        NodeType.TEST,
        "TEST-TRACER-001-E2E",
        {
            "test": "tests/test_tracer_001_e2e.py",
            "mode": HERMETIC_MODE,
        },
    )
    nodes: list[TraceNode] = [objective, requirement, test]
    edges: list[TraceEdge] = [
        TraceEdge(
            requirement.identity,
            EdgeType.IMPLEMENTS,
            objective.identity,
        ),
        TraceEdge(
            test.identity,
            EdgeType.TESTS,
            requirement.identity,
        ),
    ]
    current_grade_nodes: list[TraceNode] = []
    decision_citations: list[DecisionGradeCitation] = []
    for execution in executions:
        suffix = execution.execution_id[:16].upper()
        assignment = _trace_node(
            NodeType.ASN,
            f"ASN-TRACER-001-{suffix}",
            {
                "assignment_id": execution.assignment_id,
                "coordinate": execution.coordinate.to_dict(),
            },
            attributes={
                "execution_id": execution.execution_id,
                **execution.coordinate.to_dict(),
            },
        )
        run = _trace_node(
            NodeType.RUN,
            f"RUN-TRACER-001-{suffix}",
            execution.grade_history.run.run_envelope_hash,
            pinned=True,
            attributes={
                "runtime_run_id": execution.transport.run_id,
                "mode": HERMETIC_MODE,
            },
        )
        artifact = _trace_node(
            NodeType.ART,
            f"ART-TRACER-001-{suffix}",
            execution.blinded_result.result_hash,
            runtime_evidence=True,
            attributes={
                "frozen_result_hash": execution.blinded_result.result_hash,
                "operational_efficacy_evidence": False,
            },
        )
        revision_nodes = {
            revision.grade_id: _trace_node(
                NodeType.GRADE,
                (
                    f"GRADE-TRACER-001-{suffix}-"
                    f"REV-{revision.revision_number}"
                ),
                revision.revision_hash,
                verifier_id=revision.verifier_id,
                verifier_revision_hash=(
                    revision.verifier_implementation_hash
                ),
                attributes={
                    "record_kind": "grade_revision",
                    **revision.to_dict(),
                },
            )
            for revision in execution.grade_history.revisions
        }
        invalidation_nodes = {
            invalidation.invalidation_id: _trace_node(
                NodeType.GRADE,
                (
                    f"GRADE-TRACER-001-{suffix}-INVALIDATION-"
                    f"{invalidation.invalidation_hash[:16].upper()}"
                ),
                invalidation.invalidation_hash,
                verifier_id=revision_nodes[
                    invalidation.grade_id
                ].verifier_id,
                verifier_revision_hash=revision_nodes[
                    invalidation.grade_id
                ].verifier_revision_hash,
                attributes={
                    "record_kind": "grade_invalidation",
                    **invalidation.to_dict(),
                },
            )
            for invalidation in execution.grade_history.invalidations
        }
        current_revision = execution.grade_history.revisions[-1]
        current_grade_node = revision_nodes[current_revision.grade_id]
        nodes.extend((
            assignment,
            run,
            artifact,
            *revision_nodes.values(),
            *invalidation_nodes.values(),
        ))
        current_grade_nodes.append(current_grade_node)
        decision_citations.append(
            DecisionGradeCitation(
                current_revision.grade_id,
                current_revision.revision_hash,
            )
        )
        edges.extend(
            (
                TraceEdge(
                    assignment.identity,
                    EdgeType.SUPPORTS,
                    test.identity,
                ),
                TraceEdge(
                    run.identity,
                    EdgeType.ASSIGNED_BY,
                    assignment.identity,
                ),
                TraceEdge(
                    artifact.identity,
                    EdgeType.DERIVED_FROM,
                    run.identity,
                ),
            )
        )
        edges.extend(
            TraceEdge(
                grade.identity,
                EdgeType.EVALUATES,
                artifact.identity,
            )
            for grade in revision_nodes.values()
        )
        edges.extend(
            TraceEdge(
                revision_nodes[revision.grade_id].identity,
                EdgeType.SUPERSEDES,
                revision_nodes[revision.supersedes_grade_id].identity,
            )
            for revision in execution.grade_history.revisions
            if revision.supersedes_grade_id is not None
        )
        edges.extend(
            TraceEdge(
                invalidation_nodes[
                    invalidation.invalidation_id
                ].identity,
                EdgeType.INVALIDATES,
                revision_nodes[invalidation.grade_id].identity,
            )
            for invalidation in execution.grade_history.invalidations
        )

    analysis = _trace_node(
        NodeType.ANL,
        "ANL-TRACER-001-HERMETIC-ONLY",
        {
            "grade_revision_hashes": [
                node.identity.revision_hash
                for node in current_grade_nodes
            ],
            "claim_cap": "L1",
            "operational_efficacy_evidence": False,
        },
    )
    decision_payload = {
        "decision": "publish hermetic composition evidence only",
        "claim_cap": "L1",
        "grade_citations": [
            citation.to_dict() for citation in decision_citations
        ],
    }
    decision = _trace_node(
        NodeType.DEC,
        "DEC-TRACER-001-PUBLISH-HERMETIC-EVIDENCE",
        decision_payload,
        attributes=decision_payload,
    )
    promotion = _trace_node(
        NodeType.PROMOTION,
        "PROMOTION-TRACER-001-HERMETIC-EVIDENCE-ONLY",
        {
            "scope": "hermetic_test_evidence",
            "operational": False,
        },
    )
    nodes.extend((analysis, decision, promotion))
    edges.extend(
        TraceEdge(
            analysis.identity,
            EdgeType.DERIVED_FROM,
            grade.identity,
        )
        for grade in current_grade_nodes
    )
    edges.extend(
        (
            TraceEdge(
                decision.identity,
                EdgeType.DERIVED_FROM,
                analysis.identity,
            ),
            TraceEdge(
                promotion.identity,
                EdgeType.PROMOTES,
                decision.identity,
            ),
        )
    )
    return (
        TraceGraph(nodes=nodes, edges=edges),
        promotion.identity,
    )


def _trace_node(
    node_type: NodeType,
    logical_id: str,
    revision: Any,
    *,
    pinned: bool = False,
    runtime_evidence: bool = False,
    verifier_id: str | None = None,
    verifier_revision_hash: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> TraceNode:
    revision_hash = (
        revision
        if isinstance(revision, str) and _is_sha256(revision)
        else canonical_revision_hash(revision)
    )
    identity = TraceIdentity(
        namespace="harness-v1/tracer-001",
        node_type=node_type,
        logical_id=logical_id,
        revision_hash=revision_hash,
        instance_id=trace_instance_id_from_hash(
            timestamp_ms=int(
                datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc).timestamp()
                * 1000
            ),
            content_hash=canonical_revision_hash({
                "namespace": "harness-v1/tracer-001",
                "node_type": node_type.value,
                "logical_id": logical_id,
                "revision_hash": revision_hash,
            }),
            domain="harness-v1/tracer-001",
        ),
    )
    return TraceNode(
        identity=identity,
        pinned=pinned,
        runtime_evidence=runtime_evidence,
        verifier_id=verifier_id,
        verifier_revision_hash=verifier_revision_hash,
        attributes=dict(attributes or {}),
    )


def _build_claim_evidence(
    *,
    fixtures: tuple[_TaskFixture, _TaskFixture],
    scenario_results: list[dict[str, Any]],
    executions: list[HermeticTracerExecution],
    trace_graph: TraceGraph,
    trace_closure: ClosureResult,
) -> tuple[
    dict[str, Any],
    tuple[EvidenceArtifact, ...],
    dict[str, bytes],
]:
    artifact_payloads = (
        (
            "run_manifest",
            "artifacts/run-manifest.json",
        {
            "schema_version": "supervisor-hermetic-tracer-manifest/v1",
            "mode": HERMETIC_MODE,
            "execution_count": len(executions),
            "external_provider_calls": 0,
            "operational_efficacy_evidence": False,
            "not_executed": list(_NOT_EXECUTED),
            "tasks": [
                {
                    "task_id": fixture.task_spec.task_id,
                    "task_family": fixture.task_spec.task_family,
                    "revision": fixture.task_spec.revision,
                    "task_spec_hash": fixture.task_spec.spec_hash,
                    "verifier_id": fixture.task_spec.verifier_id,
                    "verifier_hash": fixture.task_spec.verifier_hash,
                }
                for fixture in fixtures
            ],
        },
        ),
        (
            "execution_results",
            "artifacts/executions.json",
        {
            "schema_version": "supervisor-hermetic-tracer-executions/v1",
            "scenarios": scenario_results,
            "executions": [
                {
                    "execution_id": execution.execution_id,
                    "coordinate": execution.coordinate.to_dict(),
                    "runtime_run_id": execution.transport.run_id,
                    "runtime_session_id": execution.transport.session_id,
                    "original_frozen_result_hash": (
                        execution.outcome.original_frozen_result_hash
                    ),
                    "blinded_frozen_result_hash": (
                        execution.outcome.blinded_frozen_result_hash
                    ),
                    "grade_revision_hash": (
                        execution.grade_history.revisions[-1].revision_hash
                    ),
                    "workspace_removed": execution.workspace_removed,
                    "hidden_read_blocked": (
                        execution.transport.hidden_read_blocked
                    ),
                    "cost_usd": execution.outcome.cost_usd,
                }
                for execution in executions
            ],
        },
        ),
        (
            "trace_graph",
            "artifacts/trace-graph.json",
        {
            "graph": trace_graph.to_dict(),
            "closure": trace_closure.to_dict(),
        },
        ),
        (
            "hidden_verifier_result",
            "artifacts/hidden-verifier-result.json",
        {
            "schema_version": "supervisor-hermetic-hidden-verifier-result/v1",
            "mode": HERMETIC_MODE,
            "passed": all(
                execution.outcome.grade.passed for execution in executions
            ),
            "result_count": len(executions),
            "workspace_absent_at_verification": all(
                receipt.workspace_absent_at_verification
                for execution in executions
                for receipt in execution.verifier_receipts
            ),
            "treatment_blind": all(
                receipt.treatment_blind
                for execution in executions
                for receipt in execution.verifier_receipts
            ),
            "hidden_content_absent": all(
                receipt.hidden_content_absent
                for execution in executions
                for receipt in execution.verifier_receipts
            ),
            "operational_verifier": False,
        },
        ),
    )
    artifacts = tuple(
        EvidenceArtifact(
            role=role,
            relative_path=reference,
            content=canonical_json_bytes(payload),
        )
        for role, reference, payload in artifact_payloads
    )
    evidence_bytes = {
        artifact.relative_path: artifact.content
        for artifact in artifacts
    }
    descriptors = {
        artifact.role: {
            "ref": artifact.relative_path,
            "sha256": _sha256_bytes(artifact.content),
        }
        for artifact in artifacts
    }
    task_set_hash = _sha256_json(
        [
            {
                "task_id": fixture.task_spec.task_id,
                "revision": fixture.task_spec.revision,
                "spec_hash": fixture.task_spec.spec_hash,
            }
            for fixture in fixtures
        ]
    )
    bundle = {
        "pins": {
            "mode": HERMETIC_MODE,
            "task_set": task_set_hash,
            "assignment_version": "tracer-001-hermetic/v1",
        },
        "hashes": {
            "run_manifest_sha256": descriptors["run_manifest"]["sha256"],
            "execution_results_sha256": descriptors[
                "execution_results"
            ]["sha256"],
        },
        "artifacts": list(descriptors.values()),
        "traceable_detector": {
            "detector_id": "tracer-001-hermetic-trace-closure/v1",
            "trace_ref": descriptors["trace_graph"]["ref"],
            "trace_sha256": descriptors["trace_graph"]["sha256"],
        },
        "independent_hidden_verifier": {
            "verifier_id": "tracer-001-hermetic-hidden-fixture/v1",
            "producer_principal_id": "tracer-001-hermetic",
            "verifier_principal_id": "tracer-001-hermetic",
            "independent": False,
            "hidden": True,
            "result_ref": descriptors["hidden_verifier_result"]["ref"],
            "result_sha256": descriptors[
                "hidden_verifier_result"
            ]["sha256"],
        },
    }
    return bundle, artifacts, evidence_bytes


def _create_repository(path: Path, *, unity: bool) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "--quiet")
    _git(path, "config", "user.email", "tracer@example.invalid")
    _git(path, "config", "user.name", "Harness Tracer")
    (path / "README.md").write_text(
        "Hermetic Harness v1 tracer fixture.\n",
        encoding="utf-8",
    )
    if unity:
        project_settings = path / "ProjectSettings"
        project_settings.mkdir()
        (project_settings / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.0.0f1\n",
            encoding="utf-8",
        )
        assets = path / "Assets"
        assets.mkdir()
        (assets / "TracerFixture.txt").write_text(
            "No Unity process is invoked by this fixture.\n",
            encoding="utf-8",
        )
    _git(path, "add", "-A")
    _git(path, "commit", "--quiet", "-m", "Pin hermetic tracer fixture")
    return _git(path, "rev-parse", "HEAD").lower()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _execution_id(
    *,
    task_id: str,
    runtime_kind: str,
    arm: Arm,
    assignment_id: str,
) -> str:
    return _sha256_json(
        {
            "task_id": task_id,
            "runtime_kind": runtime_kind,
            "arm": arm.value,
            "assignment_id": assignment_id,
        }
    )


def _argument_after(argv: tuple[str, ...], flag: str) -> str:
    try:
        return str(argv[argv.index(flag) + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"hermetic runtime command missing {flag}") from exc


def _argument_after_any(argv: tuple[str, ...], *flags: str) -> str:
    for flag in flags:
        try:
            return _argument_after(argv, flag)
        except ValueError:
            continue
    raise ValueError(
        "hermetic runtime command missing one of: "
        + ", ".join(flags)
    )


def _contains_treatment_identity(value: Any) -> bool:
    forbidden_keys = {"arm", "assignment", "treatment"}
    forbidden_values = {
        "a",
        "b",
        "c",
        "arm_a",
        "arm_b",
        "arm_c",
        *(arm.value for arm in Arm),
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = _normalize_token(str(key))
            if forbidden_keys.intersection(normalized_key.split("_")):
                return True
            if _contains_treatment_identity(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_treatment_identity(item) for item in value)
    if isinstance(value, str):
        return _normalize_token(value) in forbidden_values
    return False


def _normalize_token(value: str) -> str:
    return "_".join(
        token
        for token in "".join(
            character.lower() if character.isalnum() else "_"
            for character in value
        ).split("_")
        if token
    )


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "ExecutionCoordinate",
    "HermeticGradeHistory",
    "HermeticTracerExecution",
    "HermeticTracerReport",
    "HermeticTransportInvocation",
    "HermeticVerifierReceipt",
    "TreatmentWireCutReport",
    "run_hermetic_harness_tracer",
    "run_hermetic_treatment_wire_cut",
]
