from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import shlex
import subprocess
from typing import Any

import pytest

from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    ClaudeCodeRuntime,
    CodexRuntime,
    RuntimeEvent,
    SubprocessRuntimeTransport,
)
from supervisor.arm_executor import RepositoryArmExecutor, RuntimeArmPlan
from supervisor.experiment_kernel import (
    Arm,
    ArmBudget,
    ArmExecutionError,
    TreatmentDescriptor,
)
from supervisor.task_environment import (
    GenericRepositoryTask,
    TaskSpec,
    default_task_platform,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "user.name", "Harness")
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


class DeterministicRuntime:
    kind = "deterministic"

    def __init__(self) -> None:
        self.tasks: dict[str, AgentTask] = {}
        self.started_tasks: list[AgentTask] = []

    async def start(self, task: AgentTask) -> AgentRunHandle:
        self.tasks[task.task_id] = task
        self.started_tasks.append(task)
        (task.cwd / "README.md").write_text(
            f"{task.metadata['experiment']['arm']}\n",
            encoding="utf-8",
        )
        return AgentRunHandle(
            run_id=f"run-{task.task_id}",
            task_id=task.task_id,
            runtime=self.kind,
            session_id=f"session-{task.task_id}",
            capabilities={"filesystem_isolation": True},
        )

    async def resume(self, handle: AgentRunHandle, instruction: str) -> None:
        raise AssertionError("experiment arms must not share resumed sessions")

    async def cancel(self, handle: AgentRunHandle) -> None:
        return None

    async def stream(self, handle: AgentRunHandle):
        yield RuntimeEvent(
            kind="run.completed",
            payload={"type": "run.completed"},
            ts_ms=2,
        )

    async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
        return AgentRunResult(
            run_id=handle.run_id,
            task_id=handle.task_id,
            runtime=self.kind,
            session_id=handle.session_id,
            status="completed",
            output="done",
            events=(),
            started_at_ms=1,
            ended_at_ms=2,
            cost_usd=0.25,
            resolved_model="deterministic-v1",
            result_hash="f" * 64,
            token_usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "tokens_in": 10,
                "tokens_out": 5,
            },
            model_provenance="deterministic.runtime",
            cost_provenance="deterministic.runtime",
            token_provenance="deterministic.runtime",
        )


def _task(repo: Path, revision: str) -> TaskSpec:
    architecture, os_name = default_task_platform()
    return TaskSpec(
        task_id="task-1",
        task_family="generic",
        repo=str(repo),
        revision=revision,
        dataset_hash="a" * 64,
        split_hash="b" * 64,
        problem_statement="Update README.",
        image_digest="sha256:" + ("c" * 64),
        architecture=architecture,
        os_name=os_name,
        network_policy="disabled",
        resource_limits={"timeout_s": 30},
        verifier_id="hidden",
        verifier_hash="d" * 64,
        canonical_repo_id="fixture/repo",
    )


