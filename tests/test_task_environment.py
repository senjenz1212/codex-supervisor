from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest

from supervisor.task_environment import (
    bind_frozen_result_to_task,
    canonical_task_identity,
    FrozenTaskResult,
    GenericRepositoryTask,
    SweBenchVerifier,
    TaskSpec,
    UnityRepositoryTask,
    UnityTestFrameworkVerifier,
    default_task_platform,
    Grade,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _git_index(repo: Path) -> bytes:
    index_path = Path(_git(repo, "rev-parse", "--git-path", "index"))
    if not index_path.is_absolute():
        index_path = repo / index_path
    return index_path.read_bytes()


def _apply_patch(
    tmp_path: Path,
    *,
    repo: Path,
    revision: str,
    patch: str,
) -> Path:
    checkout = tmp_path / "patch-checkout"
    subprocess.run(
        ["git", "clone", "--no-hardlinks", "--quiet", str(repo), str(checkout)],
        text=True,
        capture_output=True,
        check=True,
    )
    _git(checkout, "checkout", "--quiet", "--detach", revision)
    subprocess.run(
        ["git", "apply", "--binary", "-"],
        cwd=checkout,
        input=patch,
        text=True,
        capture_output=True,
        check=True,
    )
    return checkout


def _repo(tmp_path: Path, *, unity: bool = False) -> tuple[Path, str]:
    repo = tmp_path / ("unity-repo" if unity else "generic-repo")
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "user.name", "Harness")
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    if unity:
        project_settings = repo / "ProjectSettings"
        project_settings.mkdir()
        (project_settings / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.0.0f1\n",
            encoding="utf-8",
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _spec(repo: Path, revision: str, *, family: str) -> TaskSpec:
    architecture, os_name = default_task_platform()
    return TaskSpec(
        task_id=f"{family}-task",
        task_family=family,
        repo=str(repo),
        revision=revision,
        dataset_hash="d" * 64,
        split_hash="e" * 64,
        problem_statement="Change the public behavior.",
        image_digest="sha256:" + ("f" * 64),
        architecture=architecture,
        os_name=os_name,
        network_policy="disabled",
        resource_limits={"timeout_s": 60},
        verifier_id=f"{family}-verifier",
        verifier_hash="a" * 64,
        canonical_repo_id=f"fixture/{family}-repo",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "adapter_cls"),
    (("generic", GenericRepositoryTask), ("unity", UnityRepositoryTask)),
)
async def test_generic_and_unity_tasks_materialize_and_collect_identical_result_shapes(
    tmp_path: Path,
    family: str,
    adapter_cls: type[GenericRepositoryTask] | type[UnityRepositoryTask],
) -> None:
    repo, revision = _repo(tmp_path, unity=family == "unity")
    adapter = adapter_cls(work_root=tmp_path / "work")
    materialized = await adapter.materialize(_spec(repo, revision, family=family))
    (materialized.workspace / "README.md").write_text("after\n", encoding="utf-8")

    frozen = await adapter.collect_patch(
        materialized,
        run_result_hash="run-sha",
        output="completed",
    )

    assert isinstance(frozen, FrozenTaskResult)
    assert frozen.schema_version == "supervisor-frozen-task-result/v1"
    assert "after" in frozen.patch
    assert frozen.result_hash
    assert set(frozen.to_dict()) == {
        "schema_version",
        "task_id",
        "task_family",
        "task_spec_hash",
        "run_result_hash",
        "patch",
        "patch_hash",
        "output",
        "frozen_at_ms",
        "result_hash",
        "metadata",
    }
    await adapter.teardown(materialized)
    assert not materialized.workspace.exists()


