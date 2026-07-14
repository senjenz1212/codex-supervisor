from __future__ import annotations

import copy
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

import supervisor.swe_bench_official_oracle as official_oracle
from supervisor.swe_bench_official_oracle import (
    run_task_spec_bound_official_harness_oracle,
)
from supervisor.task_environment import (
    FrozenTaskResult,
    SweBenchVerifier,
    TaskSpec,
    bind_frozen_result_to_task,
)


def _json_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _task_spec() -> TaskSpec:
    instance_id = "sympy__sympy-14711"
    revision = "1" * 40
    dataset_hash = "2" * 64
    split_hash = "3" * 64
    verifier_hash = "4" * 64
    image_digest = "sha256:" + ("5" * 64)
    problem_statement = "Preserve the public fixture behavior."
    row = {
        "instance_id": instance_id,
        "repo": "sympy/sympy",
        "base_commit": revision,
        "problem_statement": problem_statement,
        "dataset_name": "SWE-bench/SWE-bench_Verified",
        "dataset_hash": dataset_hash,
        "split": "test",
        "split_hash": split_hash,
    }
    resource_limits = {
        "timeout_s": 600,
        "max_workers": 1,
        "subprocess_timeout_s": 900,
        "memory_mb": 8192,
    }
    verifier_execution = {
        "dataset": {
            "name": "SWE-bench/SWE-bench_Verified",
            "hash": dataset_hash,
        },
        "split": {"name": "test", "hash": split_hash},
        "instance": {
            "id": instance_id,
            "row": row,
            "row_hash": _json_hash(row),
        },
        "repository": {
            "repo": "https://github.com/sympy/sympy.git",
            "canonical_repo_id": "sympy/sympy",
            "revision": revision,
            "base_commit": revision,
        },
        "verifier": {
            "id": "official-swebench",
            "version": "4.1.0",
            "package": "swebench==4.1.0",
            "hash": verifier_hash,
        },
        "container": {
            "image": (
                "swebench/sweb.eval.x86_64.sympy_14711"
                f"@{image_digest}"
            ),
            "digest": image_digest,
        },
        "platform": {
            "architecture": "x86_64",
            "os_name": "linux",
        },
        "network_policy": "disabled",
        "resource_limits": resource_limits,
        "harness": {
            "namespace": "swebench",
            "cache_level": "instance",
            "clean": False,
            "max_workers": 1,
            "timeout_s": 600,
            "subprocess_timeout_s": 900,
            "model_name": "supervisor-task-spec",
            "run_id_prefix": "supervisor-bound-oracle",
        },
    }
    return TaskSpec(
        task_id=instance_id,
        task_family="swebench-verified",
        repo="https://github.com/sympy/sympy.git",
        revision=revision,
        dataset_hash=dataset_hash,
        split_hash=split_hash,
        problem_statement=problem_statement,
        image_digest=image_digest,
        architecture="x86_64",
        os_name="linux",
        network_policy="disabled",
        resource_limits=resource_limits,
        verifier_id="official-swebench",
        verifier_hash=verifier_hash,
        canonical_task_key=instance_id,
        canonical_repo_id="sympy/sympy",
        metadata={"instance_id": instance_id},
        verifier_execution=verifier_execution,
    )


def _bound_frozen(task: TaskSpec) -> FrozenTaskResult:
    return bind_frozen_result_to_task(
        task,
        FrozenTaskResult.create(
            task_id=task.task_id,
            task_family=task.task_family,
            task_spec_hash=task.spec_hash,
            run_result_hash="6" * 64,
            patch="diff --git a/a.py b/a.py\n",
            output="done",
            metadata={},
            frozen_at_ms=1,
        ),
    )


def _verifier(
    task: TaskSpec,
    *,
    oracle_runner,
) -> SweBenchVerifier:
    return SweBenchVerifier(
        task_spec=task,
        verifier_version="4.1.0",
        verifier_hash=task.verifier_hash,
        oracle_runner=oracle_runner,
    )


def _successful_oracle_result(context: dict) -> dict:
    return {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "verifier_execution_spec_hash": (
            context["verifier_execution_spec_hash"]
        ),
        "oracle_adapter_receipt": {
            "verifier_execution_spec": context["verifier_execution_spec"],
            "verifier_execution_spec_hash": (
                context["verifier_execution_spec_hash"]
            ),
        },
    }