def _plan(
    *,
    arm: Arm,
    instruction_prefix: str,
    treatment_config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> RuntimeArmPlan:
    execution_mode = str(kwargs.pop("execution_mode", "hermetic"))
    return RuntimeArmPlan(
        treatment=TreatmentDescriptor(
            arm_adapter={
                Arm.A: "production-baseline",
                Arm.B: "supervisor-orchestration",
                Arm.C: "compute-matched-direct",
            }[arm],
            entrypoint={
                Arm.A: "baseline.execute",
                Arm.B: "supervisor.execute",
                Arm.C: "direct.execute",
            }[arm],
            instruction_template=(
                f"{instruction_prefix}\n\n{{problem_statement}}"
            ),
            treatment_config=treatment_config or {
                "test_arm": arm.value,
            },
        ),
        execution_mode=execution_mode,
        **kwargs,
    )


def _manifest(
    *,
    model: str,
    endpoint: str,
    tools: Any,
    complete: bool = True,
) -> dict[str, Any]:
    configuration = {"endpoint": endpoint, "protocol": "test/v1"}
    body = {
        "schema_version": "supervisor-agent-runtime-manifest/v1",
        "kind": "deterministic",
        "implementation": "tests.ManifestRuntime",
        "provider_route": {
            "provider": "test-provider",
            "route_kind": "test-route",
            "endpoint": endpoint,
            "model_request": model,
            "complete": complete,
        },
        "binary": {
            "requested": "test-runtime",
            "resolved_path": "/fixture/test-runtime",
            "sha256": "e" * 64 if complete else "",
            "complete": complete,
        },
        "transport": {
            "implementation": "tests.ManifestTransport",
            "configuration": configuration,
            "configuration_sha256": hashlib.sha256(
                json.dumps(
                    configuration,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "complete": complete,
        },
        "tools": tools,
        "complete": complete,
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


class ManifestRuntime(DeterministicRuntime):
    def __init__(self, *, endpoint: str = "route-a") -> None:
        super().__init__()
        self.endpoint = endpoint

    def runtime_manifest(self, task: AgentTask) -> dict[str, Any]:
        return _manifest(
            model=task.model,
            endpoint=self.endpoint,
            tools=task.metadata.get("tools"),
        )


class OperationalAttestingRuntime(ManifestRuntime):
    def __init__(
        self,
        *,
        endpoint: str = "route-a",
        corruption: str = "",
    ) -> None:
        super().__init__(endpoint=endpoint)
        self.corruption = corruption

    async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
        result = await super().collect(handle)
        if self.corruption == "missing":
            return result
        task = self.tasks[handle.task_id]
        execution_plan = task.metadata["experiment"]["runtime_plan"]
        attestation = {
            "schema_version": (
                "supervisor-execution-environment-attestation/v1"
            ),
            "attestation_id": f"attestation-{handle.run_id}",
            "mode": "operational",
            "backend": "test-backend",
            "image_digest": execution_plan["container_digest"],
            "architecture": execution_plan["architecture"],
            "os_name": execution_plan["os_name"],
            "network_policy": execution_plan["network_policy"],
            "resource_limits": dict(execution_plan["resource_limits"]),
            "enforced": True,
        }
        if self.corruption == "image":
            attestation["image_digest"] = "sha256:" + ("0" * 64)
        elif self.corruption == "architecture":
            attestation["architecture"] = "substituted-architecture"
        elif self.corruption == "network":
            attestation["network_policy"] = "enabled"
        elif self.corruption == "resource":
            attestation["resource_limits"].pop("memory_mb", None)
        return AgentRunResult(
            **{
                **result.__dict__,
                "metadata": {
                    "execution_environment_attestation": attestation,
                },
            }
        )


@pytest.mark.asyncio
async def test_repository_arm_executor_uses_fresh_checkout_and_freezes_patch(
    tmp_path,
):
    repo, revision = _repo(tmp_path)
    runtimes = {arm: DeterministicRuntime() for arm in Arm}
    plans = {
        arm: _plan(
            arm=arm,
            model="deterministic-v1",
            instruction_prefix=f"{arm.value} policy",
            denied_paths=(str(tmp_path / "hidden"),),
        )
        for arm in Arm
    }
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes=runtimes,
        plans=plans,
    )

    result = await executor.execute(
        arm=Arm.B,
        task=_task(repo, revision),
        budget=ArmBudget(
            max_tokens=1000,
            max_cost_usd=1.0,
            timeout_s=30,
            max_retries=0,
        ),
        assignment_id="assignment-1",
    )

    assert result.attempts == 1
    assert result.cost_usd == 0.25
    assert Arm.B.value in result.frozen_result.patch
    assert result.frozen_result.metadata["revision"] == revision
    runtime_task = runtimes[Arm.B].started_tasks[0]
    assert runtime_task.inherit_env is False
    assert runtime_task.metadata["max_tokens"] == 1000
    assert runtime_task.metadata["max_budget_usd"] == 1.0
    assert runtime_task.metadata["filesystem_isolation"]["required"] is True
    assert runtime_task.metadata["filesystem_isolation"]["network_policy"] == (
        "disabled"
    )
    assert runtime_task.metadata["filesystem_isolation"]["deny_paths"] == [
        str((tmp_path / "hidden").resolve())
    ]
    assert _git(repo, "status", "--short") == ""


@pytest.mark.asyncio
async def test_repository_arm_executor_auto_binds_verifier_protected_paths(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    runtimes = {arm: DeterministicRuntime() for arm in Arm}
    caller_denied = tmp_path / "caller-denied"
    verifier_hidden = tmp_path / "verifier-hidden"
    caller_denied.mkdir()
    verifier_hidden.mkdir()
    plans = {
        arm: _plan(
            arm=arm,
            model="deterministic-v1",
            instruction_prefix=f"{arm.value} policy",
            denied_paths=(str(caller_denied),),
        )
        for arm in Arm
    }
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes=runtimes,
        plans=plans,
    )
    task = _task(repo, revision)
    verifier = type(
        "ProtectedVerifier",
        (),
        {"protected_paths": (str(verifier_hidden),)},
    )()

    executor.bind_verifier(task=task, verifier=verifier)
    await executor.execute(
        arm=Arm.A,
        task=task,
        budget=ArmBudget(
            max_tokens=1000,
            max_cost_usd=1.0,
            timeout_s=30,
            max_retries=0,
        ),
        assignment_id="assignment-protected-paths",
    )

    deny_paths = runtimes[Arm.A].started_tasks[0].metadata[
        "filesystem_isolation"
    ]["deny_paths"]
    assert deny_paths == [
        str(caller_denied.resolve()),
        str(verifier_hidden.resolve()),
    ]


@pytest.mark.asyncio
async def test_executor_binds_distinct_treatments_to_compute_matched_launches(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    runtimes = {arm: DeterministicRuntime() for arm in Arm}
    plans = {
        arm: _plan(
            arm=arm,
            model="deterministic-v1",
            instruction_prefix=f"{arm.value} policy",
        )
        for arm in Arm
    }
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes=runtimes,
        plans=plans,
    )
    budget = ArmBudget(
        max_tokens=1000,
        max_cost_usd=1.0,
        timeout_s=30,
        max_retries=0,
    )

    executions = {
        arm: await executor.execute(
            arm=arm,
            task=_task(repo, revision),
            budget=budget,
            assignment_id="assignment-treatment-binding",
        )
        for arm in (Arm.B, Arm.C)
    }
    receipts = {
        arm: executions[arm].receipt
        for arm in (Arm.B, Arm.C)
    }

    assert all(receipt is not None for receipt in receipts.values())
    assert receipts[Arm.B].treatment_hash != receipts[Arm.C].treatment_hash
    assert (
        receipts[Arm.B].compute_resource_hash
        == receipts[Arm.C].compute_resource_hash
    )
    for arm in (Arm.B, Arm.C):
        receipt = receipts[arm]
        launch = executions[arm].metadata["launch_metadata"]
        runtime_task = runtimes[arm].started_tasks[0]
        assert receipt.treatment_hash == plans[arm].treatment.treatment_hash
        assert launch["treatment_hash"] == receipt.treatment_hash
        assert launch["plan_fingerprint"] == receipt.plan_fingerprint
        assert (
            runtime_task.metadata["experiment"]["treatment_hash"]
            == receipt.treatment_hash
        )


def test_repository_arm_executor_rejects_duplicate_treatment_hashes(
    tmp_path: Path,
) -> None:
    duplicate = TreatmentDescriptor(
        arm_adapter="same-adapter",
        entrypoint="same.execute",
        instruction_template="Same.\n\n{problem_statement}",
        treatment_config={"mode": "same"},
    )
    plans = {
        arm: RuntimeArmPlan(
            model="deterministic-v1",
            treatment=duplicate,
            execution_mode="hermetic",
        )
        for arm in Arm
    }

    with pytest.raises(ValueError, match="treatment hashes must all differ"):
        RepositoryArmExecutor(
            task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
            runtimes={arm: DeterministicRuntime() for arm in Arm},
            plans=plans,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["instruction", "treatment_config"])
async def test_executor_rejects_treatment_plan_mutation_after_construction(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo, revision = _repo(tmp_path)
    runtime = DeterministicRuntime()
    plans = {
        arm: _plan(
            arm=arm,
            model="deterministic-v1",
            instruction_prefix=arm.value,
        )
        for arm in Arm
    }
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans=plans,
    )
    original = plans[Arm.B].treatment
    mutated = TreatmentDescriptor(
        arm_adapter=original.arm_adapter,
        entrypoint=original.entrypoint,
        instruction_template=(
            "Changed instruction.\n\n{problem_statement}"
            if mutation == "instruction"
            else original.instruction_template
        ),
        treatment_config=(
            {"test_arm": Arm.B.value, "review_passes": 2}
            if mutation == "treatment_config"
            else dict(original.treatment_config)
        ),
    )
    executor.plans[Arm.B] = replace(
        plans[Arm.B],
        treatment=mutated,
    )

    with pytest.raises(RuntimeError, match="plan changed"):
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=100,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id="assignment-mutated-plan",
        )


@pytest.mark.asyncio
async def test_repository_arm_executor_cancels_runtime_before_teardown_on_caller_cancel(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)

    class BlockingRuntime(DeterministicRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.streaming = asyncio.Event()
            self.cancelled: list[str] = []

        async def start(self, task: AgentTask) -> AgentRunHandle:
            self.tasks[task.task_id] = task
            self.started_tasks.append(task)
            return AgentRunHandle(
                run_id=f"run-{task.task_id}",
                task_id=task.task_id,
                runtime=self.kind,
                session_id=f"session-{task.task_id}",
                capabilities={"filesystem_isolation": True},
            )

        async def cancel(self, handle: AgentRunHandle) -> None:
            self.cancelled.append(handle.run_id)

        async def stream(self, handle: AgentRunHandle):
            self.streaming.set()
            await asyncio.Future()
            yield  # pragma: no cover

    class RecordingEnvironment(GenericRepositoryTask):
        def __init__(self, *, work_root: Path) -> None:
            super().__init__(work_root=work_root)
            self.torn_down: list[Path] = []

        async def teardown(self, task) -> None:
            await super().teardown(task)
            self.torn_down.append(task.workspace)

    runtime = BlockingRuntime()
    environment = RecordingEnvironment(work_root=tmp_path / "work")
    executor = RepositoryArmExecutor(
        task_environment=environment,
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                denied_paths=(str(tmp_path / "hidden"),),
            )
            for arm in Arm
        },
    )
    execution = asyncio.create_task(
        executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=1000,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id="assignment-cancel",
        )
    )
    await asyncio.wait_for(runtime.streaming.wait(), timeout=2)

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    assert len(runtime.cancelled) == 1
    assert len(environment.torn_down) == 1
    assert not environment.torn_down[0].exists()


