from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from supervisor.experiment_kernel import (
    Arm,
    ArmBudget,
    ArmExecution,
    ExperimentKernel,
    ExperimentSpec,
    SqliteExperimentStore,
    blind_frozen_result,
    build_adjudicator_packet,
    build_primary_reviewer_packet,
)
from supervisor.task_environment import FrozenTaskResult, Grade, TaskSpec


def _task_spec(task_id: str = "task-1") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        task_family="generic",
        repo="repo",
        revision="rev",
        dataset_hash="dataset",
        split_hash="split",
        problem_statement="Fix it.",
        image_digest="sha256:image",
        architecture="arm64",
        os_name="macos",
        network_policy="disabled",
        resource_limits={"timeout_s": 30},
        verifier_id="hidden",
        verifier_hash="hidden-sha",
    )


def _experiment() -> ExperimentSpec:
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
        return ArmExecution(
            frozen_result=frozen,
            attempts=1,
            cost_usd=0.25,
            latency_ms=10,
        )


class RecordingVerifier:
    verifier_id = "hidden"
    verifier_version = "1"

    def __init__(self) -> None:
        self.received: list[FrozenTaskResult] = []

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        self.received.append(frozen_result)
        assert "arm" not in frozen_result.metadata
        assert "assignment_id" not in frozen_result.metadata
        assert frozen_result.metadata == {
            "nested": {"safe": [{}, {"public": "ok"}]},
            "public_evidence": "ok",
        }
        return Grade(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            verifier_hash="hidden-sha",
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
        assert outcome.original_frozen_result_hash == executor.original_hashes[outcome.arm]
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
    result = await ExperimentKernel(
        store=store,
        executor=RecordingExecutor(store, crash_arm=Arm.B),
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


class MismatchedVerifier(RecordingVerifier):
    def __init__(
        self,
        *,
        verifier_id: str = "hidden",
        verifier_hash: str = "hidden-sha",
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
        return ArmExecution(
            frozen_result=leaking,
            attempts=execution.attempts,
            cost_usd=execution.cost_usd,
            latency_ms=execution.latency_ms,
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


@pytest.mark.parametrize("tampered_field", ["block", "order"])
def test_persisted_assignment_is_revalidated_against_hmac_derivation(
    tmp_path: Path,
    tampered_field: str,
) -> None:
    store = SqliteExperimentStore(tmp_path / f"{tampered_field}.db")
    kernel = ExperimentKernel(store=store, executor=RecordingExecutor(store))
    expected = kernel.assign(_experiment(), _task_spec())

    if tampered_field == "block":
        store._conn.execute(  # noqa: SLF001 - intentional corruption tracer
            """UPDATE experiment_assignments
               SET block_json=?
               WHERE experiment_id=? AND task_id=?""",
            ('{"model":"tampered","repo":"repo","task_family":"generic"}', "exp-1", "task-1"),
        )
    else:
        reversed_order = list(reversed([arm.value for arm in expected.order]))
        store._conn.execute(  # noqa: SLF001 - intentional corruption tracer
            """UPDATE experiment_assignments
               SET order_json=?
               WHERE experiment_id=? AND task_id=?""",
            (json.dumps(reversed_order), "exp-1", "task-1"),
        )
    store._conn.commit()  # noqa: SLF001 - intentional corruption tracer

    with pytest.raises(ValueError, match=f"persisted assignment {tampered_field}"):
        kernel.assign(_experiment(), _task_spec())


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
            return replace(
                execution,
                frozen_result=replace(
                    execution.frozen_result,
                    result_hash="forged-result-hash",
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


def test_reviewer_packets_blind_primary_judges_but_not_late_adjudication() -> None:
    task = {
        "task_id": "task-1",
        "problem": "fix",
        "context": {"assignment": {"arm": Arm.B.value}},
    }
    evidence = {
        "diff": "patch",
        "tests": {"status": "green", "treatment": Arm.B.value},
    }
    lead_outcome = {"accepted": True, "reason": "done"}

    primary = build_primary_reviewer_packet(task=task, evidence=evidence)
    adjudicator = build_adjudicator_packet(
        task=task,
        evidence=evidence,
        primary_reviews=[{"decision": "accept"}],
        lead_outcome=lead_outcome,
    )

    assert "lead_outcome" not in primary
    assert primary["task"] == {
        "task_id": "task-1",
        "problem": "fix",
        "context": {},
    }
    assert primary["evidence"] == {
        "diff": "patch",
        "tests": {"status": "green"},
    }
    assert adjudicator["lead_outcome"] == lead_outcome
    assert adjudicator["primary_reviews"] == [{"decision": "accept"}]