@pytest.mark.asyncio
async def test_swebench_verifier_binds_complete_task_spec_into_context_and_receipt():
    task = _task_spec()
    observed: dict = {}

    def oracle_runner(context):
        observed.update(copy.deepcopy(dict(context)))
        return _successful_oracle_result(dict(context))

    verifier = _verifier(task, oracle_runner=oracle_runner)
    frozen = _bound_frozen(task)

    grade = await verifier.verify(frozen)

    assert grade.passed is True
    assert observed["task_spec"] == task.to_dict()
    assert observed["task_spec_hash"] == task.spec_hash
    assert observed["dataset_name"] == "SWE-bench/SWE-bench_Verified"
    assert observed["dataset_hash"] == task.dataset_hash
    assert observed["split"] == "test"
    assert observed["split_hash"] == task.split_hash
    assert observed["instance_id"] == task.task_id
    assert observed["instance_row_hash"] == _json_hash(
        observed["instance_row"]
    )
    assert observed["repo"] == task.repo
    assert observed["revision"] == task.revision
    assert observed["base_commit"] == task.revision
    assert observed["verifier_package"] == "swebench==4.1.0"
    assert observed["verifier_hash"] == task.verifier_hash
    assert observed["container_image"].endswith(task.image_digest)
    assert observed["image_digest"] == task.image_digest
    assert observed["architecture"] == task.architecture
    assert observed["os_name"] == task.os_name
    assert observed["network_policy"] == task.network_policy
    assert observed["resource_limits"] == dict(task.resource_limits)
    receipt = grade.evidence["oracle_adapter_receipt"]
    assert receipt["verifier_execution_spec"] == (
        observed["verifier_execution_spec"]
    )


def test_swebench_verifier_execution_spec_is_detached_and_deeply_immutable():
    task = _task_spec()
    verifier = _verifier(task, oracle_runner=lambda _context: {})
    original_dataset = verifier.execution_spec.dataset_name

    task.verifier_execution["dataset"]["name"] = "attacker/substitute"
    serialized = verifier.execution_spec.to_dict()
    serialized["dataset"]["name"] = "caller/substitute"

    assert verifier.execution_spec.dataset_name == original_dataset
    with pytest.raises(TypeError):
        verifier.execution_spec.task_spec["repo"] = "substitute"
    with pytest.raises(TypeError):
        verifier.execution_spec.instance_row["repo"] = "other/project"
    with pytest.raises(TypeError):
        verifier.execution_spec.resource_limits["timeout_s"] = 1


def test_swebench_verifier_uses_task_spec_canonical_json_for_unicode():
    values = _task_spec().to_dict()
    statement = "Preserve café behavior."
    values["problem_statement"] = statement
    instance = values["verifier_execution"]["instance"]
    instance["row"]["problem_statement"] = statement
    instance["row_hash"] = _json_hash(instance["row"])
    task = TaskSpec.from_dict(values)

    verifier = _verifier(task, oracle_runner=lambda _context: {})

    assert verifier.execution_spec.task_spec_hash == task.spec_hash


