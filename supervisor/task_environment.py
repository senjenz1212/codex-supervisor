"""Task-environment and hidden-verifier seams for repository coding tasks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import posixpath
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

from .swe_bench_official_oracle import run_official_harness_oracle


FROZEN_RESULT_SCHEMA_VERSION = "supervisor-frozen-task-result/v1"
GRADE_SCHEMA_VERSION = "supervisor-verification-grade/v1"

_VERIFIER_IDENTITY_KEY_ALIASES = {
    "repo": "repo",
    "repository": "repo",
    "repo_url": "repo",
    "repository_url": "repo",
    "revision": "revision",
    "commit": "revision",
    "commit_sha": "revision",
    "base_commit": "revision",
    "instance_id": "instance_id",
    "task_instance_id": "instance_id",
    "canonical_repo_id": "canonical_repo_id",
    "repo_id": "canonical_repo_id",
}


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_family: str
    repo: str
    revision: str
    dataset_hash: str
    split_hash: str
    problem_statement: str
    image_digest: str
    architecture: str
    os_name: str
    network_policy: str
    resource_limits: Mapping[str, Any]
    verifier_id: str
    verifier_hash: str
    canonical_task_key: str = ""
    task_class: str = ""
    canonical_repo_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        normalized_task_class = str(
            self.task_class or self.task_family
        ).strip()
        if not normalized_task_class:
            raise ValueError("task_class or task_family must be non-empty")
        object.__setattr__(self, "task_class", normalized_task_class)
        canonical_task_key = _normalize_canonical_task_key(
            str(self.canonical_task_key or self.task_id)
        )
        object.__setattr__(
            self,
            "canonical_task_key",
            canonical_task_key,
        )
        canonical_repo_id = str(self.canonical_repo_id).strip()
        if not canonical_repo_id:
            if not _is_explicit_non_operational_task(metadata):
                raise ValueError(
                    "canonical_repo_id is required for path-independent task identity"
                )
            canonical_repo_id = (
                f"non-operational:{self.task_family}/{canonical_task_key}"
            )
        object.__setattr__(
            self,
            "canonical_repo_id",
            normalize_canonical_repo_id(canonical_repo_id),
        )
        object.__setattr__(
            self,
            "resource_limits",
            MappingProxyType(dict(self.resource_limits)),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(metadata),
        )

    @property
    def spec_hash(self) -> str:
        return _sha256_json(self.to_dict())

    @property
    def canonical_task_id(self) -> str:
        return canonical_task_identity(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_family": self.task_family,
            "repo": self.repo,
            "revision": self.revision,
            "dataset_hash": self.dataset_hash,
            "split_hash": self.split_hash,
            "problem_statement": self.problem_statement,
            "image_digest": self.image_digest,
            "architecture": self.architecture,
            "os_name": self.os_name,
            "network_policy": self.network_policy,
            "resource_limits": dict(self.resource_limits),
            "verifier_id": self.verifier_id,
            "verifier_hash": self.verifier_hash,
            "canonical_task_key": self.canonical_task_key,
            "task_class": self.task_class,
            "canonical_repo_id": self.canonical_repo_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskSpec":
        return cls(**dict(value))


def canonical_task_identity(
    task: TaskSpec | Mapping[str, Any],
) -> str:
    """Return the alias-resistant identity of one underlying benchmark task."""
    if isinstance(task, TaskSpec):
        values: Mapping[str, Any] = {
            "canonical_repo_id": task.canonical_repo_id,
            "revision": task.revision,
            "dataset_hash": task.dataset_hash,
            "split_hash": task.split_hash,
            "canonical_task_key": task.canonical_task_key,
        }
    elif isinstance(task, Mapping):
        values = task
    else:
        raise ValueError("canonical task identity requires a task mapping")

    canonical_task_key = _normalize_canonical_task_key(
        _require_identity_text(values, "canonical_task_key")
    )
    revision = _require_git_object_id(values, "revision")
    dataset_hash = _require_sha256_identity(values, "dataset_hash")
    split_hash = _require_sha256_identity(values, "split_hash")
    payload = {
        "schema_version": "supervisor-canonical-task-identity/v1",
        "canonical_repo_id": normalize_canonical_repo_id(
            _require_identity_text(values, "canonical_repo_id")
        ),
        "revision": revision,
        "dataset_hash": dataset_hash,
        "split_hash": split_hash,
        "canonical_task_key": canonical_task_key,
    }
    return _sha256_json(payload)


def normalize_canonical_repo_id(value: str) -> str:
    """Normalize an explicit repository ID without consulting checkout paths."""
    raw = unicodedata.normalize("NFKC", str(value)).strip()
    if not raw:
        raise ValueError("canonical_repo_id must be non-empty")
    parsed = urlsplit(raw)
    if parsed.scheme == "file":
        raise ValueError("canonical_repo_id must not be a local file path")
    if parsed.scheme and parsed.hostname:
        return normalize_repo_identity(raw)
    scp_match = re.fullmatch(
        r"(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>.+)",
        raw,
    )
    if scp_match and "." in scp_match.group("host"):
        return normalize_repo_identity(raw)
    slash_normalized = raw.replace("\\", "/")
    is_absolute_path = (
        Path(slash_normalized).is_absolute()
        or re.fullmatch(r"[A-Za-z]:/.*", slash_normalized) is not None
    )
    normalized = slash_normalized.strip("/")
    if (
        not normalized
        or normalized.startswith(".")
        or is_absolute_path
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError(
            "canonical_repo_id must be a stable non-path repository identifier"
        )
    if normalized.casefold().endswith(".git"):
        normalized = normalized[:-4]
    return normalized.casefold()


def normalize_repo_identity(repo: str) -> str:
    """Normalize common local, HTTPS, SSH, and SCP-style repository aliases."""
    raw = str(repo).strip()
    if not raw:
        raise ValueError("repo must be non-empty for canonical task identity")

    parsed = urlsplit(raw)
    if parsed.scheme and parsed.scheme != "file":
        if not parsed.hostname:
            raise ValueError("remote repo identity must include a hostname")
        port = f":{parsed.port}" if parsed.port is not None else ""
        return _normalize_remote_repo(
            f"{parsed.hostname}{port}",
            unquote(parsed.path),
        )
    scp_match = re.fullmatch(
        r"(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>.+)",
        raw,
    )
    if scp_match and not re.fullmatch(r"[A-Za-z]:[\\/].*", raw):
        return _normalize_remote_repo(
            scp_match.group("host"),
            scp_match.group("path"),
        )
    if parsed.scheme == "file":
        local_value = unquote(parsed.path)
    else:
        local_value = raw
    return "file://" + Path(local_value).expanduser().resolve(
        strict=False
    ).as_posix()


def bind_frozen_result_to_task(
    task: TaskSpec,
    frozen_result: FrozenTaskResult,
) -> FrozenTaskResult:
    """Bind verifier-facing identity to TaskSpec, rejecting executor echoes."""
    if frozen_result.task_id != task.task_id:
        raise ValueError("FrozenTaskResult task_id does not match TaskSpec")
    if frozen_result.task_family != task.task_family:
        raise ValueError("FrozenTaskResult task_family does not match TaskSpec")
    if frozen_result.task_spec_hash != task.spec_hash:
        raise ValueError("FrozenTaskResult task_spec_hash does not match TaskSpec")

    authoritative = {
        "repo": task.repo,
        "canonical_repo_id": task.canonical_repo_id,
        "revision": task.revision,
        "instance_id": str(
            task.metadata.get("instance_id") or task.canonical_task_key
        ),
    }
    _validate_verifier_identity_echoes(
        frozen_result.metadata,
        authoritative=authoritative,
        path="metadata",
    )
    metadata = {
        **dict(frozen_result.metadata),
        **authoritative,
        "canonical_task_id": task.canonical_task_id,
    }
    return FrozenTaskResult.create(
        task_id=frozen_result.task_id,
        task_family=frozen_result.task_family,
        task_spec_hash=frozen_result.task_spec_hash,
        run_result_hash=frozen_result.run_result_hash,
        patch=frozen_result.patch,
        output=frozen_result.output,
        metadata=metadata,
        frozen_at_ms=frozen_result.frozen_at_ms,
    )


@dataclass(frozen=True)
class MaterializedTask:
    materialization_id: str
    spec: TaskSpec
    workspace: Path
    base_revision: str


@dataclass(frozen=True)
class FrozenTaskResult:
    task_id: str
    task_family: str
    task_spec_hash: str
    run_result_hash: str
    patch: str
    patch_hash: str
    output: str
    frozen_at_ms: int
    result_hash: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = FROZEN_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        task_family: str,
        task_spec_hash: str,
        run_result_hash: str,
        patch: str,
        output: str,
        metadata: Mapping[str, Any] | None = None,
        frozen_at_ms: int | None = None,
    ) -> "FrozenTaskResult":
        timestamp = int(frozen_at_ms or time.time() * 1000)
        patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        payload = {
            "schema_version": FROZEN_RESULT_SCHEMA_VERSION,
            "task_id": task_id,
            "task_family": task_family,
            "task_spec_hash": task_spec_hash,
            "run_result_hash": run_result_hash,
            "patch": patch,
            "patch_hash": patch_hash,
            "output": output,
            "frozen_at_ms": timestamp,
            "metadata": dict(metadata or {}),
        }
        return cls(
            task_id=task_id,
            task_family=task_family,
            task_spec_hash=task_spec_hash,
            run_result_hash=run_result_hash,
            patch=patch,
            patch_hash=patch_hash,
            output=output,
            frozen_at_ms=timestamp,
            result_hash=_sha256_json(payload),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "task_spec_hash": self.task_spec_hash,
            "run_result_hash": self.run_result_hash,
            "patch": self.patch,
            "patch_hash": self.patch_hash,
            "output": self.output,
            "frozen_at_ms": self.frozen_at_ms,
            "result_hash": self.result_hash,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Grade:
    verifier_id: str
    verifier_version: str
    verifier_hash: str
    frozen_result_hash: str
    passed: bool
    score: float
    evidence: Mapping[str, Any]
    failure_classification: str = ""
    flake_classification: str = ""
    schema_version: str = GRADE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.verifier_version, str)
            or not self.verifier_version.strip()
        ):
            raise ValueError("grade verifier_version must be non-empty")
        if isinstance(self.score, bool):
            raise ValueError("grade score must be numeric")
        try:
            score = float(self.score)
        except (TypeError, ValueError) as exc:
            raise ValueError("grade score must be numeric") from exc
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("grade score must be finite and between 0 and 1")
        object.__setattr__(self, "score", score)
        object.__setattr__(
            self,
            "evidence",
            MappingProxyType(dict(self.evidence)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verifier_hash": self.verifier_hash,
            "frozen_result_hash": self.frozen_result_hash,
            "passed": self.passed,
            "score": self.score,
            "evidence": dict(self.evidence),
            "failure_classification": self.failure_classification,
            "flake_classification": self.flake_classification,
        }


@runtime_checkable
class TaskEnvironmentAdapter(Protocol):
    async def materialize(self, spec: TaskSpec) -> MaterializedTask:
        ...

    async def reset(self, task: MaterializedTask) -> None:
        ...

    async def collect_patch(
        self,
        task: MaterializedTask,
        *,
        run_result_hash: str,
        output: str,
    ) -> FrozenTaskResult:
        ...

    async def teardown(self, task: MaterializedTask) -> None:
        ...


@runtime_checkable
class VerifierAdapter(Protocol):
    verifier_id: str
    verifier_version: str
    verifier_hash: str

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        ...


class GenericRepositoryTask:
    """Materialize a pinned Git revision without exposing verifier material."""

    def __init__(self, *, work_root: str | Path) -> None:
        self.work_root = Path(work_root).expanduser().resolve()

    async def materialize(self, spec: TaskSpec) -> MaterializedTask:
        self._validate_spec(spec)
        materialization_id = str(uuid.uuid4())
        workspace = self.work_root / materialization_id / "workspace"
        workspace.parent.mkdir(parents=True, exist_ok=False)
        await asyncio.to_thread(
            _run,
            ("git", "clone", "--no-hardlinks", "--quiet", spec.repo, str(workspace)),
            None,
        )
        await asyncio.to_thread(
            _run,
            ("git", "checkout", "--quiet", "--detach", spec.revision),
            workspace,
        )
        resolved_revision = (
            await asyncio.to_thread(
                _run,
                ("git", "rev-parse", "HEAD"),
                workspace,
            )
        ).strip().lower()
        if resolved_revision != spec.revision.lower():
            raise RuntimeError(
                "materialized revision does not match pinned task revision"
            )
        task = MaterializedTask(
            materialization_id=materialization_id,
            spec=spec,
            workspace=workspace,
            base_revision=resolved_revision,
        )
        self._validate_materialized(task)
        return task

    async def reset(self, task: MaterializedTask) -> None:
        await asyncio.to_thread(
            _run,
            ("git", "reset", "--hard", task.base_revision),
            task.workspace,
        )
        await asyncio.to_thread(
            _run,
            ("git", "clean", "-ffdqx"),
            task.workspace,
        )

    async def collect_patch(
        self,
        task: MaterializedTask,
        *,
        run_result_hash: str,
        output: str,
    ) -> FrozenTaskResult:
        patch = await asyncio.to_thread(
            _collect_repository_patch,
            task.workspace,
            task.base_revision,
        )
        return FrozenTaskResult.create(
            task_id=task.spec.task_id,
            task_family=task.spec.task_family,
            task_spec_hash=task.spec.spec_hash,
            run_result_hash=run_result_hash,
            patch=patch,
            output=output,
            metadata={
                **dict(task.spec.metadata),
                "repo": task.spec.repo,
                "revision": task.spec.revision,
                "materialization_id": task.materialization_id,
            },
        )

    async def teardown(self, task: MaterializedTask) -> None:
        root = task.workspace.parent
        if root.exists():
            await asyncio.to_thread(shutil.rmtree, root)

    def _validate_spec(self, spec: TaskSpec) -> None:
        required = {
            "task_id": spec.task_id,
            "task_family": spec.task_family,
            "repo": spec.repo,
            "revision": spec.revision,
            "dataset_hash": spec.dataset_hash,
            "split_hash": spec.split_hash,
            "image_digest": spec.image_digest,
            "verifier_id": spec.verifier_id,
            "verifier_hash": spec.verifier_hash,
        }
        missing = [key for key, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError("task spec missing pinned fields: " + ", ".join(missing))
        if not _is_git_commit(spec.revision):
            raise ValueError("task spec revision must be a full immutable Git commit")
        for field_name, value in (
            ("dataset_hash", spec.dataset_hash),
            ("split_hash", spec.split_hash),
            ("verifier_hash", spec.verifier_hash),
        ):
            if not _is_sha256(value):
                raise ValueError(f"task spec {field_name} must be a sha256 digest")
        if not str(spec.image_digest).startswith("sha256:") or not _is_sha256(
            str(spec.image_digest).removeprefix("sha256:")
        ):
            raise ValueError("task spec image_digest must be a pinned sha256 digest")
        if not str(spec.architecture).strip() or not str(spec.os_name).strip():
            raise ValueError("task spec architecture and os_name are required")
        actual_architecture, actual_os = default_task_platform()
        if (
            spec.architecture.lower() != actual_architecture.lower()
            or spec.os_name.lower() != actual_os.lower()
        ):
            raise ValueError(
                "task spec platform does not match the enforced execution host"
            )
        if spec.network_policy not in {"disabled", "restricted", "enabled"}:
            raise ValueError("task spec network_policy is invalid")

    def _validate_materialized(self, task: MaterializedTask) -> None:
        if not (task.workspace / ".git").exists():
            raise RuntimeError("materialized task is not an isolated Git checkout")


class UnityRepositoryTask(GenericRepositoryTask):
    """Generic repository adapter with a Unity-project identity check."""

    def _validate_materialized(self, task: MaterializedTask) -> None:
        super()._validate_materialized(task)
        if not (task.workspace / "ProjectSettings" / "ProjectVersion.txt").is_file():
            raise ValueError("Unity task is missing ProjectSettings/ProjectVersion.txt")


class SweBenchVerifier:
    verifier_id = "official-swebench"

    def __init__(
        self,
        *,
        verifier_version: str,
        verifier_hash: str,
        oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] = (
            run_official_harness_oracle
        ),
    ) -> None:
        if not str(verifier_version).strip():
            raise ValueError("verifier_version must be non-empty")
        self.verifier_version = str(verifier_version).strip()
        self.verifier_hash = verifier_hash
        self._oracle_runner = oracle_runner

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        context = {
            **dict(frozen_result.metadata),
            "candidate_id": frozen_result.result_hash,
            "model_patch": frozen_result.patch,
            "frozen_result_hash": frozen_result.result_hash,
        }
        receipt = await asyncio.to_thread(self._oracle_runner, context)
        fail_to_pass = str(receipt.get("fail_to_pass_status") or "")
        pass_to_pass = str(receipt.get("pass_to_pass_status") or "")
        passed = fail_to_pass == "passed" and pass_to_pass == "passed"
        unavailable = bool(receipt.get("oracle_unavailable"))
        return Grade(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            verifier_hash=self.verifier_hash,
            frozen_result_hash=frozen_result.result_hash,
            passed=passed,
            score=1.0 if passed else 0.0,
            evidence=dict(receipt),
            failure_classification=(
                "verifier_infrastructure_unavailable"
                if unavailable
                else ("" if passed else "official_tests_failed")
            ),
        )


class UnityTestFrameworkVerifier:
    """Run hidden Unity tests only after the agent result has been frozen.

    Callers may inject a high-level ``runner`` for deterministic tests or use
    the built-in isolated Unity Test Framework runner by supplying a pinned
    ``unity_executable``.  The built-in runner clones the recorded revision,
    applies the frozen patch, copies hidden tests into that verifier-only
    checkout, and invokes Unity in batch mode.
    """

    def __init__(
        self,
        *,
        verifier_id: str,
        verifier_version: str,
        hidden_root: str | Path,
        verifier_hash: str = "",
        runner: Callable[
            [FrozenTaskResult, Path],
            Mapping[str, Any],
        ] | None = None,
        unity_executable: str | Path | None = None,
        test_platform: str = "EditMode",
        timeout_s: int = 900,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = (
            subprocess.run
        ),
    ) -> None:
        self.verifier_id = verifier_id
        if not str(verifier_version).strip():
            raise ValueError("verifier_version must be non-empty")
        self.verifier_version = str(verifier_version).strip()
        hidden_path = Path(hidden_root).expanduser()
        if hidden_path.is_symlink():
            raise ValueError("hidden verifier root must not be a symlink")
        self._hidden_root = hidden_path.resolve()
        if not self._hidden_root.is_dir():
            raise ValueError("hidden verifier root must be an existing directory")
        self.verifier_hash = verifier_hash or _hash_tree(self._hidden_root)
        self._runner = runner
        self._unity_executable = (
            Path(unity_executable).expanduser().resolve()
            if unity_executable is not None
            else None
        )
        self._test_platform = str(test_platform).strip() or "EditMode"
        self._timeout_s = max(1, int(timeout_s))
        self._process_runner = process_runner
        if runner is None and self._unity_executable is None:
            raise ValueError(
                "Unity verifier requires runner or pinned unity_executable"
            )

    async def verify(self, frozen_result: FrozenTaskResult) -> Grade:
        if self._runner is not None:
            result = await asyncio.to_thread(
                self._runner,
                frozen_result,
                self._hidden_root,
            )
        else:
            result = await asyncio.to_thread(
                self._run_unity_test_framework,
                frozen_result,
            )
        passed = bool(result.get("passed"))
        raw_score = result.get("score")
        score = float(raw_score) if raw_score is not None else (1.0 if passed else 0.0)
        return Grade(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            verifier_hash=self.verifier_hash,
            frozen_result_hash=frozen_result.result_hash,
            passed=passed,
            score=score,
            evidence=dict(result.get("evidence") or {}),
            failure_classification=str(result.get("failure_classification") or ""),
            flake_classification=str(result.get("flake_classification") or ""),
        )

    def _run_unity_test_framework(
        self,
        frozen_result: FrozenTaskResult,
    ) -> Mapping[str, Any]:
        executable = self._unity_executable
        if executable is None:
            return _unity_infrastructure_result(
                "unity_executable_not_configured",
            )
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return _unity_infrastructure_result(
                "unity_executable_unavailable",
                evidence={"unity_executable": str(executable)},
            )

        metadata = dict(frozen_result.metadata)
        repo = str(metadata.get("repo") or "").strip()
        revision = str(metadata.get("revision") or "").strip().lower()
        if not repo or not _is_git_commit(revision):
            return _unity_infrastructure_result(
                "frozen_result_missing_pinned_repository",
            )

        try:
            with tempfile.TemporaryDirectory(
                prefix="supervisor-unity-verifier-"
            ) as temp_dir:
                verifier_root = Path(temp_dir)
                project = verifier_root / "project"
                results_path = verifier_root / "unity-test-results.xml"
                log_path = verifier_root / "unity-editor.log"
                patch_path = verifier_root / "frozen.patch"

                _run(
                    (
                        "git",
                        "clone",
                        "--no-hardlinks",
                        "--quiet",
                        repo,
                        str(project),
                    ),
                    None,
                )
                _run(
                    ("git", "checkout", "--quiet", "--detach", revision),
                    project,
                )
                if frozen_result.patch:
                    patch_path.write_text(
                        frozen_result.patch,
                        encoding="utf-8",
                    )
                    _run(
                        (
                            "git",
                            "apply",
                            "--binary",
                            "--whitespace=nowarn",
                            str(patch_path),
                        ),
                        project,
                    )
                if not (
                    project / "ProjectSettings" / "ProjectVersion.txt"
                ).is_file():
                    return _unity_infrastructure_result(
                        "frozen_repository_is_not_a_unity_project",
                    )

                hidden_file_count = _copy_hidden_verifier_tree(
                    self._hidden_root,
                    project,
                )
                if hidden_file_count == 0:
                    return _unity_infrastructure_result(
                        "hidden_verifier_has_no_files",
                    )

                command = (
                    str(executable),
                    "-batchmode",
                    "-nographics",
                    "-projectPath",
                    str(project),
                    "-runTests",
                    "-testPlatform",
                    self._test_platform,
                    "-testResults",
                    str(results_path),
                    "-logFile",
                    str(log_path),
                )
                try:
                    completed = self._process_runner(
                        command,
                        cwd=str(project),
                        capture_output=True,
                        text=True,
                        timeout=self._timeout_s,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    return _unity_infrastructure_result(
                        "unity_test_framework_timeout",
                        flake_classification="verifier_timeout",
                        evidence={
                            "timeout_s": self._timeout_s,
                            "stdout_tail": _stream_tail(exc.stdout),
                            "stderr_tail": _stream_tail(exc.stderr),
                        },
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    return _unity_infrastructure_result(
                        "unity_test_framework_invocation_failed",
                        evidence={
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )

                evidence: dict[str, Any] = {
                    "schema_version": "unity-test-framework-receipt/v1",
                    "runner": "unity_test_framework_cli",
                    "unity_version": _unity_version_from_path(executable),
                    "unity_executable_sha256": _file_sha256(executable),
                    "test_platform": self._test_platform,
                    "returncode": int(completed.returncode),
                    "repo": repo,
                    "revision": revision,
                    "patch_hash": frozen_result.patch_hash,
                    "hidden_tree_hash": _hash_tree(self._hidden_root),
                    "hidden_file_count": hidden_file_count,
                    "stdout_tail": str(completed.stdout or "")[-4000:],
                    "stderr_tail": str(completed.stderr or "")[-4000:],
                }
                if log_path.is_file():
                    evidence["log_sha256"] = _file_sha256(log_path)
                    evidence["log_tail"] = log_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )[-4000:]
                if not results_path.is_file():
                    return _unity_infrastructure_result(
                        "unity_test_results_missing",
                        evidence=evidence,
                    )

                parsed = _parse_unity_test_results(results_path)
                evidence.update(parsed)
                evidence["results_sha256"] = _file_sha256(results_path)
                passed = (
                    int(completed.returncode) == 0
                    and parsed["total"] > 0
                    and parsed["failed"] == 0
                    and parsed["result"].casefold()
                    in {"passed", "pass", "success", "successful"}
                )
                return {
                    "passed": passed,
                    "score": 1.0 if passed else 0.0,
                    "evidence": evidence,
                    "failure_classification": (
                        ""
                        if passed
                        else (
                            "unity_tests_failed"
                            if parsed["failed"] > 0
                            else "verifier_infrastructure_unavailable"
                        )
                    ),
                    "flake_classification": "",
                }
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return _unity_infrastructure_result(
                "unity_verifier_workspace_failed",
                evidence={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )


def default_task_platform() -> tuple[str, str]:
    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "macos"
    return platform.machine(), os_name


def _collect_repository_patch(workspace: Path, base_revision: str) -> str:
    object_directory = Path(
        _run(("git", "rev-parse", "--git-path", "objects"), workspace).strip()
    )
    if not object_directory.is_absolute():
        object_directory = workspace / object_directory

    with tempfile.TemporaryDirectory(prefix="supervisor-patch-") as temp_dir:
        temp_root = Path(temp_dir)
        temp_object_directory = temp_root / "objects"
        temp_object_directory.mkdir()
        git_env = {
            "GIT_INDEX_FILE": str(temp_root / "index"),
            "GIT_OBJECT_DIRECTORY": str(temp_object_directory),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(object_directory.resolve()),
        }
        _run(("git", "read-tree", base_revision), workspace, env=git_env)
        _run(("git", "add", "-A", "--"), workspace, env=git_env)
        return _run(
            ("git", "diff", "--cached", "--binary", base_revision, "--"),
            workspace,
            env=git_env,
        )


def _run(
    argv: tuple[str, ...],
    cwd: Path | None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=None if env is None else {**os.environ, **env},
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def _sha256_json(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(
                "hashed verifier trees must not contain symbolic links"
            )
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _copy_hidden_verifier_tree(hidden_root: Path, project: Path) -> int:
    """Copy a verifier-owned tree without following symbolic links."""
    file_count = 0
    for source in sorted(hidden_root.rglob("*")):
        if source.is_symlink():
            raise ValueError(
                "hidden verifier tree must not contain symbolic links"
            )
        relative = source.relative_to(hidden_root)
        target = project / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not source.is_file():
            raise ValueError("hidden verifier tree contains unsupported entry")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        file_count += 1
    return file_count


def _parse_unity_test_results(results_path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(results_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError("Unity test results are not valid XML") from exc
    cases = list(root.iter("test-case"))
    result = str(root.attrib.get("result") or "").strip()
    passed = _xml_count(root, "passed")
    failed = _xml_count(root, "failed")
    skipped = _xml_count(root, "skipped")
    inconclusive = _xml_count(root, "inconclusive")
    total = _xml_count(root, "total")
    if cases:
        case_results = [
            str(case.attrib.get("result") or "").strip().casefold()
            for case in cases
        ]
        passed = passed or sum(
            value in {"passed", "pass", "success"} for value in case_results
        )
        failed = failed or sum(
            value in {"failed", "failure", "error"} for value in case_results
        )
        skipped = skipped or sum(
            value in {"skipped", "ignored"} for value in case_results
        )
        inconclusive = inconclusive or sum(
            value in {"inconclusive"} for value in case_results
        )
        total = total or len(cases)
    if not result:
        result = "Passed" if total > 0 and failed == 0 else "Failed"
    return {
        "result": result,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "inconclusive": inconclusive,
    }


def _xml_count(root: ET.Element, name: str) -> int:
    try:
        return max(0, int(root.attrib.get(name) or 0))
    except (TypeError, ValueError):
        return 0


def _unity_infrastructure_result(
    reason: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    flake_classification: str = "",
) -> dict[str, Any]:
    return {
        "passed": False,
        "score": 0.0,
        "evidence": {
            "schema_version": "unity-test-framework-receipt/v1",
            "reason": reason,
            **dict(evidence or {}),
        },
        "failure_classification": "verifier_infrastructure_unavailable",
        "flake_classification": flake_classification,
    }


def _unity_version_from_path(executable: Path) -> str:
    parts = executable.parts
    try:
        editor_index = parts.index("Editor")
    except ValueError:
        return ""
    if editor_index + 1 >= len(parts):
        return ""
    return parts[editor_index + 1]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_tail(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-4000:]
    return str(value)[-4000:]


def _normalize_remote_repo(host: str, path: str) -> str:
    normalized_host = str(host).strip().casefold()
    normalized_path = posixpath.normpath(
        "/" + str(path).strip().replace("\\", "/").lstrip("/")
    ).lstrip("/")
    if normalized_path.casefold().endswith(".git"):
        normalized_path = normalized_path[:-4]
    normalized_path = normalized_path.rstrip("/")
    if not normalized_host or not normalized_path or normalized_path == ".":
        raise ValueError("remote repo identity must include host and path")
    if normalized_host.split(":", 1)[0] in {
        "github.com",
        "gitlab.com",
        "bitbucket.org",
    }:
        normalized_path = normalized_path.casefold()
    return f"{normalized_host}/{normalized_path}"


def _is_explicit_non_operational_task(metadata: Mapping[str, Any]) -> bool:
    mode = str(
        metadata.get("execution_mode")
        or metadata.get("mode")
        or metadata.get("tracer_mode")
        or ""
    ).strip().casefold()
    return mode in {
        "fixture",
        "hermetic",
        "non-operational",
        "non_operational",
        "test",
    }


def _validate_verifier_identity_echoes(
    value: Any,
    *,
    authoritative: Mapping[str, str],
    path: str,
) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = re.sub(
                r"[^a-z0-9]+",
                "_",
                str(raw_key).casefold(),
            ).strip("_")
            child_path = f"{path}.{raw_key}"
            identity_field = _VERIFIER_IDENTITY_KEY_ALIASES.get(key)
            if identity_field is not None:
                _validate_verifier_identity_echo(
                    field=identity_field,
                    observed=child,
                    expected=authoritative[identity_field],
                    path=child_path,
                )
            _validate_verifier_identity_echoes(
                child,
                authoritative=authoritative,
                path=child_path,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_verifier_identity_echoes(
                child,
                authoritative=authoritative,
                path=f"{path}[{index}]",
            )


def _validate_verifier_identity_echo(
    *,
    field: str,
    observed: Any,
    expected: str,
    path: str,
) -> None:
    if not isinstance(observed, str) or not observed.strip():
        raise ValueError(f"verifier identity echo {path} must be non-empty text")
    if field == "repo":
        matches = normalize_repo_identity(observed) == normalize_repo_identity(
            expected
        )
    elif field == "canonical_repo_id":
        matches = normalize_canonical_repo_id(
            observed
        ) == normalize_canonical_repo_id(expected)
    elif field == "revision":
        matches = observed.strip().casefold() == expected.strip().casefold()
    else:
        matches = observed.strip() == expected.strip()
    if not matches:
        raise ValueError(
            f"verifier identity echo {path} does not match persisted TaskSpec"
        )


def _normalize_canonical_task_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = " ".join(normalized.strip().split()).casefold()
    if not normalized:
        raise ValueError("canonical_task_key must be non-empty")
    return normalized


def _require_identity_text(
    values: Mapping[str, Any],
    field_name: str,
) -> str:
    value = values.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{field_name} must be explicitly pinned for canonical task identity"
        )
    return value.strip()


def _require_git_object_id(
    values: Mapping[str, Any],
    field_name: str,
) -> str:
    value = _require_identity_text(values, field_name).lower()
    if not _is_git_commit(value):
        raise ValueError(
            f"{field_name} must be a full immutable Git object id"
        )
    return value


def _require_sha256_identity(
    values: Mapping[str, Any],
    field_name: str,
) -> str:
    value = _require_identity_text(values, field_name).lower()
    if not _is_sha256(value):
        raise ValueError(f"{field_name} must be a canonical sha256 digest")
    return value.removeprefix("sha256:")


def _is_git_commit(value: str) -> bool:
    raw = str(value).strip().lower()
    return len(raw) in {40, 64} and all(ch in "0123456789abcdef" for ch in raw)


def _is_sha256(value: str) -> bool:
    raw = str(value).strip().lower().removeprefix("sha256:")
    return len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw)
