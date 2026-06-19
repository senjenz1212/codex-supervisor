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
from typing import Any, Callable, Mapping


MERGEABILITY_TASK_SCHEMA_VERSION = "supervisor-mergeability-task/v1"
MERGEABILITY_CANDIDATE_SCHEMA_VERSION = "supervisor-mergeability-candidate/v1"
MERGEABILITY_RESULT_SCHEMA_VERSION = "supervisor-mergeability-result/v1"
MERGEABILITY_CORPUS_MANIFEST_SCHEMA_VERSION = "supervisor-mergeability-corpus-manifest/v1"
MERGEABILITY_CALIBRATION_SCHEMA_VERSION = "supervisor-mergeability-calibration/v1"
MERGEABILITY_PAIRED_REPORT_SCHEMA_VERSION = "supervisor-mergeability-paired-report/v1"
SUPERVISOR_REVIEW_INPUT_SCHEMA_VERSION = "supervisor-mergeability-candidate-review-input/v1"
SUPERVISOR_REVIEW_RESULT_SCHEMA_VERSION = "supervisor-mergeability-candidate-review/v1"
SUPERVISOR_FULL_GATE_PACKET_SCHEMA_VERSION = "supervisor-mergeability-full-gate-review-packet/v1"
SUPERVISOR_FULL_GATE_RESULT_SCHEMA_VERSION = "supervisor-mergeability-full-gate-review/v1"

ORACLE_REVIEW_FORBIDDEN_KEYS = frozenset({
    "expected_outcome",
    "final_score",
    "oracle_accept",
    "hidden_test_commands",
})
ORACLE_REVIEW_FORBIDDEN_TEXT = ("hidden/test_behavior.py", ".mergeability/")

NEGATIVE_CONTROL_KINDS = frozenset({
    "noop",
    "known_bad",
    "hidden_behavior_miss",
    "missing_regression_test",
    "tautological_test",
    "wrong_test_path",
    "protected_path_escape",
    "scope_escape",
    "overbroad_diff",
    "lint_build_failure",
})
POSITIVE_CONTROL_KINDS = frozenset({"known_good", "secondary_rubric_only"})
REQUIRED_CALIBRATION_CONTROL_KINDS = frozenset({
    "noop",
    "known_bad",
    "known_good",
    "missing_regression_test",
    "tautological_test",
    "protected_path_escape",
    "scope_escape",
})


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


def load_mergeability_candidates(
    root: str | Path,
    *,
    task_id: str | None = None,
) -> tuple[MergeabilityCandidate, ...]:
    root_path = Path(root).expanduser().resolve()
    paths = sorted((root_path / "candidates").glob("*.json"))
    candidates = tuple(load_mergeability_candidate(path) for path in paths)
    if task_id is not None:
        candidates = tuple(candidate for candidate in candidates if candidate.task_id == task_id)
    if not candidates:
        raise MergeabilityBenchError(f"no mergeability candidate fixtures found under {root_path}")
    return candidates


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


