from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from supervisor.experiment_kernel import (
    ARM_ORDERS,
    Arm,
    ArmBudget,
    ArmExecution,
    ArmExecutionError,
    ArmExecutionReceipt,
    CommonPreTreatmentInfrastructureError,
    ExecutionEnvironmentAttestation,
    ExperimentKernel,
    ExperimentSpec,
    IsolationAttestation,
    SqliteExperimentStore,
    TreatmentDescriptor,
    blind_frozen_result,
    build_adjudicator_packet,
    build_primary_reviewer_packet,
    validate_primary_reviewer_packet,
)
from supervisor.pilot_readiness import PilotReadinessError
from supervisor.task_environment import FrozenTaskResult, Grade, TaskSpec


def _task_spec(
    task_id: str = "task-1",
    *,
    canonical_task_key: str | None = None,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        task_family="generic",
        repo="repo",
        revision="1" * 40,
        dataset_hash="2" * 64,
        split_hash="3" * 64,
        problem_statement="Fix it.",
        image_digest="sha256:" + ("4" * 64),
        architecture="arm64",
        os_name="macos",
        network_policy="disabled",
        resource_limits={"timeout_s": 30},
        verifier_id="hidden",
        verifier_hash="a" * 64,
        canonical_task_key=canonical_task_key or task_id,
        canonical_repo_id="example/repo",
    )


def _experiment(
    *,
    assignment_roster: tuple[str, ...] | None = None,
    treatments: dict[Arm, TreatmentDescriptor] | None = None,
) -> ExperimentSpec:
    matched = ArmBudget(
        max_tokens=1000,
        max_cost_usd=1.0,
        timeout_s=30,
        max_retries=1,
    )
    return ExperimentSpec(
        experiment_id="exp-1",
        assignment_version="v1",
        hmac_key=b"secret-assignment-key",
        arm_budgets={
            Arm.A: ArmBudget(
                max_tokens=500,
                max_cost_usd=0.5,
                timeout_s=20,
                max_retries=0,
            ),
            Arm.B: matched,
            Arm.C: matched,
        },
        treatments=treatments or _treatments(),
        metadata={
            "execution_mode": "hermetic",
            **(
                {"assignment_roster": assignment_roster}
                if assignment_roster is not None
                else {}
            ),
        },
    )


