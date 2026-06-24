"""Deterministic held-out mergeability bench primitives."""
from __future__ import annotations

import concurrent.futures
import html as _html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    invoke_cursor_agent,
)
from .dual_agent import ProbeResult
from .reviewer_registry import (
    ReviewerAdapter,
    ReviewerSpec,
    configured_reviewers,
    evaluate_reviewer_panel,
    independent_reviewer_results_from_review_results,
)
from .redaction import redact


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
MERGEABILITY_LIVE_GENERATION_REPORT_SCHEMA_VERSION = "supervisor-mergeability-live-generation-report/v1"
MERGEABILITY_LIVE_GENERATOR_INPUT_SCHEMA_VERSION = "supervisor-mergeability-live-generator-input/v1"
MERGEABILITY_POWERED_FACTORIAL_REPORT_SCHEMA_VERSION = "supervisor-mergeability-powered-factorial-report/v1"

FACTORIAL_ARM_DEFINITIONS = {
    "single_agent_baseline": {
        "arm_role": "single_agent_baseline",
        "decision_source": "single_agent_candidate_generation",
        "oracle_coupled": False,
    },
    "same_model_multi_agent": {
        "arm_role": "same_model_multi_agent",
        "decision_source": "same_model_multi_agent_structure",
        "oracle_coupled": False,
    },
    "hetero_multi_reviewer": {
        "arm_role": "hetero_multi_reviewer",
        "decision_source": "heterogeneous_reviewer_structure",
        "oracle_coupled": False,
    },
    "runtime_evidence_floor": {
        "arm_role": "runtime_evidence_floor",
        "decision_source": "supervisor_runtime_evidence_floor",
        "oracle_coupled": False,
    },
    "full_supervisor_stack": {
        "arm_role": "full_supervisor_stack",
        "decision_source": "runtime_evidence_floor+independent_reviewer_panel",
        "oracle_coupled": False,
    },
    "oracle_ceiling": {
        "arm_role": "oracle_ceiling",
        "decision_source": "oracle_final_score",
        "oracle_coupled": True,
    },
}

PRODUCED_BASELINE_DECISION_SOURCES = frozenset({
    "produced_single_agent_baseline",
    "replayed_single_agent_baseline",
    "single_agent_candidate_generation",
})

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
FIXTURE_DIAGNOSTIC_GROWTH_KIND = "diagnostic_positive_growth"
FIXTURE_DIAGNOSTIC_GROWTH_SOURCE_SLICE = "Slice 1A"
FIXTURE_DIAGNOSTIC_REPORT_SCHEMA_VERSION = "supervisor-mergeability-fixture-diagnostic-report/v1"
SLICE_1A_S_PROBE_TRUE_ACCEPT_RATE = 1.0
SLICE_1A_S_FULL_TRUE_ACCEPT_RATE = 0.0
SLICE_1A_FULL_GATE_MATCHED_TAR_STATUS = "not_matched"
SLICE_1A_POSITIVE_CONTROL_COUNT = 3
FIXTURE_DIAGNOSTIC_GROWTH_RATIONALE_TEXT = (
    "Slice 1A full-gate matched-TAR was not_matched because S_full true-accept "
    "rate was 0 while S_probe true-accept rate was 1 on only 3 oracle-positive "
    "candidates; grow diagnostic oracle-positive coverage so the panel "
    "marginal becomes interpretable without claiming improvement."
)
PUBLIC_PASS_HIDDEN_FAIL_CONTROL_KINDS = frozenset({"hidden_behavior_miss"})
REQUIRED_CALIBRATION_CONTROL_KINDS = frozenset({
    "noop",
    "known_bad",
    "known_good",
    "missing_regression_test",
    "tautological_test",
    "protected_path_escape",
    "scope_escape",
})
_GRADE_RESULT_CACHE: dict[tuple[str, ...], "MergeabilityResult"] = {}


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
    split: str = "held_out"
    task_class: str = ""
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
            split=str(raw.get("split") or "held_out"),
            task_class=str(raw.get("task_class") or task_id),
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
            "split": self.split,
            "task_class": self.task_class,
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
    cache_key = _grade_cache_key(
        task=task,
        candidate=candidate,
        fixture_root=fixture_root,
        timeout_s=timeout_s,
    )
    cached = _GRADE_RESULT_CACHE.get(cache_key)
    if cached is not None:
        if output_dir is not None:
            _write_result_artifact(cached, output_dir=Path(output_dir).expanduser())
        return cached

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
    _GRADE_RESULT_CACHE[cache_key] = result
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
    tasks_by_id = {task.task_id: task for task in tasks}

    task_entries: list[dict[str, Any]] = []
    included_task_ids: list[str] = []
    for task in tasks:
        if candidate_paths and not candidates_by_task.get(task.task_id):
            continue
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
        false_accept_traps = [
            candidate.candidate_id
            for candidate in task_candidates
            if _candidate_is_public_pass_hidden_fail_trap(candidate)
        ]
        missing_requirements = _heldout_task_missing_requirements(
            positive=positive,
            negative=negative,
            false_accept_traps=false_accept_traps,
        )
        grade_eligible = bool(positive and negative)
        calibration_eligible = not missing_requirements
        if grade_eligible:
            included_task_ids.append(task.task_id)
        task_entries.append({
            "task_id": task.task_id,
            "task_class": task.task_class,
            "split": task.split,
            "task_hash": task.task_hash,
            "fixture_path": task.fixture_path,
            "repo_fixture_ref": task.repo_fixture_ref,
            "candidate_count": len(task_candidates),
            "positive_controls": positive,
            "negative_controls": negative,
            "false_accept_traps": false_accept_traps,
            "control_kinds": sorted({_candidate_control_kind(candidate) for candidate in task_candidates}),
            "grade_eligible": grade_eligible,
            "calibration_eligible": calibration_eligible,
            "missing_requirements": missing_requirements,
            "excluded_reason": "" if calibration_eligible else "requires_" + "_and_".join(missing_requirements),
        })

    candidate_entries = [
        {
            "candidate_id": candidate.candidate_id,
            "task_id": candidate.task_id,
            "task_class": tasks_by_id[candidate.task_id].task_class
            if candidate.task_id in tasks_by_id else candidate.task_id,
            "split": tasks_by_id[candidate.task_id].split
            if candidate.task_id in tasks_by_id else "held_out",
            "candidate_ref": candidate.candidate_ref,
            "candidate_hash": candidate.candidate_hash,
            "control_kind": _candidate_control_kind(candidate),
            "expected_outcome": _candidate_expected_outcome(candidate),
            "baseline_accept": _baseline_accepts(candidate),
            "false_accept_trap": _candidate_is_public_pass_hidden_fail_trap(candidate),
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
        "split_policy": {
            "selection_splits": ["calibration", "dev"],
            "reporting_split": "held_out",
            "heldout_excluded_from_variant_selection": True,
        },
        "excluded_task_ids": [
            entry["task_id"] for entry in task_entries if not entry["calibration_eligible"]
        ],
        "tasks": task_entries,
        "candidates": candidate_entries,
        "control_coverage_by_task_class": _control_coverage_by_task_class(task_entries, candidate_entries),
        "fixture_growth_rationale": _build_fixture_growth_rationale(candidates),
        "manifest_sha256": "",
    }
    manifest["manifest_sha256"] = _sha256_json({
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    })
    return manifest


def _control_coverage_by_task_class(
    task_entries: list[dict[str, Any]],
    candidate_entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    tasks_by_class: dict[str, list[dict[str, Any]]] = {}
    for task in task_entries:
        if task.get("split") != "held_out":
            continue
        tasks_by_class.setdefault(str(task["task_class"]), []).append(task)

    candidates_by_class: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidate_entries:
        if candidate.get("split") != "held_out":
            continue
        candidates_by_class.setdefault(str(candidate["task_class"]), []).append(candidate)

    coverage: dict[str, dict[str, Any]] = {}
    for task_class in sorted(tasks_by_class):
        class_tasks = sorted(tasks_by_class[task_class], key=lambda item: str(item["task_id"]))
        class_candidates = sorted(
            candidates_by_class.get(task_class, []),
            key=lambda item: (str(item["task_id"]), str(item["candidate_id"])),
        )
        positive = [
            candidate for candidate in class_candidates
            if candidate.get("expected_outcome") == "pass"
        ]
        negative = [
            candidate for candidate in class_candidates
            if candidate.get("expected_outcome") == "fail"
        ]
        traps = [
            candidate for candidate in class_candidates
            if candidate.get("false_accept_trap")
        ]
        missing = []
        if not positive:
            missing.append("positive_control")
        if not negative:
            missing.append("negative_control")
        if not traps:
            missing.append("public_pass_hidden_fail_trap")
        coverage[task_class] = {
            "task_class": task_class,
            "split": "held_out",
            "task_count": len(class_tasks),
            "task_ids": [str(task["task_id"]) for task in class_tasks],
            "candidate_count": len(class_candidates),
            "positive_control_count": len(positive),
            "negative_control_count": len(negative),
            "false_accept_trap_count": len(traps),
            "control_kinds": sorted({
                str(candidate.get("control_kind") or "")
                for candidate in class_candidates
                if candidate.get("control_kind")
            }),
            "missing_control_kinds": missing,
        }
    return coverage


def _heldout_coverage_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    by_task_class = dict(manifest.get("control_coverage_by_task_class") or {})
    return {
        "split": "held_out",
        "task_class_count": len(by_task_class),
        "task_count": sum(int(entry.get("task_count") or 0) for entry in by_task_class.values()),
        "candidate_count": sum(int(entry.get("candidate_count") or 0) for entry in by_task_class.values()),
        "split_policy": dict(manifest.get("split_policy") or {}),
        "by_task_class": by_task_class,
    }


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
    for entry in manifest["tasks"]:
        if entry.get("split") != "held_out":
            continue
        missing = list(entry.get("missing_requirements") or [])
        if missing:
            errors.append(
                f"task {entry['task_id']} missing held-out controls: "
                + ", ".join(missing)
            )

    observed_control_kinds = {_candidate_control_kind(candidate) for candidate in candidates}
    missing_control_kinds = sorted(REQUIRED_CALIBRATION_CONTROL_KINDS - observed_control_kinds)
    if missing_control_kinds:
        errors.append("missing required calibration controls: " + ", ".join(missing_control_kinds))
    control_coverage_by_task_class = dict(manifest.get("control_coverage_by_task_class") or {})
    for task_class, coverage in sorted(control_coverage_by_task_class.items()):
        missing_class_controls = list(coverage.get("missing_control_kinds") or [])
        if missing_class_controls:
            errors.append(
                f"task_class {task_class} missing held-out controls: "
                + ", ".join(missing_class_controls)
            )

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
        "control_coverage_by_task_class": control_coverage_by_task_class,
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
    reviewer_panel_mode: str = "custom",
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
    single_agent_baseline_decisions: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if reviewer_panel_mode not in {"custom", "configured"}:
        raise MergeabilityBenchError(
            f"unknown reviewer_panel_mode: {reviewer_panel_mode!r}"
        )
    if reviewer_panel_mode == "configured" and reviewer_panel is None:
        reviewer_panel = build_configured_reviewer_panel(configured_reviewer_panel_options)
    codex_only_calibration_active = bool(
        configured_reviewer_panel_options is not None
        and getattr(configured_reviewer_panel_options, "codex_only_calibration", False)
    )
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
    task_hashes = {entry["task_id"]: entry["task_hash"] for entry in manifest["tasks"]}
    candidate_hashes = {
        (entry["task_id"], entry["candidate_id"]): entry["candidate_hash"]
        for entry in manifest["candidates"]
    }

    rows: list[dict[str, Any]] = []
    for result in calibration["results"]:
        candidate = candidates[result["candidate_id"]]
        supervisor_review = public_reviews[result["candidate_id"]]
        full_gate_review = _review_mergeability_candidate_full_gate(
            task=tasks[candidate.task_id],
            candidate=candidate,
            public_review=supervisor_review,
            reviewer_panel=reviewer_panel,
            codex_only_calibration=codex_only_calibration_active,
        )
        oracle_accept = float(result["final_score"]) >= 1.0
        metadata_accept_all_baseline_accept = _baseline_accepts(candidate)
        baseline_raw = (
            single_agent_baseline_decisions.get(candidate.candidate_id)
            if single_agent_baseline_decisions is not None
            else None
        )
        single_agent_baseline_decision = _resolve_powered_baseline_decision(
            raw=baseline_raw,
            expected_candidate_artifact_hash=candidate_hashes[
                (result["task_id"], result["candidate_id"])
            ],
            expected_candidate_id=candidate.candidate_id,
        )
        single_agent_baseline_available = not bool(single_agent_baseline_decision["unavailable"])
        single_agent_baseline_accept = bool(single_agent_baseline_decision["accept"])
        supervisor_accept = bool(supervisor_review["accept"])
        supervisor_full_gate_accept = bool(full_gate_review["accept"])
        panel_result = (
            full_gate_review.get("panel_result")
            if isinstance(full_gate_review.get("panel_result"), Mapping)
            else {}
        )
        if not codex_only_calibration_active:
            codex_only_calibration_unavailable = True
        elif not supervisor_accept:
            # The single-reviewer calibration measures the panel's marginal
            # effect after S_probe. Public rejects short-circuit before any
            # reviewer evidence is needed.
            codex_only_calibration_unavailable = False
        else:
            codex_only_calibration_unavailable = not bool(
                "oracle_isolation_violation" not in full_gate_review.get("gaming_flags", [])
                and panel_result.get("available")
                and not panel_result.get("missing_reviewers")
            )
        codex_only_calibration_accept = bool(
            supervisor_accept
            and not codex_only_calibration_unavailable
            and panel_result.get("decision") == "accept"
        )
        row = {
            "task_id": result["task_id"],
            "task_hash": task_hashes[result["task_id"]],
            "task_class": tasks[candidate.task_id].task_class,
            "split": tasks[candidate.task_id].split,
            "candidate_id": result["candidate_id"],
            "candidate_hash": candidate_hashes[(result["task_id"], result["candidate_id"])],
            "control_kind": result["control_kind"],
            "expected_outcome": result["expected_outcome"],
            "oracle_accept": oracle_accept,
            "baseline_accept": metadata_accept_all_baseline_accept,
            "metadata_accept_all_baseline_accept": metadata_accept_all_baseline_accept,
            "metadata_accept_all_baseline_unavailable": False,
            "single_agent_baseline_accept": single_agent_baseline_accept,
            "single_agent_baseline_unavailable": not single_agent_baseline_available,
            "supervisor_accept": supervisor_accept,
            "supervisor_candidate_review_accept": supervisor_accept,
            "supervisor_full_gate_accept": supervisor_full_gate_accept,
            "supervisor_full_gate_unavailable": bool(full_gate_review["unavailable"]),
            "codex_only_calibration_panel_accept": codex_only_calibration_accept,
            "codex_only_calibration_panel_unavailable": codex_only_calibration_unavailable,
            "oracle_ceiling_accept": oracle_accept,
            "baseline_false_accept": metadata_accept_all_baseline_accept and not oracle_accept,
            "metadata_accept_all_baseline_false_accept": (
                metadata_accept_all_baseline_accept and not oracle_accept
            ),
            "single_agent_baseline_false_accept": (
                single_agent_baseline_available and single_agent_baseline_accept and not oracle_accept
            ),
            "supervisor_false_accept": supervisor_accept and not oracle_accept,
            "supervisor_candidate_review_false_accept": supervisor_accept and not oracle_accept,
            "supervisor_full_gate_false_accept": supervisor_full_gate_accept and not oracle_accept,
            "codex_only_calibration_panel_false_accept": (
                codex_only_calibration_accept and not oracle_accept
            ),
            "oracle_ceiling_false_accept": oracle_accept and not oracle_accept,
            "baseline_false_reject": (not metadata_accept_all_baseline_accept) and oracle_accept,
            "metadata_accept_all_baseline_false_reject": (
                (not metadata_accept_all_baseline_accept) and oracle_accept
            ),
            "single_agent_baseline_false_reject": (
                single_agent_baseline_available
                and (not single_agent_baseline_accept)
                and oracle_accept
            ),
            "supervisor_false_reject": (not supervisor_accept) and oracle_accept,
            "supervisor_candidate_review_false_reject": (not supervisor_accept) and oracle_accept,
            "supervisor_full_gate_false_reject": (not supervisor_full_gate_accept) and oracle_accept,
            "codex_only_calibration_panel_false_reject": (
                (not codex_only_calibration_accept)
                and (not codex_only_calibration_unavailable)
                and oracle_accept
            ),
            "oracle_ceiling_false_reject": (not oracle_accept) and oracle_accept,
            "receipt_id": result["receipt_id"],
            "receipt": result["receipt"],
            "blocker_status": result["blocker_status"],
            "failures": result["failures"],
            "baseline_decision_source": "candidate_self_report",
            "baseline_evidence_kind": "metadata_calibration",
            "metadata_accept_all_baseline_decision_source": "candidate_self_report",
            "metadata_accept_all_baseline_evidence_kind": "metadata_calibration",
            "single_agent_baseline_candidate_id": single_agent_baseline_decision["candidate_id"],
            "single_agent_baseline_decision_source": single_agent_baseline_decision["decision_source"],
            "single_agent_baseline_evidence_kind": single_agent_baseline_decision["evidence_kind"],
            "single_agent_baseline_candidate_artifact_hash": (
                single_agent_baseline_decision["candidate_artifact_hash"]
            ),
            "single_agent_baseline_producer": dict(single_agent_baseline_decision["producer"]),
            "single_agent_baseline_prompt_sha256": single_agent_baseline_decision["prompt_sha256"],
            "single_agent_baseline_unavailable_reason": (
                single_agent_baseline_decision["unavailable_reason"]
            ),
            "oracle_ceiling_decision_source": "oracle_final_score",
            "supervisor_decision_source": "supervisor_candidate_review",
            "supervisor_candidate_review_decision_source": "supervisor_candidate_review",
            "supervisor_full_gate_decision_source": "supervisor_full_gate",
            "codex_only_calibration_panel_decision_source": (
                "codex_only_single_reviewer_calibration"
            ),
            "supervisor_review": supervisor_review,
            "supervisor_full_gate_review": full_gate_review,
            "supervisor_full_gate_reviewer_results": list(
                full_gate_review.get("reviewer_results") or []
            ),
            "supervisor_full_gate_reviewer_rationales": list(
                full_gate_review.get("reviewer_rationales") or []
            ),
            "supervisor_full_gate_reviewer_packet_refs": list(
                full_gate_review.get("reviewer_packet_refs") or []
            ),
            "supervisor_full_gate_reviewer_packet_sha256": str(
                full_gate_review.get("reviewer_packet_sha256") or ""
            ),
            "supervisor_full_gate_reviewer_panel_decision": (
                full_gate_review.get("reviewer_panel_decision")
            ),
            "s_probe_vs_s_full_disagreement": bool(
                full_gate_review.get("s_probe_vs_s_full_disagreement")
            ),
            "panel_quality_label": str(
                full_gate_review.get("panel_quality_label") or "panel_missing_verdict_block"
            ),
            "full_roster_available": bool(full_gate_review.get("full_roster_available")),
            "panel_quality_reject": bool(full_gate_review.get("panel_quality_reject")),
            "panel_missing_verdict_block": bool(
                full_gate_review.get("panel_missing_verdict_block")
            ),
            "codex_only_calibration": bool(full_gate_review.get("codex_only_calibration")),
            "reviewer_infrastructure_diagnostic": dict(
                full_gate_review.get("reviewer_infrastructure_diagnostic") or {}
            ),
        }
        row["is_no_regression_failure"] = bool(
            metadata_accept_all_baseline_accept
            and oracle_accept
            and not supervisor_full_gate_accept
            and not bool(full_gate_review["unavailable"])
        )
        rows.append(row)

    metadata_accept_all_baseline = _summarize_acceptance_arm(
        rows,
        arm="metadata_accept_all_baseline",
        arm_role="metadata_accept_all_baseline",
        decision_source="candidate_self_report",
        oracle_coupled=False,
        evidence_kind="metadata_calibration",
    )
    baseline = dict(metadata_accept_all_baseline)
    baseline["arm"] = "baseline"
    baseline["arm_role"] = "baseline_self_report"
    baseline["legacy_alias_of"] = "metadata_accept_all_baseline"

    def _single_agent_baseline_evidence_kind() -> str:
        available = [row for row in rows if not bool(row.get("single_agent_baseline_unavailable"))]
        source_rows = available if available else rows
        kinds = {str(row.get("single_agent_baseline_evidence_kind") or "missing") for row in source_rows}
        if len(kinds) == 1:
            return next(iter(kinds))
        return "mixed_produced_baseline"

    def _single_agent_baseline_decision_source() -> str:
        available = [row for row in rows if not bool(row.get("single_agent_baseline_unavailable"))]
        if not available:
            return "produced_single_agent_baseline_unavailable"
        sources = {str(row.get("single_agent_baseline_decision_source") or "") for row in available}
        sources.discard("")
        if len(sources) == 1:
            return next(iter(sources))
        return "mixed_produced_single_agent_baseline"

    single_agent_baseline = _summarize_acceptance_arm(
        rows,
        arm="single_agent_baseline",
        arm_role="single_agent_baseline",
        decision_source=_single_agent_baseline_decision_source(),
        oracle_coupled=False,
        evidence_kind=_single_agent_baseline_evidence_kind(),
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
    codex_only_calibration_panel = _summarize_acceptance_arm(
        rows,
        arm="codex_only_calibration_panel",
        arm_role="codex_only_single_reviewer_calibration",
        decision_source="codex_only_single_reviewer_calibration",
        oracle_coupled=False,
        evidence_kind="codex_only_calibration",
    )
    supervisor = dict(supervisor_candidate_review)
    supervisor["arm"] = "supervisor"
    supervisor["legacy_alias_of"] = "supervisor_candidate_review"
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
    heldout_coverage = _heldout_coverage_from_manifest(manifest)
    no_regression_findings = _no_regression_findings(rows, manifest)
    oracle_agreement = {
        "supervisor_candidate_review": _oracle_agreement(rows, arm="supervisor_candidate_review"),
        "supervisor_full_gate": _oracle_agreement(rows, arm="supervisor_full_gate"),
        "codex_only_calibration_panel": _oracle_agreement(
            rows,
            arm="codex_only_calibration_panel",
        ),
        "baseline": _oracle_agreement(rows, arm="baseline"),
        "metadata_accept_all_baseline": _oracle_agreement(rows, arm="metadata_accept_all_baseline"),
        "single_agent_baseline": _oracle_agreement(rows, arm="single_agent_baseline"),
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
    if no_regression_findings:
        gaming_flags.add("no_regression_failure_detected")
    if single_agent_baseline["unavailable_count"]:
        gaming_flags.add("baseline_evidence_unavailable")
    matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=baseline,
        supervisor=supervisor_candidate_review,
    )
    single_agent_baseline_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=single_agent_baseline,
        supervisor=supervisor_candidate_review,
    )
    full_gate_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=baseline,
        supervisor=supervisor_full_gate,
    )
    full_gate_single_agent_baseline_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=single_agent_baseline,
        supervisor=supervisor_full_gate,
    )
    full_roster_available_count = sum(
        1 for row in rows if bool(row.get("full_roster_available"))
    )
    panel_marginal_delta = _panel_marginal_delta_at_matched_true_accept(
        public_review=supervisor_candidate_review,
        full_gate=supervisor_full_gate,
        full_roster_available_count=full_roster_available_count,
    )
    codex_only_roster_available_count = sum(
        1 for row in rows if not bool(row.get("codex_only_calibration_panel_unavailable"))
    )
    if codex_only_calibration_active:
        codex_only_panel_marginal_delta = _panel_marginal_delta_at_matched_true_accept(
            public_review=supervisor_candidate_review,
            full_gate=codex_only_calibration_panel,
            full_roster_available_count=codex_only_roster_available_count,
        )
    else:
        codex_only_panel_marginal_delta = {
            "status": "unavailable",
            "reason": "codex_only_calibration_inactive",
            "full_roster_available_count": 0,
        }
    per_reviewer_arms = _per_reviewer_acceptance_arms(rows)
    inter_reviewer_agreement = _inter_reviewer_agreement(rows)
    reviewer_packet_leak_refs = _reviewer_packet_leak_refs(rows)
    reviewer_provenance = _reviewer_provenance_report(rows)
    generator_disjointness = _generator_disjointness_report(rows)
    metric_splits = _metric_splits(
        rows,
        arms={
            "baseline": baseline,
            "metadata_accept_all_baseline": metadata_accept_all_baseline,
            "single_agent_baseline": single_agent_baseline,
            "supervisor_candidate_review": supervisor_candidate_review,
            "supervisor_full_gate": supervisor_full_gate,
            "codex_only_calibration_panel": codex_only_calibration_panel,
            "oracle_ceiling": oracle_ceiling,
            "supervisor": supervisor,
        },
    )
    roster_selection_guard = _roster_selection_guard(
        inter_reviewer_agreement=inter_reviewer_agreement,
        codex_only_calibration_active=codex_only_calibration_active,
        reviewer_provenance=reviewer_provenance,
        generator_disjointness=generator_disjointness,
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
        "heldout_coverage": heldout_coverage,
        "heldout_coverage_sha256": _sha256_json(heldout_coverage),
        "split_policy": manifest["split_policy"],
        "metric_splits": metric_splits,
        "no_regression_findings": no_regression_findings,
        "no_regression_sha256": _sha256_json(no_regression_findings),
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
            "metadata_accept_all_baseline": metadata_accept_all_baseline,
            "single_agent_baseline": single_agent_baseline,
            "supervisor_candidate_review": supervisor_candidate_review,
            "supervisor_full_gate": supervisor_full_gate,
            "codex_only_calibration_panel": codex_only_calibration_panel,
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
        "single_agent_baseline_false_accept_at_matched_true_accept": (
            single_agent_baseline_matched_true_accept
        ),
        "supervisor_full_gate_false_accept_at_matched_true_accept": full_gate_matched_true_accept,
        "supervisor_full_gate_vs_single_agent_baseline_false_accept_at_matched_true_accept": (
            full_gate_single_agent_baseline_matched_true_accept
        ),
        "primary_comparison_name": "supervisor_vs_single_agent_baseline",
        "primary_matched_true_accept_status": single_agent_baseline_matched_true_accept["status"],
        "comparisons": _paired_acceptance_comparisons(
            legacy_metadata_matched_true_accept=matched_true_accept,
            single_agent_matched_true_accept=single_agent_baseline_matched_true_accept,
            full_gate_metadata_matched_true_accept=full_gate_matched_true_accept,
            full_gate_single_agent_matched_true_accept=full_gate_single_agent_baseline_matched_true_accept,
            single_agent_baseline=single_agent_baseline,
            metadata_accept_all_baseline=metadata_accept_all_baseline,
        ),
        "panel_marginal_delta_at_matched_true_accept": panel_marginal_delta,
        "codex_only_calibration_panel_marginal_delta_at_matched_true_accept": (
            codex_only_panel_marginal_delta
        ),
        "matched_true_accept_status": matched_true_accept["status"],
        "oracle_agreement": oracle_agreement,
        "per_reviewer_arms": per_reviewer_arms,
        "reviewer_provenance": reviewer_provenance,
        "generator_disjointness": generator_disjointness,
        "oracle_isolation": {
            "ok": not reviewer_packet_leak_refs,
            "violations": reviewer_packet_leak_refs,
        },
        "hidden_field_leak_check": {
            "ok": not reviewer_packet_leak_refs,
            "refs": reviewer_packet_leak_refs,
            "scope": "supervisor_full_gate_reviewer_packets",
        },
        "roster_selection_guard": roster_selection_guard,
        "disagreements": disagreements,
        "configured_reviewer_panel": {
            "mode": reviewer_panel_mode,
            "report_mode": (
                "codex_only_calibration" if codex_only_calibration_active else "full_panel"
            ),
            "full_panel_evidence_allowed": not codex_only_calibration_active,
            "codex_only_calibration": codex_only_calibration_active,
            "roster_selection_allowed": False,
            "roster_selection_guard": roster_selection_guard,
            "reviewer_provenance": reviewer_provenance,
            "generator_disjointness": generator_disjointness,
            "self_preference_warnings": list(
                generator_disjointness.get("self_preference_warnings") or []
            ),
            "configured_panel_active": (
                reviewer_panel_mode == "configured"
                and configured_reviewer_panel_options is not None
            ) or (
                reviewer_panel_mode == "configured" and reviewer_panel is not None
            ),
            "s_probe_vs_s_full_disagreement_count": sum(
                1 for row in rows if bool(row.get("s_probe_vs_s_full_disagreement"))
            ),
            "available_full_gate_count": sum(
                1 for row in rows if not bool(row.get("supervisor_full_gate_unavailable"))
            ),
            "unavailable_full_gate_count": sum(
                1 for row in rows if bool(row.get("supervisor_full_gate_unavailable"))
            ),
            "full_roster_available_count": full_roster_available_count,
            "codex_only_roster_available_count": codex_only_roster_available_count,
            "codex_only_panel_marginal_delta_at_matched_true_accept": (
                codex_only_panel_marginal_delta
            ),
            "panel_quality_reject_count": sum(
                1 for row in rows if bool(row.get("panel_quality_reject"))
            ),
            "panel_missing_verdict_block_count": sum(
                1 for row in rows if bool(row.get("panel_missing_verdict_block"))
            ),
            "inter_reviewer_agreement": inter_reviewer_agreement,
            "reviewer_infrastructure_diagnostics": [
                {
                    "task_id": row["task_id"],
                    "candidate_id": row["candidate_id"],
                    "diagnostic": dict(row.get("reviewer_infrastructure_diagnostic") or {}),
                }
                for row in rows
                if int(
                    (row.get("reviewer_infrastructure_diagnostic") or {}).get(
                        "failure_count", 0
                    )
                )
                > 0
            ],
        },
        "per_task_results": rows,
        "cost_usd": 0.0,
        "wall_clock_s": round(time.monotonic() - started, 6),
        "report_label": "calibration",
        "heldout_reporting": {
            "primary_metric_split": "held_out",
            "dev_metric_status": metric_splits["dev"]["status"],
            "best_of_k_in_sample": {
                "present": False,
                "label_allowed_as_heldout_improvement": False,
                "reason": "held_out_metrics_are_reported_separately_from_in_sample_or_peak_selection",
            },
            "heldout_improvement_claim_allowed": False,
        },
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "gaming_flags": sorted(gaming_flags),
        "baseline_evidence_kind": "metadata_calibration",
        "validity_notes": [
            "Supervisor candidate review is recorded from public-only evidence before hidden oracle "
            "grading is consulted for aggregate metrics.",
            "Supervisor full gate is recorded as the public candidate review plus an independent "
            "reviewer-panel decision from a public-only packet; unavailable reviewers are not "
            "imputed from the public-check arm.",
            "This fixture-scale report is calibration evidence only, not proof of production improvement.",
            "Fixture-only roster diagnostics cannot select Codex-only or drop reviewers; "
            "roster selection requires real or disagreement-enriched same-pool evidence.",
            "Baseline arm is metadata calibration of fixture candidate self-reports, not a produced "
            "single-agent baseline; do not treat it as replayable baseline evidence.",
            "The single_agent_baseline arm is reported only from replayable produced-baseline "
            "decision receipts; missing receipts are unavailable, not accept-all.",
        ],
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "roster_selection_allowed": False,
            "next_step": "grow an oracle-isolated corpus before any powered live-generation experiment",
        },
    }
    report["fixture_diagnostic_report"] = _build_fixture_diagnostic_report_block(
        candidates=candidate_list,
        s_probe=supervisor_candidate_review,
        s_full=supervisor_full_gate,
        full_gate_matched_true_accept=full_gate_matched_true_accept,
        fixture_growth_rationale=manifest.get("fixture_growth_rationale") or {},
    )
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