@pytest.mark.asyncio
async def test_repository_arm_executor_isolates_every_arm_identity_and_state_root(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    runtimes = {arm: DeterministicRuntime() for arm in Arm}
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes=runtimes,
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                denied_paths=(str(tmp_path / "hidden"),),
            )
            for arm in Arm
        },
    )
    budget = ArmBudget(
        max_tokens=1000,
        max_cost_usd=1.0,
        timeout_s=30,
        max_retries=0,
    )

    results = {
        arm: await executor.execute(
            arm=arm,
            task=_task(repo, revision),
            budget=budget,
            assignment_id="assignment-1",
        )
        for arm in Arm
    }
    tasks = [runtimes[arm].started_tasks[0] for arm in Arm]

    assert len({task.task_id for task in tasks}) == 3
    assert len(
        {result.metadata["execution_id"] for result in results.values()}
    ) == 3
    assert len(
        {result.metadata["session_id"] for result in results.values()}
    ) == 3
    isolation_keys = {
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "SUPERVISOR_MEMORY_ROOT",
        "SUPERVISOR_LESSON_ROOT",
        "TMPDIR",
    }
    for key in isolation_keys:
        values = {task.env[key] for task in tasks}
        assert len(values) == 3
    for task in tasks:
        isolation = task.metadata["experiment"]["isolation"]
        assert task.env["HOME"] == isolation["home"]
        assert task.env["XDG_CONFIG_HOME"] == isolation["config"]
        assert task.env["XDG_CACHE_HOME"] == isolation["cache"]
        assert task.env["SUPERVISOR_MEMORY_ROOT"] == isolation["memory"]
        assert task.env["SUPERVISOR_LESSON_ROOT"] == isolation["lessons"]
        assert str(task.cwd / ".git") in task.env["HOME"]