def _treatments() -> dict[Arm, TreatmentDescriptor]:
    return {
        Arm.A: TreatmentDescriptor(
            arm_adapter="production-baseline",
            entrypoint="baseline.execute",
            instruction_template=(
                "Use the production baseline path.\n\n{problem_statement}"
            ),
            treatment_config={"orchestration": "none", "baseline": True},
        ),
        Arm.B: TreatmentDescriptor(
            arm_adapter="supervisor-orchestration",
            entrypoint="supervisor.execute",
            instruction_template=(
                "Use supervisor orchestration.\n\n{problem_statement}"
            ),
            treatment_config={
                "orchestration": "supervisor",
                "review_passes": 1,
            },
        ),
        Arm.C: TreatmentDescriptor(
            arm_adapter="compute-matched-direct",
            entrypoint="direct.execute",
            instruction_template=(
                "Use the compute-matched direct path.\n\n{problem_statement}"
            ),
            treatment_config={"orchestration": "none", "compute_matched": True},
        ),
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_tokens", 1.5, "max_tokens"),
        ("max_cost_usd", "1.0", "max_cost_usd"),
        ("timeout_s", 1.5, "timeout_s"),
        ("max_retries", 0.5, "max_retries"),
    ],
)
def test_arm_budget_rejects_non_typed_accounting_limits(
    field: str,
    value: Any,
    message: str,
) -> None:
    values = {
        "max_tokens": 100,
        "max_cost_usd": 1.0,
        "timeout_s": 30,
        "max_retries": 0,
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        ArmBudget(**values)


def test_experiment_rejects_duplicate_treatment_hashes() -> None:
    duplicate = TreatmentDescriptor(
        arm_adapter="same-adapter",
        entrypoint="same.execute",
        instruction_template="Same instructions.\n\n{problem_statement}",
        treatment_config={"mode": "same"},
    )

    with pytest.raises(ValueError, match="treatment hashes must all differ"):
        _experiment(treatments={arm: duplicate for arm in Arm})


def test_assignment_persists_treatments_and_rejects_post_assignment_mutation(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "treatment-preregistration.db")
    kernel = ExperimentKernel(store=store, executor=RecordingExecutor(store))
    experiment = _experiment()

    assignment = kernel.assign(experiment, _task_spec())
    preregistration = store.get_preregistration(experiment.experiment_id)

    expected_hashes = {
        arm: experiment.treatments[arm].treatment_hash
        for arm in Arm
    }
    assert dict(assignment.treatment_hashes) == expected_hashes
    assert preregistration is not None
    assert preregistration["spec_hash"] == experiment.spec_hash
    assert preregistration["treatment_hashes"] == {
        arm.value: expected_hashes[arm] for arm in Arm
    }

    mutated_treatments = dict(experiment.treatments)
    mutated_treatments[Arm.B] = TreatmentDescriptor(
        arm_adapter="supervisor-orchestration",
        entrypoint="supervisor.execute",
        instruction_template=(
            "Mutated after assignment.\n\n{problem_statement}"
        ),
        treatment_config={
            "orchestration": "supervisor",
            "review_passes": 2,
        },
    )
    mutated = replace(experiment, treatments=mutated_treatments)

    with pytest.raises(ValueError, match="preregistration discrepancy"):
        kernel.assign(mutated, _task_spec())


def _runtime_manifest() -> dict[str, Any]:
    body = {
        "schema_version": "supervisor-agent-runtime-manifest/v1",
        "kind": "test",
        "provider_route": {
            "provider": "non-operational-fixture",
            "route_kind": "fixture",
            "endpoint": "not-executed",
            "model_request": "fixture",
            "complete": False,
        },
        "binary": {"sha256": "", "complete": False},
        "transport": {
            "implementation": "tests.RecordingExecutor",
            "configuration": {"mode": "fixture"},
            "configuration_sha256": hashlib.sha256(
                json.dumps(
                    {"mode": "fixture"},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "complete": False,
        },
        "tools": [],
        "complete": False,
    }
    return {
        **body,
        "manifest_sha256": hashlib.sha256(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _execution_receipt(
    *,
    arm: Arm,
    task: TaskSpec,
    budget: ArmBudget,
    assignment_id: str,
    frozen_result: FrozenTaskResult,
    sequence: int,
) -> ArmExecutionReceipt:
    execution_id = f"execution-{sequence}-{arm.name.lower()}"
    session_id = f"session-{sequence}-{arm.name.lower()}"
    token_usage = {"tokens_in": 10, "tokens_out": 5}
    treatment_hash = _treatments()[arm].treatment_hash
    plan_fingerprint = hashlib.sha256(
        f"plan::{arm.value}::{treatment_hash}".encode("utf-8")
    ).hexdigest()
    compute_resource_hash = hashlib.sha256(
        (
            "compute::matched"
            if arm in {Arm.B, Arm.C}
            else "compute::baseline"
        ).encode("utf-8")
    ).hexdigest()
    return ArmExecutionReceipt(
        execution_id=execution_id,
        result_id=f"result-{sequence}-{arm.name.lower()}",
        assignment_id=assignment_id,
        task_id=task.task_id,
        canonical_task_id=task.canonical_task_id,
        task_spec_hash=task.spec_hash,
        arm=arm,
        treatment_hash=treatment_hash,
        plan_fingerprint=plan_fingerprint,
        compute_resource_hash=compute_resource_hash,
        frozen_result_hash=frozen_result.result_hash,
        attempts=1,
        cost_usd=0.25,
        latency_ms=10,
        token_usage=token_usage,
        attempt_records=(
            {
                "attempt_index": 0,
                "execution_id": execution_id,
                "run_id": f"run-{sequence}-{arm.name.lower()}",
                "session_id": session_id,
                "status": "completed",
                "cost_usd": 0.25,
                "latency_ms": 5,
                "token_usage": token_usage,
            },
        ),
        isolation=IsolationAttestation(
            isolation_id=f"isolation-{sequence}-{arm.name.lower()}",
            workspace_id=f"workspace-{sequence}-{arm.name.lower()}",
            session_id=session_id,
            cache_namespace=f"cache-{sequence}-{arm.name.lower()}",
            memory_namespace=f"memory-{sequence}-{arm.name.lower()}",
            lesson_namespace=f"lessons-{sequence}-{arm.name.lower()}",
            enforced=True,
        ),
        environment=ExecutionEnvironmentAttestation(
            attestation_id=f"environment-{sequence}-{arm.name.lower()}",
            mode="hermetic",
            backend="test-fixture",
            image_digest=task.image_digest,
            architecture=task.architecture,
            os_name=task.os_name,
            network_policy=task.network_policy,
            resource_limits={
                **dict(task.resource_limits),
                "max_tokens": budget.max_tokens,
                "max_cost_usd": budget.max_cost_usd,
                "timeout_s": budget.timeout_s,
                "max_retries": budget.max_retries,
            },
            enforced=False,
        ),
        runtime_manifest=_runtime_manifest(),
    )


class RecordingExecutor:
    def __init__(
        self,
        store: SqliteExperimentStore,
        *,
        crash_arm: Arm | None = None,
    ) -> None:
        self.store = store
        self.crash_arm = crash_arm
        self.calls: list[Arm] = []
        self.original_hashes: dict[Arm, str] = {}

    async def execute(
        self,
        *,
        arm: Arm,
        task: TaskSpec,
        budget: ArmBudget,
        assignment_id: str,
    ) -> ArmExecution:
        assert self.store.get_assignment("exp-1", task.task_id) is not None
        self.calls.append(arm)
        if arm == self.crash_arm:
            raise RuntimeError("provider crashed")
        frozen = FrozenTaskResult.create(
            task_id=task.task_id,
            task_family=task.task_family,
            task_spec_hash=task.spec_hash,
            run_result_hash=f"run-{len(self.calls)}",
            patch=f"patch-{len(self.calls)}",
            output="done",
            metadata={
                "arm": arm.value,
                "assignment_id": assignment_id,
                "nested": {
                    "treatment": arm.value,
                    "safe": [{"harness_arm": arm.value}, {"public": "ok"}],
                },
                "public_evidence": "ok",
            },
        )
        self.original_hashes[arm] = frozen.result_hash
        receipt = _execution_receipt(
            arm=arm,
            task=task,
            budget=budget,
            assignment_id=assignment_id,
            frozen_result=frozen,
            sequence=len(self.calls),
        )
        return ArmExecution(
            frozen_result=frozen,
            attempts=1,
            cost_usd=0.25,
            latency_ms=10,
            metadata={
                "runtime_plan": {
                    "treatment_hash": receipt.treatment_hash,
                    "plan_fingerprint": receipt.plan_fingerprint,
                    "compute_resource_hash": receipt.compute_resource_hash,
                },
                "launch_metadata": {
                    "arm": arm.value,
                    "assignment_id": assignment_id,
                    "treatment_hash": receipt.treatment_hash,
                    "plan_fingerprint": receipt.plan_fingerprint,
                    "compute_resource_hash": (
                        receipt.compute_resource_hash
                    ),
                },
            },
            receipt=receipt,
        )


class RecordingVerifier:
    verifier_id = "hidden"
    verifier_version = "1"
    verifier_hash = "a" * 64

    def __init__(self) -> None:
        self.received: list[FrozenTaskResult] = []

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        self.received.append(frozen_result)
        assert "arm" not in frozen_result.metadata
        assert "assignment_id" not in frozen_result.metadata
        assert frozen_result.metadata == {
            "nested": {"safe": [{}, {"public": "ok"}]},
            "public_evidence": "ok",
            "repo": "repo",
            "canonical_repo_id": "example/repo",
            "revision": "1" * 40,
            "instance_id": "task-1",
            "canonical_task_id": _task_spec().canonical_task_id,
        }
        return Grade(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            verifier_hash="a" * 64,
            frozen_result_hash=frozen_result.result_hash,
            passed=True,
            score=1.0,
            evidence={"hidden": True},
        )


@pytest.mark.asyncio
async def test_assignment_is_deterministic_and_persisted_before_execution(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "experiment.db")
    executor = RecordingExecutor(store)
    kernel = ExperimentKernel(store=store, executor=executor)
    spec = _experiment()

    first = await kernel.run_task(spec, _task_spec(), RecordingVerifier())
    second_assignment = kernel.assign(spec, _task_spec())

    assert first.assignment.order == second_assignment.order
    assert first.assignment.assignment_id == second_assignment.assignment_id
    assert set(executor.calls) == {Arm.A, Arm.B, Arm.C}
    assert len(first.outcomes) == 3
    assert all(outcome.attempts == 1 for outcome in first.outcomes)


@pytest.mark.asyncio
async def test_pilot_readiness_is_required_before_any_arm_invocation(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "pilot-readiness-required.db")
    executor = RecordingExecutor(store)
    experiment = replace(
        _experiment(),
        experiment_id="pilot-001",
        pilot_protocol_hash="b" * 64,
        pilot_task_set_hash="c" * 64,
    )

    with pytest.raises(PilotReadinessError, match="requires"):
        await ExperimentKernel(
            store=store,
            executor=executor,
        ).run_task(experiment, _task_spec(), RecordingVerifier())

    assert executor.calls == []


def test_assignment_rejects_alias_of_an_already_assigned_underlying_task(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "canonical-alias.db")
    kernel = ExperimentKernel(store=store, executor=RecordingExecutor(store))

    first = kernel.assign(
        _experiment(),
        _task_spec("display-alias-one", canonical_task_key="issue-42"),
    )

    with pytest.raises(ValueError, match="canonical task identity"):
        kernel.assign(
            _experiment(),
            _task_spec("display-alias-two", canonical_task_key="ISSUE-42"),
        )

    assert first.canonical_task_id


def test_assignment_uses_frozen_roster_positions_independent_of_enrollment_order(
    tmp_path: Path,
) -> None:
    roster = tuple(f"task-{index:02d}" for index in range(12))

    def allocate(
        database: Path,
        enrollment_order: tuple[str, ...],
    ) -> dict[str, Any]:
        store = SqliteExperimentStore(database)
        kernel = ExperimentKernel(
            store=store,
            executor=RecordingExecutor(store),
        )
        experiment = _experiment(assignment_roster=roster)
        return {
            task_id: kernel.assign(experiment, _task_spec(task_id))
            for task_id in enrollment_order
        }

    first = allocate(tmp_path / "balanced-one.db", roster)
    second = allocate(tmp_path / "balanced-two.db", tuple(reversed(roster)))
    ordered = [first[task_id] for task_id in roster]

    assert {
        task_id: assignment.order
        for task_id, assignment in first.items()
    } == {
        task_id: assignment.order
        for task_id, assignment in second.items()
    }
    assert set(item.order for item in ordered[:6]) == set(ARM_ORDERS)
    assert set(item.order for item in ordered[6:]) == set(ARM_ORDERS)
    assert [item.block["permuted_block_position"] for item in ordered] == [
        str(index % 6) for index in range(12)
    ]
    assert [item.block["permuted_block_index"] for item in ordered] == [
        str(index // 6) for index in range(12)
    ]


def test_assignment_freezes_and_hash_binds_roster(
    tmp_path: Path,
) -> None:
    mutable_roster = ["task-1", "task-2"]
    experiment = replace(
        _experiment(),
        metadata={
            "execution_mode": "hermetic",
            "assignment_roster": mutable_roster,
        },
    )
    mutable_roster.reverse()
    store = SqliteExperimentStore(tmp_path / "frozen-roster.db")
    kernel = ExperimentKernel(store=store, executor=RecordingExecutor(store))
    assignment = kernel.assign(experiment, _task_spec("task-1"))

    assert experiment.metadata["assignment_roster"] == (
        "task-1",
        "task-2",
    )
    assert assignment.block["assignment_method"] == (
        "frozen-roster-six-order/v1"
    )
    assert len(assignment.block["assignment_roster_hash"]) == 64

    changed_roster = replace(
        experiment,
        metadata={
            "execution_mode": "hermetic",
            "assignment_roster": ("task-2", "task-1"),
        },
    )
    with pytest.raises(
        ValueError,
        match="preregistration|persisted assignment",
    ):
        kernel.assign(changed_roster, _task_spec("task-1"))


def test_assignment_blocks_on_explicit_task_class(
    tmp_path: Path,
) -> None:
    task = TaskSpec(
        **{
            **_task_spec().to_dict(),
            "task_class": "repository_bug_fix",
        }
    )
    store = SqliteExperimentStore(tmp_path / "task-class.db")
    assignment = ExperimentKernel(
        store=store,
        executor=RecordingExecutor(store),
    ).assign(_experiment(), task)

    assert assignment.block["task_family"] == "generic"
    assert assignment.block["task_class"] == "repository_bug_fix"


@pytest.mark.asyncio
async def test_verifier_is_blinded_and_arm_identity_is_joined_after_grading(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "experiment.db")
    verifier = RecordingVerifier()
    executor = RecordingExecutor(store)
    result = await ExperimentKernel(
        store=store,
        executor=executor,
    ).run_task(_experiment(), _task_spec(), verifier)

    assert len(verifier.received) == 3
    assert all("arm" not in item.metadata for item in verifier.received)
    assert {outcome.arm for outcome in result.outcomes} == {Arm.A, Arm.B, Arm.C}
    assert all(outcome.grade.passed for outcome in result.outcomes)
    for outcome in result.outcomes:
        assert (
            outcome.original_frozen_result_hash
            == executor.original_hashes[outcome.arm]
        )
        assert outcome.blinded_frozen_result_hash == outcome.frozen_result_hash
        assert outcome.grade.frozen_result_hash == outcome.blinded_frozen_result_hash
        assert outcome.original_frozen_result_hash != outcome.blinded_frozen_result_hash
        assert set(outcome.blinding_removed_paths) == {
            "metadata.arm",
            "metadata.assignment_id",
            "metadata.nested.treatment",
            "metadata.nested.safe[0].harness_arm",
        }


@pytest.mark.asyncio
async def test_crashed_arm_is_scored_as_an_intention_to_treat_failure(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "experiment.db")
    executor = RecordingExecutor(store, crash_arm=Arm.B)
    result = await ExperimentKernel(
        store=store,
        executor=executor,
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())

    outcomes = {outcome.arm: outcome for outcome in result.outcomes}
    assert outcomes[Arm.B].status == "failed"
    assert outcomes[Arm.B].grade.passed is False
    assert outcomes[Arm.B].grade.score == 0.0
    assert outcomes[Arm.B].grade.verifier_id == _task_spec().verifier_id
    assert outcomes[Arm.B].grade.verifier_hash == _task_spec().verifier_hash
    assert outcomes[Arm.B].grade.frozen_result_hash
    assert (
        outcomes[Arm.B].grade.frozen_result_hash
        == outcomes[Arm.B].blinded_frozen_result_hash
    )
    assert outcomes[Arm.B].failure_classification == "treatment_execution_failure"
    assert len(result.outcomes) == 3
    assert executor.calls == list(result.assignment.order)


@pytest.mark.asyncio
async def test_common_pre_treatment_failure_reruns_the_whole_block_once(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "common-infra.db")

    class CommonInfraExecutor(RecordingExecutor):
        def __init__(self, experiment_store: SqliteExperimentStore) -> None:
            super().__init__(experiment_store)
            self.common_failures_remaining = 1

        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
            if self.common_failures_remaining:
                self.calls.append(arm)
                self.common_failures_remaining -= 1
                raise CommonPreTreatmentInfrastructureError(
                    "shared checkout service unavailable"
                )
            return await super().execute(
                arm=arm,
                task=task,
                budget=budget,
                assignment_id=assignment_id,
            )

    executor = CommonInfraExecutor(store)
    result = await ExperimentKernel(
        store=store,
        executor=executor,
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())

    assert executor.calls == [
        result.assignment.order[0],
        *result.assignment.order,
    ]
    assert len(result.outcomes) == 3
    assert all(outcome.status == "completed" for outcome in result.outcomes)
    transitions = store.get_transitions("exp-1", "task-1")
    assert [item["kind"] for item in transitions].count(
        "block.rerun_scheduled"
    ) == 1


@pytest.mark.asyncio
async def test_repeated_common_pre_treatment_failure_never_reruns_a_third_block(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "common-infra-twice.db")

    class RepeatedCommonInfraExecutor(RecordingExecutor):
        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
            self.calls.append(arm)
            raise CommonPreTreatmentInfrastructureError(
                "shared checkout service unavailable"
            )

    executor = RepeatedCommonInfraExecutor(store)
    result = await ExperimentKernel(
        store=store,
        executor=executor,
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())

    assert executor.calls == [
        result.assignment.order[0],
        result.assignment.order[0],
    ]
    assert len(result.outcomes) == 3
    assert all(outcome.status == "failed" for outcome in result.outcomes)
    assert {
        outcome.failure_classification for outcome in result.outcomes
    } == {"common_pre_treatment_infrastructure_failure"}


@pytest.mark.asyncio
async def test_common_failure_reported_after_treatment_starts_is_not_rerunnable(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "late-common-infra.db")

    class LateCommonInfraExecutor(RecordingExecutor):
        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
            if len(self.calls) == 1:
                self.calls.append(arm)
                raise CommonPreTreatmentInfrastructureError(
                    "outage was reported after another arm completed"
                )
            return await super().execute(
                arm=arm,
                task=task,
                budget=budget,
                assignment_id=assignment_id,
            )

    executor = LateCommonInfraExecutor(store)
    result = await ExperimentKernel(
        store=store,
        executor=executor,
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())
    outcomes = {outcome.arm: outcome for outcome in result.outcomes}

    assert executor.calls == list(result.assignment.order)
    assert outcomes[result.assignment.order[1]].failure_classification == (
        "treatment_execution_failure"
    )
    assert all(
        item["kind"] != "block.rerun_scheduled"
        for item in store.get_transitions("exp-1", "task-1")
    )


class MismatchedVerifier(RecordingVerifier):
    def __init__(
        self,
        *,
        verifier_id: str = "hidden",
        verifier_hash: str = "a" * 64,
        frozen_result_hash: str | None = None,
    ) -> None:
        super().__init__()
        self.grade_verifier_id = verifier_id
        self.grade_verifier_hash = verifier_hash
        self.grade_frozen_result_hash = frozen_result_hash

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        self.received.append(frozen_result)
        return Grade(
            verifier_id=self.grade_verifier_id,
            verifier_version=self.verifier_version,
            verifier_hash=self.grade_verifier_hash,
            frozen_result_hash=(
                self.grade_frozen_result_hash or frozen_result.result_hash
            ),
            passed=True,
            score=1.0,
            evidence={"hidden": True},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verifier", "match"),
    [
        (MismatchedVerifier(verifier_id="other"), "verifier_id"),
        (MismatchedVerifier(verifier_hash="other-sha"), "verifier_hash"),
        (MismatchedVerifier(frozen_result_hash="other-result"), "frozen_result_hash"),
    ],
)
async def test_grade_must_match_blinded_result_and_task_verifier_pins(
    tmp_path: Path,
    verifier: MismatchedVerifier,
    match: str,
) -> None:
    store = SqliteExperimentStore(tmp_path / f"{match}.db")

    with pytest.raises(ValueError, match=match):
        await ExperimentKernel(
            store=store,
            executor=RecordingExecutor(store),
        ).run_task(_experiment(), _task_spec(), verifier)


@pytest.mark.asyncio
async def test_verifier_adapter_hash_must_match_persisted_task_before_execution(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "verifier-adapter-pin.db")
    executor = RecordingExecutor(store)
    verifier = RecordingVerifier()
    verifier.verifier_hash = "b" * 64

    with pytest.raises(ValueError, match="verifier_hash"):
        await ExperimentKernel(
            store=store,
            executor=executor,
        ).run_task(_experiment(), _task_spec(), verifier)

    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_field", "substitute"),
    [
        ("repo", "https://example.invalid/substitute.git"),
        ("revision", "0" * 40),
        ("instance_id", "substitute-instance"),
    ],
)
async def test_kernel_rejects_executor_verifier_identity_substitution(
    tmp_path: Path,
    identity_field: str,
    substitute: str,
) -> None:
    store = SqliteExperimentStore(
        tmp_path / f"identity-substitution-{identity_field}.db"
    )

    class SubstitutingExecutor(RecordingExecutor):
        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
            execution = await super().execute(
                arm=arm,
                task=task,
                budget=budget,
                assignment_id=assignment_id,
            )
            assert execution.receipt is not None
            substituted = FrozenTaskResult.create(
                task_id=execution.frozen_result.task_id,
                task_family=execution.frozen_result.task_family,
                task_spec_hash=execution.frozen_result.task_spec_hash,
                run_result_hash=execution.frozen_result.run_result_hash,
                patch=execution.frozen_result.patch,
                output=execution.frozen_result.output,
                metadata={
                    **dict(execution.frozen_result.metadata),
                    identity_field: substitute,
                },
                frozen_at_ms=execution.frozen_result.frozen_at_ms,
            )
            return replace(
                execution,
                frozen_result=substituted,
                receipt=replace(
                    execution.receipt,
                    frozen_result_hash=substituted.result_hash,
                ),
            )

    with pytest.raises(ValueError, match="persisted TaskSpec"):
        await ExperimentKernel(
            store=store,
            executor=SubstitutingExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
async def test_kernel_requires_typed_execution_receipt(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "missing-receipt.db")

    class UnreceiptedExecutor(RecordingExecutor):
        async def execute(self, **kwargs: Any) -> ArmExecution:
            execution = await super().execute(**kwargs)
            return replace(execution, receipt=None, metadata={})

    with pytest.raises(ValueError, match="typed execution receipt"):
        await ExperimentKernel(
            store=store,
            executor=UnreceiptedExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
async def test_explicit_hermetic_tracer_may_rebind_fixture_annotation(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "fixture-annotation.db")

    class AnnotatingFixtureExecutor(RecordingExecutor):
        async def execute(self, **kwargs: Any) -> ArmExecution:
            execution = await super().execute(**kwargs)
            assert execution.receipt is not None
            annotated = FrozenTaskResult.create(
                task_id=execution.frozen_result.task_id,
                task_family=execution.frozen_result.task_family,
                task_spec_hash=execution.frozen_result.task_spec_hash,
                run_result_hash=execution.frozen_result.run_result_hash,
                patch=execution.frozen_result.patch,
                output=execution.frozen_result.output,
                metadata={
                    **dict(execution.frozen_result.metadata),
                    "harness_arm": kwargs["arm"].value,
                    "fixture_annotation": "recorded",
                },
                frozen_at_ms=execution.frozen_result.frozen_at_ms,
            )
            return replace(
                execution,
                frozen_result=annotated,
                receipt=None,
                metadata={
                    **dict(execution.metadata),
                    "execution_receipt": execution.receipt.to_dict(),
                },
            )

    class FixtureVerifier:
        verifier_id = "hidden"
        verifier_version = "1"
        verifier_hash = "a" * 64

        async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
            assert "harness_arm" not in frozen_result.metadata
            assert frozen_result.metadata["fixture_annotation"] == "recorded"
            return Grade(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                verifier_hash=self.verifier_hash,
                frozen_result_hash=frozen_result.result_hash,
                passed=True,
                score=1.0,
                evidence={"fixture": True},
            )

    task = replace(
        _task_spec(),
        metadata={"tracer_mode": "hermetic"},
    )
    result = await ExperimentKernel(
        store=store,
        executor=AnnotatingFixtureExecutor(store),
    ).run_task(_experiment(), task, FixtureVerifier())

    assert all(outcome.status == "completed" for outcome in result.outcomes)
    assert all(
        outcome.execution_receipt is not None
        and outcome.execution_receipt.environment.mode == "hermetic"
        and outcome.execution_receipt.environment.enforced is False
        for outcome in result.outcomes
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binding_field", "substitute", "message"),
    [
        ("assignment_id", "other-assignment", "assignment_id"),
        ("task_id", "other-task", "task_id"),
        ("canonical_task_id", "0" * 64, "canonical task identity"),
        ("task_spec_hash", "0" * 64, "task_spec_hash"),
        ("treatment_hash", "0" * 64, "treatment hash"),
    ],
)
async def test_kernel_rejects_execution_receipt_binding_substitution(
    tmp_path: Path,
    binding_field: str,
    substitute: str,
    message: str,
) -> None:
    store = SqliteExperimentStore(
        tmp_path / f"receipt-binding-{binding_field}.db"
    )

    class MisboundExecutor(RecordingExecutor):
        async def execute(self, **kwargs: Any) -> ArmExecution:
            execution = await super().execute(**kwargs)
            assert execution.receipt is not None
            return replace(
                execution,
                receipt=replace(
                    execution.receipt,
                    **{binding_field: substitute},
                ),
            )

    with pytest.raises(ValueError, match=message):
        await ExperimentKernel(
            store=store,
            executor=MisboundExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_surface", ["launch_metadata", "runtime_plan"])
async def test_kernel_rejects_treatment_hash_mismatch_with_launched_plan(
    tmp_path: Path,
    launch_surface: str,
) -> None:
    store = SqliteExperimentStore(
        tmp_path / f"launch-treatment-{launch_surface}.db"
    )

    class MismatchedLaunchExecutor(RecordingExecutor):
        async def execute(self, **kwargs: Any) -> ArmExecution:
            execution = await super().execute(**kwargs)
            return replace(
                execution,
                metadata={
                    **dict(execution.metadata),
                    launch_surface: {
                        **dict(execution.metadata[launch_surface]),
                        "treatment_hash": "0" * 64,
                    },
                },
            )

    with pytest.raises(
        ValueError,
        match="launched (plan treatment_hash|runtime plan.*treatment_hash)",
    ):
        await ExperimentKernel(
            store=store,
            executor=MismatchedLaunchExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
async def test_kernel_independently_rejects_mismatched_b_c_compute_hashes(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "compute-hash-mismatch.db")

    class MismatchedComputeExecutor(RecordingExecutor):
        async def execute(self, **kwargs: Any) -> ArmExecution:
            execution = await super().execute(**kwargs)
            if kwargs["arm"] is not Arm.C:
                return execution
            assert execution.receipt is not None
            mismatched_hash = "f" * 64
            receipt = replace(
                execution.receipt,
                compute_resource_hash=mismatched_hash,
            )
            return replace(
                execution,
                receipt=receipt,
                metadata={
                    **dict(execution.metadata),
                    "runtime_plan": {
                        **dict(execution.metadata["runtime_plan"]),
                        "compute_resource_hash": mismatched_hash,
                    },
                    "launch_metadata": {
                        **dict(execution.metadata["launch_metadata"]),
                        "compute_resource_hash": mismatched_hash,
                    },
                },
            )

    with pytest.raises(ValueError, match="compute/resource hashes must match"):
        await ExperimentKernel(
            store=store,
            executor=MismatchedComputeExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity_field",
    [
        "execution_id",
        "result_id",
        "isolation_id",
        "workspace_id",
        "environment_attestation_id",
    ],
)
async def test_kernel_rejects_shared_execution_and_isolation_identity(
    tmp_path: Path,
    identity_field: str,
) -> None:
    store = SqliteExperimentStore(
        tmp_path / f"shared-{identity_field}.db"
    )

    class SharedIdentityExecutor(RecordingExecutor):
        first_receipt: ArmExecutionReceipt | None = None

        async def execute(self, **kwargs: Any) -> ArmExecution:
            execution = await super().execute(**kwargs)
            assert execution.receipt is not None
            if self.first_receipt is None:
                self.first_receipt = execution.receipt
                return execution
            first = self.first_receipt
            if identity_field in {"execution_id", "result_id"}:
                replacement = getattr(first, identity_field)
                records = execution.receipt.attempt_records
                if identity_field == "execution_id":
                    records = tuple(
                        {
                            **dict(record),
                            "execution_id": replacement,
                        }
                        for record in records
                    )
                receipt = replace(
                    execution.receipt,
                    **{identity_field: replacement},
                    attempt_records=records,
                )
            elif identity_field in {"isolation_id", "workspace_id"}:
                receipt = replace(
                    execution.receipt,
                    isolation=replace(
                        execution.receipt.isolation,
                        **{
                            identity_field: getattr(
                                first.isolation,
                                identity_field,
                            ),
                        },
                    ),
                )
            else:
                receipt = replace(
                    execution.receipt,
                    environment=replace(
                        execution.receipt.environment,
                        attestation_id=first.environment.attestation_id,
                    ),
                )
            return replace(execution, receipt=receipt)

    with pytest.raises(ValueError, match=f"shared {identity_field}"):
        await ExperimentKernel(
            store=store,
            executor=SharedIdentityExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("zero_attempts", "attempts must be a positive integer"),
        ("missing_token_axis", "tokens_out"),
        ("impossible_cost", "impossible cost accounting"),
        ("attempt_count", "attempt count"),
        ("invalid_attempt", "attempt status is invalid"),
        ("token_budget", "exceeds arm token budget"),
        ("negative_latency", "latency_ms must be a non-negative integer"),
    ],
)
async def test_kernel_rejects_impossible_execution_accounting(
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    store = SqliteExperimentStore(tmp_path / f"receipt-{corruption}.db")

    class ImpossibleAccountingExecutor(RecordingExecutor):
        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
            execution = await super().execute(
                arm=arm,
                task=task,
                budget=budget,
                assignment_id=assignment_id,
            )
            assert execution.receipt is not None
            receipt = execution.receipt
            if corruption == "zero_attempts":
                return replace(
                    execution,
                    attempts=0,
                    receipt=replace(
                        receipt,
                        attempts=0,
                        attempt_records=(),
                    ),
                )
            if corruption == "missing_token_axis":
                return replace(
                    execution,
                    receipt=replace(
                        receipt,
                        token_usage={"tokens_in": 10},
                    ),
                )
            if corruption == "impossible_cost":
                return replace(
                    execution,
                    cost_usd=0.5,
                    receipt=replace(receipt, cost_usd=0.5),
                )
            if corruption == "attempt_count":
                return replace(
                    execution,
                    attempts=2,
                    receipt=replace(receipt, attempts=2),
                )
            if corruption == "invalid_attempt":
                record = {
                    **dict(receipt.attempt_records[0]),
                    "status": "invented",
                }
                return replace(
                    execution,
                    receipt=replace(
                        receipt,
                        attempt_records=(record,),
                    ),
                )
            if corruption == "token_budget":
                tokens = {
                    "tokens_in": budget.max_tokens + 1,
                    "tokens_out": 0,
                }
                record = {
                    **dict(receipt.attempt_records[0]),
                    "token_usage": tokens,
                }
                return replace(
                    execution,
                    receipt=replace(
                        receipt,
                        token_usage=tokens,
                        attempt_records=(record,),
                    ),
                )
            return replace(
                execution,
                latency_ms=-1,
                receipt=replace(receipt, latency_ms=-1),
            )

    with pytest.raises(ValueError, match=message):
        await ExperimentKernel(
            store=store,
            executor=ImpossibleAccountingExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.asyncio
async def test_non_operational_receipt_cannot_masquerade_as_operational(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "operational-mode.db")
    operational = replace(
        _experiment(),
        metadata={"execution_mode": "operational"},
    )

    with pytest.raises(ValueError, match="mode does not match"):
        await ExperimentKernel(
            store=store,
            executor=RecordingExecutor(store),
        ).run_task(operational, _task_spec(), RecordingVerifier())


class LeakingExecutor(RecordingExecutor):
    async def execute(
        self,
        *,
        arm: Arm,
        task: TaskSpec,
        budget: ArmBudget,
        assignment_id: str,
    ) -> ArmExecution:
        execution = await super().execute(
            arm=arm,
            task=task,
            budget=budget,
            assignment_id=assignment_id,
        )
        leaking = FrozenTaskResult.create(
            task_id=task.task_id,
            task_family=task.task_family,
            task_spec_hash=task.spec_hash,
            run_result_hash=execution.frozen_result.run_result_hash,
            patch=execution.frozen_result.patch,
            output=f"candidate selected with arm={arm.name}",
            metadata={"public_evidence": "ok"},
            frozen_at_ms=execution.frozen_result.frozen_at_ms,
        )
        assert execution.receipt is not None
        return replace(
            execution,
            frozen_result=leaking,
            receipt=replace(
                execution.receipt,
                frozen_result_hash=leaking.result_hash,
            ),
        )


@pytest.mark.asyncio
async def test_output_level_arm_identity_leakage_is_rejected(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "leak.db")

    with pytest.raises(ValueError, match="arm identity leakage"):
        await ExperimentKernel(
            store=store,
            executor=LeakingExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_persisted_assignment_is_storage_immutable(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = SqliteExperimentStore(tmp_path / f"{mutation}.db")
    kernel = ExperimentKernel(store=store, executor=RecordingExecutor(store))
    expected = kernel.assign(_experiment(), _task_spec())

    statement = (
        """UPDATE experiment_assignments
           SET block_json='{}'
           WHERE experiment_id='exp-1' AND task_id='task-1'"""
        if mutation == "update"
        else """DELETE FROM experiment_assignments
                WHERE experiment_id='exp-1' AND task_id='task-1'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._conn.execute(  # noqa: SLF001 - adversarial storage mutation
            statement
        )

    assert kernel.assign(_experiment(), _task_spec()) == expected


def test_transition_append_is_idempotent_and_rejects_discrepancy(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "transitions.db")
    first = store.append_transition(
        experiment_id="exp-1",
        task_id="task-1",
        kind="task.started",
        payload={"assignment_id": "assignment-1"},
        idempotency_key="task.started",
    )
    repeated = store.append_transition(
        experiment_id="exp-1",
        task_id="task-1",
        kind="task.started",
        payload={"assignment_id": "assignment-1"},
        idempotency_key="task.started",
    )

    assert repeated == first
    assert len(store.get_transitions("exp-1", "task-1")) == 1

    with pytest.raises(ValueError, match="transition discrepancy"):
        store.append_transition(
            experiment_id="exp-1",
            task_id="task-1",
            kind="task.started",
            payload={"assignment_id": "different-assignment"},
            idempotency_key="task.started",
        )


def test_concurrent_transition_appends_preserve_one_linear_hash_chain(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent-transitions.db"
    initial = SqliteExperimentStore(database)
    initial._conn.close()  # noqa: SLF001 - each thread owns its SQLite handle
    worker_count = 4
    events_per_worker = 8
    barrier = threading.Barrier(worker_count)

    def append_worker(worker_index: int) -> None:
        store = SqliteExperimentStore(database)
        barrier.wait()
        for event_index in range(events_per_worker):
            key = f"worker.{worker_index}.event.{event_index}"
            store.append_transition(
                experiment_id="exp-1",
                task_id="task-1",
                kind="worker.event",
                payload={
                    "worker_index": worker_index,
                    "event_index": event_index,
                },
                idempotency_key=key,
            )
        store._conn.close()  # noqa: SLF001 - worker-owned SQLite handle

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        list(pool.map(append_worker, range(worker_count)))

    verifier = SqliteExperimentStore(database)
    transitions = verifier.get_transitions("exp-1", "task-1")
    assert len(transitions) == worker_count * events_per_worker
    assert len({item["transition_hash"] for item in transitions}) == len(
        transitions
    )


def test_blinding_rejects_identity_hidden_under_non_identity_key() -> None:
    frozen = FrozenTaskResult.create(
        task_id="task-1",
        task_family="generic",
        task_spec_hash="task-spec",
        run_result_hash="run-1",
        patch="patch",
        output="done",
        metadata={"candidate_label": Arm.C.value},
    )

    with pytest.raises(ValueError, match="arm identity leakage"):
        blind_frozen_result(frozen)


@pytest.mark.parametrize(
    "leaking_value",
    [
        '{"arm": "B"}',
        'prefix {"arm": "B"} suffix',
        "candidate executed under arm C",
        "logs selected compute_matched_direct",
        "candidate label Arm.C",
        "runtime selected supervisor",
    ],
)
def test_blinding_rejects_encoded_or_free_text_arm_identity(
    leaking_value: str,
) -> None:
    frozen = FrozenTaskResult.create(
        task_id="task-1",
        task_family="generic",
        task_spec_hash="task-spec",
        run_result_hash="run-1",
        patch="patch",
        output="done",
        metadata={"public_evidence": leaking_value},
    )

    with pytest.raises(ValueError, match="arm identity leakage"):
        blind_frozen_result(frozen)


@pytest.mark.asyncio
async def test_executor_frozen_result_hash_must_match_its_contents(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "tampered-result.db")

    class TamperingExecutor(RecordingExecutor):
        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
                execution = await super().execute(
                    arm=arm,
                    task=task,
                    budget=budget,
                    assignment_id=assignment_id,
                )
                assert execution.receipt is not None
                return replace(
                    execution,
                frozen_result=replace(
                    execution.frozen_result,
                    result_hash="forged-result-hash",
                ),
                receipt=replace(
                    execution.receipt,
                    frozen_result_hash="forged-result-hash",
                ),
            )

    with pytest.raises(ValueError, match="FrozenTaskResult result_hash"):
        await ExperimentKernel(
            store=store,
            executor=TamperingExecutor(store),
        ).run_task(_experiment(), _task_spec(), RecordingVerifier())


def test_primary_comparison_cannot_move_off_compute_matched_c() -> None:
    with pytest.raises(ValueError, match="primary comparison"):
        replace(_experiment(), primary_comparison=(Arm.B, Arm.A))


def _raw_tests(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": "supervisor-raw-test-artifact/v1",
        "command": ["uv", "run", "pytest", "-q"],
        "exit_code": 0,
        "stdout": "3 passed",
        "stderr": "",
        "duration_ms": 12,
        "result_files": {"junit.xml": "b" * 64},
        **overrides,
    }


def test_reviewer_packets_blind_primary_and_adjudicate_only_persisted_reviews(
    tmp_path: Path,
) -> None:
    task = {
        "task_id": "task-1",
        "problem_statement": "fix",
    }
    diff = "diff --git a/a.py b/a.py\n"
    tests = _raw_tests()
    lead_outcome = {"accepted": True, "reason": "done"}

    primary = build_primary_reviewer_packet(
        task=task,
        diff=diff,
        tests=tests,
    )
    store = SqliteExperimentStore(tmp_path / "primary-reviews.db")
    receipts = [
        store.record_primary_review(
            experiment_id="exp-1",
            task_id="task-1",
            reviewer_id=reviewer_id,
            primary_packet_hash=primary["packet_hash"],
            review={"decision": decision},
            completed_at_ms=completed_at_ms,
        )
        for reviewer_id, decision, completed_at_ms in (
            ("reviewer-one", "accept", 100),
            ("reviewer-two", "reject", 101),
        )
    ]
    adjudicator = build_adjudicator_packet(
        experiment_id="exp-1",
        task_id="task-1",
        task=task,
        diff=diff,
        tests=tests,
        review_store=store,
        adjudication_started_at_ms=(
            max(receipt.persisted_at_ms for receipt in receipts) + 1
        ),
        lead_outcome=lead_outcome,
    )

    assert "lead_outcome" not in primary
    assert primary["task"] == {
        "task_id": "task-1",
        "problem_statement": "fix",
    }
    assert primary["diff"]["patch"] == diff
    assert primary["tests"]["receipt"] == tests
    assert len(primary["packet_hash"]) == 64
    assert adjudicator["lead_outcome"] == lead_outcome
    assert [
        review["review"]["decision"]
        for review in adjudicator["primary_reviews"]
    ] == ["accept", "reject"]
    assert all(
        len(review["review_hash"]) == 64
        and len(review["receipt_hash"]) == 64
        for review in adjudicator["primary_reviews"]
    )


@pytest.mark.parametrize(
    "result_files",
    [
        {"lead_outcome": "b" * 64},
        {"lead/decision": "b" * 64},
        {"leadVerdict": "b" * 64},
    ],
)
def test_primary_reviewer_packet_rejects_recursive_lead_outcome_fields(
    result_files: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="lead outcome"):
        build_primary_reviewer_packet(
            task={"task_id": "task-1", "problem": "fix"},
            diff="diff --git a/a.py b/a.py\n",
            tests=_raw_tests(result_files=result_files),
        )


def test_primary_reviewer_packet_rejects_semantic_lead_outcome_leak() -> None:
    with pytest.raises(ValueError, match="lead outcome"):
        build_primary_reviewer_packet(
            task={"task_id": "task-1", "problem": "fix"},
            diff="diff --git a/a.py b/a.py\n",
            tests=_raw_tests(
                stdout="The lead reviewer rejected this candidate."
            ),
        )


def test_primary_reviewer_packet_rejects_free_text_test_summary() -> None:
    with pytest.raises(ValueError, match="free-text summaries"):
        build_primary_reviewer_packet(
            task={"task_id": "task-1", "problem": "fix"},
            diff="diff --git a/a.py b/a.py\n",
            tests={"summary": "looks green"},
        )


def test_primary_reviewer_packet_validation_rejects_tampered_raw_artifacts() -> None:
    packet = build_primary_reviewer_packet(
        task={"task_id": "task-1", "problem": "fix"},
        diff="diff --git a/a.py b/a.py\n",
        tests=_raw_tests(),
    )
    tampered = packet.to_dict()
    tampered["tests"] = {
        **tampered["tests"],
        "receipt": {
            **tampered["tests"]["receipt"],
            "stdout": "forged result",
        },
    }

    with pytest.raises(ValueError, match="test hash"):
        validate_primary_reviewer_packet(tampered)


def test_primary_reviewer_packet_validation_rejects_forged_arm_identity() -> None:
    packet = build_primary_reviewer_packet(
        task={"task_id": "task-1", "problem": "fix"},
        diff="diff --git a/a.py b/a.py\n",
        tests=_raw_tests(),
    ).to_dict()
    patch = "diff --git a/a.py b/a.py\n# selected arm B\n"
    packet["diff"] = {
        "patch": patch,
        "sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
    }
    body = {
        key: packet[key]
        for key in ("schema_version", "task", "diff", "tests")
    }
    packet["packet_hash"] = hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match="arm identity leakage"):
        validate_primary_reviewer_packet(packet)


def test_adjudication_rejects_reviews_not_persisted_before_it_starts(
    tmp_path: Path,
) -> None:
    task = {"task_id": "task-1", "problem": "fix"}
    diff = "diff --git a/a.py b/a.py\n"
    tests = _raw_tests()
    primary = build_primary_reviewer_packet(
        task=task,
        diff=diff,
        tests=tests,
    )
    store = SqliteExperimentStore(tmp_path / "late-review.db")
    receipts = [
        store.record_primary_review(
            experiment_id="exp-1",
            task_id="task-1",
            reviewer_id=f"reviewer-{index}",
            primary_packet_hash=primary["packet_hash"],
            review={"decision": "accept"},
            completed_at_ms=100 + index,
        )
        for index in range(2)
    ]

    with pytest.raises(ValueError, match="before adjudication"):
        build_adjudicator_packet(
            experiment_id="exp-1",
            task_id="task-1",
            task=task,
            diff=diff,
            tests=tests,
            review_store=store,
            adjudication_started_at_ms=max(
                receipt.persisted_at_ms for receipt in receipts
            ),
            lead_outcome={"accepted": True},
        )


@pytest.mark.asyncio
async def test_verifier_exception_persists_terminal_failure_and_transitions(
    tmp_path: Path,
) -> None:
    class RaisingVerifier(RecordingVerifier):
        async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
            raise RuntimeError("hidden verifier unavailable")

    store = SqliteExperimentStore(tmp_path / "verifier-failure.db")
    result = await ExperimentKernel(
        store=store,
        executor=RecordingExecutor(store),
    ).run_task(_experiment(), _task_spec(), RaisingVerifier())

    persisted = store.get_result("exp-1", "task-1")
    transitions = store.get_transitions("exp-1", "task-1")

    assert persisted == result
    assert len(result.outcomes) == 3
    assert all(outcome.status == "failed" for outcome in result.outcomes)
    assert all(
        outcome.failure_classification == "verifier_failure"
        for outcome in result.outcomes
    )
    assert all(outcome.attempts == 1 for outcome in result.outcomes)
    assert all(outcome.cost_usd == 0.25 for outcome in result.outcomes)
    assert all(outcome.latency_ms == 10 for outcome in result.outcomes)
    assert transitions[-1]["kind"] == "task.failed"
    assert transitions[-1]["payload"]["failure_count"] == 3


@pytest.mark.asyncio
async def test_execution_failure_preserves_structured_attempt_usage(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "execution-failure.db")

    class FailingExecutor(RecordingExecutor):
        async def execute(
            self,
            *,
            arm: Arm,
            task: TaskSpec,
            budget: ArmBudget,
            assignment_id: str,
        ) -> ArmExecution:
            raise ArmExecutionError(
                "paid attempts exhausted",
                attempts=2,
                cost_usd=0.75,
                latency_ms=321,
                token_usage={"tokens_in": 80, "tokens_out": 20},
                attempt_records=(
                    {"attempt_index": 0, "cost_usd": 0.25},
                    {"attempt_index": 1, "cost_usd": 0.5},
                ),
                failure_classification="budget_exhausted",
            )

    result = await ExperimentKernel(
        store=store,
        executor=FailingExecutor(store),
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())

    for outcome in result.outcomes:
        assert outcome.attempts == 2
        assert outcome.cost_usd == 0.75
        assert outcome.latency_ms == 321
        assert outcome.token_usage == {"tokens_in": 80, "tokens_out": 20}
        assert len(outcome.attempt_records) == 2
        assert outcome.failure_classification == "budget_exhausted"


@pytest.mark.asyncio
async def test_persisted_results_and_transitions_are_storage_immutable(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "immutable.db")
    await ExperimentKernel(
        store=store,
        executor=RecordingExecutor(store),
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._conn.execute(  # noqa: SLF001 - adversarial storage mutation
            """UPDATE experiment_task_results SET result_json='{}'
               WHERE experiment_id='exp-1' AND task_id='task-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._conn.execute(  # noqa: SLF001 - adversarial storage mutation
            """DELETE FROM experiment_transitions
               WHERE experiment_id='exp-1' AND task_id='task-1'"""
        )


@pytest.mark.asyncio
async def test_terminal_result_and_transition_are_idempotent_but_discrepant(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "terminal-idempotency.db")
    result = await ExperimentKernel(
        store=store,
        executor=RecordingExecutor(store),
    ).run_task(_experiment(), _task_spec(), RecordingVerifier())
    terminal = store.get_transitions("exp-1", "task-1")[-1]
    transition_count = len(store.get_transitions("exp-1", "task-1"))

    repeated = store.complete_task(
        result,
        kind=terminal["kind"],
        payload=terminal["payload"],
        idempotency_key="task.terminal",
    )

    assert repeated == terminal
    assert len(store.get_transitions("exp-1", "task-1")) == transition_count

    discrepant = replace(result, outcomes=tuple(reversed(result.outcomes)))
    with pytest.raises(ValueError, match="experiment result discrepancy"):
        store.complete_task(
            discrepant,
            kind=terminal["kind"],
            payload=terminal["payload"],
            idempotency_key="task.terminal",
        )
    assert store.get_result("exp-1", "task-1") == result
    assert len(store.get_transitions("exp-1", "task-1")) == transition_count


@pytest.mark.asyncio
async def test_task_execution_is_idempotent_and_regrade_appends_lineage_only(
    tmp_path: Path,
) -> None:
    store = SqliteExperimentStore(tmp_path / "regrade.db")
    executor = RecordingExecutor(store)
    kernel = ExperimentKernel(store=store, executor=executor)
    task = _task_spec()
    experiment = _experiment()

    first = await kernel.run_task(
        experiment,
        task,
        RecordingVerifier(),
    )
    calls_after_first = tuple(executor.calls)
    repeated = await kernel.run_task(
        experiment,
        task,
        RecordingVerifier(),
    )

    assert repeated == first
    assert tuple(executor.calls) == calls_after_first
    assert all(
        outcome.grade_revision is not None
        and outcome.grade_revision.revision_number == 1
        for outcome in first.outcomes
    )
    assert all(
        outcome.execution_receipt is not None
        and outcome.execution_receipt.environment.mode == "hermetic"
        and outcome.execution_receipt.environment.enforced is False
        for outcome in first.outcomes
    )

    class RegradingVerifier:
        verifier_id = "hidden"
        verifier_version = "2"
        verifier_hash = "a" * 64

        async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
            return Grade(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                verifier_hash=self.verifier_hash,
                frozen_result_hash=frozen_result.result_hash,
                passed=False,
                score=0.0,
                evidence={"regrade": True},
                failure_classification="tests_failed",
            )

    target = first.outcomes[0]
    assert target.grade_revision is not None
    revision = await kernel.regrade_arm(
        experiment_id=first.experiment_id,
        task_id=first.task_id,
        arm=target.arm,
        verifier=RegradingVerifier(),
        reason="pinned verifier rerun",
    )
    root = kernel.gradebook.get_revision(target.grade_revision.grade_id)
    history = kernel.gradebook.list_revisions(root.run_envelope)

    assert tuple(executor.calls) == calls_after_first
    assert store.get_result(first.experiment_id, first.task_id) == first
    assert revision.revision_number == 2
    assert revision.supersedes_grade_id == target.grade_revision.grade_id
    assert [item.grade_id for item in history] == [
        target.grade_revision.grade_id,
        revision.grade_id,
    ]
    assert store.get_transitions(first.experiment_id, first.task_id)[-1][
        "kind"
    ] == "arm.regraded"