@pytest.mark.asyncio
async def test_collect_patch_includes_applyable_untracked_text_file(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    adapter = GenericRepositoryTask(work_root=tmp_path / "work")
    materialized = await adapter.materialize(_spec(repo, revision, family="generic"))
    new_file = materialized.workspace / "notes" / "new.txt"
    new_file.parent.mkdir()
    new_file.write_text("new text content\n", encoding="utf-8")
    (materialized.workspace / "README.md").write_text(
        "tracked change\n",
        encoding="utf-8",
    )
    status_before = _git(
        materialized.workspace,
        "status",
        "--short",
        "--untracked-files=all",
    )
    index_before = _git_index(materialized.workspace)

    frozen = await adapter.collect_patch(
        materialized,
        run_result_hash="run-sha",
        output="completed",
    )

    assert _git_index(materialized.workspace) == index_before
    assert (
        _git(
            materialized.workspace,
            "status",
            "--short",
            "--untracked-files=all",
        )
        == status_before
    )
    assert new_file.read_text(encoding="utf-8") == "new text content\n"
    checkout = _apply_patch(
        tmp_path,
        repo=repo,
        revision=revision,
        patch=frozen.patch,
    )
    assert (checkout / "notes" / "new.txt").read_text(encoding="utf-8") == (
        "new text content\n"
    )
    assert (checkout / "README.md").read_text(encoding="utf-8") == "tracked change\n"


@pytest.mark.asyncio
async def test_collect_patch_includes_applyable_untracked_binary_file(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    obsolete = repo / "obsolete.txt"
    obsolete.write_text("remove me\n", encoding="utf-8")
    _git(repo, "add", "obsolete.txt")
    _git(repo, "commit", "--amend", "--no-edit")
    revision = _git(repo, "rev-parse", "HEAD")
    adapter = GenericRepositoryTask(work_root=tmp_path / "work")
    materialized = await adapter.materialize(_spec(repo, revision, family="generic"))
    binary_contents = bytes(range(256)) * 2
    new_file = materialized.workspace / "assets" / "new.bin"
    new_file.parent.mkdir()
    new_file.write_bytes(binary_contents)
    (materialized.workspace / "obsolete.txt").unlink()
    status_before = _git(
        materialized.workspace,
        "status",
        "--short",
        "--untracked-files=all",
    )
    index_before = _git_index(materialized.workspace)

    frozen = await adapter.collect_patch(
        materialized,
        run_result_hash="run-sha",
        output="completed",
    )

    assert _git_index(materialized.workspace) == index_before
    assert (
        _git(
            materialized.workspace,
            "status",
            "--short",
            "--untracked-files=all",
        )
        == status_before
    )
    assert new_file.read_bytes() == binary_contents
    checkout = _apply_patch(
        tmp_path,
        repo=repo,
        revision=revision,
        patch=frozen.patch,
    )
    assert (checkout / "assets" / "new.bin").read_bytes() == binary_contents
    assert not (checkout / "obsolete.txt").exists()
    assert "GIT binary patch" in frozen.patch


@pytest.mark.asyncio
async def test_hidden_verifier_material_never_enters_the_agent_workspace(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    hidden_root = tmp_path / "hidden"
    hidden_root.mkdir()
    (hidden_root / "secret_test.py").write_text("EXPECTED = 42\n", encoding="utf-8")
    adapter = GenericRepositoryTask(work_root=tmp_path / "work")
    materialized = await adapter.materialize(_spec(repo, revision, family="generic"))

    verifier = UnityTestFrameworkVerifier(
        verifier_id="hidden-verifier",
        verifier_version="1",
        hidden_root=hidden_root,
        runner=lambda _frozen, root: {
            "passed": (root / "secret_test.py").exists(),
            "score": 1.0,
            "evidence": {"hidden_root": str(root)},
        },
    )
    frozen = await adapter.collect_patch(
        materialized,
        run_result_hash="run-sha",
        output="completed",
    )
    grade = await verifier.verify(frozen)

    assert grade.passed is True
    assert hidden_root not in materialized.workspace.parents
    assert not (materialized.workspace / "secret_test.py").exists()
    assert "hidden_root" not in frozen.to_dict()


@pytest.mark.asyncio
async def test_unity_verifier_applies_frozen_patch_and_runs_hidden_tests_in_isolation(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path, unity=True)
    adapter = UnityRepositoryTask(work_root=tmp_path / "work")
    materialized = await adapter.materialize(
        _spec(repo, revision, family="unity")
    )
    (materialized.workspace / "README.md").write_text(
        "after\n",
        encoding="utf-8",
    )
    frozen = await adapter.collect_patch(
        materialized,
        run_result_hash="run-sha",
        output="completed",
    )
    await adapter.teardown(materialized)

    hidden_root = tmp_path / "hidden"
    hidden_test = hidden_root / "Assets" / "Tests" / "Editor" / "HiddenSmoke.cs"
    hidden_test.parent.mkdir(parents=True)
    hidden_test.write_text("// hidden verifier fixture\n", encoding="utf-8")
    unity = tmp_path / "Editor" / "6000.3.10f1" / "Unity"
    unity.parent.mkdir(parents=True)
    unity.write_text("#!/bin/sh\n", encoding="utf-8")
    unity.chmod(0o755)
    observed: dict[str, object] = {}

    def fake_unity(command, **kwargs):
        project = Path(command[command.index("-projectPath") + 1])
        results = Path(command[command.index("-testResults") + 1])
        log = Path(command[command.index("-logFile") + 1])
        observed["project"] = project
        observed["hidden_present"] = (
            project / "Assets" / "Tests" / "Editor" / "HiddenSmoke.cs"
        ).is_file()
        observed["patched_readme"] = (
            project / "README.md"
        ).read_text(encoding="utf-8")
        observed["kwargs"] = kwargs
        results.write_text(
            '<test-run result="Passed" total="1" passed="1" '
            'failed="0" skipped="0" inconclusive="0">'
            '<test-suite><test-case result="Passed" /></test-suite>'
            "</test-run>",
            encoding="utf-8",
        )
        log.write_text("Unity test run completed\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    grade = await UnityTestFrameworkVerifier(
        verifier_id="unity-hidden",
        verifier_version="6000.3.10f1",
        hidden_root=hidden_root,
        unity_executable=unity,
        process_runner=fake_unity,
    ).verify(frozen)

    assert grade.passed is True
    assert grade.score == 1.0
    assert grade.failure_classification == ""
    assert observed["hidden_present"] is True
    assert observed["patched_readme"] == "after\n"
    assert not Path(observed["project"]).exists()
    assert grade.evidence["runner"] == "unity_test_framework_cli"
    assert grade.evidence["unity_version"] == "6000.3.10f1"
    assert grade.evidence["total"] == 1
    assert grade.evidence["failed"] == 0
    assert grade.evidence["revision"] == revision


def test_unity_verifier_rejects_symlinks_in_hidden_test_tree(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.cs"
    outside.write_text("secret\n", encoding="utf-8")
    hidden_root = tmp_path / "hidden"
    hidden_root.mkdir()
    (hidden_root / "escaped.cs").symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic links"):
        UnityTestFrameworkVerifier(
            verifier_id="unity-hidden",
            verifier_version="1",
            hidden_root=hidden_root,
            unity_executable=tmp_path / "Unity",
        )


def test_unity_verifier_declares_hidden_root_as_protected(
    tmp_path: Path,
) -> None:
    hidden_root = tmp_path / "hidden"
    hidden_root.mkdir()

    verifier = UnityTestFrameworkVerifier(
        verifier_id="unity-hidden",
        verifier_version="1",
        hidden_root=hidden_root,
        runner=lambda _frozen, _hidden: {"passed": True},
    )

    assert verifier.protected_paths == (str(hidden_root.resolve()),)


@pytest.mark.asyncio
async def test_swebench_verifier_delegates_to_official_oracle_without_rewrite(
    tmp_path: Path,
) -> None:
    observed: list[dict[str, object]] = []

    def official_oracle(context):
        observed.append(dict(context))
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": {"official": True},
        }

    frozen = FrozenTaskResult.create(
        task_id="swe-task",
        task_family="swebench",
        task_spec_hash="spec-sha",
        run_result_hash="run-sha",
        patch="diff --git a/a b/a\n",
        output="done",
        metadata={"instance_id": "repo__issue-1"},
    )
    grade = await SweBenchVerifier(
        verifier_version="swebench-4.1",
        verifier_hash="official-harness-sha",
        oracle_runner=official_oracle,
    ).verify(frozen)

    assert grade.passed is True
    assert grade.score == 1.0
    assert observed[0]["model_patch"] == frozen.patch
    assert grade.evidence["oracle_adapter_receipt"] == {"official": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_passed"),
    [
        ("pass", True),
        ("passed", True),
        ("fail", False),
        ("unavailable", False),
    ],
)
async def test_swebench_verifier_preserves_official_status_semantics(
    status: str,
    expected_passed: bool,
) -> None:
    frozen = FrozenTaskResult.create(
        task_id="swe-task",
        task_family="swebench",
        task_spec_hash="spec-sha",
        run_result_hash="run-sha",
        patch="diff --git a/a b/a\n",
        output="done",
        metadata={"instance_id": "repo__issue-1"},
    )

    grade = await SweBenchVerifier(
        verifier_version="swebench-4.1",
        verifier_hash="official-harness-sha",
        oracle_runner=lambda _context: {
            "fail_to_pass_status": status,
            "pass_to_pass_status": status,
        },
    ).verify(frozen)

    assert grade.passed is expected_passed
    assert grade.score == (1.0 if expected_passed else 0.0)


@pytest.mark.asyncio
async def test_materialize_rejects_symbolic_revision_and_unpinned_digests(tmp_path):
    repo, revision = _repo(tmp_path)
    adapter = GenericRepositoryTask(work_root=tmp_path / "work")
    spec = _spec(repo, revision, family="generic")

    with pytest.raises(ValueError, match="full immutable Git commit"):
        await adapter.materialize(
            TaskSpec(**{**spec.to_dict(), "revision": "main"})
        )
    with pytest.raises(ValueError, match="dataset_hash must be a sha256"):
        await adapter.materialize(
            TaskSpec(**{**spec.to_dict(), "dataset_hash": "not-pinned"})
        )
    with pytest.raises(ValueError, match="platform does not match"):
        await adapter.materialize(
            TaskSpec(**{**spec.to_dict(), "architecture": "wrong-architecture"})
        )


@pytest.mark.asyncio
async def test_materialized_task_records_the_exact_pinned_commit(tmp_path):
    repo, revision = _repo(tmp_path)
    adapter = GenericRepositoryTask(work_root=tmp_path / "work")

    materialized = await adapter.materialize(
        _spec(repo, revision, family="generic")
    )

    assert materialized.base_revision == revision
    assert _git(materialized.workspace, "rev-parse", "HEAD") == revision


@pytest.mark.parametrize("score", [math.nan, math.inf, -0.01, 1.01])
def test_grade_rejects_non_finite_or_out_of_range_scores(score: float) -> None:
    with pytest.raises(ValueError, match="score"):
        Grade(
            verifier_id="hidden",
            verifier_version="1",
            verifier_hash="a" * 64,
            frozen_result_hash="b" * 64,
            passed=False,
            score=score,
            evidence={},
        )


def test_grade_rejects_empty_verifier_version() -> None:
    with pytest.raises(ValueError, match="verifier_version"):
        Grade(
            verifier_id="hidden",
            verifier_version="",
            verifier_hash="a" * 64,
            frozen_result_hash="b" * 64,
            passed=False,
            score=0.0,
            evidence={},
        )


def test_canonical_task_identity_collapses_repo_and_task_aliases() -> None:
    base = {
        "revision": "1" * 40,
        "dataset_hash": "2" * 64,
        "split_hash": "3" * 64,
        "canonical_task_key": "  Dataset/Issue-42  ",
    }

    https_identity = canonical_task_identity(
        {
            **base,
            "canonical_repo_id": (
                "https://GitHub.com/Unity-Technologies/example.git"
            ),
        }
    )
    ssh_identity = canonical_task_identity(
        {
            **base,
            "canonical_repo_id": (
                "git@github.com:unity-technologies/example"
            ),
            "canonical_task_key": "dataset/issue-42",
        }
    )

    assert https_identity == ssh_identity
    assert len(https_identity) == 64


def test_canonical_task_identity_is_independent_of_checkout_path(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    first_root.mkdir()
    first_repo, revision = _repo(first_root)
    second_root = tmp_path / "second"
    second_root.mkdir()
    second_repo = second_root / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", str(first_repo), str(second_repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    first = _spec(first_repo, revision, family="generic")
    second = TaskSpec(
        **{
            **first.to_dict(),
            "repo": str(second_repo),
        }
    )

    assert first.repo != second.repo
    assert first.spec_hash != second.spec_hash
    assert first.canonical_task_id == second.canonical_task_id


def test_operational_task_requires_non_path_canonical_repo_id(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    values = _spec(repo, revision, family="generic").to_dict()

    with pytest.raises(ValueError, match="canonical_repo_id is required"):
        TaskSpec(**{**values, "canonical_repo_id": ""})
    with pytest.raises(ValueError, match="stable non-path"):
        TaskSpec(**{**values, "canonical_repo_id": str(repo)})


def test_bind_frozen_result_derives_verifier_identity_from_task_spec(
    tmp_path: Path,
) -> None:
    repo, revision = _repo(tmp_path)
    task = _spec(repo, revision, family="generic")
    frozen = FrozenTaskResult.create(
        task_id=task.task_id,
        task_family=task.task_family,
        task_spec_hash=task.spec_hash,
        run_result_hash="run-sha",
        patch="diff --git a/a b/a\n",
        output="done",
        metadata={"public": "evidence"},
        frozen_at_ms=1,
    )

    bound = bind_frozen_result_to_task(task, frozen)

    assert bound.metadata["repo"] == task.repo
    assert bound.metadata["canonical_repo_id"] == task.canonical_repo_id
    assert bound.metadata["revision"] == task.revision
    assert bound.metadata["instance_id"] == task.canonical_task_key
    assert bound.metadata["canonical_task_id"] == task.canonical_task_id
    assert bound.metadata["public"] == "evidence"


@pytest.mark.parametrize(
    ("metadata", "field"),
    [
        ({"repository": "https://example.invalid/substitute.git"}, "repository"),
        ({"commit_sha": "0" * 40}, "commit_sha"),
        ({"task_instance_id": "substitute-task"}, "task_instance_id"),
        ({"repo_id": "other/repo"}, "repo_id"),
    ],
)
def test_bind_frozen_result_rejects_substituted_identity_echoes(
    tmp_path: Path,
    metadata: dict[str, str],
    field: str,
) -> None:
    repo, revision = _repo(tmp_path)
    task = _spec(repo, revision, family="generic")
    frozen = FrozenTaskResult.create(
        task_id=task.task_id,
        task_family=task.task_family,
        task_spec_hash=task.spec_hash,
        run_result_hash="run-sha",
        patch="diff --git a/a b/a\n",
        output="done",
        metadata={"nested": metadata},
        frozen_at_ms=1,
    )

    with pytest.raises(ValueError, match=field):
        bind_frozen_result_to_task(task, frozen)
