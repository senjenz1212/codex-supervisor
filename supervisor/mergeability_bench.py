"""Deterministic held-out mergeability bench primitives."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


MERGEABILITY_TASK_SCHEMA_VERSION = "supervisor-mergeability-task/v1"
MERGEABILITY_CANDIDATE_SCHEMA_VERSION = "supervisor-mergeability-candidate/v1"
MERGEABILITY_RESULT_SCHEMA_VERSION = "supervisor-mergeability-result/v1"


class MergeabilityBenchError(RuntimeError):
    """Raised when a mergeability bench fixture or candidate is invalid."""


@dataclass(frozen=True)
class CommandResult:
    name: str
    argv: tuple[str, ...]
    status: str
    returncode: int | None
    duration_s: float
    stdout_sha256: str
    stderr_sha256: str
    stdout_tail: str
    stderr_tail: str
    expected_failure: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "status": self.status,
            "returncode": self.returncode,
            "duration_s": self.duration_s,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "expected_failure": self.expected_failure,
        }


@dataclass(frozen=True)
class MergeabilityTask:
    task_id: str
    repo_fixture_ref: str
    prompt: str
    allowed_mutable_paths: tuple[str, ...]
    hidden_test_commands: tuple[tuple[str, ...], ...]
    reverse_test_commands: tuple[tuple[str, ...], ...] = ()
    lint_build_commands: tuple[tuple[str, ...], ...] = ()
    scope_constraints: tuple[str, ...] = ()
    blocker_criteria: tuple[str, ...] = ()
    weighted_secondary_rubric: dict[str, float] = field(default_factory=dict)
    protected_paths: tuple[str, ...] = ("hidden/", ".mergeability/")
    fixture_path: str = ""
    task_hash: str = ""

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        fixture_path: str = "",
        task_hash: str = "",
    ) -> "MergeabilityTask":
        _require_schema(raw, MERGEABILITY_TASK_SCHEMA_VERSION, "task")
        task_id = _required_text(raw, "task_id")
        repo_fixture_ref = _required_text(raw, "repo_fixture_ref")
        prompt = _required_text(raw, "prompt")
        allowed_mutable_paths = _tuple_text(raw.get("allowed_mutable_paths"))
        hidden = _commands(raw.get("hidden_test_commands"), "hidden_test_commands")
        if not allowed_mutable_paths:
            raise MergeabilityBenchError(f"task {task_id} must declare allowed_mutable_paths")
        if not hidden:
            raise MergeabilityBenchError(f"task {task_id} must declare hidden_test_commands")
        return cls(
            task_id=task_id,
            repo_fixture_ref=repo_fixture_ref,
            prompt=prompt,
            allowed_mutable_paths=allowed_mutable_paths,
            hidden_test_commands=hidden,
            reverse_test_commands=_commands(raw.get("reverse_test_commands", ()), "reverse_test_commands"),
            lint_build_commands=_commands(raw.get("lint_build_commands", ()), "lint_build_commands"),
            scope_constraints=_tuple_text(raw.get("scope_constraints", ())),
            blocker_criteria=_tuple_text(raw.get("blocker_criteria", ())),
            weighted_secondary_rubric={
                str(key): float(value)
                for key, value in dict(raw.get("weighted_secondary_rubric") or {}).items()
            },
            protected_paths=_tuple_text(raw.get("protected_paths", ("hidden/", ".mergeability/"))),
            fixture_path=fixture_path,
            task_hash=task_hash,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": MERGEABILITY_TASK_SCHEMA_VERSION,
            "task_id": self.task_id,
            "repo_fixture_ref": self.repo_fixture_ref,
            "prompt": self.prompt,
            "allowed_mutable_paths": list(self.allowed_mutable_paths),
            "hidden_test_commands": [list(command) for command in self.hidden_test_commands],
            "reverse_test_commands": [list(command) for command in self.reverse_test_commands],
            "lint_build_commands": [list(command) for command in self.lint_build_commands],
            "scope_constraints": list(self.scope_constraints),
            "blocker_criteria": list(self.blocker_criteria),
            "weighted_secondary_rubric": dict(sorted(self.weighted_secondary_rubric.items())),
            "protected_paths": list(self.protected_paths),
            "fixture_path": self.fixture_path,
            "task_hash": self.task_hash,
        }


@dataclass(frozen=True)
class MergeabilityCandidate:
    candidate_id: str
    task_id: str
    files: dict[str, str]
    changed_files: tuple[str, ...]
    provenance: dict[str, Any] = field(default_factory=dict)
    generator_metadata: dict[str, Any] = field(default_factory=dict)
    candidate_ref: str = ""
    candidate_hash: str = ""

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        candidate_ref: str = "",
        candidate_hash: str = "",
    ) -> "MergeabilityCandidate":
        _require_schema(raw, MERGEABILITY_CANDIDATE_SCHEMA_VERSION, "candidate")
        candidate_id = _required_text(raw, "candidate_id")
        task_id = _required_text(raw, "task_id")
        files = raw.get("files")
        if not isinstance(files, Mapping):
            raise MergeabilityBenchError(f"candidate {candidate_id} must provide files mapping")
        clean_files = {
            _normalise_relpath(str(path)): str(content)
            for path, content in files.items()
        }
        changed_files = tuple(_tuple_text(raw.get("changed_files") or tuple(clean_files)))
        return cls(
            candidate_id=candidate_id,
            task_id=task_id,
            files=clean_files,
            changed_files=changed_files,
            provenance=dict(raw.get("provenance") or {}),
            generator_metadata=dict(raw.get("generator_metadata") or {}),
            candidate_ref=candidate_ref,
            candidate_hash=candidate_hash,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": MERGEABILITY_CANDIDATE_SCHEMA_VERSION,
            "candidate_id": self.candidate_id,
            "task_id": self.task_id,
            "changed_files": list(self.changed_files),
            "files": dict(sorted(self.files.items())),
            "provenance": self.provenance,
            "generator_metadata": self.generator_metadata,
            "candidate_ref": self.candidate_ref,
            "candidate_hash": self.candidate_hash,
        }


@dataclass(frozen=True)
class MergeabilityResult:
    task_id: str
    candidate_id: str
    blocker_status: str
    hidden_test_status: str
    reverse_test_status: str
    scope_status: str
    lint_build_status: str
    weighted_secondary_score: float | None
    final_score: float
    command_results: tuple[CommandResult, ...]
    evidence_refs: tuple[str, ...]
    receipt_ids: tuple[str, ...]
    artifact_hashes: dict[str, str]
    failures: tuple[str, ...] = ()
    secondary_report_only: bool = True

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": MERGEABILITY_RESULT_SCHEMA_VERSION,
            "task_id": self.task_id,
            "candidate_id": self.candidate_id,
            "blocker_status": self.blocker_status,
            "hidden_test_status": self.hidden_test_status,
            "reverse_test_status": self.reverse_test_status,
            "scope_status": self.scope_status,
            "lint_build_status": self.lint_build_status,
            "weighted_secondary_score": self.weighted_secondary_score,
            "secondary_report_only": self.secondary_report_only,
            "final_score": self.final_score,
            "command_results": [result.to_payload() for result in self.command_results],
            "evidence_refs": list(self.evidence_refs),
            "receipt_ids": list(self.receipt_ids),
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "failures": list(self.failures),
        }
        payload["result_sha256"] = _sha256_json({k: v for k, v in payload.items() if k != "result_sha256"})
        return payload


def load_mergeability_tasks(root: str | Path) -> tuple[MergeabilityTask, ...]:
    root_path = Path(root).expanduser().resolve()
    paths = [root_path] if root_path.is_file() else sorted((root_path / "tasks").glob("*.json"))
    tasks = []
    for path in paths:
        raw = _read_json(path)
        tasks.append(MergeabilityTask.from_mapping(
            raw,
            fixture_path=path.as_posix(),
            task_hash=sha256(path.read_bytes()).hexdigest(),
        ))
    if not tasks:
        raise MergeabilityBenchError(f"no mergeability task fixtures found under {root_path}")
    return tuple(tasks)


def load_mergeability_task(root: str | Path, task_id: str) -> MergeabilityTask:
    for task in load_mergeability_tasks(root):
        if task.task_id == task_id:
            return task
    raise MergeabilityBenchError(f"mergeability task not found: {task_id}")


def load_mergeability_candidate(path: str | Path) -> MergeabilityCandidate:
    candidate_path = Path(path).expanduser().resolve()
    raw = _read_json(candidate_path)
    return MergeabilityCandidate.from_mapping(
        raw,
        candidate_ref=candidate_path.as_posix(),
        candidate_hash=sha256(candidate_path.read_bytes()).hexdigest(),
    )


def grade_mergeability_candidate(
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    *,
    bench_root: str | Path,
    output_dir: str | Path | None = None,
    timeout_s: float = 30.0,
) -> MergeabilityResult:
    if candidate.task_id != task.task_id:
        raise MergeabilityBenchError(
            f"candidate {candidate.candidate_id} targets {candidate.task_id}, expected {task.task_id}"
        )
    bench_root_path = Path(bench_root).expanduser().resolve()
    fixture_root = (bench_root_path / task.repo_fixture_ref).resolve()
    if not fixture_root.exists():
        raise MergeabilityBenchError(f"repo fixture missing: {fixture_root}")

    failures = _scope_failures(task, candidate)
    command_results: list[CommandResult] = []
    artifact_hashes = {
        "task": task.task_hash,
        "candidate": candidate.candidate_hash or _sha256_json(candidate.to_payload()),
    }

    with tempfile.TemporaryDirectory(prefix="mergeability-bench-") as temp_dir:
        temp_root = Path(temp_dir)
        candidate_worktree = temp_root / "candidate"
        reverse_worktree = temp_root / "reverse"
        shutil.copytree(fixture_root, candidate_worktree)
        shutil.copytree(fixture_root, reverse_worktree)

        if not failures:
            _apply_candidate_files(candidate_worktree, candidate.files)
            candidate_test_files = _candidate_test_files(candidate.files)
            _apply_candidate_files(reverse_worktree, candidate_test_files)

            hidden_results = [
                _run_command(command, cwd=candidate_worktree, timeout_s=timeout_s, name="hidden_test")
                for command in task.hidden_test_commands
            ]
            command_results.extend(hidden_results)

            reverse_precheck_failures = _reverse_test_precheck_failures(
                task.reverse_test_commands,
                reverse_worktree=reverse_worktree,
                candidate_test_files=candidate_test_files,
            )
            failures.extend(reverse_precheck_failures)
            reverse_results = [
                _run_command(
                    command,
                    cwd=reverse_worktree,
                    timeout_s=timeout_s,
                    name="reverse_test",
                    expected_failure=True,
                )
                for command in task.reverse_test_commands
                if not reverse_precheck_failures
            ]
            command_results.extend(reverse_results)

            lint_results = [
                _run_command(command, cwd=candidate_worktree, timeout_s=timeout_s, name="lint_build")
                for command in task.lint_build_commands
            ]
            command_results.extend(lint_results)

            failures.extend(_command_failures("hidden tests", hidden_results, expect_pass=True))
            failures.extend(_command_failures("reverse tests", reverse_results, expect_pass=False))
            failures.extend(_command_failures("lint/build", lint_results, expect_pass=True))

        result = _build_result(
            task=task,
            candidate=candidate,
            command_results=tuple(command_results),
            failures=tuple(failures),
            artifact_hashes=artifact_hashes,
        )

    if output_dir is not None:
        _write_result_artifact(result, output_dir=Path(output_dir).expanduser())
    return result


def result_receipt(result: MergeabilityResult, *, result_ref: str = "") -> dict[str, Any]:
    payload = result.to_payload()
    return {
        "receipt_id": f"mergeability:{result.task_id}:{result.candidate_id}",
        "kind": "mergeability_result",
        "source": "supervisor",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "mergeability_bench",
        "status": "passed" if result.final_score >= 1.0 else "failed",
        "task_id": result.task_id,
        "candidate_id": result.candidate_id,
        "final_score": result.final_score,
        "blocker_status": result.blocker_status,
        "result_ref": result_ref,
        "result_sha256": payload["result_sha256"],
    }


def _build_result(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    command_results: tuple[CommandResult, ...],
    failures: tuple[str, ...],
    artifact_hashes: dict[str, str],
) -> MergeabilityResult:
    hidden = _status_for(command_results, "hidden_test", expect_pass=True, empty_pass=False)
    reverse = _status_for(command_results, "reverse_test", expect_pass=False, empty_pass=True)
    lint = _status_for(command_results, "lint_build", expect_pass=True, empty_pass=True)
    scope = "failed" if any(failure.startswith("scope:") for failure in failures) else "passed"
    if any(failure.startswith("reverse tests:") for failure in failures):
        reverse = "failed"
    blocker_status = "passed" if not failures else "failed"
    final_score = 1.0 if blocker_status == "passed" else 0.0
    evidence_refs = (
        f"mergeability_task:{task.fixture_path}",
        f"mergeability_candidate:{candidate.candidate_ref or candidate.candidate_id}",
    )
    receipt_ids = (f"mergeability:{task.task_id}:{candidate.candidate_id}",)
    return MergeabilityResult(
        task_id=task.task_id,
        candidate_id=candidate.candidate_id,
        blocker_status=blocker_status,
        hidden_test_status=hidden,
        reverse_test_status=reverse,
        scope_status=scope,
        lint_build_status=lint,
        weighted_secondary_score=None,
        final_score=final_score,
        command_results=command_results,
        evidence_refs=evidence_refs,
        receipt_ids=receipt_ids,
        artifact_hashes=artifact_hashes,
        failures=failures,
    )


def _write_result_artifact(result: MergeabilityResult, *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result.task_id}-{result.candidate_id}.json"
    path.write_text(json.dumps(result.to_payload(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _scope_failures(task: MergeabilityTask, candidate: MergeabilityCandidate) -> list[str]:
    failures: list[str] = []
    for raw_path in set(candidate.changed_files) | set(candidate.files):
        path = _normalise_relpath(raw_path)
        if not _matches_prefix(path, task.allowed_mutable_paths):
            failures.append(f"scope:path_outside_allowed_mutable_surface:{path}")
        if _matches_prefix(path, task.protected_paths):
            failures.append(f"scope:protected_path_mutation:{path}")
    return sorted(set(failures))


def _apply_candidate_files(root: Path, files: Mapping[str, str]) -> None:
    for raw_path, content in files.items():
        path = _normalise_relpath(raw_path)
        target = (root / path).resolve()
        if not _is_relative_to(target, root.resolve()):
            raise MergeabilityBenchError(f"candidate path escapes workspace: {raw_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


def _candidate_test_files(files: Mapping[str, str]) -> dict[str, str]:
    return {
        path: content
        for path, content in files.items()
        if path.startswith("tests/") or Path(path).name.startswith("test_")
    }


def _reverse_test_precheck_failures(
    commands: tuple[tuple[str, ...], ...],
    *,
    reverse_worktree: Path,
    candidate_test_files: Mapping[str, str],
) -> list[str]:
    if not commands:
        return []
    if not candidate_test_files:
        return ["reverse tests:no_candidate_tests_submitted"]

    failures: list[str] = []
    for command in commands:
        for target in _pytest_target_paths(command):
            if not (reverse_worktree / target).exists():
                failures.append(f"reverse tests:missing_candidate_test_target:{target}")
    return sorted(set(failures))


def _pytest_target_paths(command: tuple[str, ...]) -> tuple[str, ...]:
    try:
        pytest_index = command.index("pytest")
    except ValueError:
        if len(command) >= 3 and command[0] in {"python", "python3"} and command[1] == "-m" and command[2] == "pytest":
            pytest_index = 2
        else:
            return ()

    targets: list[str] = []
    skip_next = False
    options_with_values = {"-k", "-m", "--tb", "--junitxml", "-o", "--maxfail"}
    for raw_part in command[pytest_index + 1:]:
        part = str(raw_part)
        if skip_next:
            skip_next = False
            continue
        if part.startswith("-"):
            if part in options_with_values:
                skip_next = True
            continue
        target = part.split("::", 1)[0]
        if target and target != ".":
            targets.append(_normalise_relpath(target))
    return tuple(targets)


def _run_command(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_s: float,
    name: str,
    expected_failure: bool = False,
) -> CommandResult:
    argv = _resolved_argv(command)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            input="",
            capture_output=True,
            text=True,
            timeout=max(0.001, timeout_s),
            check=False,
            env=_command_env(cwd),
        )
        returncode = completed.returncode
        passed = returncode != 0 if expected_failure else returncode == 0
        status = "passed" if passed else "failed"
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        returncode = None
        status = "failed"
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else "timeout"
    return CommandResult(
        name=name,
        argv=tuple(argv),
        status=status,
        returncode=returncode,
        duration_s=0.0,
        stdout_sha256=sha256(_stable_command_text(stdout).encode("utf-8")).hexdigest(),
        stderr_sha256=sha256(_stable_command_text(stderr).encode("utf-8")).hexdigest(),
        stdout_tail=_tail(_stable_command_text(stdout)),
        stderr_tail=_tail(_stable_command_text(stderr)),
        expected_failure=expected_failure,
    )


def _resolved_argv(command: tuple[str, ...]) -> list[str]:
    if not command:
        raise MergeabilityBenchError("empty command is not allowed")
    if command[0] in {"python", "python3"}:
        return [sys.executable, *command[1:]]
    return list(command)


def _command_env(cwd: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(cwd),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _command_failures(label: str, results: list[CommandResult], *, expect_pass: bool) -> list[str]:
    failures = []
    for result in results:
        if result.status != "passed":
            expectation = "fail_on_base" if not expect_pass else "pass"
            failures.append(f"{label}:{result.argv}:expected_{expectation}:returncode_{result.returncode}")
    return failures


def _status_for(
    command_results: tuple[CommandResult, ...],
    name: str,
    *,
    expect_pass: bool,
    empty_pass: bool,
) -> str:
    matching = [result for result in command_results if result.name == name]
    if not matching:
        return "passed" if empty_pass else "failed"
    return "passed" if all(result.status == "passed" for result in matching) else "failed"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MergeabilityBenchError(f"failed to read JSON fixture {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MergeabilityBenchError(f"JSON fixture must be an object: {path}")
    return raw


def _require_schema(raw: Mapping[str, Any], expected: str, label: str) -> None:
    observed = str(raw.get("schema_version") or "")
    if observed != expected:
        raise MergeabilityBenchError(f"{label} schema_version {observed!r} != {expected!r}")


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise MergeabilityBenchError(f"missing required field: {key}")
    return value


def _tuple_text(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _commands(value: Any, field_name: str) -> tuple[tuple[str, ...], ...]:
    if value is None or value == "":
        return ()
    commands = []
    for item in value:
        if not isinstance(item, list) or not all(isinstance(part, str) and part for part in item):
            raise MergeabilityBenchError(f"{field_name} commands must be non-empty argv arrays")
        commands.append(tuple(item))
    return tuple(commands)


def _normalise_relpath(raw_path: str) -> str:
    path = Path(str(raw_path))
    if path.is_absolute():
        raise MergeabilityBenchError(f"absolute paths are not allowed: {raw_path}")
    normalized = path.as_posix().strip()
    if not normalized or normalized == "." or normalized.startswith("../") or "/../" in normalized:
        raise MergeabilityBenchError(f"path escapes bench workspace: {raw_path}")
    return normalized


def _matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    clean = path.rstrip("/")
    for raw_prefix in prefixes:
        prefix = str(raw_prefix).strip().rstrip("/")
        if not prefix:
            continue
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256_json(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _tail(text: str, limit: int = 1200) -> str:
    return text[-limit:]


def _stable_command_text(text: str) -> str:
    return re.sub(r"in \d+(?:\.\d+)?s", "in <duration>s", text)