@pytest.mark.asyncio
async def test_repository_arm_executor_rejects_reused_cross_arm_session(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)

    class ReusedSessionRuntime(DeterministicRuntime):
        async def start(self, task: AgentTask) -> AgentRunHandle:
            handle = await super().start(task)
            return AgentRunHandle(
                **{
                    **handle.__dict__,
                    "session_id": "shared-session",
                }
            )

    runtime = ReusedSessionRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                denied_paths=(str(tmp_path / "hidden"),),
            )
            for arm in Arm
        },
    )
    budget = ArmBudget(
        max_tokens=1000,
        max_cost_usd=1.0,
        timeout_s=30,
        max_retries=0,
    )
    await executor.execute(
        arm=Arm.A,
        task=_task(repo, revision),
        budget=budget,
        assignment_id="assignment-1",
    )

    with pytest.raises(ArmExecutionError, match="session_id was reused"):
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=budget,
            assignment_id="assignment-1",
        )


@pytest.mark.asyncio
async def test_repository_arm_executor_retries_inside_the_same_arm(tmp_path):
    repo, revision = _repo(tmp_path)

    class FlakyRuntime(DeterministicRuntime):
        calls = 0

        async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
            self.calls += 1
            result = await super().collect(handle)
            if self.calls == 1:
                return AgentRunResult(
                    **{
                        **result.__dict__,
                        "status": "failed",
                        "result_hash": "e" * 64,
                    }
                )
            return result

    runtime = FlakyRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
            )
            for arm in Arm
        },
    )

    result = await executor.execute(
        arm=Arm.C,
        task=_task(repo, revision),
        budget=ArmBudget(
            max_tokens=1000,
            max_cost_usd=1.0,
            timeout_s=30,
            max_retries=1,
        ),
        assignment_id="assignment-1",
    )

    assert result.attempts == 2


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("model", "other-model"),
        ("container_digest", "sha256:" + ("e" * 64)),
        ("network_policy", "enabled"),
        ("resource_limits", {"memory_mb": 2048}),
        ("runtime_metadata", {"max_turns": 99}),
        ("environment", {"ROUTE_HINT": "hidden"}),
        ("tools", ("shell",)),
        ("denied_paths", ("/hidden/route",)),
    ],
)
def test_repository_arm_executor_rejects_mismatched_b_c_execution_plans(
    tmp_path: Path,
    changed_field: str,
    changed_value,
) -> None:
    runtime = DeterministicRuntime()
    common = {
        "model": "deterministic-v1",
        "instruction_prefix": "policy",
        "container_digest": "sha256:" + ("c" * 64),
        "network_policy": "disabled",
        "resource_limits": {"memory_mb": 1024},
    }
    plans = {arm: _plan(arm=arm, **common) for arm in Arm}
    plans[Arm.C] = _plan(
        arm=Arm.C,
        **{
            **common,
            changed_field: changed_value,
        }
    )

    with pytest.raises(ValueError, match="arms B and C"):
        RepositoryArmExecutor(
            task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
            runtimes={arm: runtime for arm in Arm},
            plans=plans,
        )