@pytest.mark.parametrize(
    ("path", "replacement", "expected"),
    [
        (("dataset", "hash"), "a" * 64, "dataset hash"),
        (("split", "hash"), "a" * 64, "split hash"),
        (("repository", "repo"), "https://example.invalid/other.git", "repo"),
        (("repository", "revision"), "a" * 40, "revision"),
        (("verifier", "hash"), "a" * 64, "verifier package hash"),
        (("container", "digest"), "sha256:" + ("a" * 64), "container digest"),
        (("platform", "architecture"), "arm64", "architecture"),
        (("network_policy",), "enabled", "network policy"),
        (
            ("resource_limits", "memory_mb"),
            4096,
            "resource limits",
        ),
        (("harness", "timeout_s"), 601, "timeout_s"),
    ],
)
def test_swebench_verifier_rejects_execution_pin_mismatch_at_binding(
    path: tuple[str, ...],
    replacement: object,
    expected: str,
) -> None:
    values = _task_spec().to_dict()
    target = values["verifier_execution"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    task = TaskSpec.from_dict(values)

    with pytest.raises(ValueError, match=expected):
        _verifier(task, oracle_runner=lambda _context: {})


@pytest.mark.parametrize(
    ("row_key", "replacement", "expected"),
    [
        ("instance_id", "sympy__sympy-other", "row id"),
        ("repo", "other/project", "row repository"),
        ("base_commit", "a" * 40, "row base_commit"),
    ],
)
def test_swebench_verifier_rejects_instance_row_mismatch_at_binding(
    row_key: str,
    replacement: str,
    expected: str,
) -> None:
    values = _task_spec().to_dict()
    instance = values["verifier_execution"]["instance"]
    instance["row"][row_key] = replacement
    instance["row_hash"] = _json_hash(instance["row"])
    task = TaskSpec.from_dict(values)

    with pytest.raises(ValueError, match=expected):
        _verifier(task, oracle_runner=lambda _context: {})


def test_swebench_verifier_rejects_adapter_hash_mismatch() -> None:
    task = _task_spec()

    with pytest.raises(ValueError, match="package hash"):
        SweBenchVerifier(
            task_spec=task,
            verifier_version="4.1.0",
            verifier_hash="a" * 64,
            oracle_runner=lambda _context: {},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_key", "replacement"),
    [
        ("repo", "https://example.invalid/substitute.git"),
        ("revision", "a" * 40),
        ("instance_id", "sympy__sympy-other"),
        ("canonical_repo_id", "other/project"),
    ],
)
async def test_swebench_verifier_rejects_frozen_identity_mismatch_before_oracle(
    metadata_key: str,
    replacement: str,
) -> None:
    task = _task_spec()
    frozen = _bound_frozen(task)
    metadata = dict(frozen.metadata)
    metadata[metadata_key] = replacement
    substituted = FrozenTaskResult.create(
        task_id=frozen.task_id,
        task_family=frozen.task_family,
        task_spec_hash=frozen.task_spec_hash,
        run_result_hash=frozen.run_result_hash,
        patch=frozen.patch,
        output=frozen.output,
        metadata=metadata,
        frozen_at_ms=frozen.frozen_at_ms,
    )

    def must_not_run(_context):
        raise AssertionError("oracle must not run for mismatched authority")

    with pytest.raises(ValueError, match=metadata_key):
        await _verifier(task, oracle_runner=must_not_run).verify(substituted)


@pytest.mark.asyncio
async def test_swebench_verifier_rejects_oracle_receipt_for_other_bound_spec():
    task = _task_spec()

    def oracle_runner(context):
        result = _successful_oracle_result(dict(context))
        result["verifier_execution_spec_hash"] = "a" * 64
        return result

    with pytest.raises(ValueError, match="not bound"):
        await _verifier(task, oracle_runner=oracle_runner).verify(
            _bound_frozen(task)
        )


def _oracle_context(task: TaskSpec) -> dict:
    verifier = _verifier(task, oracle_runner=lambda _context: {})
    frozen = _bound_frozen(task)
    return {
        **verifier.execution_spec.context_binding(),
        "candidate_id": frozen.result_hash,
        "frozen_result_hash": frozen.result_hash,
        "model_patch": frozen.patch,
        "model_patch_sha256": frozen.patch_hash,
    }


@pytest.mark.parametrize(
    ("context_key", "replacement"),
    [
        ("dataset_name", "substitute/dataset"),
        ("dataset", "substitute/dataset"),
        ("dataset_hash", "a" * 64),
        ("split", "validation"),
        ("split_hash", "a" * 64),
        ("instance_id", "sympy__sympy-other"),
        ("instance_row_hash", "a" * 64),
        ("repo", "https://example.invalid/other.git"),
        ("revision", "a" * 40),
        ("base_commit", "a" * 40),
        ("verifier_package", "swebench==0.0.0"),
        ("verifier_hash", "a" * 64),
        ("container_image", "example.invalid/other:latest"),
        ("image_digest", "sha256:" + ("a" * 64)),
        ("architecture", "arm64"),
        ("os_name", "macos"),
        ("network_policy", "enabled"),
        (
            "resource_limits",
            {
                "timeout_s": 1,
                "max_workers": 1,
                "subprocess_timeout_s": 2,
                "memory_mb": 1,
            },
        ),
    ],
)
def test_bound_oracle_rejects_context_mismatch_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context_key: str,
    replacement: object,
) -> None:
    context = _oracle_context(_task_spec())
    context["artifact_root"] = str(tmp_path / "oracle")
    context[context_key] = replacement

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("subprocess must not run for mismatched authority")

    monkeypatch.setattr(official_oracle.subprocess, "run", must_not_run)

    with pytest.raises(ValueError, match=context_key):
        run_task_spec_bound_official_harness_oracle(context)
    assert not (tmp_path / "oracle").exists()


def test_bound_oracle_uses_pinned_spec_and_exposes_it_in_fixture_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_spec()
    context = _oracle_context(task)
    context["artifact_root"] = str(tmp_path / "oracle")
    monkeypatch.setenv(
        "SWEBENCH_OFFICIAL_ORACLE_DATASET",
        "attacker/substitute",
    )
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_SPLIT", "validation")
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        calls.append(list(command))
        assert "SWEBENCH_OFFICIAL_ORACLE_DATASET" not in env
        assert "SWEBENCH_OFFICIAL_ORACLE_SPLIT" not in env
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        model_name = task.verifier_execution["harness"]["model_name"]
        report_dir = (
            Path(cwd)
            / "logs"
            / "run_evaluation"
            / run_id
            / model_name
            / instance_id
        )
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(
            json.dumps({
                instance_id: {
                    "resolved": True,
                    "tests_status": {
                        "FAIL_TO_PASS": {
                            "success": ["fixture::fixed"],
                            "failure": [],
                        },
                        "PASS_TO_PASS": {
                            "success": ["fixture::existing"],
                            "failure": [],
                        },
                    },
                }
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "fixture stdout", "")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_task_spec_bound_official_harness_oracle(context)

    assert calls
    command = calls[0]
    assert command[:3] == [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
    ]
    assert command[command.index("--dataset_name") + 1] == (
        "SWE-bench/SWE-bench_Verified"
    )
    assert command[command.index("--split") + 1] == "test"
    assert result["fail_to_pass_status"] == "pass"
    assert result["pass_to_pass_status"] == "pass"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["task_spec_hash"] == task.spec_hash
    assert receipt["verifier_execution_spec"] == (
        context["verifier_execution_spec"]
    )
    assert receipt["container"]["digest"] == task.image_digest
    assert receipt["network_policy"] == task.network_policy