def build_fixture_single_agent_baseline_decisions(
    bench_root: str | Path,
    *,
    candidate_paths: tuple[str | Path, ...] = (),
    producer_label: str = "fixture-generator-metadata-replay",
    model: str = "fixture-baseline-replay",
    provider: str = "fixture",
) -> dict[str, dict[str, Any]]:
    """Build replayable single-agent baseline receipts for fixture candidates.

    The fixture corpus records the candidate producer's public self-acceptance in
    `generator_metadata.baseline_accept`. This helper turns that field into the
    same receipt shape consumed by the paired report without reading oracle
    labels or hidden-test results.
    """

    bench_root_path = Path(bench_root).expanduser().resolve()
    manifest = build_mergeability_corpus_manifest(
        bench_root_path,
        candidate_paths=candidate_paths,
    )
    candidates = {
        candidate.candidate_id: candidate
        for candidate in (
            tuple(load_mergeability_candidate(path) for path in candidate_paths)
            if candidate_paths else load_mergeability_candidates(bench_root_path)
        )
    }
    candidate_hashes = {
        entry["candidate_id"]: entry["candidate_hash"]
        for entry in manifest["candidates"]
    }
    decisions: dict[str, dict[str, Any]] = {}
    for candidate_id, candidate_hash in candidate_hashes.items():
        candidate = candidates[candidate_id]
        baseline_accept = candidate.generator_metadata.get("baseline_accept")
        prompt_sha256 = sha256(
            json.dumps(
                {
                    "candidate_hash": candidate_hash,
                    "candidate_id": candidate_id,
                    "decision_source": "replayed_single_agent_baseline",
                    "producer_label": producer_label,
                    "task_id": candidate.task_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        decision = {
            "candidate_id": candidate_id,
            "accept": bool(baseline_accept) if isinstance(baseline_accept, bool) else False,
            "candidate_artifact_hash": candidate_hash,
            "decision_source": "replayed_single_agent_baseline",
            "producer": {
                "agent": "single-agent-baseline-replay",
                "runner_label": producer_label,
                "model": model,
                "provider": provider,
                "budget_usd": 0.0,
                "source": "fixture_generator_metadata",
            },
            "prompt_sha256": prompt_sha256,
        }
        if not isinstance(baseline_accept, bool):
            decision["unavailable"] = True
            decision["unavailable_reason"] = "fixture_baseline_accept_missing"
        decisions[candidate_id] = decision
    return decisions


def run_fixture_panel_produced_baseline_measurement(
    bench_root: str | Path,
    *,
    output_dir: str | Path,
    candidate_paths: tuple[str | Path, ...] = (),
    timeout_s: float = 30.0,
    strict_calibration: bool = True,
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
) -> dict[str, Any]:
    """Run the fixture calibration with configured panel and produced baseline receipts."""

    output_path = Path(output_dir).expanduser()
    baseline_decisions = build_fixture_single_agent_baseline_decisions(
        bench_root,
        candidate_paths=candidate_paths,
    )
    report = run_paired_acceptance_pilot(
        bench_root,
        output_dir=output_path,
        candidate_paths=candidate_paths,
        timeout_s=timeout_s,
        strict_calibration=strict_calibration,
        reviewer_panel_mode="configured",
        configured_reviewer_panel_options=configured_reviewer_panel_options,
        single_agent_baseline_decisions=baseline_decisions,
    )
    report["measurement_run"] = {
        "schema_version": "supervisor-mergeability-fixture-measurement-run/v1",
        "name": "fixture_panel_produced_baseline",
        "reviewer_panel_mode": "configured",
        "baseline_decision_source": "replayed_single_agent_baseline",
        "baseline_receipt_count": len(baseline_decisions),
        "output_dir": output_path.as_posix(),
        "report_only": True,
    }
    _validate_fixture_panel_measurement_report(report)
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "paired_acceptance_report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _build_fixture_diagnostic_report_block(
    *,
    candidates: Sequence[MergeabilityCandidate],
    s_probe: Mapping[str, Any],
    s_full: Mapping[str, Any],
    full_gate_matched_true_accept: Mapping[str, Any],
    fixture_growth_rationale: Mapping[str, Any],
) -> dict[str, Any]:
    s_probe_tar = float(s_probe.get("true_accept_rate") or 0.0)
    s_full_tar = float(s_full.get("true_accept_rate") or 0.0)
    true_accept_loss = round(s_probe_tar - s_full_tar, 6)
    matched_status = (
        full_gate_matched_true_accept.get("status")
        if isinstance(full_gate_matched_true_accept, Mapping)
        else None
    )
    added_positive_ids = list(
        _fixture_diagnostic_growth_added_candidate_ids(candidates)
    )
    block = {
        "schema_version": FIXTURE_DIAGNOSTIC_REPORT_SCHEMA_VERSION,
        "growth_rationale": dict(fixture_growth_rationale),
        "added_positive_candidate_ids": added_positive_ids,
        "added_positive_candidate_count": len(added_positive_ids),
        "n_good": int(s_full.get("true_accept_denominator") or 0),
        "n_bad": int(s_full.get("false_accept_denominator") or 0),
        "s_probe_arm": s_probe.get("arm"),
        "s_full_arm": s_full.get("arm"),
        "s_probe_true_accept_count": int(s_probe.get("true_accept_count") or 0),
        "s_full_true_accept_count": int(s_full.get("true_accept_count") or 0),
        "s_probe_false_accept_count": int(s_probe.get("false_accept_count") or 0),
        "s_full_false_accept_count": int(s_full.get("false_accept_count") or 0),
        "s_probe_false_accepts": int(s_probe.get("false_accept_count") or 0),
        "s_full_false_accepts": int(s_full.get("false_accept_count") or 0),
        "s_probe_true_accept_rate": s_probe_tar,
        "s_full_true_accept_rate": s_full_tar,
        "s_probe_false_accept_rate": float(s_probe.get("false_accept_rate") or 0.0),
        "s_full_false_accept_rate": float(s_full.get("false_accept_rate") or 0.0),
        "true_accept_loss": true_accept_loss,
        "tar_loss": true_accept_loss,
        "matched_true_accept_status": matched_status,
        "full_gate_matched_true_accept": dict(full_gate_matched_true_accept)
        if isinstance(full_gate_matched_true_accept, Mapping) else {},
        "s_probe_true_accept_confidence_interval": dict(
            s_probe.get("true_accept_confidence_interval") or {}
        ),
        "s_full_true_accept_confidence_interval": dict(
            s_full.get("true_accept_confidence_interval") or {}
        ),
        "s_probe_false_accept_confidence_interval": dict(
            s_probe.get("false_accept_confidence_interval") or {}
        ),
        "s_full_false_accept_confidence_interval": dict(
            s_full.get("false_accept_confidence_interval") or {}
        ),
        "report_only": True,
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "default_change_allowed": False,
        "best_of_k_in_sample_label_allowed_as_heldout_improvement": False,
    }
    return block


def _paired_acceptance_comparisons(
    *,
    legacy_metadata_matched_true_accept: Mapping[str, Any],
    single_agent_matched_true_accept: Mapping[str, Any],
    full_gate_metadata_matched_true_accept: Mapping[str, Any],
    full_gate_single_agent_matched_true_accept: Mapping[str, Any],
    single_agent_baseline: Mapping[str, Any],
    metadata_accept_all_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "supervisor_vs_single_agent_baseline": {
            "name": "supervisor_vs_single_agent_baseline",
            "primary": True,
            "baseline_arm": "single_agent_baseline",
            "supervisor_arm": "supervisor_candidate_review",
            "full_gate_arm": "supervisor_full_gate",
            "baseline_evidence_kind": single_agent_baseline.get("evidence_kind"),
            "baseline_decision_source": single_agent_baseline.get("decision_source"),
            "matched_true_accept": dict(single_agent_matched_true_accept),
            "full_gate_matched_true_accept": dict(full_gate_single_agent_matched_true_accept),
            "metric_applyable": False,
            "improvement_claim_allowed": False,
        },
        "legacy_metadata_accept_all_baseline": {
            "name": "legacy_metadata_accept_all_baseline",
            "primary": False,
            "baseline_arm": "metadata_accept_all_baseline",
            "supervisor_arm": "supervisor_candidate_review",
            "full_gate_arm": "supervisor_full_gate",
            "baseline_evidence_kind": metadata_accept_all_baseline.get("evidence_kind"),
            "baseline_decision_source": metadata_accept_all_baseline.get("decision_source"),
            "matched_true_accept": dict(legacy_metadata_matched_true_accept),
            "full_gate_matched_true_accept": dict(full_gate_metadata_matched_true_accept),
            "metric_applyable": False,
            "improvement_claim_allowed": False,
        },
    }


def _roster_selection_guard(
    *,
    inter_reviewer_agreement: Sequence[Mapping[str, Any]],
    codex_only_calibration_active: bool,
    reviewer_provenance: Mapping[str, Any] | None = None,
    generator_disjointness: Mapping[str, Any] | None = None,
    evidence_scope: str = "fixture_diagnostic_only",
) -> dict[str, Any]:
    shared_pairs = [
        entry for entry in inter_reviewer_agreement
        if int(entry.get("shared_candidate_count") or 0) > 0
    ]
    saturated_agreement = bool(shared_pairs) and all(
        float(entry.get("agreement_rate") or 0.0) >= 1.0
        for entry in shared_pairs
    )
    reasons = [
        "fixture_only_evidence_cannot_select_reviewer_roster",
        "real_or_disagreement_enriched_candidate_pool_required",
    ]
    if codex_only_calibration_active:
        reasons.append("codex_only_calibration_is_not_full_panel_evidence")
    if saturated_agreement:
        reasons.append("fixture_corpus_saturated_for_roster_selection")
    cursor_default_ids = (
        list(reviewer_provenance.get("cursor_default_unproven_reviewer_ids") or [])
        if isinstance(reviewer_provenance, Mapping)
        else []
    )
    text_only_ids = (
        list(reviewer_provenance.get("text_only_reviewer_ids") or [])
        if isinstance(reviewer_provenance, Mapping)
        else []
    )
    same_family_count = (
        int(generator_disjointness.get("same_family_decisive_vote_count") or 0)
        if isinstance(generator_disjointness, Mapping)
        else 0
    )
    if cursor_default_ids:
        reasons.append("cursor_default_provider_family_unproven")
    if text_only_ids:
        reasons.append("text_only_reviewer_without_tool_backed_evidence")
    if same_family_count:
        reasons.append("same_family_generator_reviewer_decisive_vote")
    return {
        "schema_version": "supervisor-mergeability-roster-selection-guard/v1",
        "evidence_scope": evidence_scope,
        "roster_selection_allowed": False,
        "fixture_only_can_select_roster": False,
        "codex_only_can_select_roster": False,
        "saturated_reviewer_agreement": saturated_agreement,
        "decision_authority": "blocked",
        "reasons": reasons,
        "required_evidence": [
            "same_candidate_pool_across_roster_arms",
            "real_or_disagreement_enriched_candidates",
            "oracle_grounded_far_tar_by_roster",
            "reviewer_disagreement_and_self_preference_analysis",
            "reviewer_provider_family_and_tool_access_provenance",
            "generator_family_disjointness_by_candidate",
            "report_only_until_powered_live_evidence",
        ],
        "forbidden_conclusions": [
            "drop_reviewers_from_fixture_only_ablation",
            "select_codex_only_from_fixture_only_ablation",
            "treat_cursor_default_as_proven_cross_family_without_provider_evidence",
            "treat_saturated_fixture_agreement_as_no_diversity_value",
            "treat_text_only_reviewer_as_tool_backed_without_command_evidence",
            "allow_single_family_reviewer_as_sole_decisive_same_family_judge",
        ],
    }


def _validate_fixture_panel_measurement_report(report: Mapping[str, Any]) -> None:
    arms = report.get("arms")
    if not isinstance(arms, Mapping):
        raise MergeabilityBenchError("fixture measurement report missing arms")
    full_gate = arms.get("supervisor_full_gate")
    if not isinstance(full_gate, Mapping) or full_gate.get("availability_status") != "available":
        status = full_gate.get("availability_status") if isinstance(full_gate, Mapping) else "missing"
        raise MergeabilityBenchError(f"fixture measurement full gate unavailable: {status}")
    single_agent = arms.get("single_agent_baseline")
    if not isinstance(single_agent, Mapping) or single_agent.get("availability_status") != "available":
        status = single_agent.get("availability_status") if isinstance(single_agent, Mapping) else "missing"
        raise MergeabilityBenchError(f"fixture measurement single-agent baseline unavailable: {status}")
    panel_delta = report.get("panel_marginal_delta_at_matched_true_accept")
    if not isinstance(panel_delta, Mapping) or panel_delta.get("status") not in {"computed", "not_matched"}:
        status = panel_delta.get("status") if isinstance(panel_delta, Mapping) else "missing"
        raise MergeabilityBenchError(f"fixture measurement panel marginal unavailable: {status}")
    guard = report.get("roster_selection_guard")
    if not isinstance(guard, Mapping) or guard.get("roster_selection_allowed") is not False:
        raise MergeabilityBenchError("fixture measurement missing roster selection guard")
    comparisons = report.get("comparisons")
    if not isinstance(comparisons, Mapping):
        raise MergeabilityBenchError("fixture measurement report missing comparisons")
    primary = comparisons.get("supervisor_vs_single_agent_baseline")
    if not isinstance(primary, Mapping) or primary.get("primary") is not True:
        raise MergeabilityBenchError("fixture measurement primary comparison missing")
    for row in report.get("per_task_results") or []:
        if not isinstance(row, Mapping):
            continue
        if not row.get("supervisor_full_gate_reviewer_packet_sha256"):
            raise MergeabilityBenchError(
                f"fixture measurement missing reviewer packet hash: {row.get('candidate_id')}"
            )
        if not row.get("supervisor_accept"):
            panel_result = (
                row.get("supervisor_full_gate_review") or {}
            ).get("panel_result")
            panel_reason = str(
                panel_result.get("reason") if isinstance(panel_result, Mapping) else ""
            )
            if panel_reason != "public_review_rejected":
                raise MergeabilityBenchError(
                    f"fixture measurement public reject did not short-circuit: {row.get('candidate_id')}"
                )
            continue
        if not row.get("supervisor_full_gate_reviewer_results"):
            raise MergeabilityBenchError(
                f"fixture measurement missing reviewer results: {row.get('candidate_id')}"
            )
        if not row.get("supervisor_full_gate_reviewer_rationales"):
            raise MergeabilityBenchError(
                f"fixture measurement missing reviewer rationales: {row.get('candidate_id')}"
            )


def run_powered_factorial_mergeability_evaluation(
    bench_root: str | Path,
    *,
    output_dir: str | Path | None = None,
    candidate_paths: tuple[str | Path, ...] = (),
    timeout_s: float = 30.0,
    strict_calibration: bool = True,
    arm_decisions: Mapping[str, Mapping[str, Any]] | None = None,
    reviewer_panel_results: Mapping[str, list[Mapping[str, Any]]] | None = None,
    powered_thresholds: Mapping[str, int] | None = None,
    gaming_flags: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build a report-only powered factorial mergeability evaluation.

    The report compares the same candidate pool across factorial arms. Oracle
    labels are used only after arm decisions are fixed, for aggregate metrics.
    """
    started = time.monotonic()
    bench_root_path = Path(bench_root).expanduser().resolve()
    output_path = Path(output_dir).expanduser() if output_dir is not None else None
    manifest = build_mergeability_corpus_manifest(bench_root_path, candidate_paths=candidate_paths)
    tasks = {task.task_id: task for task in load_mergeability_tasks(bench_root_path)}
    candidate_list = (
        tuple(load_mergeability_candidate(path) for path in candidate_paths)
        if candidate_paths else load_mergeability_candidates(bench_root_path)
    )
    candidate_ids = tuple(candidate.candidate_id for candidate in candidate_list if candidate.task_id in tasks)
    _validate_factorial_arm_pool(arm_decisions or {}, candidate_ids=candidate_ids)
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
    task_hashes = {entry["task_id"]: entry["task_hash"] for entry in manifest["tasks"]}
    candidate_hashes = {
        (entry["task_id"], entry["candidate_id"]): entry["candidate_hash"]
        for entry in manifest["candidates"]
    }

    rows: list[dict[str, Any]] = []
    for result in calibration["results"]:
        candidate = candidates[result["candidate_id"]]
        task = tasks[candidate.task_id]
        oracle_accept = float(result["final_score"]) >= 1.0
        public_review = public_reviews[candidate.candidate_id]
        reviewer_results = list((reviewer_panel_results or {}).get(candidate.candidate_id) or [])
        full_gate_from_reviewers = _factorial_full_stack_decision(
            public_accept=bool(public_review["accept"]),
            reviewer_results=reviewer_results,
        )
        baseline_per_arm = (arm_decisions or {}).get("single_agent_baseline") or {}
        baseline_raw = baseline_per_arm.get(candidate.candidate_id) if baseline_per_arm else None
        baseline_decision = _resolve_powered_baseline_decision(
            raw=baseline_raw,
            expected_candidate_artifact_hash=candidate_hashes[
                (result["task_id"], result["candidate_id"])
            ],
        )
        defaults = {
            "single_agent_baseline": {
                "accept": baseline_decision["accept"],
                "unavailable": baseline_decision["unavailable"],
                "decision_source": baseline_decision["decision_source"],
            },
            "same_model_multi_agent": {
                "accept": bool(public_review["accept"]),
                "unavailable": False,
                "decision_source": "same_model_multi_agent_structure",
            },
            "hetero_multi_reviewer": {
                "accept": bool(public_review["accept"]),
                "unavailable": False,
                "decision_source": "heterogeneous_reviewer_structure",
            },
            "runtime_evidence_floor": {
                "accept": bool(public_review["accept"]),
                "unavailable": False,
                "decision_source": "supervisor_runtime_evidence_floor",
            },
            "full_supervisor_stack": full_gate_from_reviewers,
            "oracle_ceiling": {
                "accept": oracle_accept,
                "unavailable": False,
                "decision_source": "oracle_final_score",
            },
        }
        row: dict[str, Any] = {
            "task_id": result["task_id"],
            "task_class": task.task_class,
            "split": task.split,
            "candidate_id": result["candidate_id"],
            "candidate_hash": candidate_hashes[(result["task_id"], result["candidate_id"])],
            "task_hash": task_hashes[result["task_id"]],
            "control_kind": result["control_kind"],
            "oracle_accept": oracle_accept,
            "receipt_id": result["receipt_id"],
            "receipt": result["receipt"],
            "blocker_status": result["blocker_status"],
            "failures": result["failures"],
            "supervisor_public_review": public_review,
            "independent_reviewer_results": [_normalise_factorial_reviewer_result(item) for item in reviewer_results],
        }
        for arm in FACTORIAL_ARM_DEFINITIONS:
            if arm == "single_agent_baseline":
                row[f"{arm}_accept"] = bool(baseline_decision["accept"])
                row[f"{arm}_unavailable"] = bool(baseline_decision["unavailable"])
                row[f"{arm}_decision_source"] = baseline_decision["decision_source"]
                row[f"{arm}_evidence_kind"] = baseline_decision["evidence_kind"]
                row[f"{arm}_candidate_artifact_hash"] = baseline_decision["candidate_artifact_hash"]
                row[f"{arm}_producer"] = dict(baseline_decision["producer"])
                row[f"{arm}_prompt_sha256"] = baseline_decision["prompt_sha256"]
                row[f"{arm}_unavailable_reason"] = baseline_decision["unavailable_reason"]
                continue
            decision = _factorial_arm_decision(
                arm_decisions=arm_decisions or {},
                arm=arm,
                candidate_id=candidate.candidate_id,
                default=defaults[arm],
            )
            row[f"{arm}_accept"] = bool(decision["accept"])
            row[f"{arm}_unavailable"] = bool(decision["unavailable"])
            row[f"{arm}_decision_source"] = decision["decision_source"]
        rows.append(row)

    def _evidence_kind_for_arm(arm: str) -> str | None:
        if arm != "single_agent_baseline":
            return None
        available = [row for row in rows if not bool(row.get("single_agent_baseline_unavailable"))]
        if available:
            kinds = {str(row.get("single_agent_baseline_evidence_kind") or "") for row in available}
            kinds.discard("")
            if len(kinds) == 1:
                return next(iter(kinds))
            if kinds:
                return "mixed_produced_baseline"
            return "produced_single_agent_baseline"
        unavailable_kinds = {str(row.get("single_agent_baseline_evidence_kind") or "missing") for row in rows}
        if len(unavailable_kinds) == 1:
            return next(iter(unavailable_kinds))
        return "unavailable_mixed"

    def _decision_source_for_arm(arm: str, default: str) -> str:
        if arm != "single_agent_baseline":
            return default
        available = [row for row in rows if not bool(row.get("single_agent_baseline_unavailable"))]
        if not available:
            return default
        sources = {str(row.get("single_agent_baseline_decision_source") or "") for row in available}
        sources.discard("")
        if len(sources) == 1:
            return next(iter(sources))
        return default

    arms = {
        arm: _summarize_acceptance_arm(
            rows,
            arm=arm,
            arm_role=str(definition["arm_role"]),
            decision_source=_decision_source_for_arm(arm, str(definition["decision_source"])),
            oracle_coupled=bool(definition["oracle_coupled"]),
            evidence_kind=_evidence_kind_for_arm(arm),
        )
        for arm, definition in FACTORIAL_ARM_DEFINITIONS.items()
    }
    for arm_name, arm in arms.items():
        arm["false_reject_confidence_interval"] = _wilson_interval(
            int(arm["false_reject_count"]),
            int(arm["false_reject_denominator"]),
        )
        arm["improvement_claim_allowed"] = False if arm_name == "oracle_ceiling" else None

    candidate_pool = [
        {
            "task_id": row["task_id"],
            "task_hash": row["task_hash"],
            "candidate_id": row["candidate_id"],
            "candidate_hash": row["candidate_hash"],
        }
        for row in rows
    ]
    candidate_pool_sha256 = _sha256_json(candidate_pool)
    pool_by_arm = {arm: candidate_pool_sha256 for arm in FACTORIAL_ARM_DEFINITIONS}
    matched_true_accept = {
        arm: _matched_true_accept_for_factorial_arm(
            baseline=arms["single_agent_baseline"],
            arm=summary,
        )
        for arm, summary in arms.items()
        if arm != "single_agent_baseline"
    }
    paired_discordant = {
        arm: _paired_discordant_counts(
            rows,
            left_arm="single_agent_baseline",
            right_arm=arm,
        )
        for arm in FACTORIAL_ARM_DEFINITIONS
        if arm != "single_agent_baseline"
    }
    leave_one = _leave_one_reviewer_out_analysis(
        rows,
        reviewer_panel_results=reviewer_panel_results or {},
        full_stack=arms["full_supervisor_stack"],
    )
    sample_size = _factorial_sample_size_sufficiency(
        rows,
        powered_thresholds=powered_thresholds or {},
    )
    all_gaming_flags = set(calibration.get("gaming_flags") or [])
    all_gaming_flags.update(str(flag) for flag in gaming_flags)
    if arms["full_supervisor_stack"]["unavailable_count"]:
        all_gaming_flags.add("reviewer_panel_unavailable")
    if arms["single_agent_baseline"]["unavailable_count"]:
        all_gaming_flags.add("baseline_evidence_unavailable")
    powered = sample_size["status"] == "sufficient"
    full_stack_available = arms["full_supervisor_stack"]["unavailable_count"] == 0
    metric_applyable = bool(powered and full_stack_available and not all_gaming_flags)
    trend_rows = _factorial_trend_rows(
        arms=arms,
        task_classes=sorted({row["task_class"] for row in rows}),
        candidate_pool_sha256=candidate_pool_sha256,
    )
    report = {
        "schema_version": MERGEABILITY_POWERED_FACTORIAL_REPORT_SCHEMA_VERSION,
        "report_label": "powered_factorial_evaluation",
        "bench_root": bench_root_path.as_posix(),
        "manifest_sha256": manifest["manifest_sha256"],
        "calibration_summary_sha256": calibration["summary_sha256"],
        "candidate_pool_sha256": candidate_pool_sha256,
        "candidate_pool_by_arm_sha256": pool_by_arm,
        "same_candidate_pool": len(set(pool_by_arm.values())) == 1,
        "task_count": manifest["task_count"],
        "candidate_count": len(rows),
        "arms": arms,
        "matched_true_accept": matched_true_accept,
        "paired_discordant_counts": paired_discordant,
        "leave_one_reviewer_out": leave_one,
        "sample_size_sufficiency": sample_size,
        "per_task_results": rows,
        "trend_rows": trend_rows,
        "promotion_guardrails": {
            "powered_threshold_required": True,
            "powered_threshold_met": powered,
            "gaming_flags_block_promotion": bool(all_gaming_flags),
            "reviewer_panel_unavailable_blocks_full_stack_claim": not full_stack_available,
            "oracle_ceiling_supervisor_claim_allowed": False,
            "policy_mutation_allowed": False,
        },
        "metric_applyable": metric_applyable,
        "improvement_claim_allowed": False,
        "gaming_flags": sorted(all_gaming_flags),
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "cost_usd": 0.0,
        "wall_clock_s": round(time.monotonic() - started, 6),
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "operator_approval_required_for_any_policy_change": True,
            "next_step": "operator-reviewed policy evolution proposal after powered evidence, not automatic mutation",
        },
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    if output_path is not None:
        _export_powered_factorial_artifacts(
            output_path,
            manifest=manifest,
            calibration=calibration,
            report=report,
            rows=rows,
        )
    return report


def run_live_mergeability_candidate_generation(
    bench_root: str | Path,
    *,
    task_id: str,
    baseline_generator: Callable[[Mapping[str, Any]], Mapping[str, Any] | MergeabilityCandidate],
    supervisor_generator: Callable[[Mapping[str, Any]], Mapping[str, Any] | MergeabilityCandidate],
    allow_live: bool,
    model: str = "",
    provider: str = "",
    budget_usd: float = 0.0,
    timeout_s: float = 30.0,
    baseline_config: Mapping[str, Any] | None = None,
    supervisor_config: Mapping[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Generate baseline and supervisor candidates through injected live adapters.

    The function is intentionally report-only. The generator callables are the
    external-provider seam; everything after generation uses the real
    mergeability bench oracle.
    """
    started = time.monotonic()
    bench_root_path = Path(bench_root).expanduser().resolve()
    output_path = Path(output_dir).expanduser() if output_dir is not None else None
    task = load_mergeability_task(bench_root_path, task_id)
    default_config = {
        "model": str(model),
        "provider": str(provider),
        "budget_usd": float(budget_usd),
        "timeout_s": float(timeout_s),
    }
    baseline_cfg = _normalise_live_arm_config(baseline_config or default_config, default_config=default_config)
    supervisor_cfg = _normalise_live_arm_config(supervisor_config or default_config, default_config=default_config)
    evaluator_hash = _live_generation_evaluator_hash(task)

    if not allow_live:
        report = _live_generation_unavailable_report(
            task=task,
            bench_root_path=bench_root_path,
            started=started,
            reason="live_generation_disabled",
            baseline_config=baseline_cfg,
            supervisor_config=supervisor_cfg,
            evaluator_hash=evaluator_hash,
        )
        _export_live_generation_report(output_path, report)
        return report

    mismatches = _live_arm_config_mismatches(baseline_cfg, supervisor_cfg)
    if mismatches:
        report = _live_generation_unavailable_report(
            task=task,
            bench_root_path=bench_root_path,
            started=started,
            reason="arm_config_mismatch",
            baseline_config=baseline_cfg,
            supervisor_config=supervisor_cfg,
            evaluator_hash=evaluator_hash,
            config_mismatches=mismatches,
        )
        _export_live_generation_report(output_path, report)
        return report

    live_workspace: tempfile.TemporaryDirectory[str] | None = None
    if output_path is not None:
        live_public_root = output_path / "live-public-worktrees"
        live_public_root.mkdir(parents=True, exist_ok=True)
    else:
        live_workspace = tempfile.TemporaryDirectory(prefix="mergeability-live-public-")
        live_public_root = Path(live_workspace.name)
    try:
        baseline_input = _build_live_generator_input(
            bench_root_path=bench_root_path,
            task=task,
            config=baseline_cfg,
            public_root=live_public_root / "baseline",
        )
        supervisor_input = _build_live_generator_input(
            bench_root_path=bench_root_path,
            task=task,
            config=supervisor_cfg,
            public_root=live_public_root / "supervisor",
        )
        input_leaks = sorted(set(
            _public_input_oracle_refs(baseline_input)
            + _public_input_oracle_refs(supervisor_input)
        ))
        if input_leaks:
            report = _live_generation_unavailable_report(
                task=task,
                bench_root_path=bench_root_path,
                started=started,
                reason="oracle_isolation_violation",
                baseline_config=baseline_cfg,
                supervisor_config=supervisor_cfg,
                evaluator_hash=evaluator_hash,
                gaming_flags=("oracle_isolation_violation",),
            )
            report["oracle_isolation_violations"] = input_leaks
            _export_live_generation_report(output_path, report)
            return report

        baseline = _run_live_generation_arm(
            arm="baseline",
            generator=baseline_generator,
            generator_input=baseline_input,
            config=baseline_cfg,
            task=task,
            bench_root_path=bench_root_path,
            evaluator_hash=evaluator_hash,
            output_path=output_path,
        )
        supervisor = _run_live_generation_arm(
            arm="supervisor",
            generator=supervisor_generator,
            generator_input=supervisor_input,
            config=supervisor_cfg,
            task=task,
            bench_root_path=bench_root_path,
            evaluator_hash=evaluator_hash,
            output_path=output_path,
        )
    finally:
        if live_workspace is not None:
            live_workspace.cleanup()
    arms = {"baseline": baseline, "supervisor": supervisor}
    gaming_flags = sorted({
        flag
        for arm in arms.values()
        for flag in arm.get("gaming_flags", [])
    })
    unavailable = any(arm.get("status") == "unavailable" for arm in arms.values())
    report = _live_generation_report_base(
        task=task,
        bench_root_path=bench_root_path,
        started=started,
        status="unavailable" if unavailable else "completed",
        unavailable_reason="arm_unavailable" if unavailable else "",
        arms=arms,
        evaluator_hash=evaluator_hash,
        gaming_flags=gaming_flags,
    )
    report["candidate_artifacts"] = {
        arm: {
            "candidate_id": str(payload.get("candidate_id") or ""),
            "candidate_artifact_hash": str(payload.get("candidate_artifact_hash") or ""),
            "candidate_artifact_ref": str(payload.get("candidate_artifact_ref") or ""),
        }
        for arm, payload in arms.items()
        if payload.get("candidate_artifact_hash")
    }
    report["heldout_oracle"] = {
        "decision_source": "grade_mergeability_candidate",
        "task_id": task.task_id,
        "evaluator_hash": evaluator_hash,
        "same_evaluator_for_all_arms": len({arm["evaluator_hash"] for arm in arms.values()}) == 1,
    }
    report["total_cost_usd"] = round(sum(float(arm.get("cost_usd") or 0.0) for arm in arms.values()), 6)
    report["report_sha256"] = _sha256_json({key: value for key, value in report.items() if key != "report_sha256"})
    _export_live_generation_report(output_path, report)
    return report


def _normalise_live_arm_config(
    config: Mapping[str, Any],
    *,
    default_config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "model": str(config.get("model", default_config.get("model", "")) or ""),
        "provider": str(config.get("provider", default_config.get("provider", "")) or ""),
        "budget_usd": float(config.get("budget_usd", default_config.get("budget_usd", 0.0)) or 0.0),
        "timeout_s": float(config.get("timeout_s", default_config.get("timeout_s", 30.0)) or 0.0),
    }


def _live_arm_config_mismatches(
    baseline_config: Mapping[str, Any],
    supervisor_config: Mapping[str, Any],
) -> list[str]:
    mismatches = []
    for key in ("model", "provider", "budget_usd", "timeout_s"):
        if baseline_config.get(key) != supervisor_config.get(key):
            mismatches.append(key)
    return mismatches


def _live_generation_unavailable_report(
    *,
    task: MergeabilityTask,
    bench_root_path: Path,
    started: float,
    reason: str,
    baseline_config: Mapping[str, Any],
    supervisor_config: Mapping[str, Any],
    evaluator_hash: str,
    config_mismatches: list[str] | None = None,
    gaming_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    arms = {
        "baseline": _not_invoked_live_arm("baseline", baseline_config, evaluator_hash=evaluator_hash),
        "supervisor": _not_invoked_live_arm("supervisor", supervisor_config, evaluator_hash=evaluator_hash),
    }
    report = _live_generation_report_base(
        task=task,
        bench_root_path=bench_root_path,
        started=started,
        status="unavailable",
        unavailable_reason=reason,
        arms=arms,
        evaluator_hash=evaluator_hash,
        gaming_flags=gaming_flags,
    )
    report["candidate_artifacts"] = {}
    report["config_mismatches"] = list(config_mismatches or [])
    report["heldout_oracle"] = {
        "decision_source": "grade_mergeability_candidate",
        "task_id": task.task_id,
        "evaluator_hash": evaluator_hash,
        "same_evaluator_for_all_arms": True,
    }
    report["total_cost_usd"] = 0.0
    report["report_sha256"] = _sha256_json({key: value for key, value in report.items() if key != "report_sha256"})
    return report


def _live_generation_report_base(
    *,
    task: MergeabilityTask,
    bench_root_path: Path,
    started: float,
    status: str,
    unavailable_reason: str,
    arms: Mapping[str, Mapping[str, Any]],
    evaluator_hash: str,
    gaming_flags: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    return {
        "schema_version": MERGEABILITY_LIVE_GENERATION_REPORT_SCHEMA_VERSION,
        "status": status,
        "unavailable_reason": unavailable_reason,
        "report_label": "live_generation_calibration",
        "bench_root": bench_root_path.as_posix(),
        "task_id": task.task_id,
        "task_class": task.task_class,
        "split": task.split,
        "arms": {key: dict(value) for key, value in arms.items()},
        "evaluator_hash": evaluator_hash,
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "gaming_flags": sorted(set(gaming_flags)),
        "validity_notes": [
            "Live generation is disabled unless allow_live is explicitly true.",
            "Baseline and supervisor arms are budget matched before generator invocation.",
            "Generated candidates are graded by the same held-out bench oracle after generation.",
            "This live report is calibration evidence only and cannot create policy proposals.",
        ],
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "next_step": "collect powered live evidence before policy proposal derivation",
        },
        "wall_clock_s": round(time.monotonic() - started, 6),
    }


def _not_invoked_live_arm(
    arm: str,
    config: Mapping[str, Any],
    *,
    evaluator_hash: str,
) -> dict[str, Any]:
    return {
        "arm": arm,
        "status": "not_invoked",
        "accepted": False,
        "config": dict(config),
        "model": str(config.get("model") or ""),
        "provider": str(config.get("provider") or ""),
        "budget_usd": float(config.get("budget_usd") or 0.0),
        "timeout_s": float(config.get("timeout_s") or 0.0),
        "evaluator_hash": evaluator_hash,
        "cost_usd": 0.0,
        "wall_clock_s": 0.0,
        "token_usage": {},
        "candidate_artifact_hash": "",
        "candidate_artifact_ref": "",
        "candidate_artifact_hash_recomputed": "",
        "prompt_hash": "",
        "generator_input_hash": "",
        "oracle_result": {},
        "gaming_flags": [],
        "unavailable_reason": "not_invoked",
    }


def _build_live_generator_input(
    *,
    bench_root_path: Path,
    task: MergeabilityTask,
    config: Mapping[str, Any],
    public_root: Path,
) -> dict[str, Any]:
    fixture_root = (bench_root_path / task.repo_fixture_ref).resolve()
    if not fixture_root.exists():
        raise MergeabilityBenchError(f"repo fixture missing: {fixture_root}")
    if public_root.exists():
        shutil.rmtree(public_root)
    _copy_public_fixture_tree(fixture_root, public_root, protected_paths=task.protected_paths)
    protected_refs = _protected_refs_in_tree(public_root, protected_paths=task.protected_paths)
    if protected_refs:
        raise MergeabilityBenchError(
            "live public worktree contains protected refs: " + ", ".join(protected_refs)
        )
    public_worktree_hash = _hash_tree(public_root)
    public_manifest = _public_worktree_manifest(public_root)
    stable_payload = {
        "schema_version": MERGEABILITY_LIVE_GENERATOR_INPUT_SCHEMA_VERSION,
        "task_id": task.task_id,
        "task_class": task.task_class,
        "split": task.split,
        "prompt": task.prompt,
        "allowed_mutable_paths": list(task.allowed_mutable_paths),
        "scope_constraints": list(task.scope_constraints),
        "blocker_criteria": list(task.blocker_criteria),
        "protected_path_policy_sha256": _sha256_json(list(task.protected_paths)),
        "protected_path_policy_count": len(task.protected_paths),
        "visible_commands": {
            "reverse_test_commands": [list(command) for command in task.reverse_test_commands],
            "lint_build_commands": [list(command) for command in task.lint_build_commands],
        },
        "public_worktree_hash": public_worktree_hash,
        "public_worktree_manifest": public_manifest,
        "generation_config": dict(config),
    }
    payload = {
        **stable_payload,
        "public_worktree_ref": public_root.as_posix(),
    }
    leaks = _public_input_oracle_refs(payload)
    if leaks:
        raise MergeabilityBenchError("live generator input contains oracle references: " + ", ".join(leaks))
    payload["generator_input_hash"] = _sha256_json(stable_payload)
    return payload


def _public_worktree_manifest(root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = _normalise_relpath(path.relative_to(root).as_posix())
        entries.append({
            "path": rel,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    return entries


def _run_live_generation_arm(
    *,
    arm: str,
    generator: Callable[[Mapping[str, Any]], Mapping[str, Any] | MergeabilityCandidate],
    generator_input: Mapping[str, Any],
    config: Mapping[str, Any],
    task: MergeabilityTask,
    bench_root_path: Path,
    evaluator_hash: str,
    output_path: Path | None,
) -> dict[str, Any]:
    started = time.monotonic()
    raw_result = generator(generator_input)
    measured_wall_clock = time.monotonic() - started
    candidate, generator_payload = _candidate_from_live_generator_result(raw_result)
    candidate = MergeabilityCandidate(
        candidate_id=candidate.candidate_id,
        task_id=candidate.task_id,
        files=candidate.files,
        changed_files=candidate.changed_files,
        provenance={
            **candidate.provenance,
            "live_generation_arm": arm,
            "generator_input_hash": str(generator_input.get("generator_input_hash") or ""),
        },
        generator_metadata=candidate.generator_metadata,
        candidate_ref=candidate.candidate_ref,
        candidate_hash="",
    )
    candidate_hash = _sha256_json(_live_candidate_artifact_payload(candidate))
    candidate = MergeabilityCandidate(
        candidate_id=candidate.candidate_id,
        task_id=candidate.task_id,
        files=candidate.files,
        changed_files=candidate.changed_files,
        provenance=candidate.provenance,
        generator_metadata=candidate.generator_metadata,
        candidate_ref=candidate.candidate_ref,
        candidate_hash=candidate_hash,
    )
    artifact_ref = _export_live_candidate(output_path, arm=arm, candidate=candidate)
    cost_usd = float(generator_payload.get("cost_usd") or 0.0)
    wall_clock_s = float(generator_payload.get("wall_clock_s") or measured_wall_clock)
    token_usage = generator_payload.get("token_usage") if isinstance(generator_payload.get("token_usage"), Mapping) else {}
    gaming_flags: list[str] = []
    unavailable_reason = ""
    if float(config.get("budget_usd") or 0.0) > 0 and cost_usd > float(config.get("budget_usd") or 0.0):
        gaming_flags.append("budget_exceeded")
        unavailable_reason = "budget_exceeded"
    if float(config.get("timeout_s") or 0.0) > 0 and wall_clock_s > float(config.get("timeout_s") or 0.0):
        gaming_flags.append("timeout")
        unavailable_reason = unavailable_reason or "timeout"

    oracle_result: dict[str, Any] = {}
    accepted = False
    status = "unavailable" if unavailable_reason else "completed"
    if not unavailable_reason:
        result = grade_mergeability_candidate(
            task,
            candidate,
            bench_root=bench_root_path,
            output_dir=(output_path / "live-generation-results" / arm) if output_path is not None else None,
            timeout_s=float(config.get("timeout_s") or 30.0),
        )
        oracle_result = result.to_payload()
        accepted = bool(result.final_score >= 1.0)
    return {
        "arm": arm,
        "status": status,
        "accepted": accepted,
        "unavailable_reason": unavailable_reason,
        "config": dict(config),
        "model": str(config.get("model") or ""),
        "provider": str(config.get("provider") or ""),
        "budget_usd": float(config.get("budget_usd") or 0.0),
        "timeout_s": float(config.get("timeout_s") or 0.0),
        "cost_usd": round(cost_usd, 6),
        "wall_clock_s": round(wall_clock_s, 6),
        "token_usage": dict(token_usage),
        "prompt_hash": _sha256_json({
            "prompt": generator_input.get("prompt"),
            "allowed_mutable_paths": generator_input.get("allowed_mutable_paths"),
            "scope_constraints": generator_input.get("scope_constraints"),
        }),
        "generator_input_hash": str(generator_input.get("generator_input_hash") or ""),
        "public_worktree_ref": str(generator_input.get("public_worktree_ref") or ""),
        "public_worktree_hash": str(generator_input.get("public_worktree_hash") or ""),
        "candidate_id": candidate.candidate_id,
        "candidate_artifact_hash": candidate_hash,
        "candidate_artifact_hash_recomputed": _sha256_json(_live_candidate_artifact_payload(candidate)),
        "candidate_artifact_ref": artifact_ref,
        "changed_files": list(candidate.changed_files),
        "evaluator_hash": evaluator_hash,
        "oracle_result": oracle_result,
        "gaming_flags": sorted(set(gaming_flags)),
    }


def _candidate_from_live_generator_result(
    raw_result: Mapping[str, Any] | MergeabilityCandidate,
) -> tuple[MergeabilityCandidate, Mapping[str, Any]]:
    if isinstance(raw_result, MergeabilityCandidate):
        return raw_result, {}
    if not isinstance(raw_result, Mapping):
        raise MergeabilityBenchError("live generator must return a mapping or MergeabilityCandidate")
    raw_candidate = raw_result.get("candidate", raw_result.get("candidate_payload"))
    if isinstance(raw_candidate, MergeabilityCandidate):
        return raw_candidate, raw_result
    if isinstance(raw_candidate, Mapping):
        return MergeabilityCandidate.from_mapping(raw_candidate), raw_result
    if "schema_version" in raw_result:
        return MergeabilityCandidate.from_mapping(raw_result), {}
    raise MergeabilityBenchError("live generator result missing candidate payload")


def _live_candidate_artifact_payload(candidate: MergeabilityCandidate) -> dict[str, Any]:
    payload = candidate.to_payload()
    payload["candidate_hash"] = ""
    payload["candidate_ref"] = ""
    return payload


def _export_live_candidate(
    output_path: Path | None,
    *,
    arm: str,
    candidate: MergeabilityCandidate,
) -> str:
    if output_path is None:
        return ""
    candidate_dir = output_path / "live-candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    path = candidate_dir / f"{arm}.json"
    path.write_text(json.dumps(candidate.to_payload(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path.as_posix()


def _export_live_generation_report(output_path: Path | None, report: Mapping[str, Any]) -> None:
    if output_path is None:
        return
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "live_candidate_generation_report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _live_generation_evaluator_hash(task: MergeabilityTask) -> str:
    return _sha256_json({
        "schema_version": "supervisor-mergeability-live-evaluator-hash/v1",
        "decision_source": "grade_mergeability_candidate",
        "task_id": task.task_id,
        "task_hash": task.task_hash,
        "oracle_command_count": len(task.hidden_test_commands),
        "reverse_command_count": len(task.reverse_test_commands),
        "lint_build_command_count": len(task.lint_build_commands),
    })


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


_RECOVERABLE_REVIEWER_FAILURE_CLASSIFICATIONS = frozenset({
    "reviewer_infrastructure",
    "reviewer_infrastructure_unavailable",
    "reviewer_invocation_failed",
    "reviewer_runtime_timeout",
})


def _classify_panel_quality_label(
    *,
    panel: Mapping[str, Any],
    leaks: Sequence[str],
    public_accept: bool,
    codex_only_calibration: bool,
) -> tuple[str, bool]:
    """Return ``(panel_quality_label, full_roster_available)``.

    The label separates infrastructure unavailability (`panel_missing_verdict_block`)
    from quality rejection (`panel_quality_reject`) at the mergeability report
    boundary. Codex-only calibration mode overrides both because the partial
    roster cannot stand in for full-panel evidence.
    """

    if codex_only_calibration:
        return ("codex_only_calibration", False)
    if leaks:
        return ("panel_oracle_isolation_violation", False)
    if not public_accept:
        return ("panel_public_short_circuit", False)
    available = bool(panel.get("available"))
    if not available:
        return ("panel_missing_verdict_block", False)
    missing_reviewers = list(panel.get("missing_reviewers") or [])
    if missing_reviewers:
        return ("panel_missing_verdict_block", False)
    decision = str(panel.get("decision") or "unavailable")
    if decision == "accept":
        return ("panel_quality_accept", True)
    return ("panel_quality_reject", True)


def _build_reviewer_infrastructure_diagnostic(
    panel: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarise per-reviewer infrastructure failures.

    The diagnostic carries only fields that are safe to surface in public
    reports: reviewer identity, runtime, model, failure classification,
    recoverability hint, and transcript/output hashes. Oracle material, hidden
    tests, and protected-path content are never included.
    """

    reviewers: list[dict[str, Any]] = []
    failure_count = 0
    recoverable_count = 0
    for summary in panel.get("reviewer_results") or []:
        if not isinstance(summary, Mapping):
            continue
        classification = summary.get("failure_classification")
        if classification is None and bool(summary.get("verdict_present")):
            continue
        if classification is None and not bool(summary.get("verdict_present")):
            classification = "reviewer_missing_verdict"
        classification_str = str(classification)
        recoverable = classification_str in _RECOVERABLE_REVIEWER_FAILURE_CLASSIFICATIONS
        reviewer_entry = {
            "reviewer_id": str(summary.get("reviewer_id") or "unknown-reviewer"),
            "runtime": str(summary.get("runtime") or "unknown"),
            "model": summary.get("model"),
            "failure_classification": classification_str,
            "recoverable": recoverable,
            "transcript_sha256": summary.get("transcript_sha256"),
            "output_sha256": summary.get("output_sha256"),
        }
        if isinstance(summary.get("failure_details"), Mapping):
            reviewer_entry["failure_details"] = redact(dict(summary["failure_details"]))
        if isinstance(summary.get("diagnostics_failure"), Mapping):
            reviewer_entry["diagnostics_failure"] = redact(
                dict(summary["diagnostics_failure"])
            )
        reviewers.append(reviewer_entry)
        failure_count += 1
        if recoverable:
            recoverable_count += 1
    return {
        "schema_version": "supervisor-reviewer-infrastructure-diagnostic/v1",
        "reviewers": reviewers,
        "failure_count": failure_count,
        "recoverable_count": recoverable_count,
        "missing_reviewers": list(panel.get("missing_reviewers") or []),
    }


def _review_mergeability_candidate_full_gate(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    public_review: Mapping[str, Any],
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    codex_only_calibration: bool = False,
) -> dict[str, Any]:
    packet = _build_full_gate_reviewer_packet(
        task=task,
        candidate=candidate,
        public_review=public_review,
    )
    leaks = _public_input_oracle_refs(packet)
    public_accept = bool(public_review.get("accept"))
    panel_fn = reviewer_panel or _default_unavailable_reviewer_panel
    if leaks:
        raw_panel = {
            "decision": "unavailable",
            "available": False,
            "reason": "oracle_isolation_violation",
            "blocking_findings": leaks,
        }
    elif not public_accept:
        raw_panel = _public_review_rejected_panel_result(public_review)
    else:
        raw_panel = panel_fn(packet)
    panel = _normalise_reviewer_panel_result(raw_panel)
    reviewer_rationales = _reviewer_rationales_from_results(panel["reviewer_results"])
    missing_reviewers = bool(panel.get("missing_reviewers"))
    unavailable = (
        bool(leaks)
        or not panel["available"]
        or missing_reviewers
        or codex_only_calibration
    )
    panel_accept = bool(
        panel["available"]
        and panel["decision"] == "accept"
        and not codex_only_calibration
    )
    accept = bool(public_accept and panel_accept and not unavailable)
    panel_quality_label, full_roster_available = _classify_panel_quality_label(
        panel=panel,
        leaks=leaks,
        public_accept=public_accept,
        codex_only_calibration=codex_only_calibration,
    )
    reviewer_infrastructure_diagnostic = _build_reviewer_infrastructure_diagnostic(panel)
    gaming_flags = set(public_review.get("gaming_flags") or [])
    if leaks:
        gaming_flags.add("oracle_isolation_violation")
    if unavailable:
        gaming_flags.add("reviewer_panel_unavailable")
    if panel_quality_label == "panel_missing_verdict_block":
        gaming_flags.add("panel_missing_verdict_block")
    if panel_quality_label == "panel_quality_reject":
        gaming_flags.add("panel_quality_reject")
    if codex_only_calibration:
        gaming_flags.add("codex_only_calibration")
    packet_ref = {
        "packet_id": packet["packet_id"],
        "packet_sha256": packet["packet_sha256"],
        "source": "supervisor",
        "evidence_grade": "runtime_native",
    }
    s_probe_vs_s_full_disagreement = (
        (not unavailable) and (public_accept != accept)
    )
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
        "reviewer_results": list(panel.get("reviewer_results") or []),
        "reviewer_rationales": reviewer_rationales,
        "reviewer_panel_decision": panel.get("panel_decision"),
        "available_reviewers": list(panel.get("available_reviewers") or []),
        "s_probe_vs_s_full_disagreement": s_probe_vs_s_full_disagreement,
        "reviewer_packet": packet,
        "reviewer_packet_refs": [packet_ref],
        "reviewer_packet_sha256": packet["packet_sha256"],
        "evidence_refs": [
            f"mergeability_full_gate_packet:{packet['packet_id']}:{packet['packet_sha256']}",
        ],
        "gaming_flags": sorted(gaming_flags),
        "decision_source": "supervisor_candidate_review+independent_reviewer_panel",
        "oracle_coupled": False,
        "panel_quality_label": panel_quality_label,
        "full_roster_available": full_roster_available,
        "panel_quality_reject": panel_quality_label == "panel_quality_reject",
        "panel_missing_verdict_block": panel_quality_label == "panel_missing_verdict_block",
        "codex_only_calibration": codex_only_calibration,
        "reviewer_infrastructure_diagnostic": reviewer_infrastructure_diagnostic,
    }


def _public_review_rejected_panel_result(public_review: Mapping[str, Any]) -> dict[str, Any]:
    reasons = [str(item) for item in public_review.get("reasons") or []]
    panel_decision = {
        "schema_version": "independent-reviewer-panel-decision/v1",
        "decision": "revise",
        "reason": "public_review_rejected",
        "aggregation_mode": "short_circuit",
        "available_reviewers": [],
        "accepted_reviewers": [],
        "blocking_reviewers": [],
        "non_accepting_reviewers": [],
        "missing_reviewers": [],
        "low_confidence_reviewers": [],
        "reviewer_inputs": [],
    }
    return {
        "decision": "revise",
        "available": True,
        "reason": "public_review_rejected",
        "reviewer_ids": [],
        "accepted_reviewers": [],
        "missing_reviewers": [],
        "blocking_findings": reasons,
        "reviewer_results": [],
        "panel_decision": panel_decision,
        "available_reviewers": [],
    }


def _stable_public_command_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"value": str(raw)}
    payload = dict(raw)
    if "duration_s" in payload:
        payload["duration_s"] = "<duration_s>"
    for text_key in ("stdout_tail", "stderr_tail"):
        if text_key in payload:
            payload[text_key] = _stable_command_text(str(payload[text_key]))
    return payload


def _command_evidence_status(
    command_results: Sequence[Mapping[str, Any]],
    *,
    name: str,
    empty_status: str,
) -> str:
    matching = [
        result for result in command_results
        if str(result.get("name") or "") == name
    ]
    if not matching:
        return empty_status
    return "passed" if all(str(result.get("status") or "") == "passed" for result in matching) else "failed"


def _public_execution_evidence(
    *,
    task: MergeabilityTask,
    public_candidate: MergeabilityCandidate,
    public_review: Mapping[str, Any],
    command_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    reasons = [str(reason) for reason in public_review.get("reasons") or []]
    gaming_flags = {str(flag) for flag in public_review.get("gaming_flags") or []}
    candidate_test_files = _candidate_test_files(public_candidate.files)
    candidate_test_status = _command_evidence_status(
        command_results,
        name="public_candidate_test",
        empty_status="not_submitted" if not candidate_test_files else "not_executed",
    )
    reverse_test_status = _command_evidence_status(
        command_results,
        name="public_reverse_test",
        empty_status="not_submitted" if not candidate_test_files else "not_executed",
    )
    if not candidate_test_files:
        reverse_classical_status = "not_submitted"
    elif candidate_test_status == "passed" and reverse_test_status == "passed":
        reverse_classical_status = "passed"
    elif candidate_test_status == "failed":
        reverse_classical_status = "candidate_tests_fail_on_patched_code"
    elif reverse_test_status == "failed":
        reverse_classical_status = "weak_or_non_discriminating_tests"
    else:
        reverse_classical_status = "not_executed"
    scope_failure_count = sum(1 for reason in reasons if reason.startswith("scope:"))
    protected_refs = list(public_review.get("protected_paths_present_in_review_worktree") or [])
    protected_candidate_path_count = sum(
        1 for path in set(public_candidate.changed_files) | set(public_candidate.files)
        if _matches_prefix(_normalise_relpath(path), task.protected_paths)
    )
    hidden_oracle_clean = "oracle_isolation_violation" not in gaming_flags
    return {
        "schema_version": "supervisor-mergeability-public-execution-evidence/v1",
        "evidence_boundary": "public_only",
        "patch_apply_check": {
            "status": "not_applicable",
            "reason": "fixture_candidate_file_overlay",
        },
        "candidate_file_overlay": {
            "status": "passed" if hidden_oracle_clean else "failed",
            "public_file_count": len(public_candidate.files),
            "public_changed_file_count": len(public_candidate.changed_files),
        },
        "public_candidate_tests": {
            "status": candidate_test_status,
            "submitted": bool(candidate_test_files),
            "candidate_test_file_count": len(candidate_test_files),
        },
        "reverse_classical_test_quality": {
            "status": reverse_classical_status,
            "candidate_tests_on_patched_code": candidate_test_status,
            "candidate_tests_on_original_code": reverse_test_status,
        },
        "public_ci_lint": {
            "status": _command_evidence_status(
                command_results,
                name="public_lint_build",
                empty_status="not_configured",
            ),
            "configured_command_count": len(task.lint_build_commands),
        },
        "scope_locality": {
            "status": "failed" if scope_failure_count else "passed",
            "scope_failure_count": scope_failure_count,
            "allowed_mutable_paths": list(task.allowed_mutable_paths),
            "public_changed_files": list(public_candidate.changed_files),
        },
        "protected_path_exclusion": {
            "status": "failed" if protected_refs or protected_candidate_path_count else "passed",
            "protected_path_content_included": False,
            "protected_candidate_path_count": protected_candidate_path_count,
            "protected_refs_in_public_worktree_count": len(protected_refs),
        },
        "hidden_oracle_exclusion": {
            "status": "passed" if hidden_oracle_clean else "failed",
            "hidden_oracle_material_included": False,
            "excluded_material_categories": [
                "held_out_behavior_tests",
                "answer_key_acceptance_labels",
                "answer_key_scores",
                "answer_key_expected_results",
                "protected_path_content",
            ],
        },
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
    runtime_receipt_ids = _public_review_runtime_receipt_refs(
        task=task,
        candidate=candidate,
        public_review=public_review,
    )
    stable_command_results = [
        _stable_public_command_payload(result)
        for result in public_review.get("command_results") or []
    ]
    packet = {
        "schema_version": SUPERVISOR_FULL_GATE_PACKET_SCHEMA_VERSION,
        "packet_id": f"mergeability-full-gate:{task.task_id}:{candidate.candidate_id}",
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "prompt": task.prompt,
        "changed_files": [
            {"path": path, "status": "modified"}
            for path in public_changed_files
        ],
        "acceptance_items": _full_gate_public_acceptance_items(
            task=task,
            public_review=public_review,
        ),
        "runtime_receipt_ids": runtime_receipt_ids,
        "public_evidence_refs": list(public_review.get("evidence_refs") or []),
        "public_execution_evidence": _public_execution_evidence(
            task=task,
            public_candidate=public_candidate,
            public_review=public_review,
            command_results=stable_command_results,
        ),
        "public_input_payload": public_input,
        "public_review": {
            "decision": str(public_review.get("decision") or ""),
            "accept": bool(public_review.get("accept")),
            "reasons": list(public_review.get("reasons") or []),
            "probe_results": list(public_review.get("probe_results") or []),
            "command_results": stable_command_results,
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


def _full_gate_public_acceptance_items(
    *,
    task: MergeabilityTask,
    public_review: Mapping[str, Any],
) -> list[str]:
    items = [
        f"task_prompt: {task.prompt}",
        "candidate_changes_stay_within_allowed_mutable_paths",
        "reviewer_uses_only_public_packet_evidence",
    ]
    items.extend(f"scope_constraint: {constraint}" for constraint in task.scope_constraints)
    items.extend(f"blocker_criterion: {criterion}" for criterion in task.blocker_criteria)
    probe_results = public_review.get("probe_results")
    if isinstance(probe_results, Sequence):
        for probe in probe_results:
            if not isinstance(probe, Mapping):
                continue
            probe_id = str(probe.get("probe_id") or "probe")
            reason = str(probe.get("reason") or "")
            items.append(f"public_probe:{probe_id}:{reason}")
    return sorted(dict.fromkeys(item for item in items if item.strip()))


def _public_review_runtime_receipt_refs(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    public_review: Mapping[str, Any],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    command_results = public_review.get("command_results")
    if isinstance(command_results, Sequence):
        for index, result in enumerate(command_results):
            if not isinstance(result, Mapping):
                continue
            name = str(result.get("name") or f"command_{index}")
            refs.append({
                "receipt_id": (
                    f"mergeability-public-review:{task.task_id}:"
                    f"{candidate.candidate_id}:{name}:{index}"
                ),
                "kind": "public_runtime_command",
                "status": str(result.get("status") or "unknown"),
                "source": "supervisor",
                "evidence_grade": "runtime_native",
            })
    refs.append({
        "receipt_id": f"mergeability-public-input:{task.task_id}:{candidate.candidate_id}",
        "kind": "public_review_packet",
        "status": str(public_review.get("decision") or "unknown"),
        "source": "supervisor",
        "evidence_grade": "runtime_native",
    })
    return refs


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
    reviewer_results_raw = raw.get("reviewer_results")
    reviewer_result_summaries = (
        [_reviewer_result_summary(item) for item in reviewer_results_raw]
        if isinstance(reviewer_results_raw, (list, tuple))
        else []
    )
    raw_panel_decision = raw.get("panel_decision")
    panel_decision_summary = (
        _reviewer_panel_decision_summary(raw_panel_decision)
        if isinstance(raw_panel_decision, Mapping)
        else None
    )
    available_reviewers_raw = raw.get("available_reviewers")
    if isinstance(available_reviewers_raw, (list, tuple)):
        available_reviewers = [str(item) for item in available_reviewers_raw]
    else:
        available_reviewers = [
            summary["reviewer_id"]
            for summary in reviewer_result_summaries
            if summary["verdict_present"]
        ]
    return {
        "schema_version": "supervisor-mergeability-reviewer-panel-result/v1",
        "decision": decision,
        "available": available,
        "reason": reason,
        "reviewer_ids": list(raw.get("reviewer_ids") or []),
        "accepted_reviewers": list(raw.get("accepted_reviewers") or []),
        "blocking_findings": list(raw.get("blocking_findings") or []),
        "missing_reviewers": list(raw.get("missing_reviewers") or []),
        "available_reviewers": available_reviewers,
        "reviewer_results": reviewer_result_summaries,
        "panel_decision": panel_decision_summary,
    }


def _reviewer_result_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {
            "reviewer_id": "unknown-reviewer",
            "decision": "unavailable",
            "verdict_present": False,
            "severity": "unknown",
            "runtime": "unknown",
            "model": None,
            "provider_family": "unknown",
            "lineage": [],
            "tool_access": "unknown",
            "tool_backed_command_evidence": False,
            "assurance_grade": "self_reported",
        }
    return {
        "reviewer_id": str(raw.get("reviewer_id") or "unknown-reviewer"),
        "decision": str(raw.get("decision") or "unavailable"),
        "verdict_present": bool(raw.get("verdict_present")),
        "accepted": bool(raw.get("accepted")),
        "severity": str(raw.get("severity") or "unknown"),
        "confidence": raw.get("confidence"),
        "runtime": str(raw.get("runtime") or raw.get("reviewer_runtime") or "unknown"),
        "model": raw.get("model"),
        "provider_family": str(raw.get("provider_family") or "unknown"),
        "lineage": list(raw.get("lineage") or []),
        "tool_access": str(raw.get("tool_access") or "unknown"),
        "tool_backed_command_evidence": bool(raw.get("tool_backed_command_evidence")),
        "assurance_grade": str(raw.get("assurance_grade") or "self_reported"),
        "reviewer_assurance": raw.get("reviewer_assurance"),
        "summary": str(raw.get("summary") or ""),
        "confidence_rationale": str(raw.get("confidence_rationale") or ""),
        "critical_review": dict(raw.get("critical_review") or {})
        if isinstance(raw.get("critical_review"), Mapping) else {},
        "tests": list(raw.get("tests") or []),
        "transcript_sha256": raw.get("transcript_sha256"),
        "output_sha256": raw.get("output_sha256"),
        "failure_classification": raw.get("failure_classification"),
        "failure_details": redact(
            dict(raw.get("failure_details") or {})
            if isinstance(raw.get("failure_details"), Mapping)
            else {}
        ),
        "diagnostics_failure": redact(
            dict(raw.get("diagnostics_failure") or {})
            if isinstance(raw.get("diagnostics_failure"), Mapping)
            else {}
        ),
        "reviewer_runtime": raw.get("reviewer_runtime") or raw.get("runtime"),
        "reviewer_output_mode": raw.get("reviewer_output_mode"),
        "worktree_isolation": (
            dict(raw.get("worktree_isolation"))
            if isinstance(raw.get("worktree_isolation"), Mapping)
            else None
        ),
    }


def _reviewer_rationales_from_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rationales: list[dict[str, Any]] = []
    for result in results:
        critical_review = (
            dict(result.get("critical_review") or {})
            if isinstance(result.get("critical_review"), Mapping)
            else {}
        )
        strongest_objection = str(critical_review.get("strongest_objection") or "")
        text = (
            str(result.get("summary") or "").strip()
            or str(result.get("confidence_rationale") or "").strip()
            or strongest_objection.strip()
            or str(critical_review.get("reason") or "").strip()
            or str(result.get("decision") or "").strip()
        )
        rationales.append({
            "reviewer_id": str(result.get("reviewer_id") or "unknown-reviewer"),
            "decision": str(result.get("decision") or "unavailable"),
            "severity": str(result.get("severity") or "unknown"),
            "confidence": result.get("confidence"),
            "summary": text,
            "strongest_objection": strongest_objection,
            "confidence_rationale": str(result.get("confidence_rationale") or ""),
        })
    return rationales


def _reviewer_panel_decision_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": str(
            raw.get("schema_version") or "independent-reviewer-panel-decision/v1"
        ),
        "decision": str(raw.get("decision") or "unavailable"),
        "reason": str(raw.get("reason") or ""),
        "aggregation_mode": str(raw.get("aggregation_mode") or "conservative"),
        "available_reviewers": list(raw.get("available_reviewers") or []),
        "accepted_reviewers": list(raw.get("accepted_reviewers") or []),
        "blocking_reviewers": list(raw.get("blocking_reviewers") or []),
        "non_accepting_reviewers": list(raw.get("non_accepting_reviewers") or []),
        "missing_reviewers": list(raw.get("missing_reviewers") or []),
        "low_confidence_reviewers": list(raw.get("low_confidence_reviewers") or []),
    }


def _default_unavailable_reviewer_panel(_packet: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "decision": "unavailable",
        "available": False,
        "reason": "reviewer_panel_not_configured",
    }


SUPERVISOR_CONFIGURED_PANEL_GATE = "mergeability_full_gate"
SUPERVISOR_CONFIGURED_PANEL_REVIEWER_MODE_DEFAULT = "cursor_sdk"


@dataclass(frozen=True)
class ConfiguredReviewerPanelOptions:
    """Knobs for the configured mergeability reviewer panel.

    Production callers set the reviewer roster knobs (output mode, model,
    runner). Tests inject ``reviewers`` and/or ``reviewer_invoker`` to bypass
    real reviewer infrastructure while still exercising the same registry
    aggregation seams (`independent_reviewer_results_from_review_results` and
    `evaluate_reviewer_panel`).
    """

    reviewer_output_mode: str = SUPERVISOR_CONFIGURED_PANEL_REVIEWER_MODE_DEFAULT
    reviewer_model: str | None = None
    codex_model: str = "gpt-5.5"
    reviewers: Sequence[ReviewerAdapter] | None = None
    runner: Callable[[CursorInvocationRequest], CursorInvocationResult] | None = None
    codex_runner: Callable[..., Any] | None = None
    reviewer_invoker: (
        Callable[[ReviewerAdapter, CursorInvocationRequest], CursorInvocationResult] | None
    ) = None
    review_cwd: str | Path | None = None
    review_timeout_s: int = 600
    cursor_api_key: str | None = None
    codex_config_path: str | Path | None = None
    load_codex_mcp_env: bool = True
    gate: str = SUPERVISOR_CONFIGURED_PANEL_GATE
    round_index: int = 0
    low_confidence_threshold: float = 0.0
    codex_only_calibration: bool = False


def build_configured_reviewer_panel(
    options: ConfiguredReviewerPanelOptions | None = None,
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Return a reviewer-panel callable backed by the configured reviewer roster.

    The returned callable converts a full-gate reviewer packet into an
    independent reviewer request, invokes every configured adapter (or the
    injected test substitutes), and aggregates the verdicts through the
    existing reviewer registry seam without leaking hidden oracle material.
    """

    opts = options or ConfiguredReviewerPanelOptions()

    def _panel(packet: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            adapters = _resolve_configured_panel_adapters(opts)
        except Exception as exc:  # pragma: no cover - registry errors surfaced
            return {
                "decision": "unavailable",
                "available": False,
                "reason": "reviewer_registry_unavailable",
                "blocking_findings": [f"{type(exc).__name__}: {exc}"],
                "reviewer_ids": [],
                "reviewer_results": [],
            }
        if not adapters:
            return {
                "decision": "unavailable",
                "available": False,
                "reason": "configured_reviewer_panel_empty",
                "reviewer_ids": [],
                "reviewer_results": [],
            }

        request = _configured_panel_review_request(packet, options=opts)
        task_id = str(packet.get("task_id") or "")
        review_results: list[tuple[ReviewerSpec, CursorInvocationResult]] = []
        per_adapter_errors: list[str] = []
        for adapter in adapters:
            try:
                if opts.reviewer_invoker is not None:
                    result = opts.reviewer_invoker(adapter, request)
                else:
                    result = adapter.review(request)
            except Exception as exc:
                per_adapter_errors.append(
                    f"{adapter.spec.reviewer_id}:{type(exc).__name__}:{exc}"
                )
                result = _configured_panel_infrastructure_failure_result(
                    spec=adapter.spec,
                    reason="reviewer_invocation_failed",
                    detail=str(exc),
                )
            if not isinstance(result, CursorInvocationResult):
                per_adapter_errors.append(
                    f"{adapter.spec.reviewer_id}:non_cursor_invocation_result"
                )
                result = _configured_panel_infrastructure_failure_result(
                    spec=adapter.spec,
                    reason="reviewer_invocation_returned_non_cursor_result",
                    detail=type(result).__name__,
                )
            review_results.append((adapter.spec, result))

        reviewer_results = independent_reviewer_results_from_review_results(
            review_results,
            task_id=task_id,
            gate=opts.gate,
            round_index=opts.round_index,
        )
        panel_decision = evaluate_reviewer_panel(
            reviewer_results,
            low_confidence_threshold=opts.low_confidence_threshold,
        )
        available_reviewers = list(panel_decision.get("available_reviewers") or [])
        missing_reviewers = list(panel_decision.get("missing_reviewers") or [])
        accepted_reviewers = list(panel_decision.get("accepted_reviewers") or [])
        reviewer_ids = [adapter.spec.reviewer_id for adapter in adapters]

        if not available_reviewers:
            return {
                "decision": "unavailable",
                "available": False,
                "reason": "reviewer_panel_unavailable",
                "reviewer_ids": reviewer_ids,
                "missing_reviewers": missing_reviewers,
                "blocking_findings": per_adapter_errors,
                "reviewer_results": reviewer_results,
                "panel_decision": panel_decision,
                "available_reviewers": available_reviewers,
            }

        decision = str(panel_decision.get("decision") or "unavailable")
        if decision == "escalate":
            decision = "revise"
        return {
            "decision": decision,
            "available": True,
            "reason": str(panel_decision.get("reason") or ""),
            "reviewer_ids": reviewer_ids,
            "accepted_reviewers": accepted_reviewers,
            "missing_reviewers": missing_reviewers,
            "blocking_findings": per_adapter_errors,
            "reviewer_results": reviewer_results,
            "panel_decision": panel_decision,
            "available_reviewers": available_reviewers,
        }

    return _panel


def _resolve_configured_panel_adapters(
    options: ConfiguredReviewerPanelOptions,
) -> list[ReviewerAdapter]:
    if options.reviewers is not None:
        return list(options.reviewers)
    runner = options.runner if options.runner is not None else invoke_cursor_agent
    codex_runner = options.codex_runner if options.codex_runner is not None else subprocess.run
    adapters = list(
        configured_reviewers(
            reviewer_output_mode=options.reviewer_output_mode,
            reviewer_model=options.reviewer_model,
            runner=runner,
            codex_runner=codex_runner,
            codex_model=options.codex_model,
        )
    )
    if options.codex_only_calibration:
        return [adapter for adapter in adapters if adapter.spec.runtime == "codex_cli"]
    return adapters


def _configured_panel_review_request(
    packet: Mapping[str, Any],
    *,
    options: ConfiguredReviewerPanelOptions,
) -> CursorInvocationRequest:
    cwd = Path(options.review_cwd) if options.review_cwd is not None else Path.cwd()
    api_key = _configured_panel_cursor_api_key(options)
    instruction = (
        "Independently review whether the mergeability candidate evidence should "
        "pass the full supervisor gate. Use only the public reviewer packet and "
        "do not rely on hidden tests, oracle labels, final scores, or protected "
        "path content."
    )
    return CursorInvocationRequest(
        task_id=str(packet.get("task_id") or "mergeability-full-gate"),
        gate=options.gate,
        instruction=instruction,
        cwd=str(cwd),
        review_packet=dict(packet),
        timeout_s=options.review_timeout_s,
        api_key=api_key,
        expected_specialists=("Independent Reviewer",),
    )


def _configured_panel_cursor_api_key(
    options: ConfiguredReviewerPanelOptions,
) -> str | None:
    if options.cursor_api_key:
        return options.cursor_api_key
    if options.load_codex_mcp_env and not os.environ.get("CURSOR_API_KEY"):
        config_path = (
            Path(options.codex_config_path).expanduser()
            if options.codex_config_path is not None
            else Path.home() / ".codex" / "config.toml"
        )
        _load_configured_panel_codex_mcp_env(config_path)
    return os.environ.get("CURSOR_API_KEY")


def _load_configured_panel_codex_mcp_env(path: Path) -> dict[str, str]:
    """Load the same Codex MCP env layer used by the workflow CLI.

    Mergeability diagnostics can run as direct library calls instead of through
    AXI/dispatcher startup. Those local calls still need the Cursor credential
    boundary hydrated, but the secret value must not be copied into reports or
    repo files.
    """

    try:
        from mcp_tools.codex_supervisor_workflow_cli import load_codex_mcp_env
    except Exception:
        return {}
    return load_codex_mcp_env(path.expanduser())


def _configured_panel_infrastructure_failure_result(
    *,
    spec: ReviewerSpec,
    reason: str,
    detail: str,
) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "INDEPENDENT_REVIEWER",
            "red",
            reason,
            {"detail": detail},
        ),
        outcome=None,
        transcript="",
        model=spec.model,
        reviewer_runtime=spec.runtime,
        reviewer_output_mode=spec.runtime,
        reviewer_assurance=spec.assurance_grade,
        failure_classification="reviewer_infrastructure",
        recoverable=False,
        attempts=1,
    )


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


def _candidate_is_public_pass_hidden_fail_trap(candidate: MergeabilityCandidate) -> bool:
    return bool(
        _candidate_expected_outcome(candidate) == "fail"
        and _candidate_control_kind(candidate) in PUBLIC_PASS_HIDDEN_FAIL_CONTROL_KINDS
        and _baseline_accepts(candidate)
        and (
            candidate.generator_metadata.get("visible_tests_passed") is True
            or candidate.provenance.get("visible_tests_passed") is True
        )
    )


def _heldout_task_missing_requirements(
    *,
    positive: list[str],
    negative: list[str],
    false_accept_traps: list[str],
) -> list[str]:
    missing: list[str] = []
    if not positive:
        missing.append("positive_control")
    if not negative:
        missing.append("negative_control")
    if not false_accept_traps:
        missing.append("public_pass_hidden_fail_trap")
    return missing


def _is_fixture_diagnostic_growth_candidate(candidate: MergeabilityCandidate) -> bool:
    provenance = candidate.provenance or {}
    kind = str(provenance.get("kind") or "").strip()
    source_slice = str(provenance.get("source_slice") or "").strip()
    return (
        kind == FIXTURE_DIAGNOSTIC_GROWTH_KIND
        and source_slice == FIXTURE_DIAGNOSTIC_GROWTH_SOURCE_SLICE
    )


def _fixture_diagnostic_growth_added_candidate_ids(
    candidates: Sequence[MergeabilityCandidate],
) -> list[str]:
    return sorted(
        candidate.candidate_id
        for candidate in candidates
        if _is_fixture_diagnostic_growth_candidate(candidate)
    )


def _build_fixture_growth_rationale(
    candidates: Sequence[MergeabilityCandidate],
) -> dict[str, Any]:
    added = _fixture_diagnostic_growth_added_candidate_ids(candidates)
    return {
        "source_slice": FIXTURE_DIAGNOSTIC_GROWTH_SOURCE_SLICE,
        "rationale": FIXTURE_DIAGNOSTIC_GROWTH_RATIONALE_TEXT,
        "slice1a_s_probe_true_accept_rate": SLICE_1A_S_PROBE_TRUE_ACCEPT_RATE,
        "slice1a_s_full_true_accept_rate": SLICE_1A_S_FULL_TRUE_ACCEPT_RATE,
        "slice1a_full_gate_matched_true_accept_status": SLICE_1A_FULL_GATE_MATCHED_TAR_STATUS,
        "slice1a_positive_control_count": SLICE_1A_POSITIVE_CONTROL_COUNT,
        "added_positive_candidate_ids": added,
        "added_positive_candidate_count": len(added),
        "growth_kind": FIXTURE_DIAGNOSTIC_GROWTH_KIND,
    }


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


def _no_regression_findings(
    rows: list[dict[str, Any]],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidate_meta = {
        (entry["task_id"], entry["candidate_id"]): entry
        for entry in manifest.get("candidates", [])
    }
    findings: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("is_no_regression_failure"):
            continue
        meta = candidate_meta.get((row["task_id"], row["candidate_id"]), {})
        findings.append({
            "task_id": row["task_id"],
            "task_class": meta.get("task_class", row["task_id"]),
            "split": meta.get("split", "held_out"),
            "candidate_id": row["candidate_id"],
            "control_kind": row["control_kind"],
            "baseline_accept": bool(row["baseline_accept"]),
            "supervisor_candidate_review_accept": bool(row["supervisor_candidate_review_accept"]),
            "supervisor_full_gate_accept": bool(row["supervisor_full_gate_accept"]),
            "oracle_accept": bool(row["oracle_accept"]),
            "reason": "prior_true_positive_accept_rejected_by_supervisor_full_gate",
            "protected_scope": "baseline_accepted_oracle_positive_cases",
            "receipt_id": row["receipt_id"],
        })
    return sorted(
        findings,
        key=lambda item: (str(item["task_class"]), str(item["task_id"]), str(item["candidate_id"])),
    )


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
    evidence_kind: str | None = None,
) -> dict[str, Any]:
    accept_key = f"{arm}_accept"
    unavailable_key = f"{arm}_unavailable"
    available_rows = [row for row in rows if not bool(row.get(unavailable_key))]
    false_accept_denominator = sum(1 for row in available_rows if not row["oracle_accept"])
    true_accept_denominator = sum(1 for row in available_rows if row["oracle_accept"])
    false_accept_count = sum(
        1 for row in available_rows if row[accept_key] and not row["oracle_accept"]
    )
    true_accept_count = sum(
        1 for row in available_rows if row[accept_key] and row["oracle_accept"]
    )
    false_reject_count = sum(
        1 for row in available_rows if not row[accept_key] and row["oracle_accept"]
    )
    true_reject_count = sum(
        1 for row in available_rows if not row[accept_key] and not row["oracle_accept"]
    )
    unavailable_count = sum(1 for row in rows if bool(row.get(unavailable_key)))
    summary = {
        "arm": arm,
        "arm_role": arm_role,
        "decision_source": decision_source,
        "oracle_coupled": oracle_coupled,
        "candidate_count": len(rows),
        "available_count": len(available_rows),
        "unavailable_count": unavailable_count,
        "availability_status": "unavailable" if rows and unavailable_count == len(rows) else "available",
        "accepted_count": sum(1 for row in available_rows if row[accept_key]),
        "rejected_count": sum(1 for row in available_rows if not row[accept_key]),
        "false_accept_count": false_accept_count,
        "n_bad": false_accept_denominator,
        "false_accept_denominator": false_accept_denominator,
        "false_accept_rate": _rate(false_accept_count, false_accept_denominator),
        "false_accept_confidence_interval": _wilson_interval(false_accept_count, false_accept_denominator),
        "true_accept_count": true_accept_count,
        "n_good": true_accept_denominator,
        "true_accept_denominator": true_accept_denominator,
        "true_accept_rate": _rate(true_accept_count, true_accept_denominator),
        "true_accept_confidence_interval": _wilson_interval(true_accept_count, true_accept_denominator),
        "false_reject_count": false_reject_count,
        "false_reject_denominator": true_accept_denominator,
        "false_reject_rate": _rate(false_reject_count, true_accept_denominator),
        "true_reject_count": true_reject_count,
        "cost_usd": 0.0,
    }
    if evidence_kind is not None:
        summary["evidence_kind"] = evidence_kind
    return summary


def _normalise_mergeability_reviewer_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    reviewer_id = str(raw.get("reviewer_id") or raw.get("id") or "reviewer")
    decision = str(raw.get("decision") or raw.get("status") or "unavailable")
    if decision == "approved":
        decision = "accept"
    if decision == "reject":
        decision = "deny"
    if decision not in {"accept", "deny", "revise", "unavailable"}:
        decision = "unavailable"
    verdict_present = bool(raw.get("verdict_present"))
    available = bool(verdict_present and decision in {"accept", "deny", "revise"})
    runtime = str(raw.get("runtime") or raw.get("reviewer_runtime") or "unknown")
    model = raw.get("model")
    provider_family = str(
        raw.get("provider_family") or _provider_family_from_runtime_model(runtime, model)
    )
    lineage_raw = raw.get("lineage")
    lineage = (
        list(lineage_raw)
        if isinstance(lineage_raw, (list, tuple))
        else _reviewer_lineage(runtime, model, provider_family)
    )
    tool_backed_command_evidence = bool(raw.get("tool_backed_command_evidence"))
    tool_access = str(raw.get("tool_access") or _tool_access_from_runtime(runtime))
    assurance_grade = str(raw.get("assurance_grade") or "self_reported")
    if "litellm" in runtime.lower() and not tool_backed_command_evidence:
        tool_access = "text_only"
        assurance_grade = "text_only"
    return {
        "reviewer_id": reviewer_id,
        "decision": decision,
        "accept": bool(available and decision == "accept" and raw.get("accepted", True)),
        "available": available,
        "verdict_present": verdict_present,
        "reason": str(raw.get("reason") or raw.get("unavailable_reason") or ""),
        "failure_classification": raw.get("failure_classification"),
        "runtime": runtime,
        "reviewer_runtime": raw.get("reviewer_runtime") or raw.get("runtime"),
        "model": model,
        "provider_family": provider_family,
        "lineage": lineage,
        "tool_access": tool_access,
        "assurance_grade": assurance_grade,
        "tool_backed_command_evidence": tool_backed_command_evidence,
        "transcript_sha256": raw.get("transcript_sha256"),
        "output_sha256": raw.get("output_sha256"),
        "worktree_isolation": raw.get("worktree_isolation"),
    }


def _provider_family_from_runtime_model(runtime: Any, model: Any) -> str:
    runtime_text = str(runtime or "").lower()
    model_text = str(model or "").lower()
    if "codex" in runtime_text:
        return "openai"
    if "cursor" in runtime_text:
        return "cursor"
    if "gemini" in model_text:
        return "google"
    if "claude" in model_text:
        return "anthropic"
    if "gpt" in model_text or "openai" in runtime_text:
        return "openai"
    if "litellm" in runtime_text:
        return "openai_compatible"
    provider_text = str(model or runtime or "").lower()
    if "openai" in provider_text:
        return "openai"
    return "unknown"


def _tool_access_from_runtime(runtime: Any) -> str:
    runtime_text = str(runtime or "").lower()
    if "codex_cli" in runtime_text or "cursor" in runtime_text:
        return "codebase_tools"
    if "litellm" in runtime_text:
        return "text_only"
    return "unknown"


def _reviewer_lineage(runtime: Any, model: Any, provider_family: str) -> list[str]:
    values = [provider_family, str(runtime or ""), str(model or "")]
    return list(dict.fromkeys(value for value in values if value and value != "unknown"))


def _provenance_value(items: Sequence[Mapping[str, Any]], key: str, default: Any) -> Any:
    values = {
        item.get(key)
        for item in items
        if item.get(key) not in (None, "", [])
    }
    if not values:
        return default
    if len(values) == 1:
        return next(iter(values))
    return "mixed"


def _reviewer_cross_family_claim_status(item: Mapping[str, Any]) -> tuple[str, bool]:
    runtime = str(item.get("runtime") or item.get("reviewer_runtime") or "").lower()
    model = str(item.get("model") or "").strip().lower()
    provider_family = str(item.get("provider_family") or "unknown")
    if "cursor" in runtime and model in {"", "default"}:
        return ("unproven_default_model", False)
    if provider_family in {"", "unknown", "openai_compatible"}:
        return ("unproven_provider_family", False)
    return ("recorded_provider_family", True)


def _reviewer_provenance_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_reviewer: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for raw_result in row.get("supervisor_full_gate_reviewer_results") or []:
            if not isinstance(raw_result, Mapping):
                continue
            normalised = _normalise_mergeability_reviewer_result(raw_result)
            by_reviewer.setdefault(normalised["reviewer_id"], []).append(normalised)

    reviewers: list[dict[str, Any]] = []
    for reviewer_id, entries in sorted(by_reviewer.items()):
        runtime = _provenance_value(entries, "runtime", "unknown")
        model = _provenance_value(entries, "model", None)
        provider_family = _provenance_value(entries, "provider_family", "unknown")
        lineage_values: list[str] = []
        for entry in entries:
            for value in entry.get("lineage") or []:
                value_text = str(value)
                if value_text and value_text not in lineage_values:
                    lineage_values.append(value_text)
        tool_access = _provenance_value(entries, "tool_access", "unknown")
        assurance_grade = _provenance_value(entries, "assurance_grade", "self_reported")
        tool_backed_command_evidence = any(
            bool(entry.get("tool_backed_command_evidence")) for entry in entries
        )
        if str(runtime).lower() == "litellm_structured" and not tool_backed_command_evidence:
            tool_access = "text_only"
            assurance_grade = "text_only"
        status, counts_as_proven_cross_family = _reviewer_cross_family_claim_status({
            "runtime": runtime,
            "model": model,
            "provider_family": provider_family,
        })
        reviewers.append({
            "reviewer_id": reviewer_id,
            "runtime": runtime,
            "model": model,
            "provider_family": provider_family,
            "lineage": lineage_values,
            "tool_access": tool_access,
            "assurance_grade": assurance_grade,
            "tool_backed_command_evidence": tool_backed_command_evidence,
            "cross_family_claim_status": status,
            "counts_as_proven_cross_family": counts_as_proven_cross_family,
            "candidate_count": len(entries),
            "available_count": sum(1 for entry in entries if entry["available"]),
        })

    cursor_default_ids = [
        item["reviewer_id"]
        for item in reviewers
        if item["cross_family_claim_status"] == "unproven_default_model"
    ]
    text_only_ids = [
        item["reviewer_id"]
        for item in reviewers
        if item["tool_access"] == "text_only" and not item["tool_backed_command_evidence"]
    ]
    return {
        "schema_version": "supervisor-mergeability-reviewer-provenance/v1",
        "reviewer_count": len(reviewers),
        "reviewers": reviewers,
        "cursor_default_unproven_reviewer_ids": cursor_default_ids,
        "text_only_reviewer_ids": text_only_ids,
        "tool_backed_reviewer_ids": [
            item["reviewer_id"]
            for item in reviewers
            if item["tool_access"] == "codebase_tools"
        ],
        "tool_backed_command_evidence_reviewer_ids": [
            item["reviewer_id"]
            for item in reviewers
            if item["tool_backed_command_evidence"]
        ],
    }


def _producer_family_from_row(row: Mapping[str, Any]) -> str:
    producer = row.get("single_agent_baseline_producer")
    if not isinstance(producer, Mapping):
        return "unknown"
    explicit = str(producer.get("provider_family") or "").strip().lower()
    if explicit:
        return explicit
    provider = str(producer.get("provider") or producer.get("family") or "").strip().lower()
    model = str(producer.get("model") or "").strip().lower()
    return _provider_family_from_runtime_model(provider, model)


def _generator_disjointness_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    known_generator_rows = 0
    for row in rows:
        generator_family = _producer_family_from_row(row)
        if generator_family == "unknown":
            continue
        known_generator_rows += 1
        available_reviewers: list[dict[str, Any]] = []
        for raw_result in row.get("supervisor_full_gate_reviewer_results") or []:
            if not isinstance(raw_result, Mapping):
                continue
            normalised = _normalise_mergeability_reviewer_result(raw_result)
            if normalised["available"]:
                available_reviewers.append(normalised)
        if len(available_reviewers) != 1:
            continue
        reviewer = available_reviewers[0]
        reviewer_family = str(reviewer.get("provider_family") or "unknown")
        if reviewer_family != "unknown" and reviewer_family == generator_family:
            warnings.append({
                "task_id": str(row.get("task_id") or ""),
                "candidate_id": str(row.get("candidate_id") or ""),
                "generator_family": generator_family,
                "reviewer_id": reviewer["reviewer_id"],
                "reviewer_family": reviewer_family,
                "reviewer_decision": reviewer["decision"],
                "reason": "sole_same_family_decisive_reviewer",
            })
    return {
        "schema_version": "supervisor-mergeability-generator-disjointness/v1",
        "generator_family_source": "single_agent_baseline_producer",
        "known_generator_family_row_count": known_generator_rows,
        "same_family_decisive_vote_count": len(warnings),
        "self_preference_warnings": warnings,
    }


def _per_reviewer_acceptance_arms(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    per_reviewer_candidates: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for raw_result in row.get("supervisor_full_gate_reviewer_results") or []:
            if not isinstance(raw_result, Mapping):
                continue
            normalised = _normalise_mergeability_reviewer_result(raw_result)
            reviewer_id = normalised["reviewer_id"]
            per_reviewer_candidates.setdefault(reviewer_id, []).append({
                "task_id": row["task_id"],
                "candidate_id": row["candidate_id"],
                "candidate_hash": row.get("candidate_hash"),
                "control_kind": row.get("control_kind"),
                "oracle_accept": bool(row["oracle_accept"]),
                "accept": bool(normalised["accept"]),
                "available": bool(normalised["available"]),
                "unavailable": not bool(normalised["available"]),
                "decision": normalised["decision"],
                "verdict_present": bool(normalised["verdict_present"]),
                "reason": normalised["reason"],
                "failure_classification": normalised["failure_classification"],
                "runtime": normalised["runtime"],
                "reviewer_runtime": normalised["reviewer_runtime"],
                "model": normalised["model"],
                "provider_family": normalised["provider_family"],
                "lineage": normalised["lineage"],
                "tool_access": normalised["tool_access"],
                "assurance_grade": normalised["assurance_grade"],
                "tool_backed_command_evidence": normalised["tool_backed_command_evidence"],
                "transcript_sha256": normalised["transcript_sha256"],
                "output_sha256": normalised["output_sha256"],
                "worktree_isolation": normalised["worktree_isolation"],
            })

    arms: dict[str, dict[str, Any]] = {}
    for reviewer_id, candidate_rows in sorted(per_reviewer_candidates.items()):
        available_rows = [row for row in candidate_rows if row["available"]]
        false_accept_denominator = sum(1 for row in available_rows if not row["oracle_accept"])
        true_accept_denominator = sum(1 for row in available_rows if row["oracle_accept"])
        false_accept_count = sum(
            1 for row in available_rows if row["accept"] and not row["oracle_accept"]
        )
        true_accept_count = sum(
            1 for row in available_rows if row["accept"] and row["oracle_accept"]
        )
        false_reject_count = sum(
            1 for row in available_rows if not row["accept"] and row["oracle_accept"]
        )
        true_reject_count = sum(
            1 for row in available_rows if not row["accept"] and not row["oracle_accept"]
        )
        unavailable_count = sum(1 for row in candidate_rows if row["unavailable"])
        runtimes = sorted({
            str(row.get("reviewer_runtime") or row.get("runtime") or "")
            for row in candidate_rows
            if row.get("reviewer_runtime") or row.get("runtime")
        })
        models = sorted({
            str(row.get("model") or "")
            for row in candidate_rows
            if row.get("model")
        })
        provider_families = sorted({
            str(row.get("provider_family") or "")
            for row in candidate_rows
            if row.get("provider_family")
        })
        tool_accesses = sorted({
            str(row.get("tool_access") or "")
            for row in candidate_rows
            if row.get("tool_access")
        })
        assurance_grades = sorted({
            str(row.get("assurance_grade") or "")
            for row in candidate_rows
            if row.get("assurance_grade")
        })
        lineage_values: list[str] = []
        for row in candidate_rows:
            for value in row.get("lineage") or []:
                value_text = str(value)
                if value_text and value_text not in lineage_values:
                    lineage_values.append(value_text)
        tool_backed_command_evidence = any(
            bool(row.get("tool_backed_command_evidence")) for row in candidate_rows
        )
        arms[reviewer_id] = {
            "arm": reviewer_id,
            "arm_role": "independent_reviewer",
            "reviewer_id": reviewer_id,
            "decision_source": "independent_reviewer_panel_member",
            "oracle_coupled": False,
            "candidate_count": len(candidate_rows),
            "available_count": len(available_rows),
            "unavailable_count": unavailable_count,
            "availability_status": (
                "unavailable"
                if candidate_rows and unavailable_count == len(candidate_rows)
                else "available"
            ),
            "accepted_count": sum(1 for row in available_rows if row["accept"]),
            "rejected_count": sum(1 for row in available_rows if not row["accept"]),
            "false_accept_count": false_accept_count,
            "n_bad": false_accept_denominator,
            "false_accept_denominator": false_accept_denominator,
            "false_accept_rate": _rate(false_accept_count, false_accept_denominator),
            "false_accept_confidence_interval": _wilson_interval(
                false_accept_count,
                false_accept_denominator,
            ),
            "true_accept_count": true_accept_count,
            "n_good": true_accept_denominator,
            "true_accept_denominator": true_accept_denominator,
            "true_accept_rate": _rate(true_accept_count, true_accept_denominator),
            "true_accept_confidence_interval": _wilson_interval(
                true_accept_count,
                true_accept_denominator,
            ),
            "false_reject_count": false_reject_count,
            "false_reject_denominator": true_accept_denominator,
            "false_reject_rate": _rate(false_reject_count, true_accept_denominator),
            "true_reject_count": true_reject_count,
            "runtime": runtimes[0] if len(runtimes) == 1 else ("mixed" if runtimes else "unknown"),
            "model": models[0] if len(models) == 1 else ("mixed" if models else None),
            "provider_family": provider_families[0]
            if len(provider_families) == 1 else ("mixed" if provider_families else "unknown"),
            "lineage": lineage_values,
            "tool_access": tool_accesses[0]
            if len(tool_accesses) == 1 else ("mixed" if tool_accesses else "unknown"),
            "assurance_grade": assurance_grades[0]
            if len(assurance_grades) == 1 else ("mixed" if assurance_grades else "self_reported"),
            "tool_backed_command_evidence": tool_backed_command_evidence,
            "cost_usd": 0.0,
            "per_candidate_results": candidate_rows,
        }
    return arms


def _inter_reviewer_agreement(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_reviewer: dict[str, dict[str, bool]] = {}
    for row in rows:
        candidate_key = f"{row['task_id']}:{row['candidate_id']}"
        for raw_result in row.get("supervisor_full_gate_reviewer_results") or []:
            if not isinstance(raw_result, Mapping):
                continue
            normalised = _normalise_mergeability_reviewer_result(raw_result)
            if not normalised["available"]:
                continue
            by_reviewer.setdefault(normalised["reviewer_id"], {})[candidate_key] = bool(
                normalised["accept"]
            )
    reviewers = sorted(by_reviewer)
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(reviewers):
        for right in reviewers[left_index + 1:]:
            shared = sorted(set(by_reviewer[left]) & set(by_reviewer[right]))
            agreement_count = sum(
                1
                for candidate_key in shared
                if by_reviewer[left][candidate_key] == by_reviewer[right][candidate_key]
            )
            pairs.append({
                "reviewer_pair": [left, right],
                "shared_candidate_count": len(shared),
                "agreement_count": agreement_count,
                "agreement_rate": _rate(agreement_count, len(shared)),
            })
    return pairs


def _reviewer_packet_leak_refs(rows: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for row in rows:
        review = row.get("supervisor_full_gate_review")
        if not isinstance(review, Mapping):
            continue
        packet = review.get("reviewer_packet")
        if not isinstance(packet, Mapping):
            continue
        prefix = f"{row['task_id']}:{row['candidate_id']}"
        refs.extend(f"{prefix}:{ref}" for ref in _public_input_oracle_refs(packet))
    return sorted(set(refs))


def _validate_factorial_arm_pool(
    arm_decisions: Mapping[str, Mapping[str, Any]],
    *,
    candidate_ids: tuple[str, ...],
) -> None:
    expected = set(candidate_ids)
    for arm, decisions in arm_decisions.items():
        if arm not in FACTORIAL_ARM_DEFINITIONS:
            raise MergeabilityBenchError(f"unknown factorial arm: {arm}")
        actual = set(str(candidate_id) for candidate_id in decisions)
        extra = sorted(actual - expected)
        if arm == "single_agent_baseline":
            if extra:
                raise MergeabilityBenchError(
                    "candidate pool mismatch for factorial arm "
                    f"{arm}: missing=[] extra={extra}"
                )
            continue
        if actual != expected:
            missing = sorted(expected - actual)
            raise MergeabilityBenchError(
                "candidate pool mismatch for factorial arm "
                f"{arm}: missing={missing} extra={extra}"
            )


def _resolve_powered_baseline_decision(
    *,
    raw: Any,
    expected_candidate_artifact_hash: str,
    expected_candidate_id: str | None = None,
) -> dict[str, Any]:
    """Normalise a powered ``single_agent_baseline`` arm row.

    Missing, malformed, hash-mismatched, or explicitly unavailable rows mark
    the baseline decision unavailable so the powered factorial measurement path
    cannot silently inherit a metadata acceptance default.
    """
    unavailable_template = {
        "accept": False,
        "unavailable": True,
        "decision_source": "produced_single_agent_baseline_unavailable",
        "candidate_id": expected_candidate_id or "",
        "candidate_artifact_hash": "",
        "producer": {},
        "prompt_sha256": "",
        "evidence_kind": "missing",
        "unavailable_reason": "baseline_decisions_not_supplied",
    }
    if raw is None:
        return dict(unavailable_template)
    if isinstance(raw, Mapping):
        explicit_unavailable = bool(raw.get("unavailable"))
        supplied_hash = str(raw.get("candidate_artifact_hash") or "")
        supplied_candidate_id = str(raw.get("candidate_id") or "")
        supplied_decision_source = str(raw.get("decision_source") or "").strip()
        producer_raw = raw.get("producer")
        producer = dict(producer_raw) if isinstance(producer_raw, Mapping) else {}
        runner_label = str(
            raw.get("runner_label")
            or producer.get("runner_label")
            or producer.get("runner")
            or producer.get("agent")
            or ""
        ).strip()
        model_label = str(raw.get("model") or producer.get("model") or "").strip()
        if expected_candidate_id is not None and supplied_candidate_id:
            if supplied_candidate_id != expected_candidate_id:
                decision = dict(unavailable_template)
                decision["candidate_id"] = supplied_candidate_id
                decision["candidate_artifact_hash"] = supplied_hash
                decision["unavailable_reason"] = "candidate_id_mismatch"
                decision["evidence_kind"] = "candidate_id_mismatch"
                decision["producer"] = producer
                decision["prompt_sha256"] = str(raw.get("prompt_sha256") or "")
                return decision
        if explicit_unavailable:
            decision = dict(unavailable_template)
            decision["candidate_id"] = supplied_candidate_id or expected_candidate_id or ""
            decision["candidate_artifact_hash"] = supplied_hash
            decision["unavailable_reason"] = str(
                raw.get("unavailable_reason") or "baseline_producer_marked_unavailable"
            )
            decision["evidence_kind"] = "explicit_unavailable"
            decision["producer"] = producer
            decision["prompt_sha256"] = str(raw.get("prompt_sha256") or "")
            return decision
        if not supplied_hash:
            decision = dict(unavailable_template)
            decision["candidate_id"] = supplied_candidate_id or expected_candidate_id or ""
            decision["unavailable_reason"] = "malformed_baseline_row_missing_candidate_artifact_hash"
            decision["evidence_kind"] = "malformed"
            decision["producer"] = producer
            decision["prompt_sha256"] = str(raw.get("prompt_sha256") or "")
            return decision
        if supplied_hash != expected_candidate_artifact_hash:
            decision = dict(unavailable_template)
            decision["candidate_id"] = supplied_candidate_id or expected_candidate_id or ""
            decision["candidate_artifact_hash"] = supplied_hash
            decision["unavailable_reason"] = "candidate_artifact_hash_mismatch"
            decision["evidence_kind"] = "hash_mismatch"
            decision["producer"] = producer
            decision["prompt_sha256"] = str(raw.get("prompt_sha256") or "")
            return decision
        missing_replay_fields: list[str] = []
        if expected_candidate_id is not None and not supplied_candidate_id:
            missing_replay_fields.append("candidate_id")
        if not isinstance(raw.get("accept"), bool):
            missing_replay_fields.append("accept")
        if not supplied_decision_source:
            missing_replay_fields.append("decision_source")
        if not isinstance(producer, Mapping) or not producer:
            missing_replay_fields.append("producer")
        if expected_candidate_id is not None and not model_label:
            missing_replay_fields.append("model")
        if expected_candidate_id is not None and not runner_label:
            missing_replay_fields.append("runner_label")
        if not str(raw.get("prompt_sha256") or "").strip():
            missing_replay_fields.append("prompt_sha256")
        if missing_replay_fields:
            decision = dict(unavailable_template)
            decision["candidate_id"] = supplied_candidate_id or expected_candidate_id or ""
            decision["candidate_artifact_hash"] = supplied_hash
            decision["unavailable_reason"] = (
                "malformed_baseline_row_missing_replay_evidence:"
                + ",".join(sorted(set(missing_replay_fields)))
            )
            decision["evidence_kind"] = "malformed"
            decision["producer"] = producer
            decision["prompt_sha256"] = str(raw.get("prompt_sha256") or "")
            return decision
        if supplied_decision_source not in PRODUCED_BASELINE_DECISION_SOURCES:
            decision = dict(unavailable_template)
            decision["candidate_id"] = supplied_candidate_id or expected_candidate_id or ""
            decision["candidate_artifact_hash"] = supplied_hash
            decision["unavailable_reason"] = (
                "malformed_baseline_row_untrusted_decision_source:"
                + supplied_decision_source
            )
            decision["evidence_kind"] = "malformed"
            decision["producer"] = producer
            decision["prompt_sha256"] = str(raw.get("prompt_sha256") or "")
            return decision
        return {
            "accept": bool(raw.get("accept")),
            "unavailable": False,
            "decision_source": supplied_decision_source,
            "candidate_id": supplied_candidate_id or expected_candidate_id or "",
            "candidate_artifact_hash": supplied_hash,
            "producer": producer,
            "prompt_sha256": str(raw.get("prompt_sha256") or ""),
            "evidence_kind": "produced_single_agent_baseline",
            "unavailable_reason": "",
        }
    decision = dict(unavailable_template)
    decision["unavailable_reason"] = "legacy_bool_baseline_row_not_replayable"
    decision["evidence_kind"] = "legacy_bool"
    return decision


def _factorial_arm_decision(
    *,
    arm_decisions: Mapping[str, Mapping[str, Any]],
    arm: str,
    candidate_id: str,
    default: Mapping[str, Any],
) -> dict[str, Any]:
    per_arm = arm_decisions.get(arm)
    if per_arm is None:
        return {
            "accept": bool(default.get("accept")),
            "unavailable": bool(default.get("unavailable")),
            "decision_source": str(default.get("decision_source") or FACTORIAL_ARM_DEFINITIONS[arm]["decision_source"]),
        }
    raw = per_arm[candidate_id]
    if isinstance(raw, Mapping):
        return {
            "accept": bool(raw.get("accept")),
            "unavailable": bool(raw.get("unavailable")),
            "decision_source": str(raw.get("decision_source") or FACTORIAL_ARM_DEFINITIONS[arm]["decision_source"]),
        }
    return {
        "accept": bool(raw),
        "unavailable": False,
        "decision_source": str(FACTORIAL_ARM_DEFINITIONS[arm]["decision_source"]),
    }


def _normalise_factorial_reviewer_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    reviewer_id = str(raw.get("reviewer_id") or raw.get("id") or "reviewer")
    decision = str(raw.get("decision") or raw.get("status") or "unavailable")
    if decision == "approved":
        decision = "accept"
    if decision == "reject":
        decision = "deny"
    if decision not in {"accept", "deny", "revise", "unavailable"}:
        decision = "unavailable"
    available = bool(raw.get("available", decision != "unavailable"))
    return {
        "reviewer_id": reviewer_id,
        "decision": decision,
        "accept": bool(available and decision == "accept"),
        "available": available,
        "reason": str(raw.get("reason") or raw.get("unavailable_reason") or ""),
    }


def _factorial_full_stack_decision(
    *,
    public_accept: bool,
    reviewer_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    normalised = [_normalise_factorial_reviewer_result(result) for result in reviewer_results]
    if not normalised:
        return {
            "accept": False,
            "unavailable": True,
            "decision_source": "runtime_evidence_floor+independent_reviewer_panel",
        }
    if any(not result["available"] for result in normalised):
        return {
            "accept": False,
            "unavailable": True,
            "decision_source": "runtime_evidence_floor+independent_reviewer_panel",
        }
    panel_accept = all(result["decision"] == "accept" for result in normalised)
    return {
        "accept": bool(public_accept and panel_accept),
        "unavailable": False,
        "decision_source": "runtime_evidence_floor+independent_reviewer_panel",
    }


def _matched_true_accept_for_factorial_arm(
    *,
    baseline: Mapping[str, Any],
    arm: Mapping[str, Any],
) -> dict[str, Any]:
    unavailable_count = int(arm.get("unavailable_count") or 0)
    if unavailable_count:
        return {
            "status": "unavailable",
            "reason": "arm_unavailable",
            "unavailable_count": unavailable_count,
        }
    return _false_accept_at_matched_true_accept(baseline=baseline, supervisor=arm)


def _paired_discordant_counts(
    rows: list[dict[str, Any]],
    *,
    left_arm: str,
    right_arm: str,
) -> dict[str, Any]:
    left_key = f"{left_arm}_accept"
    right_key = f"{right_arm}_accept"
    left_accept_right_reject = sum(1 for row in rows if row[left_key] and not row[right_key])
    left_reject_right_accept = sum(1 for row in rows if not row[left_key] and row[right_key])
    both_accept = sum(1 for row in rows if row[left_key] and row[right_key])
    both_reject = sum(1 for row in rows if not row[left_key] and not row[right_key])
    return {
        "left_arm": left_arm,
        "right_arm": right_arm,
        "candidate_count": len(rows),
        "left_accept_right_reject": left_accept_right_reject,
        "left_reject_right_accept": left_reject_right_accept,
        "both_accept": both_accept,
        "both_reject": both_reject,
    }


def _leave_one_reviewer_out_analysis(
    rows: list[dict[str, Any]],
    *,
    reviewer_panel_results: Mapping[str, list[Mapping[str, Any]]],
    full_stack: Mapping[str, Any],
) -> dict[str, Any]:
    reviewers = sorted({
        _normalise_factorial_reviewer_result(result)["reviewer_id"]
        for results in reviewer_panel_results.values()
        for result in results
    })
    if not reviewers:
        return {
            "status": "unavailable",
            "reason": "reviewer_panel_unavailable",
            "reviewer_effects": [],
            "reviewer_correlation": {"pairwise_agreement": []},
        }
    effects = []
    for reviewer_id in reviewers:
        leave_rows: list[dict[str, Any]] = []
        for row in rows:
            remaining = [
                result
                for result in reviewer_panel_results.get(str(row["candidate_id"]), [])
                if _normalise_factorial_reviewer_result(result)["reviewer_id"] != reviewer_id
            ]
            decision = _factorial_full_stack_decision(
                public_accept=bool(row["runtime_evidence_floor_accept"]),
                reviewer_results=remaining,
            )
            leave_row = dict(row)
            leave_row["leave_one_out_accept"] = bool(decision["accept"])
            leave_row["leave_one_out_unavailable"] = bool(decision["unavailable"])
            leave_rows.append(leave_row)
        summary = _summarize_acceptance_arm(
            leave_rows,
            arm="leave_one_out",
            arm_role="leave_one_out",
            decision_source="leave_one_reviewer_out",
            oracle_coupled=False,
        )
        effects.append({
            "reviewer_id": reviewer_id,
            "remaining_reviewer_count": max(0, len(reviewers) - 1),
            "full_stack_false_accept_rate": full_stack.get("false_accept_rate"),
            "leave_one_out_false_accept_rate": summary["false_accept_rate"],
            "false_accept_rate_delta": round(
                float(summary["false_accept_rate"] or 0.0)
                - float(full_stack.get("false_accept_rate") or 0.0),
                6,
            ),
            "full_stack_true_accept_rate": full_stack.get("true_accept_rate"),
            "leave_one_out_true_accept_rate": summary["true_accept_rate"],
            "true_accept_rate_delta": round(
                float(summary["true_accept_rate"] or 0.0)
                - float(full_stack.get("true_accept_rate") or 0.0),
                6,
            ),
        })
    return {
        "status": "computed",
        "reviewer_effects": effects,
        "reviewer_correlation": {
            "pairwise_agreement": _reviewer_pairwise_agreement(reviewer_panel_results),
        },
    }


def _reviewer_pairwise_agreement(
    reviewer_panel_results: Mapping[str, list[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    by_reviewer: dict[str, dict[str, bool]] = {}
    for candidate_id, results in reviewer_panel_results.items():
        for result in results:
            normalised = _normalise_factorial_reviewer_result(result)
            if not normalised["available"]:
                continue
            by_reviewer.setdefault(normalised["reviewer_id"], {})[candidate_id] = bool(normalised["accept"])
    reviewers = sorted(by_reviewer)
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(reviewers):
        for right in reviewers[left_index + 1:]:
            shared = sorted(set(by_reviewer[left]) & set(by_reviewer[right]))
            agree = sum(
                1
                for candidate_id in shared
                if by_reviewer[left][candidate_id] == by_reviewer[right][candidate_id]
            )
            pairs.append({
                "reviewer_pair": [left, right],
                "shared_candidate_count": len(shared),
                "agreement_count": agree,
                "agreement_rate": _rate(agree, len(shared)),
            })
    return pairs


def _factorial_sample_size_sufficiency(
    rows: list[dict[str, Any]],
    *,
    powered_thresholds: Mapping[str, int],
) -> dict[str, Any]:
    min_bad = int(powered_thresholds.get("min_bad", 30))
    min_good = int(powered_thresholds.get("min_good", 30))
    n_bad = sum(1 for row in rows if not row["oracle_accept"])
    n_good = sum(1 for row in rows if row["oracle_accept"])
    sufficient = n_bad >= min_bad and n_good >= min_good
    return {
        "status": "sufficient" if sufficient else "underpowered",
        "n_bad": n_bad,
        "n_good": n_good,
        "false_accept_denominator": n_bad,
        "true_accept_denominator": n_good,
        "min_bad": min_bad,
        "min_good": min_good,
    }


def _factorial_trend_rows(
    *,
    arms: Mapping[str, Mapping[str, Any]],
    task_classes: list[str],
    candidate_pool_sha256: str,
) -> list[dict[str, Any]]:
    rows = []
    for arm, payload in sorted(arms.items()):
        rows.append({
            "schema_version": "supervisor-quality-trend-row/v1",
            "task_class": ",".join(task_classes),
            "gate": "powered_factorial_eval",
            "arm": arm,
            "candidate_pool_sha256": candidate_pool_sha256,
            "false_accept_rate": payload.get("false_accept_rate"),
            "true_accept_rate": payload.get("true_accept_rate"),
            "false_reject_rate": payload.get("false_reject_rate"),
            "n_bad": payload.get("n_bad"),
            "n_good": payload.get("n_good"),
            "observational_only": True,
        })
    return rows


def _metric_splits(
    rows: list[dict[str, Any]],
    *,
    arms: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    heldout_rows = [row for row in rows if row.get("split") == "held_out"]
    return {
        "held_out": {
            "status": "reported",
            "row_count": len(heldout_rows),
            "task_classes": sorted({str(row.get("task_class") or "") for row in heldout_rows}),
            "arms": {
                arm: {
                    "false_accept_rate": payload.get("false_accept_rate"),
                    "true_accept_rate": payload.get("true_accept_rate"),
                    "n_bad": payload.get("n_bad"),
                    "n_good": payload.get("n_good"),
                }
                for arm, payload in sorted(arms.items())
            },
        },
        "dev": {
            "status": "not_reported",
            "reason": "mergeability calibration fixtures are not used for variant selection in this report",
            "row_count": 0,
        },
    }


def _wilson_interval(count: int, denominator: int) -> dict[str, Any]:
    if denominator <= 0:
        return {
            "method": "wilson_score",
            "confidence": 0.95,
            "label": "approximate_binary_proportion_interval",
            "count": count,
            "denominator": denominator,
            "lower": None,
            "upper": None,
        }
    z = 1.959963984540054
    p_hat = count / denominator
    z2 = z * z
    centre = (p_hat + z2 / (2 * denominator)) / (1 + z2 / denominator)
    radius = (
        z
        * math.sqrt((p_hat * (1 - p_hat) + z2 / (4 * denominator)) / denominator)
        / (1 + z2 / denominator)
    )
    return {
        "method": "wilson_score",
        "confidence": 0.95,
        "label": "approximate_binary_proportion_interval",
        "count": count,
        "denominator": denominator,
        "lower": round(max(0.0, centre - radius), 6),
        "upper": round(min(1.0, centre + radius), 6),
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
        for marker in tuple(ORACLE_REVIEW_FORBIDDEN_TEXT) + tuple(ORACLE_REVIEW_FORBIDDEN_KEYS):
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
    if target_root.exists():
        shutil.rmtree(target_root)
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
        relative_path = path.relative_to(root)
        if "__pycache__" in relative_path.parts or relative_path.suffix == ".pyc":
            continue
        rel = _normalise_relpath(relative_path.as_posix())
        entries.append({
            "path": rel,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    return _sha256_json(entries)


def _grade_cache_key(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    fixture_root: Path,
    timeout_s: float,
) -> tuple[str, ...]:
    return (
        task.task_id,
        task.task_hash,
        candidate.candidate_id,
        candidate.candidate_hash or _sha256_json(candidate.to_payload()),
        _hash_tree(fixture_root),
        str(timeout_s),
        sys.executable,
    )


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
    if arm == "supervisor_candidate_review":
        return "supervisor_candidate_review_accept"
    if arm == "supervisor":
        return "supervisor_accept"
    return f"{arm}_accept"


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
    baseline_unavailable = int(baseline.get("unavailable_count") or 0)
    if baseline_unavailable:
        return {
            "status": "unavailable",
            "reason": "baseline_arm_unavailable",
            "unavailable_count": baseline_unavailable,
        }
    supervisor_unavailable = int(supervisor.get("unavailable_count") or 0)
    if supervisor_unavailable:
        return {
            "status": "unavailable",
            "reason": "supervisor_arm_unavailable",
            "unavailable_count": supervisor_unavailable,
        }
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
    full_roster_available_count: int = 0,
) -> dict[str, Any]:
    if full_roster_available_count <= 0:
        return {
            "status": "unavailable",
            "reason": "no_full_roster_available_rows",
            "full_roster_available_count": full_roster_available_count,
        }
    unavailable_count = int(full_gate.get("unavailable_count") or 0)
    if unavailable_count:
        return {
            "status": "unavailable",
            "reason": "reviewer_panel_unavailable",
            "unavailable_count": unavailable_count,
            "full_roster_available_count": full_roster_available_count,
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
    (output_dir / "heldout_coverage.json").write_text(
        json.dumps(report.get("heldout_coverage", {}), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "no_regression_findings.json").write_text(
        json.dumps(report.get("no_regression_findings", []), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "per_task_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _export_powered_factorial_artifacts(
    output_dir: Path,
    *,
    manifest: dict[str, Any],
    calibration: dict[str, Any],
    report: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    _export_calibration_artifacts(output_dir, manifest=manifest, summary=calibration)
    (output_dir / "powered_factorial_report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "powered_factorial_trend_rows.json").write_text(
        json.dumps(report.get("trend_rows", []), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "powered_factorial_per_task_results.jsonl").open("w", encoding="utf-8") as handle:
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
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
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
    stable = re.sub(r"in \d+(?:\.\d+)?s", "in <duration>s", text)
    stable = re.sub(
        r'(?:"?)(?:/private)?/var/folders/[^"\s]+/mergeability-public-review-[^/"\s]+',
        "<mergeability-public-review>",
        stable,
    )
    stable = re.sub(
        r'(?:"?)(?:/private)?/var/folders/[^"\s]+/cursor-reviewer-[^/"\s]+',
        "<cursor-reviewer-worktree>",
        stable,
    )
    return stable


# ---------------------------------------------------------------------------
# Bounded parallel checkpointed full-panel mergeability fixture corpus runner.
# ---------------------------------------------------------------------------


BOUNDED_PANEL_RUNNER_SCHEMA_VERSION = "supervisor-mergeability-bounded-panel-runner/v1"


@dataclass(frozen=True)
class BoundedPanelRunnerOptions:
    """Knobs for the bounded parallel full-panel mergeability corpus runner.

    The defaults keep the runner conservative: small candidate-worker count, a
    deliberately serial reviewer fanout (recorded as such in the report), and
    a generous per-review timeout. Wall-clock and candidate-selection knobs
    default to "no limit"; checkpointing is opt-in via ``checkpoint_dir``.
    """

    max_candidate_workers: int = 2
    max_reviewer_workers: int = 1
    review_timeout_s: float = 600.0
    max_wall_clock_s: float | None = None
    max_candidates: int | None = None
    candidate_selector: tuple[str, ...] | None = None
    checkpoint_dir: str | Path | None = None
    resume: bool = True


def _bounded_runner_unavailable_full_gate(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    public_review: Mapping[str, Any],
    reason: str,
    detail: str = "",
) -> dict[str, Any]:
    """Build an unavailable full-gate review dict that never accepts.

    Mirrors the shape of `_review_mergeability_candidate_full_gate` so the
    aggregation helpers can consume the row without special-casing.
    """

    panel = _normalise_reviewer_panel_result({
        "decision": "unavailable",
        "available": False,
        "reason": reason,
        "blocking_findings": [detail] if detail else [],
    })
    packet = _build_full_gate_reviewer_packet(
        task=task,
        candidate=candidate,
        public_review=public_review,
    )
    packet_ref = {
        "packet_id": packet["packet_id"],
        "packet_sha256": packet["packet_sha256"],
        "source": "supervisor",
        "evidence_grade": "runtime_native",
    }
    gaming_flags = sorted(set(public_review.get("gaming_flags") or []) | {
        "reviewer_panel_unavailable",
    })
    return {
        "schema_version": SUPERVISOR_FULL_GATE_RESULT_SCHEMA_VERSION,
        "task_id": task.task_id,
        "candidate_id": candidate.candidate_id,
        "decision": "reject",
        "accept": False,
        "available": False,
        "unavailable": True,
        "unavailable_reason": reason,
        "public_review_accept": bool(public_review.get("accept")),
        "panel_accept": False,
        "panel_decision": "unavailable",
        "panel_result": panel,
        "reviewer_results": [],
        "reviewer_rationales": [],
        "reviewer_panel_decision": None,
        "available_reviewers": [],
        "s_probe_vs_s_full_disagreement": False,
        "reviewer_packet": packet,
        "reviewer_packet_refs": [packet_ref],
        "reviewer_packet_sha256": packet["packet_sha256"],
        "evidence_refs": [
            f"mergeability_full_gate_packet:{packet['packet_id']}:{packet['packet_sha256']}",
        ],
        "gaming_flags": gaming_flags,
        "decision_source": "supervisor_candidate_review+independent_reviewer_panel",
        "oracle_coupled": False,
        "panel_quality_label": "panel_missing_verdict_block",
        "full_roster_available": False,
        "panel_quality_reject": False,
        "panel_missing_verdict_block": True,
        "codex_only_calibration": False,
        "reviewer_infrastructure_diagnostic": {
            "schema_version": "supervisor-reviewer-infrastructure-diagnostic/v1",
            "reviewers": [],
            "failure_count": 0,
            "recoverable_count": 0,
            "missing_reviewers": [],
        },
    }


def _bounded_runner_option_hash(
    options: BoundedPanelRunnerOptions,
    *,
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None,
) -> str:
    panel_keys: dict[str, Any] = {}
    if configured_reviewer_panel_options is not None:
        opts = configured_reviewer_panel_options
        panel_keys = {
            "reviewer_output_mode": opts.reviewer_output_mode,
            "reviewer_model": opts.reviewer_model,
            "codex_model": opts.codex_model,
            "review_timeout_s": opts.review_timeout_s,
            "gate": opts.gate,
            "round_index": opts.round_index,
            "low_confidence_threshold": opts.low_confidence_threshold,
            "codex_only_calibration": opts.codex_only_calibration,
        }
    return _sha256_json({
        "max_candidate_workers": options.max_candidate_workers,
        "max_reviewer_workers": options.max_reviewer_workers,
        "review_timeout_s": options.review_timeout_s,
        "max_wall_clock_s": options.max_wall_clock_s,
        "candidate_selector": list(options.candidate_selector or ()),
        "max_candidates": options.max_candidates,
        "panel": panel_keys,
    })


def _bounded_runner_reviewer_roster_ids(
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None,
) -> tuple[str, ...]:
    if configured_reviewer_panel_options is None:
        return ()
    reviewers = configured_reviewer_panel_options.reviewers
    if reviewers is None:
        reviewers = _resolve_configured_panel_adapters(configured_reviewer_panel_options)
    ids: list[str] = []
    for reviewer in reviewers:
        spec = getattr(reviewer, "spec", None)
        rid = getattr(spec, "reviewer_id", None) if spec is not None else None
        if rid is None:
            rid = getattr(reviewer, "reviewer_id", None) or "unknown-reviewer"
        ids.append(str(rid))
    return tuple(ids)


def _bounded_runner_resume_command(
    options: BoundedPanelRunnerOptions,
    *,
    bench_root: Path,
    output_dir: Path | None,
) -> str:
    parts = [
        "python -m supervisor.mergeability_bench",
        "--bench-root", str(bench_root),
        "--max-candidate-workers", str(options.max_candidate_workers),
        "--max-reviewer-workers", str(options.max_reviewer_workers),
        "--review-timeout-s", str(options.review_timeout_s),
        "--reviewer-panel-mode", "configured",
    ]
    if options.max_wall_clock_s is not None:
        parts.extend(["--max-wall-clock-s", str(options.max_wall_clock_s)])
    if options.max_candidates is not None:
        parts.extend(["--max-candidates", str(options.max_candidates)])
    for candidate_id in options.candidate_selector or ():
        parts.extend(["--candidate-id", str(candidate_id)])
    if options.checkpoint_dir is not None:
        parts.extend(["--checkpoint-dir", str(options.checkpoint_dir)])
    if output_dir is not None:
        parts.extend(["--output-dir", str(output_dir)])
    return " ".join(parts)


def _checkpoint_path_for_candidate(
    checkpoint_dir: Path | None,
    *,
    task_id: str,
    candidate_id: str,
) -> Path | None:
    if checkpoint_dir is None:
        return None
    return checkpoint_dir / "per_candidate_results" / f"{task_id}_{candidate_id}.json"


def _bounded_runner_identity(
    *,
    candidate: MergeabilityCandidate,
    candidate_hash: str,
    packet_sha256: str,
    reviewer_roster_ids: tuple[str, ...],
    option_hash: str,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_hash": candidate_hash,
        "packet_hash": packet_sha256,
        "reviewer_roster_ids": list(reviewer_roster_ids),
        "schema_version": BOUNDED_PANEL_RUNNER_SCHEMA_VERSION,
        "option_hash": option_hash,
        "runner_schema_version": BOUNDED_PANEL_RUNNER_SCHEMA_VERSION,
    }


def _bounded_runner_load_checkpoint(
    path: Path | None,
    *,
    expected_identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    identity = payload.get("identity")
    if not isinstance(identity, Mapping):
        return None
    for key in (
        "candidate_hash",
        "packet_hash",
        "schema_version",
        "option_hash",
        "runner_schema_version",
    ):
        if identity.get(key) != expected_identity.get(key):
            return None
    if list(identity.get("reviewer_roster_ids") or []) != list(
        expected_identity.get("reviewer_roster_ids") or []
    ):
        return None
    if _public_input_oracle_refs(payload):
        return None
    full_gate_review = payload.get("full_gate_review")
    if not isinstance(full_gate_review, Mapping):
        return None
    return dict(full_gate_review)


def _bounded_runner_public_safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if key_text in ORACLE_REVIEW_FORBIDDEN_KEYS:
                continue
            safe[key_text] = _bounded_runner_public_safe_value(nested)
        return safe
    if isinstance(value, (list, tuple)):
        return [_bounded_runner_public_safe_value(nested) for nested in value]
    if isinstance(value, str):
        text = value
        markers = (
            tuple(ORACLE_REVIEW_FORBIDDEN_TEXT)
            + tuple(ORACLE_REVIEW_FORBIDDEN_KEYS)
            + (
                ".mergeability",
                "hidden_test_commands.json",
                "oracle_outputs.json",
            )
        )
        for marker in markers:
            text = text.replace(marker, "[redacted-oracle-marker]")
        return text
    return value


def _bounded_runner_public_full_gate_review(
    review: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a checkpoint/report-safe full-gate review.

    Live reviewers can mention protected paths while explaining that they did
    not inspect them. Public artifacts only need reviewer verdict metadata and
    packet hashes, so strip free-form reviewer payloads down to normalized
    verdict fields before leak scanning.
    """

    safe = _bounded_runner_public_safe_value(dict(review))
    normalized_results: list[dict[str, Any]] = []
    for raw in review.get("reviewer_results") or []:
        if not isinstance(raw, Mapping):
            continue
        normalised = _normalise_mergeability_reviewer_result(raw)
        if normalised.get("worktree_isolation") is not None:
            normalised["worktree_isolation"] = "isolated_worktree"
        normalized_results.append(_bounded_runner_public_safe_value(normalised))
    safe["reviewer_results"] = normalized_results
    safe["reviewer_rationales"] = []
    panel_result = safe.get("panel_result")
    if isinstance(panel_result, Mapping):
        panel_result = dict(panel_result)
        panel_result["reviewer_results"] = normalized_results
        panel_result["blocking_findings"] = _bounded_runner_public_safe_value(
            panel_result.get("blocking_findings") or []
        )
        panel_result["panel_decision"] = _bounded_runner_public_safe_value(
            panel_result.get("panel_decision")
        )
        safe["panel_result"] = panel_result
    leaks = _public_input_oracle_refs(safe)
    if leaks:
        raise MergeabilityBenchError(
            "bounded panel runner full-gate review leaked oracle material: "
            + ", ".join(leaks)
        )
    return safe


def _bounded_runner_write_checkpoint(
    path: Path,
    *,
    identity: Mapping[str, Any],
    row: Mapping[str, Any],
    timing_s: float,
) -> None:
    full_gate_review = row.get("supervisor_full_gate_review")
    if not isinstance(full_gate_review, Mapping):
        raise MergeabilityBenchError(
            "bounded panel runner checkpoint missing full gate review"
        )
    full_gate_review = _bounded_runner_public_full_gate_review(full_gate_review)
    reviewer_verdicts: list[dict[str, Any]] = []
    for raw_result in row.get("supervisor_full_gate_reviewer_results") or []:
        if not isinstance(raw_result, Mapping):
            continue
        normalised = _normalise_mergeability_reviewer_result(raw_result)
        worktree_isolation = (
            "isolated_worktree"
            if normalised.get("worktree_isolation") is not None
            else None
        )
        reviewer_verdicts.append({
            "reviewer_id": normalised["reviewer_id"],
            "decision": normalised["decision"],
            "accept": bool(normalised["accept"]),
            "available": bool(normalised["available"]),
            "verdict_present": bool(normalised["verdict_present"]),
            "reason": normalised["reason"],
            "failure_classification": normalised["failure_classification"],
            "runtime": normalised["runtime"],
            "reviewer_runtime": normalised["reviewer_runtime"],
            "model": normalised["model"],
            "transcript_sha256": normalised["transcript_sha256"],
            "output_sha256": normalised["output_sha256"],
            "worktree_isolation": worktree_isolation,
        })
    candidate_result = {
        "task_id": str(row.get("task_id") or ""),
        "candidate_id": str(row.get("candidate_id") or ""),
        "candidate_hash": str(row.get("candidate_hash") or ""),
        "reviewer_packet_hash": str(
            row.get("supervisor_full_gate_reviewer_packet_sha256") or ""
        ),
        "reviewer_packet_refs": list(
            row.get("supervisor_full_gate_reviewer_packet_refs") or []
        ),
        "reviewer_ids": [
            verdict["reviewer_id"] for verdict in reviewer_verdicts
        ],
        "reviewer_verdicts": reviewer_verdicts,
        "availability_status": (
            "unavailable"
            if bool(row.get("supervisor_full_gate_unavailable"))
            else "available"
        ),
        "supervisor_candidate_review_accept": bool(
            row.get("supervisor_candidate_review_accept")
        ),
        "supervisor_full_gate_accept": bool(row.get("supervisor_full_gate_accept")),
        "supervisor_full_gate_unavailable": bool(
            row.get("supervisor_full_gate_unavailable")
        ),
        "unavailable_reason": str(
            full_gate_review.get("unavailable_reason")
            or row.get("panel_quality_label")
            or ""
        ),
        "panel_quality_label": str(row.get("panel_quality_label") or ""),
        "full_roster_available": bool(row.get("full_roster_available")),
        "s_probe_vs_s_full_disagreement": bool(
            row.get("s_probe_vs_s_full_disagreement")
        ),
        "oracle_isolation": {
            "ok": True,
            "proof": "checkpoint excludes oracle labels and stores only public packet/panel evidence",
        },
    }
    payload = {
        "schema_version": BOUNDED_PANEL_RUNNER_SCHEMA_VERSION,
        "identity": dict(identity),
        "candidate_result": candidate_result,
        "full_gate_review": dict(full_gate_review),
        "timing_s": float(timing_s),
    }
    leaks = _public_input_oracle_refs(payload)
    if leaks:
        raise MergeabilityBenchError(
            "bounded panel runner checkpoint leaked oracle material: "
            + ", ".join(leaks)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _bounded_runner_build_row(
    *,
    task: MergeabilityTask,
    candidate: MergeabilityCandidate,
    grading_entry: Mapping[str, Any],
    task_hash: str,
    candidate_hash: str,
    supervisor_review: Mapping[str, Any],
    full_gate_review: Mapping[str, Any],
    single_agent_baseline_decisions: Mapping[str, Mapping[str, Any]] | None,
    codex_only_calibration_active: bool,
) -> dict[str, Any]:
    """Build a per-candidate row mirroring `run_paired_acceptance_pilot` rows.

    Copied here intentionally; the existing runner is not refactored so this
    bounded variant can evolve independently per the implementation plan.
    """

    oracle_accept = float(grading_entry["final_score"]) >= 1.0
    metadata_accept_all_baseline_accept = _baseline_accepts(candidate)
    baseline_raw = (
        single_agent_baseline_decisions.get(candidate.candidate_id)
        if single_agent_baseline_decisions is not None
        else None
    )
    single_agent_baseline_decision = _resolve_powered_baseline_decision(
        raw=baseline_raw,
        expected_candidate_artifact_hash=candidate_hash,
        expected_candidate_id=candidate.candidate_id,
    )
    single_agent_baseline_available = not bool(single_agent_baseline_decision["unavailable"])
    single_agent_baseline_accept = bool(single_agent_baseline_decision["accept"])
    supervisor_accept = bool(supervisor_review["accept"])
    supervisor_full_gate_accept = bool(full_gate_review["accept"])
    panel_result = (
        full_gate_review.get("panel_result")
        if isinstance(full_gate_review.get("panel_result"), Mapping)
        else {}
    )
    if not codex_only_calibration_active:
        codex_only_calibration_unavailable = True
    elif not supervisor_accept:
        codex_only_calibration_unavailable = False
    else:
        codex_only_calibration_unavailable = not bool(
            "oracle_isolation_violation" not in full_gate_review.get("gaming_flags", [])
            and panel_result.get("available")
            and not panel_result.get("missing_reviewers")
        )
    codex_only_calibration_accept = bool(
        supervisor_accept
        and not codex_only_calibration_unavailable
        and panel_result.get("decision") == "accept"
    )
    row: dict[str, Any] = {
        "task_id": grading_entry["task_id"],
        "task_hash": task_hash,
        "task_class": task.task_class,
        "split": task.split,
        "candidate_id": grading_entry["candidate_id"],
        "candidate_hash": candidate_hash,
        "control_kind": grading_entry["control_kind"],
        "expected_outcome": grading_entry["expected_outcome"],
        "oracle_accept": oracle_accept,
        "baseline_accept": metadata_accept_all_baseline_accept,
        "metadata_accept_all_baseline_accept": metadata_accept_all_baseline_accept,
        "metadata_accept_all_baseline_unavailable": False,
        "single_agent_baseline_accept": single_agent_baseline_accept,
        "single_agent_baseline_unavailable": not single_agent_baseline_available,
        "supervisor_accept": supervisor_accept,
        "supervisor_candidate_review_accept": supervisor_accept,
        "supervisor_full_gate_accept": supervisor_full_gate_accept,
        "supervisor_full_gate_unavailable": bool(full_gate_review["unavailable"]),
        "codex_only_calibration_panel_accept": codex_only_calibration_accept,
        "codex_only_calibration_panel_unavailable": codex_only_calibration_unavailable,
        "oracle_ceiling_accept": oracle_accept,
        "baseline_false_accept": metadata_accept_all_baseline_accept and not oracle_accept,
        "metadata_accept_all_baseline_false_accept": (
            metadata_accept_all_baseline_accept and not oracle_accept
        ),
        "single_agent_baseline_false_accept": (
            single_agent_baseline_available and single_agent_baseline_accept and not oracle_accept
        ),
        "supervisor_false_accept": supervisor_accept and not oracle_accept,
        "supervisor_candidate_review_false_accept": supervisor_accept and not oracle_accept,
        "supervisor_full_gate_false_accept": supervisor_full_gate_accept and not oracle_accept,
        "codex_only_calibration_panel_false_accept": (
            codex_only_calibration_accept and not oracle_accept
        ),
        "oracle_ceiling_false_accept": oracle_accept and not oracle_accept,
        "baseline_false_reject": (not metadata_accept_all_baseline_accept) and oracle_accept,
        "metadata_accept_all_baseline_false_reject": (
            (not metadata_accept_all_baseline_accept) and oracle_accept
        ),
        "single_agent_baseline_false_reject": (
            single_agent_baseline_available
            and (not single_agent_baseline_accept)
            and oracle_accept
        ),
        "supervisor_false_reject": (not supervisor_accept) and oracle_accept,
        "supervisor_candidate_review_false_reject": (not supervisor_accept) and oracle_accept,
        "supervisor_full_gate_false_reject": (not supervisor_full_gate_accept) and oracle_accept,
        "codex_only_calibration_panel_false_reject": (
            (not codex_only_calibration_accept)
            and (not codex_only_calibration_unavailable)
            and oracle_accept
        ),
        "oracle_ceiling_false_reject": (not oracle_accept) and oracle_accept,
        "receipt_id": grading_entry["receipt_id"],
        "receipt": grading_entry["receipt"],
        "blocker_status": grading_entry["blocker_status"],
        "failures": list(grading_entry["failures"]),
        "baseline_decision_source": "candidate_self_report",
        "baseline_evidence_kind": "metadata_calibration",
        "metadata_accept_all_baseline_decision_source": "candidate_self_report",
        "metadata_accept_all_baseline_evidence_kind": "metadata_calibration",
        "single_agent_baseline_candidate_id": single_agent_baseline_decision["candidate_id"],
        "single_agent_baseline_decision_source": single_agent_baseline_decision["decision_source"],
        "single_agent_baseline_evidence_kind": single_agent_baseline_decision["evidence_kind"],
        "single_agent_baseline_candidate_artifact_hash": (
            single_agent_baseline_decision["candidate_artifact_hash"]
        ),
        "single_agent_baseline_producer": dict(single_agent_baseline_decision["producer"]),
        "single_agent_baseline_prompt_sha256": single_agent_baseline_decision["prompt_sha256"],
        "single_agent_baseline_unavailable_reason": (
            single_agent_baseline_decision["unavailable_reason"]
        ),
        "oracle_ceiling_decision_source": "oracle_final_score",
        "supervisor_decision_source": "supervisor_candidate_review",
        "supervisor_candidate_review_decision_source": "supervisor_candidate_review",
        "supervisor_full_gate_decision_source": "supervisor_full_gate",
        "codex_only_calibration_panel_decision_source": (
            "codex_only_single_reviewer_calibration"
        ),
        "supervisor_review": dict(supervisor_review),
        "supervisor_full_gate_review": dict(full_gate_review),
        "supervisor_full_gate_reviewer_results": list(
            full_gate_review.get("reviewer_results") or []
        ),
        "supervisor_full_gate_reviewer_rationales": list(
            full_gate_review.get("reviewer_rationales") or []
        ),
        "supervisor_full_gate_reviewer_packet_refs": list(
            full_gate_review.get("reviewer_packet_refs") or []
        ),
        "supervisor_full_gate_reviewer_packet_sha256": str(
            full_gate_review.get("reviewer_packet_sha256") or ""
        ),
        "supervisor_full_gate_reviewer_panel_decision": (
            full_gate_review.get("reviewer_panel_decision")
        ),
        "s_probe_vs_s_full_disagreement": bool(
            full_gate_review.get("s_probe_vs_s_full_disagreement")
        ),
        "panel_quality_label": str(
            full_gate_review.get("panel_quality_label") or "panel_missing_verdict_block"
        ),
        "full_roster_available": bool(full_gate_review.get("full_roster_available")),
        "panel_quality_reject": bool(full_gate_review.get("panel_quality_reject")),
        "panel_missing_verdict_block": bool(
            full_gate_review.get("panel_missing_verdict_block")
        ),
        "codex_only_calibration": bool(full_gate_review.get("codex_only_calibration")),
        "reviewer_infrastructure_diagnostic": dict(
            full_gate_review.get("reviewer_infrastructure_diagnostic") or {}
        ),
    }
    row["is_no_regression_failure"] = bool(
        metadata_accept_all_baseline_accept
        and oracle_accept
        and not supervisor_full_gate_accept
        and not bool(full_gate_review["unavailable"])
    )
    return row


def run_bounded_parallel_panel_corpus(
    bench_root: str | Path,
    *,
    options: BoundedPanelRunnerOptions | None = None,
    output_dir: str | Path | None = None,
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "custom",
    single_agent_baseline_decisions: Mapping[str, Mapping[str, Any]] | None = None,
    strict_calibration: bool = True,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run the fixture mergeability corpus with bounded parallel panel calls.

    Sorts rows by ``(task_id, candidate_id)`` regardless of completion order,
    writes per-candidate checkpoints before the aggregate report, applies
    candidate selection and wall-clock limits honestly (no claim of full
    corpus when filtered), and emits a public-only annotation HTML dashboard.
    All results are calibration evidence only; report-only invariants stay
    false even on partial or wall-clock-truncated runs.
    """

    if reviewer_panel_mode not in {"custom", "configured"}:
        raise MergeabilityBenchError(
            f"unknown reviewer_panel_mode: {reviewer_panel_mode!r}"
        )
    opts = options or BoundedPanelRunnerOptions()
    codex_only_calibration_active = bool(
        configured_reviewer_panel_options is not None
        and getattr(configured_reviewer_panel_options, "codex_only_calibration", False)
    )
    if reviewer_panel_mode == "configured" and reviewer_panel is None:
        reviewer_panel = build_configured_reviewer_panel(configured_reviewer_panel_options)
    panel_fn = reviewer_panel or _default_unavailable_reviewer_panel

    started = time.monotonic()
    bench_root_path = Path(bench_root).expanduser().resolve()
    output_path = Path(output_dir).expanduser() if output_dir is not None else None
    checkpoint_dir = (
        Path(opts.checkpoint_dir).expanduser() if opts.checkpoint_dir is not None else None
    )

    # Build manifest from full corpus for top-level provenance, then apply
    # filter to derive the actually-scheduled candidate set.
    full_manifest = build_mergeability_corpus_manifest(bench_root_path)
    tasks = {task.task_id: task for task in load_mergeability_tasks(bench_root_path)}
    all_candidates = list(load_mergeability_candidates(bench_root_path))
    selected_candidates = list(all_candidates)
    if opts.candidate_selector is not None:
        wanted = set(opts.candidate_selector)
        selected_candidates = [c for c in selected_candidates if c.candidate_id in wanted]
    selected_candidates.sort(key=lambda c: (c.task_id, c.candidate_id))
    if opts.max_candidates is not None:
        selected_candidates = selected_candidates[: opts.max_candidates]
    filtered = (
        opts.candidate_selector is not None
        or opts.max_candidates is not None
    )
    if not selected_candidates:
        raise MergeabilityBenchError(
            "bounded panel runner: no candidates remain after applying selector"
        )
    selected_paths = tuple(
        Path(candidate.candidate_ref) for candidate in selected_candidates
    )
    selected_path_strs = tuple(str(p) for p in selected_paths)

    # Manifest reflecting the SCHEDULED candidates for hashing/provenance.
    manifest = build_mergeability_corpus_manifest(
        bench_root_path, candidate_paths=selected_path_strs
    )
    task_hashes = {entry["task_id"]: entry["task_hash"] for entry in manifest["tasks"]}
    candidate_hashes = {
        (entry["task_id"], entry["candidate_id"]): entry["candidate_hash"]
        for entry in manifest["candidates"]
    }

    # Strict calibration would refuse a filtered corpus; only enforce when the
    # caller explicitly opts in *and* did not filter (filter implies relax).
    calibration = validate_mergeability_corpus(
        bench_root_path,
        strict=strict_calibration and not filtered,
        candidate_paths=selected_path_strs,
        timeout_s=timeout_s,
    )

    if filtered:
        # `validate_mergeability_corpus` keeps aggregate manifests honest by
        # excluding task slices that lack both positive and negative controls.
        # Diagnostic selectors are intentionally allowed to run narrower slices,
        # so grade the selected candidates directly while keeping the report's
        # full-corpus claim disabled.
        present = {
            (entry["task_id"], entry["candidate_id"])
            for entry in calibration.get("results", [])
            if isinstance(entry, Mapping)
        }
        diagnostic_results = list(calibration.get("results", []))
        for candidate in selected_candidates:
            key = (candidate.task_id, candidate.candidate_id)
            task = tasks.get(candidate.task_id)
            if key in present or task is None:
                continue
            result = grade_mergeability_candidate(
                task,
                candidate,
                bench_root=bench_root_path,
                timeout_s=timeout_s,
            )
            receipt = result_receipt(result)
            expected = _candidate_expected_outcome(candidate)
            observed = "pass" if result.final_score >= 1.0 else "fail"
            diagnostic_results.append({
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
        calibration = dict(calibration)
        calibration["results"] = sorted(
            diagnostic_results,
            key=lambda entry: (entry["task_id"], entry["candidate_id"]),
        )
        calibration["summary_sha256"] = _sha256_json({
            key: value for key, value in calibration.items()
            if key != "summary_sha256"
        })

    # Public reviews are cheap and run serially.
    public_reviews: dict[str, dict[str, Any]] = {}
    for candidate in selected_candidates:
        if candidate.task_id not in tasks:
            continue
        public_reviews[candidate.candidate_id] = review_mergeability_candidate_publicly(
            tasks[candidate.task_id],
            candidate,
            bench_root=bench_root_path,
            timeout_s=timeout_s,
        )

    grading_index = {entry["candidate_id"]: entry for entry in calibration["results"]}
    reviewer_roster_ids = _bounded_runner_reviewer_roster_ids(
        configured_reviewer_panel_options
    )
    option_hash = _bounded_runner_option_hash(
        opts,
        configured_reviewer_panel_options=configured_reviewer_panel_options,
    )

    # Pre-build packets so we can hash them for checkpoint identity and pass
    # them to the panel callable.
    packets: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    for candidate in selected_candidates:
        if candidate.candidate_id not in public_reviews:
            continue
        packet = _build_full_gate_reviewer_packet(
            task=tasks[candidate.task_id],
            candidate=candidate,
            public_review=public_reviews[candidate.candidate_id],
        )
        packets[candidate.candidate_id] = packet
        identities[candidate.candidate_id] = _bounded_runner_identity(
            candidate=candidate,
            candidate_hash=candidate_hashes[(candidate.task_id, candidate.candidate_id)],
            packet_sha256=str(packet["packet_sha256"]),
            reviewer_roster_ids=reviewer_roster_ids,
            option_hash=option_hash,
        )

    # First pass: resume public-safe full-gate reviews whose checkpoints match
    # identity. Oracle-derived row fields are rebuilt from the current
    # deterministic grader below; checkpoints do not store oracle labels.
    cached_full_gate_reviews: dict[str, dict[str, Any]] = {}
    needs_panel: list[MergeabilityCandidate] = []
    if checkpoint_dir is not None and opts.resume:
        for candidate in selected_candidates:
            if candidate.candidate_id not in identities:
                continue
            ckpt_path = _checkpoint_path_for_candidate(
                checkpoint_dir,
                task_id=candidate.task_id,
                candidate_id=candidate.candidate_id,
            )
            existing = _bounded_runner_load_checkpoint(
                ckpt_path,
                expected_identity=identities[candidate.candidate_id],
            )
            if existing is not None:
                cached_full_gate_reviews[candidate.candidate_id] = existing
            else:
                needs_panel.append(candidate)
    else:
        needs_panel = [
            candidate for candidate in selected_candidates
            if candidate.candidate_id in identities
        ]

    # Schedule panel calls with bounded parallelism + wall-clock budget.
    panel_results: dict[str, dict[str, Any]] = {}
    interrupted_ids: list[str] = []
    timed_out_ids: list[str] = []
    wall_clock_stopped = False
    wall_start = time.monotonic()
    max_workers = max(1, int(opts.max_candidate_workers))

    def _wall_clock_remaining() -> float | None:
        if opts.max_wall_clock_s is None:
            return None
        return max(0.0, float(opts.max_wall_clock_s) - (time.monotonic() - wall_start))

    if needs_panel:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        try:
            in_flight: dict[concurrent.futures.Future, str] = {}
            pending_iter = iter(needs_panel)
            exhausted = False
            while True:
                while len(in_flight) < max_workers and not exhausted:
                    remaining = _wall_clock_remaining()
                    if remaining is not None and remaining <= 0.0:
                        wall_clock_stopped = True
                        break
                    try:
                        candidate = next(pending_iter)
                    except StopIteration:
                        exhausted = True
                        break
                    cid = candidate.candidate_id
                    packet = packets[cid]
                    future = executor.submit(panel_fn, packet)
                    in_flight[future] = cid

                if wall_clock_stopped:
                    for remaining_candidate in pending_iter:
                        interrupted_ids.append(remaining_candidate.candidate_id)
                    exhausted = True

                if not in_flight:
                    break

                # Wait for at least one future; bound by review timeout and
                # any remaining wall-clock budget.
                remaining_wall = _wall_clock_remaining()
                if remaining_wall is not None and remaining_wall <= 0.0:
                    wait_timeout: float | None = 0.0
                else:
                    wait_timeout = opts.review_timeout_s
                    if remaining_wall is not None:
                        wait_timeout = min(wait_timeout, remaining_wall)
                done, _not_done = concurrent.futures.wait(
                    list(in_flight.keys()),
                    timeout=wait_timeout,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    remaining_wall = _wall_clock_remaining()
                    if remaining_wall is not None and remaining_wall <= 0.0:
                        wall_clock_stopped = True
                        for fut, cid in list(in_flight.items()):
                            fut.cancel()
                            interrupted_ids.append(cid)
                            del in_flight[fut]
                        continue
                    # Per-review timeout: cancel and mark unavailable.
                    for fut, cid in list(in_flight.items()):
                        fut.cancel()
                        timed_out_ids.append(cid)
                        del in_flight[fut]
                    continue
                for fut in done:
                    cid = in_flight.pop(fut)
                    try:
                        panel_results[cid] = dict(fut.result(timeout=0.001))
                    except Exception as exc:  # noqa: BLE001
                        panel_results[cid] = {
                            "decision": "unavailable",
                            "available": False,
                            "reason": "reviewer_invocation_failed",
                            "blocking_findings": [f"{type(exc).__name__}: {exc}"],
                        }
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # Build rows for every selected candidate. Resumed full-gate reviews take
    # precedence; panel results are wrapped through the full-gate path;
    # interrupted/timeout become unavailable rows that never accept.
    rows: list[dict[str, Any]] = []
    completed_count = 0
    timing_per_candidate: dict[str, float] = {}
    for candidate in selected_candidates:
        cid = candidate.candidate_id
        if cid not in identities:
            continue
        public_review = public_reviews[cid]
        row_start = time.monotonic()
        if cid in cached_full_gate_reviews:
            full_gate_review = dict(cached_full_gate_reviews[cid])
        elif cid in interrupted_ids:
            full_gate_review = _bounded_runner_unavailable_full_gate(
                task=tasks[candidate.task_id],
                candidate=candidate,
                public_review=public_review,
                reason="wall_clock_exceeded",
                detail="bounded panel runner stopped scheduling new candidates",
            )
        elif cid in timed_out_ids:
            full_gate_review = _bounded_runner_unavailable_full_gate(
                task=tasks[candidate.task_id],
                candidate=candidate,
                public_review=public_review,
                reason="review_timeout",
                detail=f"panel call exceeded review_timeout_s={opts.review_timeout_s}",
            )
        elif cid in panel_results:
            cached_panel_result = panel_results[cid]

            def _cached_panel(_packet, _result=cached_panel_result):
                return _result

            full_gate_review = _review_mergeability_candidate_full_gate(
                task=tasks[candidate.task_id],
                candidate=candidate,
                public_review=public_review,
                reviewer_panel=_cached_panel,
                codex_only_calibration=codex_only_calibration_active,
            )
        else:
            # Should not happen, but fail closed.
            full_gate_review = _bounded_runner_unavailable_full_gate(
                task=tasks[candidate.task_id],
                candidate=candidate,
                public_review=public_review,
                reason="reviewer_panel_unavailable",
                detail="missing panel result for candidate",
            )
        full_gate_review = _bounded_runner_public_full_gate_review(full_gate_review)
        grading_entry = grading_index.get(cid)
        if grading_entry is None:
            # Filtered candidate excluded by manifest task allowlist.
            continue
        row = _bounded_runner_build_row(
            task=tasks[candidate.task_id],
            candidate=candidate,
            grading_entry=grading_entry,
            task_hash=task_hashes[candidate.task_id],
            candidate_hash=candidate_hashes[(candidate.task_id, cid)],
            supervisor_review=public_review,
            full_gate_review=full_gate_review,
            single_agent_baseline_decisions=single_agent_baseline_decisions,
            codex_only_calibration_active=codex_only_calibration_active,
        )
        timing_per_candidate[cid] = round(time.monotonic() - row_start, 6)
        rows.append(row)
        completed_count += 1

    # Checkpoint write happens BEFORE we assemble or write the aggregate
    # report. Each checkpoint is leak-scanned via `_public_input_oracle_refs`.
    checkpoint_refs: list[str] = []
    if checkpoint_dir is not None:
        for row in rows:
            cid = row["candidate_id"]
            if cid not in identities:
                continue
            ckpt_path = _checkpoint_path_for_candidate(
                checkpoint_dir,
                task_id=row["task_id"],
                candidate_id=cid,
            )
            if ckpt_path is None:
                continue
            if cid in cached_full_gate_reviews and ckpt_path.exists():
                # Already on disk, identity already validated. Keep as-is.
                checkpoint_refs.append(str(ckpt_path))
                continue
            _bounded_runner_write_checkpoint(
                ckpt_path,
                identity=identities[cid],
                row=row,
                timing_s=timing_per_candidate.get(cid, 0.0),
            )
            checkpoint_refs.append(str(ckpt_path))

    # Deterministic ordering.
    rows.sort(key=lambda r: (r["task_id"], r["candidate_id"]))

    # Aggregate arms — mirror run_paired_acceptance_pilot.
    metadata_accept_all_baseline = _summarize_acceptance_arm(
        rows,
        arm="metadata_accept_all_baseline",
        arm_role="metadata_accept_all_baseline",
        decision_source="candidate_self_report",
        oracle_coupled=False,
        evidence_kind="metadata_calibration",
    )
    baseline = dict(metadata_accept_all_baseline)
    baseline["arm"] = "baseline"
    baseline["arm_role"] = "baseline_self_report"
    baseline["legacy_alias_of"] = "metadata_accept_all_baseline"

    def _single_agent_baseline_evidence_kind() -> str:
        available = [row for row in rows if not bool(row.get("single_agent_baseline_unavailable"))]
        source_rows = available if available else rows
        kinds = {str(row.get("single_agent_baseline_evidence_kind") or "missing") for row in source_rows}
        if len(kinds) == 1:
            return next(iter(kinds))
        return "mixed_produced_baseline"

    def _single_agent_baseline_decision_source() -> str:
        available = [row for row in rows if not bool(row.get("single_agent_baseline_unavailable"))]
        if not available:
            return "produced_single_agent_baseline_unavailable"
        sources = {str(row.get("single_agent_baseline_decision_source") or "") for row in available}
        sources.discard("")
        if len(sources) == 1:
            return next(iter(sources))
        return "mixed_produced_single_agent_baseline"

    single_agent_baseline = _summarize_acceptance_arm(
        rows,
        arm="single_agent_baseline",
        arm_role="single_agent_baseline",
        decision_source=_single_agent_baseline_decision_source(),
        oracle_coupled=False,
        evidence_kind=_single_agent_baseline_evidence_kind(),
    )
    oracle_ceiling = _summarize_acceptance_arm(
        rows,
        arm="oracle_ceiling",
        arm_role="oracle_ceiling",
        decision_source="oracle_final_score",
        oracle_coupled=True,
    )
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
    codex_only_calibration_panel = _summarize_acceptance_arm(
        rows,
        arm="codex_only_calibration_panel",
        arm_role="codex_only_single_reviewer_calibration",
        decision_source="codex_only_single_reviewer_calibration",
        oracle_coupled=False,
        evidence_kind="codex_only_calibration",
    )
    supervisor = dict(supervisor_candidate_review)
    supervisor["arm"] = "supervisor"
    supervisor["legacy_alias_of"] = "supervisor_candidate_review"

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
    heldout_coverage = _heldout_coverage_from_manifest(manifest)
    no_regression_findings = _no_regression_findings(rows, manifest)
    oracle_agreement = {
        "supervisor_candidate_review": _oracle_agreement(rows, arm="supervisor_candidate_review"),
        "supervisor_full_gate": _oracle_agreement(rows, arm="supervisor_full_gate"),
        "codex_only_calibration_panel": _oracle_agreement(
            rows,
            arm="codex_only_calibration_panel",
        ),
        "baseline": _oracle_agreement(rows, arm="baseline"),
        "metadata_accept_all_baseline": _oracle_agreement(rows, arm="metadata_accept_all_baseline"),
        "single_agent_baseline": _oracle_agreement(rows, arm="single_agent_baseline"),
        "oracle_ceiling": _oracle_agreement(rows, arm="oracle_ceiling"),
    }
    gaming_flags: set[str] = set(calibration["gaming_flags"])
    for row in rows:
        gaming_flags.update(row["supervisor_review"].get("gaming_flags", []))
        gaming_flags.update(row["supervisor_full_gate_review"].get("gaming_flags", []))
    if _should_trip_perfect_agreement(rows, oracle_agreement["supervisor_candidate_review"]):
        gaming_flags.add("perfect_oracle_agreement_tripwire")
    if no_regression_findings:
        gaming_flags.add("no_regression_failure_detected")
    if single_agent_baseline["unavailable_count"]:
        gaming_flags.add("baseline_evidence_unavailable")

    full_roster_available_count = sum(
        1 for row in rows if bool(row.get("full_roster_available"))
    )
    panel_marginal_delta = _panel_marginal_delta_at_matched_true_accept(
        public_review=supervisor_candidate_review,
        full_gate=supervisor_full_gate,
        full_roster_available_count=full_roster_available_count,
    )
    codex_only_roster_available_count = sum(
        1 for row in rows if not bool(row.get("codex_only_calibration_panel_unavailable"))
    )
    if codex_only_calibration_active:
        codex_only_panel_marginal_delta = _panel_marginal_delta_at_matched_true_accept(
            public_review=supervisor_candidate_review,
            full_gate=codex_only_calibration_panel,
            full_roster_available_count=codex_only_roster_available_count,
        )
    else:
        codex_only_panel_marginal_delta = {
            "status": "unavailable",
            "reason": "codex_only_calibration_inactive",
            "full_roster_available_count": 0,
        }
    per_reviewer_arms = _per_reviewer_acceptance_arms(rows)
    inter_reviewer_agreement = _inter_reviewer_agreement(rows)
    reviewer_packet_leak_refs = _reviewer_packet_leak_refs(rows)
    reviewer_provenance = _reviewer_provenance_report(rows)
    generator_disjointness = _generator_disjointness_report(rows)
    metric_splits = _metric_splits(
        rows,
        arms={
            "baseline": baseline,
            "metadata_accept_all_baseline": metadata_accept_all_baseline,
            "single_agent_baseline": single_agent_baseline,
            "supervisor_candidate_review": supervisor_candidate_review,
            "supervisor_full_gate": supervisor_full_gate,
            "codex_only_calibration_panel": codex_only_calibration_panel,
            "oracle_ceiling": oracle_ceiling,
            "supervisor": supervisor,
        },
    )
    roster_selection_guard = _roster_selection_guard(
        inter_reviewer_agreement=inter_reviewer_agreement,
        codex_only_calibration_active=codex_only_calibration_active,
        reviewer_provenance=reviewer_provenance,
        generator_disjointness=generator_disjointness,
    )
    matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=baseline,
        supervisor=supervisor_candidate_review,
    )
    single_agent_baseline_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=single_agent_baseline,
        supervisor=supervisor_candidate_review,
    )
    full_gate_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=baseline,
        supervisor=supervisor_full_gate,
    )
    full_gate_single_agent_baseline_matched_true_accept = _false_accept_at_matched_true_accept(
        baseline=single_agent_baseline,
        supervisor=supervisor_full_gate,
    )

    s_probe_vs_s_full_discordant_count = sum(
        1 for row in rows
        if (not bool(row.get("supervisor_full_gate_unavailable")))
        and bool(row.get("supervisor_full_gate_accept")) != bool(row.get("supervisor_accept"))
    )

    bounded_runner_block = {
        "max_candidate_workers": int(opts.max_candidate_workers),
        "max_reviewer_workers": int(opts.max_reviewer_workers),
        "reviewer_fanout": "serial",
        "review_timeout_s": float(opts.review_timeout_s),
        "max_wall_clock_s": opts.max_wall_clock_s,
        "wall_clock_stopped": bool(wall_clock_stopped),
        "completed_candidate_count": int(completed_count),
        "unavailable_candidate_count": sum(
            1 for row in rows if bool(row.get("supervisor_full_gate_unavailable"))
        ),
        "interrupted_candidate_count": len(interrupted_ids) + len(timed_out_ids),
        "checkpoint_refs": list(checkpoint_refs),
        "resume_command": _bounded_runner_resume_command(
            opts, bench_root=bench_root_path, output_dir=output_path
        ),
        "runner_schema_version": BOUNDED_PANEL_RUNNER_SCHEMA_VERSION,
        "option_hash": option_hash,
        "reviewer_roster_ids": list(reviewer_roster_ids),
        "cache_policy": {
            "reviewer_roster_ids_in_identity": True,
            "option_hash_in_identity": True,
            "changed_roster_recomputes": True,
            "changed_options_recompute": True,
            "checkpoint_reuse_requires": [
                "candidate_hash",
                "packet_hash",
                "schema_version",
                "runner_schema_version",
                "option_hash",
                "reviewer_roster_ids",
            ],
            "stale_checkpoint_action": "recompute_reviewer_work",
        },
    }

    total_candidate_count = len(all_candidates)
    selected_candidate_count = len(selected_candidates)
    diagnostic_scope = {
        "full_corpus_claim": not filtered,
        "selected_candidate_count": selected_candidate_count,
        "total_candidate_count": total_candidate_count,
    }
    if filtered:
        diagnostic_scope["candidate_selector"] = list(opts.candidate_selector or ())
        diagnostic_scope["max_candidates"] = opts.max_candidates

    report_only_invariants = {
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }

    report: dict[str, Any] = {
        "schema_version": MERGEABILITY_PAIRED_REPORT_SCHEMA_VERSION,
        "bench_root": bench_root_path.as_posix(),
        "manifest_sha256": manifest["manifest_sha256"],
        "full_corpus_manifest_sha256": full_manifest["manifest_sha256"],
        "calibration_summary_sha256": calibration["summary_sha256"],
        "task_count": manifest["task_count"],
        "included_task_ids": manifest["included_task_ids"],
        "candidate_count": len(rows),
        "positive_control_count": positive_control_count,
        "negative_control_count": negative_control_count,
        "heldout_coverage": heldout_coverage,
        "heldout_coverage_sha256": _sha256_json(heldout_coverage),
        "split_policy": manifest["split_policy"],
        "metric_splits": metric_splits,
        "no_regression_findings": no_regression_findings,
        "no_regression_sha256": _sha256_json(no_regression_findings),
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
            "metadata_accept_all_baseline": metadata_accept_all_baseline,
            "single_agent_baseline": single_agent_baseline,
            "supervisor_candidate_review": supervisor_candidate_review,
            "supervisor_full_gate": supervisor_full_gate,
            "codex_only_calibration_panel": codex_only_calibration_panel,
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
        "single_agent_baseline_false_accept_at_matched_true_accept": (
            single_agent_baseline_matched_true_accept
        ),
        "supervisor_full_gate_false_accept_at_matched_true_accept": full_gate_matched_true_accept,
        "supervisor_full_gate_vs_single_agent_baseline_false_accept_at_matched_true_accept": (
            full_gate_single_agent_baseline_matched_true_accept
        ),
        "primary_comparison_name": "supervisor_vs_single_agent_baseline",
        "primary_matched_true_accept_status": single_agent_baseline_matched_true_accept["status"],
        "comparisons": _paired_acceptance_comparisons(
            legacy_metadata_matched_true_accept=matched_true_accept,
            single_agent_matched_true_accept=single_agent_baseline_matched_true_accept,
            full_gate_metadata_matched_true_accept=full_gate_matched_true_accept,
            full_gate_single_agent_matched_true_accept=full_gate_single_agent_baseline_matched_true_accept,
            single_agent_baseline=single_agent_baseline,
            metadata_accept_all_baseline=metadata_accept_all_baseline,
        ),
        "panel_marginal_delta_at_matched_true_accept": panel_marginal_delta,
        "codex_only_calibration_panel_marginal_delta_at_matched_true_accept": (
            codex_only_panel_marginal_delta
        ),
        "matched_true_accept_status": matched_true_accept["status"],
        "oracle_agreement": oracle_agreement,
        "per_reviewer_arms": per_reviewer_arms,
        "reviewer_provenance": reviewer_provenance,
        "generator_disjointness": generator_disjointness,
        "oracle_isolation": {
            "ok": not reviewer_packet_leak_refs,
            "violations": reviewer_packet_leak_refs,
        },
        "hidden_field_leak_check": {
            "ok": not reviewer_packet_leak_refs,
            "refs": reviewer_packet_leak_refs,
            "scope": "supervisor_full_gate_reviewer_packets",
        },
        "roster_selection_guard": roster_selection_guard,
        "disagreements": disagreements,
        "configured_reviewer_panel": {
            "mode": reviewer_panel_mode,
            "report_mode": (
                "codex_only_calibration" if codex_only_calibration_active else "full_panel"
            ),
            "full_panel_evidence_allowed": not codex_only_calibration_active,
            "codex_only_calibration": codex_only_calibration_active,
            "roster_selection_allowed": False,
            "roster_selection_guard": roster_selection_guard,
            "reviewer_provenance": reviewer_provenance,
            "generator_disjointness": generator_disjointness,
            "self_preference_warnings": list(
                generator_disjointness.get("self_preference_warnings") or []
            ),
            "configured_panel_active": (
                reviewer_panel_mode == "configured"
                and configured_reviewer_panel_options is not None
            ) or (
                reviewer_panel_mode == "configured" and reviewer_panel is not None
            ),
            "s_probe_vs_s_full_disagreement_count": sum(
                1 for row in rows if bool(row.get("s_probe_vs_s_full_disagreement"))
            ),
            "available_full_gate_count": sum(
                1 for row in rows if not bool(row.get("supervisor_full_gate_unavailable"))
            ),
            "unavailable_full_gate_count": sum(
                1 for row in rows if bool(row.get("supervisor_full_gate_unavailable"))
            ),
            "full_roster_available_count": full_roster_available_count,
            "codex_only_roster_available_count": codex_only_roster_available_count,
            "codex_only_panel_marginal_delta_at_matched_true_accept": (
                codex_only_panel_marginal_delta
            ),
            "panel_quality_reject_count": sum(
                1 for row in rows if bool(row.get("panel_quality_reject"))
            ),
            "panel_missing_verdict_block_count": sum(
                1 for row in rows if bool(row.get("panel_missing_verdict_block"))
            ),
            "inter_reviewer_agreement": inter_reviewer_agreement,
            "reviewer_infrastructure_diagnostics": [
                {
                    "task_id": row["task_id"],
                    "candidate_id": row["candidate_id"],
                    "diagnostic": dict(row.get("reviewer_infrastructure_diagnostic") or {}),
                }
                for row in rows
                if int(
                    (row.get("reviewer_infrastructure_diagnostic") or {}).get(
                        "failure_count", 0
                    )
                )
                > 0
            ],
        },
        "per_task_results": rows,
        "cost_usd": 0.0,
        "wall_clock_s": round(time.monotonic() - started, 6),
        "report_label": "calibration",
        "heldout_reporting": {
            "primary_metric_split": "held_out",
            "dev_metric_status": metric_splits["dev"]["status"],
            "best_of_k_in_sample": {
                "present": False,
                "label_allowed_as_heldout_improvement": False,
                "reason": "held_out_metrics_are_reported_separately_from_in_sample_or_peak_selection",
            },
            "heldout_improvement_claim_allowed": False,
        },
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "gaming_flags": sorted(gaming_flags),
        "baseline_evidence_kind": "metadata_calibration",
        "validity_notes": [
            "Bounded panel runner: rows preserve S_probe and S_full semantics; unavailable "
            "reviewer panel rows are never accepted.",
            "Fixture-only roster diagnostics cannot select Codex-only or drop reviewers; "
            "roster selection requires real or disagreement-enriched same-pool evidence.",
            "Filtered, wall-clock-truncated, and timed-out runs are calibration evidence "
            "only; report-only invariants stay false.",
        ],
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "roster_selection_allowed": False,
            "next_step": "grow an oracle-isolated corpus before any powered live-generation experiment",
        },
        "bounded_runner": bounded_runner_block,
        "diagnostic_scope": diagnostic_scope,
        "s_probe_vs_s_full_discordant_count": int(s_probe_vs_s_full_discordant_count),
        "report_only_invariants": report_only_invariants,
    }

    # Leak detection on the public aggregate/reporting path. The internal JSON
    # report carries oracle-derived scoring labels by design; checkpoints and
    # dashboard/public projections must not.
    report_leaks = _public_input_oracle_refs(_public_dashboard_report(report))
    if report_leaks:
        raise MergeabilityBenchError(
            "bounded panel runner aggregate report leaked oracle material: "
            + ", ".join(report_leaks)
        )

    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })

    if output_path is not None:
        output_path.mkdir(parents=True, exist_ok=True)
        _export_paired_acceptance_artifacts(
            output_path,
            manifest=manifest,
            calibration=calibration,
            report=report,
            rows=rows,
        )
        # runtime evidence markdown
        runtime_evidence_lines = [
            "# Bounded Panel Runner Runtime Evidence",
            "",
            f"- bench_root: {bench_root_path.as_posix()}",
            f"- runner_schema_version: {BOUNDED_PANEL_RUNNER_SCHEMA_VERSION}",
            f"- max_candidate_workers: {opts.max_candidate_workers}",
            f"- max_reviewer_workers: {opts.max_reviewer_workers}",
            f"- reviewer_fanout: serial",
            f"- review_timeout_s: {opts.review_timeout_s}",
            f"- max_wall_clock_s: {opts.max_wall_clock_s}",
            f"- wall_clock_stopped: {wall_clock_stopped}",
            f"- completed_candidate_count: {completed_count}",
            f"- interrupted_candidate_count: {len(interrupted_ids) + len(timed_out_ids)}",
            f"- selected_candidate_count: {selected_candidate_count}",
            f"- total_candidate_count: {total_candidate_count}",
            f"- full_corpus_claim: {not filtered}",
            f"- metric_applyable: False",
            f"- improvement_claim_allowed: False",
        ]
        (output_path / "runtime-evidence.md").write_text(
            "\n".join(runtime_evidence_lines) + "\n",
            encoding="utf-8",
        )
        html_text = render_panel_dashboard_html(_public_dashboard_report(report))
        (output_path / "review.html").write_text(html_text, encoding="utf-8")

    return report


def _public_dashboard_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a dashboard-safe view of the report with oracle fields stripped.

    The dashboard helper must accept only public surfaces. Build this as a
    whitelist rather than a shallow copy so oracle labels in the internal
    scoring report cannot drift into Lavish/reviewer annotation surfaces.
    """

    def _public_arm(arm: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(arm, Mapping):
            return {}
        keys = (
            "arm",
            "arm_role",
            "candidate_count",
            "available_count",
            "unavailable_count",
            "availability_status",
            "accepted_count",
            "rejected_count",
            "false_accept_count",
            "n_bad",
            "false_accept_denominator",
            "false_accept_rate",
            "false_accept_confidence_interval",
            "true_accept_count",
            "n_good",
            "true_accept_denominator",
            "true_accept_rate",
            "true_accept_confidence_interval",
            "false_reject_count",
            "false_reject_denominator",
            "false_reject_rate",
            "true_reject_count",
            "runtime",
            "model",
            "provider_family",
            "lineage",
            "tool_access",
            "assurance_grade",
            "tool_backed_command_evidence",
            "cost_usd",
        )
        return {key: arm[key] for key in keys if key in arm}

    arms = report.get("arms") if isinstance(report.get("arms"), Mapping) else {}
    panel_block = (
        report.get("configured_reviewer_panel")
        if isinstance(report.get("configured_reviewer_panel"), Mapping)
        else {}
    )
    per_reviewer = (
        report.get("per_reviewer_arms")
        if isinstance(report.get("per_reviewer_arms"), Mapping)
        else {}
    )
    roster_guard = (
        report.get("roster_selection_guard")
        if isinstance(report.get("roster_selection_guard"), Mapping)
        else {}
    )
    safe: dict[str, Any] = {
        "schema_version": report.get("schema_version"),
        "candidate_count": report.get("candidate_count"),
        "arms": {
            "supervisor_candidate_review": _public_arm(
                arms.get("supervisor_candidate_review") if isinstance(arms, Mapping) else {}
            ),
            "supervisor_full_gate": _public_arm(
                arms.get("supervisor_full_gate") if isinstance(arms, Mapping) else {}
            ),
        },
        "panel_marginal_delta_at_matched_true_accept": dict(
            report.get("panel_marginal_delta_at_matched_true_accept") or {}
        ),
        "roster_selection_guard": {
            "evidence_scope": roster_guard.get("evidence_scope"),
            "roster_selection_allowed": roster_guard.get("roster_selection_allowed"),
            "fixture_only_can_select_roster": roster_guard.get(
                "fixture_only_can_select_roster"
            ),
            "codex_only_can_select_roster": roster_guard.get(
                "codex_only_can_select_roster"
            ),
            "saturated_reviewer_agreement": roster_guard.get(
                "saturated_reviewer_agreement"
            ),
            "decision_authority": roster_guard.get("decision_authority"),
            "reasons": list(roster_guard.get("reasons") or []),
            "required_evidence": list(roster_guard.get("required_evidence") or []),
        },
        "configured_reviewer_panel": {
            "mode": panel_block.get("mode") if isinstance(panel_block, Mapping) else None,
            "report_mode": (
                panel_block.get("report_mode") if isinstance(panel_block, Mapping) else None
            ),
            "configured_panel_active": (
                panel_block.get("configured_panel_active")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "s_probe_vs_s_full_disagreement_count": (
                panel_block.get("s_probe_vs_s_full_disagreement_count")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "available_full_gate_count": (
                panel_block.get("available_full_gate_count")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "unavailable_full_gate_count": (
                panel_block.get("unavailable_full_gate_count")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "full_roster_available_count": (
                panel_block.get("full_roster_available_count")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "panel_quality_reject_count": (
                panel_block.get("panel_quality_reject_count")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "panel_missing_verdict_block_count": (
                panel_block.get("panel_missing_verdict_block_count")
                if isinstance(panel_block, Mapping)
                else None
            ),
            "inter_reviewer_agreement": list(
                panel_block.get("inter_reviewer_agreement") or []
            )
            if isinstance(panel_block, Mapping)
            else [],
            "reviewer_provenance": dict(
                panel_block.get("reviewer_provenance") or {}
            )
            if isinstance(panel_block, Mapping)
            else {},
            "generator_disjointness": dict(
                panel_block.get("generator_disjointness") or {}
            )
            if isinstance(panel_block, Mapping)
            else {},
        },
        "per_reviewer_arms": {
            str(reviewer_id): _public_arm(arm if isinstance(arm, Mapping) else {})
            for reviewer_id, arm in sorted((per_reviewer or {}).items())
        }
        if isinstance(per_reviewer, Mapping)
        else {},
        "bounded_runner": dict(report.get("bounded_runner") or {}),
        "diagnostic_scope": dict(report.get("diagnostic_scope") or {}),
        "s_probe_vs_s_full_discordant_count": report.get(
            "s_probe_vs_s_full_discordant_count"
        ),
        "report_only_invariants": dict(report.get("report_only_invariants") or {}),
    }
    # Provide a minimal public projection per row.
    rows = report.get("per_task_results") or []
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        public_rows.append({
            "task_id": str(row.get("task_id") or ""),
            "candidate_id": str(row.get("candidate_id") or ""),
            "control_kind": str(row.get("control_kind") or ""),
            "split": str(row.get("split") or ""),
            "supervisor_accept": bool(row.get("supervisor_accept")),
            "supervisor_full_gate_accept": bool(row.get("supervisor_full_gate_accept")),
            "supervisor_full_gate_unavailable": bool(
                row.get("supervisor_full_gate_unavailable")
            ),
            "panel_quality_label": str(row.get("panel_quality_label") or ""),
            "full_roster_available": bool(row.get("full_roster_available")),
            "s_probe_vs_s_full_disagreement": bool(
                row.get("s_probe_vs_s_full_disagreement")
            ),
        })
    safe["dashboard_rows"] = public_rows
    leaks = _public_input_oracle_refs(safe)
    if leaks:
        raise MergeabilityBenchError(
            "public dashboard projection leaked oracle material: " + ", ".join(leaks)
        )
    return safe


def render_panel_dashboard_html(report: Mapping[str, Any]) -> str:
    """Render an annotation-ready HTML dashboard from a public report.

    The dashboard is intentionally simple: it is a public-only view that can
    be opened in Lavish for human annotation without affecting scoring,
    policy, or gate authority. Leak detection runs before rendering and a
    second scan runs on the rendered HTML; either finding raises
    ``MergeabilityBenchError`` so hidden oracle material never enters a
    public review surface.
    """

    leaks = _public_input_oracle_refs(dict(report))
    if leaks:
        raise MergeabilityBenchError(
            "panel dashboard input leaked oracle material: " + ", ".join(leaks)
        )
    esc = _html.escape

    arms = report.get("arms") or {}
    probe = arms.get("supervisor_candidate_review") or {}
    full = arms.get("supervisor_full_gate") or {}
    bounded = report.get("bounded_runner") or {}
    panel_block = report.get("configured_reviewer_panel") or {}
    panel_marginal = report.get("panel_marginal_delta_at_matched_true_accept") or {}
    roster_guard = report.get("roster_selection_guard") or {}
    invariants = report.get("report_only_invariants") or {
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }
    per_reviewer = report.get("per_reviewer_arms") or {}
    inter_reviewer = panel_block.get("inter_reviewer_agreement") or []
    diagnostic_scope = report.get("diagnostic_scope") or {}
    rows = report.get("dashboard_rows") or []

    def _fmt(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.6f}"
        if value is None:
            return ""
        return str(value)

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append("<html><head><meta charset='utf-8'>")
    parts.append("<title>Mergeability Panel Annotation Dashboard</title>")
    parts.append(
        "<style>body{font-family:system-ui,sans-serif;margin:2em;}"
        "table{border-collapse:collapse;margin:1em 0;}"
        "th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;}"
        "h2{margin-top:1.5em;}</style>"
    )
    parts.append("</head><body>")
    parts.append("<h1>Mergeability Panel Annotation Dashboard</h1>")
    parts.append("<p>Public-only review surface. No oracle material.</p>")

    parts.append("<h2>Candidate Counts</h2>")
    parts.append("<table>")
    parts.append(
        "<tr><th>total candidates</th><td>"
        + esc(_fmt(report.get("candidate_count")))
        + "</td></tr>"
    )
    parts.append(
        "<tr><th>n_good</th><td>" + esc(_fmt(full.get("n_good"))) + "</td></tr>"
    )
    parts.append(
        "<tr><th>n_bad</th><td>" + esc(_fmt(full.get("n_bad"))) + "</td></tr>"
    )
    parts.append("</table>")

    parts.append("<h2>Arms Table (S_probe vs S_full)</h2>")
    parts.append("<table>")
    parts.append(
        "<tr><th>arm</th><th>FAR</th><th>TAR</th><th>available</th><th>unavailable</th></tr>"
    )
    for arm_name, arm in (
        ("S_probe (supervisor_candidate_review)", probe),
        ("S_full (supervisor_full_gate)", full),
    ):
        parts.append(
            "<tr><td>"
            + esc(arm_name)
            + "</td><td>"
            + esc(_fmt(arm.get("false_accept_rate")))
            + "</td><td>"
            + esc(_fmt(arm.get("true_accept_rate")))
            + "</td><td>"
            + esc(_fmt(arm.get("available_count")))
            + "</td><td>"
            + esc(_fmt(arm.get("unavailable_count")))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Panel Marginal</h2>")
    parts.append(
        "<p>Status: "
        + esc(_fmt(panel_marginal.get("status")))
        + " - reason: "
        + esc(_fmt(panel_marginal.get("reason") or ""))
        + "</p>"
    )

    parts.append("<h2>Roster Selection Guard</h2>")
    parts.append("<table>")
    for key in (
        "evidence_scope",
        "roster_selection_allowed",
        "fixture_only_can_select_roster",
        "codex_only_can_select_roster",
        "saturated_reviewer_agreement",
        "decision_authority",
    ):
        parts.append(
            "<tr><th>"
            + esc(str(key))
            + "</th><td>"
            + esc(_fmt(roster_guard.get(key)))
            + "</td></tr>"
        )
    parts.append(
        "<tr><th>required evidence</th><td>"
        + esc(", ".join(str(item) for item in roster_guard.get("required_evidence") or []))
        + "</td></tr>"
    )
    parts.append("</table>")

    parts.append("<h2>S_probe-vs-S_full Discordance</h2>")
    parts.append(
        "<p>Discordant rows: "
        + esc(_fmt(report.get("s_probe_vs_s_full_discordant_count")))
        + "</p>"
    )

    parts.append("<h2>Per-Reviewer Arms</h2>")
    parts.append("<table><tr><th>reviewer</th><th>FAR</th><th>TAR</th><th>available</th></tr>")
    for reviewer_id, arm in sorted((per_reviewer or {}).items()):
        if not isinstance(arm, Mapping):
            continue
        parts.append(
            "<tr><td>"
            + esc(str(reviewer_id))
            + "</td><td>"
            + esc(_fmt(arm.get("false_accept_rate")))
            + "</td><td>"
            + esc(_fmt(arm.get("true_accept_rate")))
            + "</td><td>"
            + esc(_fmt(arm.get("available_count")))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Inter-Reviewer Agreement</h2>")
    parts.append("<table><tr><th>pair</th><th>agreement</th></tr>")
    for entry in inter_reviewer:
        if not isinstance(entry, Mapping):
            continue
        pair = entry.get("reviewer_pair") or entry.get("pair") or "?"
        parts.append(
            "<tr><td>"
            + esc(_fmt(pair))
            + "</td><td>"
            + esc(_fmt(entry.get("agreement_rate")))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Unavailable Rows</h2>")
    parts.append("<table><tr><th>task</th><th>candidate</th><th>reason</th></tr>")
    for row in rows:
        if not row.get("supervisor_full_gate_unavailable"):
            continue
        parts.append(
            "<tr><td>"
            + esc(_fmt(row.get("task_id")))
            + "</td><td>"
            + esc(_fmt(row.get("candidate_id")))
            + "</td><td>"
            + esc(_fmt(row.get("panel_quality_label")))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Report-Only Flags</h2>")
    parts.append("<table>")
    for key, value in sorted(invariants.items()):
        parts.append(
            "<tr><th>"
            + esc(str(key))
            + "</th><td>"
            + esc(_fmt(value))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Bounded Runner</h2>")
    parts.append("<table>")
    for key in (
        "max_candidate_workers",
        "max_reviewer_workers",
        "reviewer_fanout",
        "review_timeout_s",
        "max_wall_clock_s",
        "wall_clock_stopped",
        "completed_candidate_count",
        "interrupted_candidate_count",
    ):
        parts.append(
            "<tr><th>"
            + esc(str(key))
            + "</th><td>"
            + esc(_fmt(bounded.get(key)))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Diagnostic Scope</h2>")
    parts.append("<table>")
    for key in (
        "full_corpus_claim",
        "selected_candidate_count",
        "total_candidate_count",
    ):
        parts.append(
            "<tr><th>"
            + esc(str(key))
            + "</th><td>"
            + esc(_fmt(diagnostic_scope.get(key)))
            + "</td></tr>"
        )
    parts.append("</table>")

    parts.append("</body></html>")
    html_text = "".join(parts)

    for marker in ORACLE_REVIEW_FORBIDDEN_TEXT:
        if marker in html_text:
            raise MergeabilityBenchError(
                "panel dashboard HTML leaked oracle marker: " + marker
            )
    for key in ORACLE_REVIEW_FORBIDDEN_KEYS:
        if key in html_text:
            raise MergeabilityBenchError(
                "panel dashboard HTML leaked forbidden key: " + key
            )
    return html_text


def _bounded_panel_runner_main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the bounded checkpointed mergeability panel corpus runner."
    )
    parser.add_argument("--bench-root", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--max-candidate-workers", type=int, default=2)
    parser.add_argument("--max-reviewer-workers", type=int, default=1)
    parser.add_argument("--review-timeout-s", type=float, default=600.0)
    parser.add_argument("--max-wall-clock-s", type=float)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids")
    parser.add_argument(
        "--reviewer-panel-mode",
        choices=("configured", "custom"),
        default="configured",
    )
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--relaxed-calibration", action="store_true")
    args = parser.parse_args(argv)

    options = BoundedPanelRunnerOptions(
        max_candidate_workers=args.max_candidate_workers,
        max_reviewer_workers=args.max_reviewer_workers,
        review_timeout_s=args.review_timeout_s,
        max_wall_clock_s=args.max_wall_clock_s,
        max_candidates=args.max_candidates,
        candidate_selector=tuple(args.candidate_ids) if args.candidate_ids else None,
        checkpoint_dir=args.checkpoint_dir,
        resume=not args.no_resume,
    )
    configured_options = (
        ConfiguredReviewerPanelOptions()
        if args.reviewer_panel_mode == "configured"
        else None
    )
    report = run_bounded_parallel_panel_corpus(
        bench_root=args.bench_root,
        options=options,
        output_dir=args.output_dir,
        configured_reviewer_panel_options=configured_options,
        reviewer_panel_mode=args.reviewer_panel_mode,
        strict_calibration=not args.relaxed_calibration,
    )
    summary = {
        "candidate_count": report.get("candidate_count"),
        "report_label": report.get("report_label"),
        "metric_applyable": report.get("metric_applyable"),
        "improvement_claim_allowed": report.get("improvement_claim_allowed"),
        "bounded_runner": report.get("bounded_runner"),
        "output_dir": args.output_dir,
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_bounded_panel_runner_main())