@pytest.mark.asyncio
async def test_repository_arm_executor_treats_empty_patch_as_itt_failure(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)

    class EmptyPatchRuntime(DeterministicRuntime):
        async def start(self, task: AgentTask) -> AgentRunHandle:
            self.tasks[task.task_id] = task
            self.started_tasks.append(task)
            return AgentRunHandle(
                run_id=f"run-{task.task_id}",
                task_id=task.task_id,
                runtime=self.kind,
                session_id=f"session-{task.task_id}",
                capabilities={"filesystem_isolation": True},
            )

    runtime = EmptyPatchRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
            )
            for arm in Arm
        },
    )

    with pytest.raises(ArmExecutionError, match="empty patch") as failure:
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=1000,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id="assignment-1",
        )

    assert failure.value.failure_classification == "empty_patch"
    assert failure.value.attempts == 1


def test_repository_arm_executor_rejects_mismatched_b_c_runtimes(
    tmp_path: Path,
) -> None:
    class OtherRuntime(DeterministicRuntime):
        kind = "other-runtime"

    plans = {
        arm: _plan(
            arm=arm,
            model="deterministic-v1",
            instruction_prefix=arm.value,
        )
        for arm in Arm
    }
    runtimes = {arm: DeterministicRuntime() for arm in Arm}
    runtimes[Arm.C] = OtherRuntime()

    with pytest.raises(ValueError, match="arms B and C"):
        RepositoryArmExecutor(
            task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
            runtimes=runtimes,
            plans=plans,
        )