def build_mergeability_corpus_manifest(
    bench_root: str | Path,
    *,
    candidate_paths: tuple[str | Path, ...] = (),
) -> dict[str, Any]:
    bench_root_path = Path(bench_root).expanduser().resolve()
    tasks = load_mergeability_tasks(bench_root_path)
    candidates = (
        tuple(load_mergeability_candidate(path) for path in candidate_paths)
        if candidate_paths
        else load_mergeability_candidates(bench_root_path)
    )
    candidates_by_task: dict[str, list[MergeabilityCandidate]] = {}
    for candidate in candidates:
        candidates_by_task.setdefault(candidate.task_id, []).append(candidate)

    task_entries: list[dict[str, Any]] = []
    included_task_ids: list[str] = []
    for task in tasks:
        task_candidates = sorted(
            candidates_by_task.get(task.task_id, []),
            key=lambda candidate: candidate.candidate_id,
        )
        positive = [
            candidate.candidate_id
            for candidate in task_candidates
            if _candidate_expected_outcome(candidate) == "pass"
        ]
        negative = [
            candidate.candidate_id
            for candidate in task_candidates
            if _candidate_expected_outcome(candidate) == "fail"
        ]
        calibration_eligible = bool(positive and negative)
        if calibration_eligible:
            included_task_ids.append(task.task_id)
        task_entries.append({
            "task_id": task.task_id,
            "task_hash": task.task_hash,
            "fixture_path": task.fixture_path,
            "repo_fixture_ref": task.repo_fixture_ref,
            "candidate_count": len(task_candidates),
            "positive_controls": positive,
            "negative_controls": negative,
            "control_kinds": sorted({_candidate_control_kind(candidate) for candidate in task_candidates}),
            "calibration_eligible": calibration_eligible,
            "excluded_reason": "" if calibration_eligible else "requires_positive_and_negative_controls",
        })

    candidate_entries = [
        {
            "candidate_id": candidate.candidate_id,
            "task_id": candidate.task_id,
            "candidate_ref": candidate.candidate_ref,
            "candidate_hash": candidate.candidate_hash,
            "control_kind": _candidate_control_kind(candidate),
            "expected_outcome": _candidate_expected_outcome(candidate),
            "baseline_accept": _baseline_accepts(candidate),
            "changed_files": list(candidate.changed_files),
        }
        for candidate in sorted(candidates, key=lambda item: (item.task_id, item.candidate_id))
    ]
    manifest = {
        "schema_version": MERGEABILITY_CORPUS_MANIFEST_SCHEMA_VERSION,
        "bench_root": bench_root_path.as_posix(),
        "task_count": len(tasks),
        "candidate_count": len(candidates),
        "included_task_ids": included_task_ids,
        "excluded_task_ids": [
            entry["task_id"] for entry in task_entries if not entry["calibration_eligible"]
        ],
        "tasks": task_entries,
        "candidates": candidate_entries,
        "manifest_sha256": "",
    }
    manifest["manifest_sha256"] = _sha256_json({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    return manifest


def validate_mergeability_corpus(
    bench_root: str | Path,
    *,
    output_dir: str | Path | None = None,
    strict: bool = True,
    candidate_paths: tuple[str | Path, ...] = (),
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    bench_root_path = Path(bench_root).expanduser().resolve()
    manifest = build_mergeability_corpus_manifest(bench_root_path, candidate_paths=candidate_paths)
    tasks = {task.task_id: task for task in load_mergeability_tasks(bench_root_path)}
    candidates = (
        tuple(load_mergeability_candidate(path) for path in candidate_paths)
        if candidate_paths
        else load_mergeability_candidates(bench_root_path)
    )
    included_task_ids = set(manifest["included_task_ids"])
    errors: list[str] = []
    if manifest["excluded_task_ids"]:
        errors.append(
            "tasks excluded from aggregate reporting: "
            + ", ".join(
                f"{entry['task_id']}:{entry['excluded_reason']}"
                for entry in manifest["tasks"]
                if not entry["calibration_eligible"]
            )
        )

    observed_control_kinds = {_candidate_control_kind(candidate) for candidate in candidates}
    missing_control_kinds = sorted(REQUIRED_CALIBRATION_CONTROL_KINDS - observed_control_kinds)
    if missing_control_kinds:
        errors.append("missing required calibration controls: " + ", ".join(missing_control_kinds))

    output_path = Path(output_dir).expanduser() if output_dir is not None else None
    result_entries: list[dict[str, Any]] = []
    receipt_entries: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item.task_id, item.candidate_id)):
        task = tasks.get(candidate.task_id)
        if task is None:
            errors.append(f"candidate {candidate.candidate_id} targets missing task {candidate.task_id}")
            continue
        if candidate.task_id not in included_task_ids:
            continue
        result_output = output_path / "mergeability-results" if output_path is not None else None
        result = grade_mergeability_candidate(
            task,
            candidate,
            bench_root=bench_root_path,
            output_dir=result_output,
            timeout_s=timeout_s,
        )
        result_ref = (
            (result_output / f"{result.task_id}-{result.candidate_id}.json").as_posix()
            if result_output is not None else ""
        )
        receipt = result_receipt(result, result_ref=result_ref)
        expected = _candidate_expected_outcome(candidate)
        observed = "pass" if result.final_score >= 1.0 else "fail"
        if observed != expected:
            errors.append(
                f"candidate {candidate.candidate_id} expected {expected} but observed {observed}"
            )
        result_entries.append({
            "task_id": result.task_id,
            "candidate_id": result.candidate_id,
            "control_kind": _candidate_control_kind(candidate),
            "expected_outcome": expected,
            "observed_outcome": observed,
            "final_score": result.final_score,
            "blocker_status": result.blocker_status,
            "hidden_test_status": result.hidden_test_status,
            "reverse_test_status": result.reverse_test_status,
            "scope_status": result.scope_status,
            "lint_build_status": result.lint_build_status,
            "failures": list(result.failures),
            "receipt_id": receipt["receipt_id"],
            "receipt": receipt,
        })
        receipt_entries.append(receipt)

    calibration_flags = _calibration_non_applyable_flags(result_entries)
    if "saturated_all_ones" in calibration_flags:
        errors.append("all calibration results scored 1.0; corpus is non-discriminating")
    if "no_passing_positive_control" in calibration_flags:
        errors.append("corpus has no passing positive control")
    if "no_failing_negative_control" in calibration_flags:
        errors.append("corpus has no failing negative control")

    summary = {
        "schema_version": MERGEABILITY_CALIBRATION_SCHEMA_VERSION,
        "status": "accepted" if not errors else "rejected",
        "bench_root": bench_root_path.as_posix(),
        "manifest_sha256": manifest["manifest_sha256"],
        "task_count": manifest["task_count"],
        "candidate_count": manifest["candidate_count"],
        "included_task_ids": manifest["included_task_ids"],
        "excluded_task_ids": manifest["excluded_task_ids"],
        "required_control_kinds": sorted(REQUIRED_CALIBRATION_CONTROL_KINDS),
        "observed_control_kinds": sorted(observed_control_kinds),
        "results": result_entries,
        "receipt_ids": [receipt["receipt_id"] for receipt in receipt_entries],
        "gaming_flags": calibration_flags,
        "calibration_metric_applyable": not bool(errors),
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "errors": errors,
    }
    summary["summary_sha256"] = _sha256_json({
        key: value for key, value in summary.items() if key != "summary_sha256"
    })
    if output_path is not None:
        _export_calibration_artifacts(output_path, manifest=manifest, summary=summary)
    if strict and errors:
        raise MergeabilityBenchError("; ".join(errors))
    return summary


def run_paired_acceptance_pilot(
    bench_root: str | Path,
    *,
    output_dir: str | Path | None = None,
    candidate_paths: tuple[str | Path, ...] = (),
    timeout_s: float = 30.0,
    strict_calibration: bool = True,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    bench_root_path = Path(bench_root).expanduser().resolve()
    output_path = Path(output_dir).expanduser() if output_dir is not None else None
    manifest = build_mergeability_corpus_manifest(bench_root_path, candidate_paths=candidate_paths)
    tasks = {task.task_id: task for task in load_mergeability_tasks(bench_root_path)}
    candidate_list = (
        tuple(load_mergeability_candidate(path) for path in candidate_paths)
        if candidate_paths else load_mergeability_candidates(bench_root_path)
    )
    public_reviews = {
        candidate.candidate_id: review_mergeability_candidate_publicly(
            tasks[candidate.task_id],
            candidate,
            bench_root=bench_root_path,
            timeout_s=timeout_s,
        )
        for candidate in candidate_list
        if candidate.task_id in tasks
    }
    calibration = validate_mergeability_corpus(
        bench_root_path,
        output_dir=output_path,
        strict=strict_calibration,
        candidate_paths=candidate_paths,
        timeout_s=timeout_s,
    )
    candidates = {candidate.candidate_id: candidate for candidate in candidate_list}

    rows: list[dict[str, Any]] = []
    for result in calibration["results"]:
        candidate = candidates[result["candidate_id"]]
        supervisor_review = public_reviews[result["candidate_id"]]
        full_gate_review = _review_mergeability_candidate_full_gate(
            task=tasks[candidate.task_id],
            candidate=candidate,
            public_review=supervisor_review,
            reviewer_panel=reviewer_panel,
        )
        oracle_accept = float(result["final_score"]) >= 1.0
        baseline_accept = _baseline_accepts(candidate)
        supervisor_accept = bool(supervisor_review["accept"])
        supervisor_full_gate_accept = bool(full_gate_review["accept"])
        row = {
            "task_id": result["task_id"],
            "candidate_id": result["candidate_id"],
            "control_kind": result["control_kind"],
            "expected_outcome": result["expected_outcome"],
            "oracle_accept": oracle_accept,
            "baseline_accept": baseline_accept,
            "supervisor_accept": supervisor_accept,
            "supervisor_candidate_review_accept": supervisor_accept,
            "supervisor_full_gate_accept": supervisor_full_gate_accept,
            "supervisor_full_gate_unavailable": bool(full_gate_review["unavailable"]),
            "oracle_ceiling_accept": oracle_accept,
            "baseline_false_accept": baseline_accept and not oracle_accept,
            "supervisor_false_accept": supervisor_accept and not oracle_accept,
            "supervisor_candidate_review_false_accept": supervisor_accept and not oracle_accept,
            "supervisor_full_gate_false_accept": supervisor_full_gate_accept and not oracle_accept,
            "oracle_ceiling_false_accept": oracle_accept and not oracle_accept,
            "baseline_false_reject": (not baseline_accept) and oracle_accept,
            "supervisor_false_reject": (not supervisor_accept) and oracle_accept,
            "supervisor_candidate_review_false_reject": (not supervisor_accept) and oracle_accept,
            "supervisor_full_gate_false_reject": (not supervisor_full_gate_accept) and oracle_accept,
            "oracle_ceiling_false_reject": (not oracle_accept) and oracle_accept,
            "receipt_id": result["receipt_id"],
            "receipt": result["receipt"],
            "blocker_status": result["blocker_status"],
            "failures": result["failures"],
            "baseline_decision_source": "candidate_self_report",
            "oracle_ceiling_decision_source": "oracle_final_score",
            "supervisor_decision_source": "supervisor_candidate_review",
            "supervisor_candidate_review_decision_source": "supervisor_candidate_review",
            "supervisor_full_gate_decision_source": "supervisor_full_gate",
            "supervisor_review": supervisor_review,
            "supervisor_full_gate_review": full_gate_review,
        }
        rows.append(row)

    baseline = _summarize_acceptance_arm(
        rows,
        arm="baseline",
        arm_role="baseline_self_report",
        decision_source="candidate_self_report",
        oracle_coupled=False,
    )
    oracle_ceiling = _summarize_acceptance_arm(
        rows,
        arm="oracle_ceiling",
        arm_role="oracle_ceiling",
        decision_source="oracle_final_score",
        oracle_coupled=True,
    )
    supervisor = dict(oracle_ceiling)
    supervisor_candidate_review = _summarize_acceptance_arm(
        rows,
        arm="supervisor_candidate_review",
        arm_role="supervisor_candidate_review",
        decision_source="supervisor_candidate_review",
        oracle_coupled=False,
    )
    supervisor_full_gate = _summarize_acceptance_arm(
        rows,
        arm="supervisor_full_gate",
        arm_role="supervisor_full_gate",
        decision_source="supervisor_candidate_review+independent_reviewer_panel",
        oracle_coupled=False,
    )
    supervisor = dict(supervisor_candidate_review)
    supervisor["arm"] = "supervisor"
    supervisor["legacy_alias_of"] = "supervisor_candidate_review"
    task_hashes = {entry["task_id"]: entry["task_hash"] for entry in manifest["tasks"]}
    candidate_hashes = {
        (entry["task_id"], entry["candidate_id"]): entry["candidate_hash"]
        for entry in manifest["candidates"]
    }
    disagreements = [
        {
            "task_id": row["task_id"],
            "candidate_id": row["candidate_id"],
            "control_kind": row["control_kind"],
            "baseline_accept": row["baseline_accept"],
            "supervisor_accept": row["supervisor_accept"],
            "oracle_accept": row["oracle_accept"],
            "reason": "supervisor_candidate_review_disagreed_with_oracle",
        }
        for row in rows
        if row["supervisor_accept"] != row["oracle_accept"]
    ]
    positive_control_count = sum(
        1 for entry in manifest["candidates"] if entry["expected_outcome"] == "pass"
    )
    negative_control_count = sum(
        1 for entry in manifest["candidates"] if entry["expected_outcome"] == "fail"
    )
    oracle_agreement = {
        "supervisor_candidate_review": _oracle_agreement(rows, arm="supervisor_candidate_review"),
        "supervisor_full_gate": _oracle_agreement(rows, arm="supervisor_full_gate"),
        "baseline": _oracle_agreement(rows, arm="baseline"),
        "oracle_ceiling": _oracle_agreement(rows, arm="oracle_ceiling"),
    }
    gaming_flags = set(calibration["gaming_flags"])
    gaming_flags.update(
        flag
        for row in rows
        for flag in row["supervisor_review"].get("gaming_flags", [])
    )
    gaming_flags.update(
        flag
        for row in rows
        for flag in row["supervisor_full_gate_review"].get("gaming_flags", [])
    )
    if _should_trip_perfect_agreement(rows, oracle_agreement["supervisor_candidate_review"]):
        gaming_flags.add("perfect_oracle_agreement_tripwire")
    matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=baseline,
        supervisor=supervisor_candidate_review,
    )
    full_gate_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=baseline,
        supervisor=supervisor_full_gate,
    )
    panel_marginal_delta = _panel_marginal_delta_at_matched_true_accept(
        public_review=supervisor_candidate_review,
        full_gate=supervisor_full_gate,
    )
    report = {
        "schema_version": MERGEABILITY_PAIRED_REPORT_SCHEMA_VERSION,
        "bench_root": bench_root_path.as_posix(),
        "manifest_sha256": manifest["manifest_sha256"],
        "calibration_summary_sha256": calibration["summary_sha256"],
        "task_count": manifest["task_count"],
        "included_task_ids": manifest["included_task_ids"],
        "candidate_count": len(rows),
        "positive_control_count": positive_control_count,
        "negative_control_count": negative_control_count,
        "candidate_pool_sha256": _sha256_json([
            {
                "task_id": row["task_id"],
                "task_hash": task_hashes[row["task_id"]],
                "candidate_id": row["candidate_id"],
                "candidate_hash": candidate_hashes[(row["task_id"], row["candidate_id"])],
            }
            for row in rows
        ]),
        "arms": {
            "baseline": baseline,
            "supervisor_candidate_review": supervisor_candidate_review,
            "supervisor_full_gate": supervisor_full_gate,
            "oracle_ceiling": oracle_ceiling,
            "supervisor": supervisor,
        },
        "delta": {
            "oracle_ceiling_minus_baseline_false_accept_rate": round(
                oracle_ceiling["false_accept_rate"] - baseline["false_accept_rate"], 6
            ),
            "oracle_ceiling_minus_baseline_true_accept_rate": round(
                oracle_ceiling["true_accept_rate"] - baseline["true_accept_rate"], 6
            ),
            "supervisor_minus_baseline_false_accept_rate": round(
                supervisor_candidate_review["false_accept_rate"] - baseline["false_accept_rate"], 6
            ),
            "supervisor_minus_baseline_true_accept_rate": round(
                supervisor_candidate_review["true_accept_rate"] - baseline["true_accept_rate"], 6
            ),
            "supervisor_full_gate_minus_baseline_false_accept_rate": round(
                supervisor_full_gate["false_accept_rate"] - baseline["false_accept_rate"], 6
            ),
            "supervisor_full_gate_minus_baseline_true_accept_rate": round(
                supervisor_full_gate["true_accept_rate"] - baseline["true_accept_rate"], 6
            ),
        },
        "false_accept_at_matched_true_accept": matched_true_accept,
        "supervisor_full_gate_false_accept_at_matched_true_accept": full_gate_matched_true_accept,
        "panel_marginal_delta_at_matched_true_accept": panel_marginal_delta,
        "matched_true_accept_status": matched_true_accept["status"],
        "oracle_agreement": oracle_agreement,
        "disagreements": disagreements,
        "per_task_results": rows,
        "cost_usd": 0.0,
        "wall_clock_s": round(time.monotonic() - started, 6),
        "report_label": "calibration",
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "gaming_flags": sorted(gaming_flags),
        "validity_notes": [
            "Supervisor candidate review is recorded from public-only evidence before hidden oracle "
            "grading is consulted for aggregate metrics.",
            "Supervisor full gate is recorded as the public candidate review plus an independent "
            "reviewer-panel decision from a public-only packet; unavailable reviewers are not "
            "imputed from the public-check arm.",
            "This fixture-scale report is calibration evidence only, not proof of production improvement.",
        ],
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "next_step": "grow an oracle-isolated corpus before any powered live-generation experiment",
        },
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    if output_path is not None:
        _export_paired_acceptance_artifacts(
            output_path,
            manifest=manifest,
            calibration=calibration,
            report=report,
            rows=rows,
        )
    return report