@pytest.mark.asyncio
async def test_repository_arm_executor_rejects_hidden_b_c_provider_routes(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    runtimes = {
        Arm.A: ManifestRuntime(endpoint="route-a"),
        Arm.B: ManifestRuntime(endpoint="route-b"),
        Arm.C: ManifestRuntime(endpoint="route-c"),
    }
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes=runtimes,
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                tools=("shell",),
            )
            for arm in Arm
        },
    )

    with pytest.raises(ValueError, match="complete runtime manifests"):
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=100,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id="assignment-routes",
        )


@pytest.mark.asyncio
async def test_operational_executor_requires_complete_runtime_manifest(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)

    class IncompleteManifestRuntime(ManifestRuntime):
        def runtime_manifest(self, task: AgentTask) -> dict[str, Any]:
            return _manifest(
                model=task.model,
                endpoint=self.endpoint,
                tools=task.metadata.get("tools"),
                complete=False,
            )

    runtime = IncompleteManifestRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                execution_mode="operational",
                tools=("shell",),
            )
            for arm in Arm
        },
    )

    with pytest.raises(ValueError, match="must be complete"):
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=100,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id="assignment-incomplete-manifest",
        )


@pytest.mark.asyncio
async def test_operational_executor_persists_backend_environment_attestation(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    runtime = OperationalAttestingRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                execution_mode="operational",
                tools=("shell",),
            )
            for arm in Arm
        },
    )

    execution = await executor.execute(
        arm=Arm.B,
        task=_task(repo, revision),
        budget=ArmBudget(
            max_tokens=100,
            max_cost_usd=1.0,
            timeout_s=30,
            max_retries=0,
        ),
        assignment_id="assignment-operational",
    )

    assert execution.receipt is not None
    assert execution.receipt.environment.mode == "operational"
    assert execution.receipt.environment.enforced is True
    assert execution.receipt.runtime_manifest["complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_cls", "binary_name"),
    (
        (ClaudeCodeRuntime, "claude"),
        (CodexRuntime, "codex"),
    ),
)
async def test_concrete_subprocess_runtimes_fail_closed_on_unenforced_pins(
    tmp_path: Path,
    runtime_cls: type[ClaudeCodeRuntime] | type[CodexRuntime],
    binary_name: str,
) -> None:
    transport = SubprocessRuntimeTransport()
    if not transport.supports_filesystem_isolation("workspace_only"):
        pytest.skip("local workspace isolation backend is unavailable")
    repo, revision = _repo(tmp_path)
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    model = f"{binary_name}-served-model-v1"
    payload = json.dumps(
        {
            "type": "result",
            "result": "done",
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "modelUsage": {
                model: {
                    "inputTokens": 3,
                    "outputTokens": 2,
                    "costUSD": 0.01,
                }
            },
        },
    )
    target = tmp_path / "runtime-targets" / f"{binary_name}-v1"
    target.parent.mkdir()
    target.write_text(
        "#!/bin/sh\n"
        "printf 'after\\n' > README.md\n"
        f"printf '%s\\n' {shlex.quote(payload)}\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
    invoked = tmp_path / "runtime-bin" / binary_name
    invoked.parent.mkdir()
    invoked.symlink_to(target)
    runtime = runtime_cls(
        transport=transport,
        binary=str(invoked),
    )
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model=model,
                instruction_prefix=arm.value,
                execution_mode="operational",
                denied_paths=(str(hidden),),
                tools=("shell",),
            )
            for arm in Arm
        },
    )

    with pytest.raises(
        ArmExecutionError,
        match=(
            "operational backend did not enforce execution environment pins: "
            "image_digest:no_container_backend"
        ),
    ) as failure:
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=100,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id=f"assignment-concrete-{binary_name}",
        )

    record = failure.value.attempt_records[0]
    attestation = record["execution_environment_attestation"]
    assert attestation["attestation_source"] == "runtime_transport"
    assert attestation["enforced"] is False
    assert "image_digest:no_container_backend" in attestation["unmet_pins"]
    assert (
        "resource_limits.max_tokens:not_enforced_by_local_subprocess"
        in attestation["unmet_pins"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("missing", "did not attest execution environment pins"),
        ("image", "does not match frozen task/resource pins"),
        ("architecture", "does not match frozen task/resource pins"),
        ("network", "does not match frozen task/resource pins"),
        ("resource", "does not match frozen task/resource pins"),
    ],
)
async def test_operational_executor_fails_closed_on_unattested_pins(
    tmp_path: Path,
    corruption: str,
    message: str,
) -> None:
    repo, revision = _repo(tmp_path)
    task = replace(
        _task(repo, revision),
        resource_limits={
            "timeout_s": 30,
            "memory_mb": 512,
        },
    )
    runtime = OperationalAttestingRuntime(corruption=corruption)
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                execution_mode="operational",
                tools=("shell",),
            )
            for arm in Arm
        },
    )

    with pytest.raises(ArmExecutionError, match=message):
        await executor.execute(
            arm=Arm.C,
            task=task,
            budget=ArmBudget(
                max_tokens=100,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=0,
            ),
            assignment_id=f"assignment-{corruption}",
        )


@pytest.mark.asyncio
async def test_budget_exhaustion_preserves_failure_usage_without_paid_retry(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)

    class BudgetExhaustingRuntime(DeterministicRuntime):
        calls = 0

        async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("budget exhaustion must not launch a retry")
            result = await super().collect(handle)
            return AgentRunResult(
                **{
                    **result.__dict__,
                    "status": "failed",
                    "cost_usd": 1.0,
                    "token_usage": {
                        "input_tokens": 6,
                        "output_tokens": 4,
                        "tokens_in": 6,
                        "tokens_out": 4,
                    },
                    "result_hash": "e" * 64,
                }
            )

    runtime = BudgetExhaustingRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                denied_paths=(str(tmp_path / "hidden"),),
            )
            for arm in Arm
        },
    )

    with pytest.raises(ArmExecutionError) as failure:
        await executor.execute(
            arm=Arm.C,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=10,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=2,
            ),
            assignment_id="assignment-1",
        )

    assert runtime.calls == 1
    assert failure.value.attempts == 1
    assert failure.value.cost_usd == 1.0
    assert failure.value.latency_ms >= 0
    assert failure.value.token_usage["tokens_in"] == 6
    assert failure.value.token_usage["tokens_out"] == 4
    assert len(failure.value.attempt_records) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ceiling", "message"),
    [("tokens", "token ceiling"), ("cost", "cost ceiling")],
)
async def test_completed_run_over_budget_fails_without_retry(
    tmp_path: Path,
    ceiling: str,
    message: str,
) -> None:
    repo, revision = _repo(tmp_path)

    class OverBudgetRuntime(DeterministicRuntime):
        calls = 0

        async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
            self.calls += 1
            result = await super().collect(handle)
            overrides = (
                {
                    "token_usage": {
                        "input_tokens": 8,
                        "output_tokens": 3,
                        "tokens_in": 8,
                        "tokens_out": 3,
                    }
                }
                if ceiling == "tokens"
                else {"cost_usd": 1.01}
            )
            return AgentRunResult(
                **{
                    **result.__dict__,
                    **overrides,
                    "result_hash": "d" * 64,
                }
            )

    runtime = OverBudgetRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                denied_paths=(str(tmp_path / "hidden"),),
            )
            for arm in Arm
        },
    )

    with pytest.raises(ArmExecutionError, match=message):
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=10 if ceiling == "tokens" else 100,
                max_cost_usd=1.0,
                timeout_s=30,
                max_retries=2,
            ),
            assignment_id="assignment-1",
        )

    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_arm_time_ceiling_is_cumulative_and_prevents_retry(
    tmp_path: Path,
) -> None:
    import asyncio

    repo, revision = _repo(tmp_path)

    class SlowRuntime(DeterministicRuntime):
        calls = 0

        async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
            self.calls += 1
            await asyncio.sleep(1.05)
            return await super().collect(handle)

    runtime = SlowRuntime()
    executor = RepositoryArmExecutor(
        task_environment=GenericRepositoryTask(work_root=tmp_path / "work"),
        runtimes={arm: runtime for arm in Arm},
        plans={
            arm: _plan(
                arm=arm,
                model="deterministic-v1",
                instruction_prefix=arm.value,
                denied_paths=(str(tmp_path / "hidden"),),
            )
            for arm in Arm
        },
    )

    with pytest.raises(ArmExecutionError, match="time ceiling"):
        await executor.execute(
            arm=Arm.B,
            task=_task(repo, revision),
            budget=ArmBudget(
                max_tokens=100,
                max_cost_usd=1.0,
                timeout_s=1,
                max_retries=1,
            ),
            assignment_id="assignment-1",
        )

    assert runtime.calls == 1