def review_mergeability_candidate_publicly(
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    *,
    bench_root: str | Path,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Review a candidate from public task/candidate evidence only."""
    if candidate.task_id != task.task_id:
        raise MergeabilityBenchError(
            f"candidate {candidate.candidate_id} targets {candidate.task_id}, expected {task.task_id}"
        )
    bench_root_path = Path(bench_root).expanduser().resolve()
    fixture_root = (bench_root_path / task.repo_fixture_ref).resolve()
    if not fixture_root.exists():
        raise MergeabilityBenchError(f"repo fixture missing: {fixture_root}")

    protected_candidate_paths = sorted(
        path for path in set(candidate.changed_files) | set(candidate.files)
        if _matches_prefix(_normalise_relpath(path), task.protected_paths)
    )
    public_files = {
        path: content
        for path, content in candidate.files.items()
        if not _matches_prefix(_normalise_relpath(path), task.protected_paths)
    }
    public_changed_files = [
        path for path in candidate.changed_files
        if not _matches_prefix(_normalise_relpath(path), task.protected_paths)
    ]
    public_candidate = MergeabilityCandidate(
        candidate_id=candidate.candidate_id,
        task_id=candidate.task_id,
        files=public_files,
        changed_files=tuple(public_changed_files),
        provenance={},
        generator_metadata={},
        candidate_ref=candidate.candidate_ref,
        candidate_hash=candidate.candidate_hash,
    )
    public_input_payload = _supervisor_review_public_input(
        task=task,
        candidate=public_candidate,
    )
    public_input_violations = _public_input_oracle_refs(public_input_payload)
    failures: list[str] = []
    gaming_flags: list[str] = []
    probe_results: list[dict[str, Any]] = []
    command_results: list[CommandResult] = []
    worktree_protected_refs: tuple[str, ...] = ()
    worktree_hash = ""

    if protected_candidate_paths or public_input_violations:
        failures.append("oracle_isolation_violation")
        gaming_flags.append("oracle_isolation_violation")
        probe_results.append({
            "probe_id": "oracle_isolation",
            "status": "red",
            "reason": "oracle_isolation_violation",
        })

    with tempfile.TemporaryDirectory(prefix="mergeability-public-review-") as temp_dir:
        temp_root = Path(temp_dir)
        candidate_worktree = temp_root / "candidate"
        reverse_worktree = temp_root / "reverse"
        _copy_public_fixture_tree(fixture_root, candidate_worktree, protected_paths=task.protected_paths)
        _copy_public_fixture_tree(fixture_root, reverse_worktree, protected_paths=task.protected_paths)
        worktree_protected_refs = _protected_refs_in_tree(
            candidate_worktree,
            protected_paths=task.protected_paths,
        )
        if worktree_protected_refs:
            failures.append("oracle_isolation_violation")
            gaming_flags.append("oracle_isolation_violation")
            probe_results.append({
                "probe_id": "oracle_isolation",
                "status": "red",
                "reason": "protected_material_in_public_worktree",
            })

        if "oracle_isolation_violation" not in failures:
            _apply_candidate_files(candidate_worktree, public_candidate.files)
            candidate_test_files = _candidate_test_files(public_candidate.files)
            _apply_candidate_files(reverse_worktree, candidate_test_files)

            scope_failures = _scope_failures(task, public_candidate)
            failures.extend(scope_failures)
            if scope_failures:
                probe_results.append({
                    "probe_id": "mutable_surface",
                    "status": "red",
                    "reason": "scope_failures",
                    "failure_count": len(scope_failures),
                })

            if not candidate_test_files:
                failures.append("public_review:no_candidate_tests_submitted")
                probe_results.append({
                    "probe_id": "candidate_tests",
                    "status": "red",
                    "reason": "no_candidate_tests_submitted",
                })

            candidate_precheck = _public_test_target_failures(
                task.reverse_test_commands,
                worktree=candidate_worktree,
                label="candidate tests",
            )
            failures.extend(candidate_precheck)
            candidate_results = [
                _run_command(command, cwd=candidate_worktree, timeout_s=timeout_s, name="public_candidate_test")
                for command in task.reverse_test_commands
                if candidate_test_files and not candidate_precheck
            ]
            command_results.extend(candidate_results)

            reverse_precheck = _reverse_test_precheck_failures(
                task.reverse_test_commands,
                reverse_worktree=reverse_worktree,
                candidate_test_files=candidate_test_files,
            )
            failures.extend(reverse_precheck)
            reverse_results = [
                _run_command(
                    command,
                    cwd=reverse_worktree,
                    timeout_s=timeout_s,
                    name="public_reverse_test",
                    expected_failure=True,
                )
                for command in task.reverse_test_commands
                if candidate_test_files and not reverse_precheck
            ]
            command_results.extend(reverse_results)

            lint_results = [
                _run_command(command, cwd=candidate_worktree, timeout_s=timeout_s, name="public_lint_build")
                for command in task.lint_build_commands
            ]
            command_results.extend(lint_results)

            failures.extend(_command_failures("candidate tests", candidate_results, expect_pass=True))
            failures.extend(_command_failures("reverse tests", reverse_results, expect_pass=False))
            failures.extend(_command_failures("lint/build", lint_results, expect_pass=True))
            _append_command_probe(probe_results, "candidate_tests", candidate_results, empty_ok=False)
            _append_command_probe(probe_results, "reverse_tests", reverse_results, empty_ok=not bool(candidate_test_files))
            _append_command_probe(probe_results, "lint_build", lint_results, empty_ok=True)

        worktree_hash = _hash_tree(candidate_worktree)

    clean_failures = sorted(set(failures))
    accept = not clean_failures
    if accept:
        probe_results.append({
            "probe_id": "supervisor_candidate_review",
            "status": "green",
            "reason": "public_review_passed",
        })
    return {
        "schema_version": SUPERVISOR_REVIEW_RESULT_SCHEMA_VERSION,
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "decision": "accept" if accept else "reject",
        "accept": accept,
        "reasons": clean_failures,
        "probe_results": probe_results,
        "command_results": [result.to_payload() for result in command_results],
        "reviewer_packet_refs": [],
        "evidence_refs": [
            f"mergeability_task_public:{task.fixture_path}",
            f"mergeability_candidate_public:{candidate.candidate_ref or candidate.candidate_id}",
        ],
        "candidate_review_worktree_hash": worktree_hash,
        "public_input_hash": _sha256_json(public_input_payload),
        "public_input_payload": public_input_payload,
        "protected_paths_present_in_review_worktree": list(worktree_protected_refs),
        "gaming_flags": sorted(set(gaming_flags)),
        "decision_source": "supervisor_candidate_review",
        "oracle_coupled": False,
    }


def _review_mergeability_candidate_full_gate(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    public_review: Mapping[str, Any],
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
) -> dict[str, Any]:
    packet = _build_full_gate_reviewer_packet(
        task=task,
        candidate=candidate,
        public_review=public_review,
    )
    leaks = _public_input_oracle_refs(packet)
    panel_fn = reviewer_panel or _default_unavailable_reviewer_panel
    raw_panel = panel_fn(packet) if not leaks else {
        "decision": "unavailable",
        "available": False,
        "reason": "oracle_isolation_violation",
        "blocking_findings": leaks,
    }
    panel = _normalise_reviewer_panel_result(raw_panel)
    unavailable = bool(leaks) or not panel["available"]
    panel_accept = bool(panel["available"] and panel["decision"] == "accept")
    public_accept = bool(public_review.get("accept"))
    accept = bool(public_accept and panel_accept and not unavailable)
    gaming_flags = set(public_review.get("gaming_flags") or [])
    if leaks:
        gaming_flags.add("oracle_isolation_violation")
    if unavailable:
        gaming_flags.add("reviewer_panel_unavailable")
    packet_ref = {
        "packet_id": packet["packet_id"],
        "packet_sha256": packet["packet_sha256"],
        "source": "supervisor",
        "evidence_grade": "runtime_native",
    }
    return {
        "schema_version": SUPERVISOR_FULL_GATE_RESULT_SCHEMA_VERSION,
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "decision": "accept" if accept else "reject",
        "accept": accept,
        "available": not unavailable,
        "unavailable": unavailable,
        "unavailable_reason": panel["reason"] if unavailable else "",
        "public_review_accept": public_accept,
        "panel_accept": panel_accept,
        "panel_decision": panel["decision"],
        "panel_result": panel,
        "reviewer_packet": packet,
        "reviewer_packet_refs": [packet_ref],
        "reviewer_packet_sha256": packet["packet_sha256"],
        "evidence_refs": [
            f"mergeability_full_gate_packet:{packet['packet_id']}:{packet['packet_sha256']}",
        ],
        "gaming_flags": sorted(gaming_flags),
        "decision_source": "supervisor_candidate_review+independent_reviewer_panel",
        "oracle_coupled": False,
    }


def _build_full_gate_reviewer_packet(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    public_review: Mapping[str, Any],
) -> dict[str, Any]:
    public_files = {
        path: content
        for path, content in candidate.files.items()
        if not _matches_prefix(_normalise_relpath(path), task.protected_paths)
    }
    public_changed_files = [
        path for path in candidate.changed_files
        if not _matches_prefix(_normalise_relpath(path), task.protected_paths)
    ]
    public_candidate = MergeabilityCandidate(
        candidate_id=candidate.candidate_id,
        task_id=candidate.task_id,
        files=public_files,
        changed_files=tuple(public_changed_files),
        provenance={},
        generator_metadata={},
        candidate_ref=candidate.candidate_ref,
        candidate_hash=candidate.candidate_hash,
    )
    public_input = _supervisor_review_public_input(task=task, candidate=public_candidate)
    packet = {
        "schema_version": SUPERVISOR_FULL_GATE_PACKET_SCHEMA_VERSION,
        "packet_id": f"mergeability-full-gate:{task.task_id}:{candidate.candidate_id}",
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "prompt": task.prompt,
        "public_input_payload": public_input,
        "public_review": {
            "decision": str(public_review.get("decision") or ""),
            "accept": bool(public_review.get("accept")),
            "reasons": list(public_review.get("reasons") or []),
            "probe_results": list(public_review.get("probe_results") or []),
            "command_results": list(public_review.get("command_results") or []),
            "evidence_refs": list(public_review.get("evidence_refs") or []),
            "public_input_hash": str(public_review.get("public_input_hash") or ""),
            "candidate_review_worktree_hash": str(public_review.get("candidate_review_worktree_hash") or ""),
            "gaming_flags": list(public_review.get("gaming_flags") or []),
            "decision_source": str(public_review.get("decision_source") or "supervisor_candidate_review"),
        },
        "review_instructions": {
            "objective": "Independently review whether the public candidate evidence should pass the full supervisor gate.",
            "oracle_isolation": "Do not use hidden tests, oracle labels, final scores, expected outcomes, or protected paths.",
        },
    }
    leaks = _public_input_oracle_refs(packet)
    if leaks:
        packet["oracle_isolation_violations"] = leaks
    packet["packet_sha256"] = _sha256_json({key: value for key, value in packet.items() if key != "packet_sha256"})
    return packet


def _normalise_reviewer_panel_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    decision = str(raw.get("decision") or raw.get("status") or "unavailable")
    if decision == "approved":
        decision = "accept"
    if decision not in {"accept", "revise", "deny", "reject", "unavailable"}:
        decision = "unavailable"
    if decision == "reject":
        decision = "deny"
    available = bool(raw.get("available", decision != "unavailable"))
    reason = str(raw.get("reason") or raw.get("unavailable_reason") or "")
    if not available and not reason:
        reason = "reviewer_panel_unavailable"
    return {
        "schema_version": "supervisor-mergeability-reviewer-panel-result/v1",
        "decision": decision,
        "available": available,
        "reason": reason,
        "reviewer_ids": list(raw.get("reviewer_ids") or []),
        "accepted_reviewers": list(raw.get("accepted_reviewers") or []),
        "blocking_findings": list(raw.get("blocking_findings") or []),
        "missing_reviewers": list(raw.get("missing_reviewers") or []),
    }


def _default_unavailable_reviewer_panel(_packet: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "decision": "unavailable",
        "available": False,
        "reason": "reviewer_panel_not_configured",
    }


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


def _candidate_control_kind(candidate: MergeabilityCandidate) -> str:
    metadata = candidate.generator_metadata
    control = str(metadata.get("control") or metadata.get("control_kind") or "").strip()
    if control:
        return control
    candidate_id = candidate.candidate_id.replace("-", "_")
    if candidate_id in POSITIVE_CONTROL_KINDS | NEGATIVE_CONTROL_KINDS:
        return candidate_id
    return "candidate"


def _candidate_expected_outcome(candidate: MergeabilityCandidate) -> str:
    raw = str(candidate.generator_metadata.get("expected_outcome") or "").strip().lower()
    if raw in {"pass", "passed", "accept", "accepted", "true_positive"}:
        return "pass"
    if raw in {"fail", "failed", "reject", "rejected", "false_positive"}:
        return "fail"
    control = _candidate_control_kind(candidate)
    if control in POSITIVE_CONTROL_KINDS:
        return "pass"
    return "fail"


def _baseline_accepts(candidate: MergeabilityCandidate) -> bool:
    metadata = candidate.generator_metadata
    provenance = candidate.provenance
    explicit = metadata.get("baseline_accept", provenance.get("baseline_accept"))
    if isinstance(explicit, bool):
        return explicit
    for key in ("declared_success", "visible_tests_passed", "self_reported_success"):
        value = metadata.get(key, provenance.get(key))
        if isinstance(value, bool) and value:
            return True
    return _candidate_expected_outcome(candidate) == "pass"


def _calibration_non_applyable_flags(results: list[dict[str, Any]]) -> list[str]:
    if not results:
        return ["empty_calibration_results"]
    flags: list[str] = []
    if all(float(result.get("final_score") or 0.0) >= 1.0 for result in results):
        flags.append("saturated_all_ones")
    if not any(
        result["expected_outcome"] == "pass" and result["observed_outcome"] == "pass"
        for result in results
    ):
        flags.append("no_passing_positive_control")
    if not any(
        result["expected_outcome"] == "fail" and result["observed_outcome"] == "fail"
        for result in results
    ):
        flags.append("no_failing_negative_control")
    return flags


def _summarize_acceptance_arm(
    rows: list[dict[str, Any]],
    *,
    arm: str,
    arm_role: str,
    decision_source: str,
    oracle_coupled: bool,
) -> dict[str, Any]:
    accept_key = f"{arm}_accept"
    unavailable_key = f"{arm}_unavailable"
    false_accept_denominator = sum(1 for row in rows if not row["oracle_accept"])
    true_accept_denominator = sum(1 for row in rows if row["oracle_accept"])
    false_accept_count = sum(1 for row in rows if row[accept_key] and not row["oracle_accept"])
    true_accept_count = sum(1 for row in rows if row[accept_key] and row["oracle_accept"])
    false_reject_count = sum(1 for row in rows if not row[accept_key] and row["oracle_accept"])
    unavailable_count = sum(1 for row in rows if bool(row.get(unavailable_key)))
    return {
        "arm": arm,
        "arm_role": arm_role,
        "decision_source": decision_source,
        "oracle_coupled": oracle_coupled,
        "candidate_count": len(rows),
        "available_count": len(rows) - unavailable_count,
        "unavailable_count": unavailable_count,
        "availability_status": "unavailable" if rows and unavailable_count == len(rows) else "available",
        "accepted_count": sum(1 for row in rows if row[accept_key]),
        "rejected_count": sum(1 for row in rows if not row[accept_key]),
        "false_accept_count": false_accept_count,
        "false_accept_denominator": false_accept_denominator,
        "false_accept_rate": _rate(false_accept_count, false_accept_denominator),
        "true_accept_count": true_accept_count,
        "true_accept_denominator": true_accept_denominator,
        "true_accept_rate": _rate(true_accept_count, true_accept_denominator),
        "false_reject_count": false_reject_count,
        "false_reject_denominator": true_accept_denominator,
        "false_reject_rate": _rate(false_reject_count, true_accept_denominator),
        "cost_usd": 0.0,
    }


def _supervisor_review_public_input(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
) -> dict[str, Any]:
    candidate_test_files = _candidate_test_files(candidate.files)
    return {
        "schema_version": SUPERVISOR_REVIEW_INPUT_SCHEMA_VERSION,
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "prompt": task.prompt,
        "allowed_mutable_paths": list(task.allowed_mutable_paths),
        "scope_constraints": list(task.scope_constraints),
        "protected_path_policy_sha256": _sha256_json(list(task.protected_paths)),
        "protected_path_policy_count": len(task.protected_paths),
        "changed_files": list(candidate.changed_files),
        "candidate_files": [
            {"path": path, "content": content}
            for path, content in sorted(candidate.files.items())
        ],
        "candidate_submitted_tests": [
            {"path": path, "content": content}
            for path, content in sorted(candidate_test_files.items())
        ],
        "visible_commands": {
            "candidate_test_commands": [list(command) for command in task.reverse_test_commands],
            "reverse_test_commands": [list(command) for command in task.reverse_test_commands],
            "lint_build_commands": [list(command) for command in task.lint_build_commands],
        },
    }


def _public_input_oracle_refs(value: Any, *, path: str = "") -> list[str]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text in ORACLE_REVIEW_FORBIDDEN_KEYS:
                refs.append(nested_path)
            refs.extend(_public_input_oracle_refs(nested, path=nested_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            refs.extend(_public_input_oracle_refs(nested, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        for marker in ORACLE_REVIEW_FORBIDDEN_TEXT:
            if marker in value:
                refs.append(path or marker)
                break
    return sorted(set(refs))


def _copy_public_fixture_tree(
    source_root: Path,
    target_root: Path,
    *,
    protected_paths: tuple[str, ...],
) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_root.rglob("*")):
        rel = _normalise_relpath(source.relative_to(source_root).as_posix())
        if _matches_prefix(rel, protected_paths):
            continue
        target = target_root / rel
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _protected_refs_in_tree(root: Path, *, protected_paths: tuple[str, ...]) -> tuple[str, ...]:
    refs = []
    for path in root.rglob("*"):
        rel = _normalise_relpath(path.relative_to(root).as_posix())
        if _matches_prefix(rel, protected_paths):
            refs.append(rel)
    return tuple(sorted(refs))


def _hash_tree(root: Path) -> str:
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = _normalise_relpath(path.relative_to(root).as_posix())
        entries.append({
            "path": rel,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    return _sha256_json(entries)


def _public_test_target_failures(
    commands: tuple[tuple[str, ...], ...],
    *,
    worktree: Path,
    label: str,
) -> list[str]:
    failures: list[str] = []
    for command in commands:
        for target in _pytest_target_paths(command):
            if not (worktree / target).exists():
                failures.append(f"{label}:missing_candidate_test_target:{target}")
    return sorted(set(failures))


def _append_command_probe(
    probes: list[dict[str, Any]],
    probe_id: str,
    results: list[CommandResult],
    *,
    empty_ok: bool,
) -> None:
    if not results:
        probes.append({
            "probe_id": probe_id,
            "status": "green" if empty_ok else "red",
            "reason": "not_required" if empty_ok else "not_executed",
        })
        return
    failures = [result for result in results if result.status != "passed"]
    probes.append({
        "probe_id": probe_id,
        "status": "red" if failures else "green",
        "reason": "command_failed" if failures else "command_passed",
        "command_count": len(results),
    })


def _oracle_agreement(rows: list[dict[str, Any]], *, arm: str) -> dict[str, Any]:
    accept_key = _accept_key_for_arm(arm)
    agreement_count = sum(1 for row in rows if bool(row[accept_key]) == bool(row["oracle_accept"]))
    return {
        "agreement_count": agreement_count,
        "candidate_count": len(rows),
        "agreement_rate": _rate(agreement_count, len(rows)),
    }


def _accept_key_for_arm(arm: str) -> str:
    if arm == "baseline":
        return "baseline_accept"
    if arm == "oracle_ceiling":
        return "oracle_ceiling_accept"
    if arm == "supervisor_full_gate":
        return "supervisor_full_gate_accept"
    return "supervisor_candidate_review_accept"


def _should_trip_perfect_agreement(rows: list[dict[str, Any]], agreement: Mapping[str, Any]) -> bool:
    if len(rows) < 2:
        return False
    has_positive = any(row["oracle_accept"] for row in rows)
    has_negative = any(not row["oracle_accept"] for row in rows)
    return bool(has_positive and has_negative and float(agreement.get("agreement_rate") or 0.0) >= 1.0)


def _false_accept_at_matched_true_accept(
    *,
    baseline: Mapping[str, Any],
    supervisor: Mapping[str, Any],
) -> dict[str, Any]:
    denominator = int(supervisor.get("true_accept_denominator") or 0)
    if denominator < 2:
        return {
            "status": "insufficient_candidate_pool",
            "reason": "requires_at_least_two_oracle_positive_candidates",
        }
    baseline_true = float(baseline.get("true_accept_rate") or 0.0)
    supervisor_true = float(supervisor.get("true_accept_rate") or 0.0)
    if baseline_true != supervisor_true:
        return {
            "status": "not_matched",
            "baseline_true_accept_rate": baseline_true,
            "supervisor_true_accept_rate": supervisor_true,
        }
    return {
        "status": "computed",
        "matched_true_accept_rate": supervisor_true,
        "baseline_false_accept_rate": float(baseline.get("false_accept_rate") or 0.0),
        "supervisor_false_accept_rate": float(supervisor.get("false_accept_rate") or 0.0),
        "false_accept_delta": round(
            float(supervisor.get("false_accept_rate") or 0.0)
            - float(baseline.get("false_accept_rate") or 0.0),
            6,
        ),
    }


def _panel_marginal_delta_at_matched_true_accept(
    *,
    public_review: Mapping[str, Any],
    full_gate: Mapping[str, Any],
) -> dict[str, Any]:
    unavailable_count = int(full_gate.get("unavailable_count") or 0)
    if unavailable_count:
        return {
            "status": "unavailable",
            "reason": "reviewer_panel_unavailable",
            "unavailable_count": unavailable_count,
        }
    denominator = int(full_gate.get("true_accept_denominator") or 0)
    if denominator < 2:
        return {
            "status": "insufficient_candidate_pool",
            "reason": "requires_at_least_two_oracle_positive_candidates",
        }
    public_true = float(public_review.get("true_accept_rate") or 0.0)
    full_true = float(full_gate.get("true_accept_rate") or 0.0)
    if public_true != full_true:
        return {
            "status": "not_matched",
            "reason": "public_review_and_full_gate_true_accept_rates_differ",
            "public_review_true_accept_rate": public_true,
            "supervisor_full_gate_true_accept_rate": full_true,
        }
    return {
        "status": "computed",
        "false_accept_rate_delta": round(
            float(full_gate.get("false_accept_rate") or 0.0)
            - float(public_review.get("false_accept_rate") or 0.0),
            6,
        ),
        "public_review_false_accept_rate": public_review.get("false_accept_rate"),
        "supervisor_full_gate_false_accept_rate": full_gate.get("false_accept_rate"),
        "matched_true_accept_rate": full_true,
    }


def _rate(count: int, denominator: int) -> float:
    return round(count / denominator, 6) if denominator else 0.0


def _export_calibration_artifacts(
    output_dir: Path,
    *,
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "corpus_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "calibration_summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _export_paired_acceptance_artifacts(
    output_dir: Path,
    *,
    manifest: dict[str, Any],
    calibration: dict[str, Any],
    report: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    _export_calibration_artifacts(output_dir, manifest=manifest, summary=calibration)
    (output_dir / "paired_acceptance_report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "per_task_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


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
