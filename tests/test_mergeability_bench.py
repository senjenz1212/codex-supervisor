from __future__ import annotations

import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from supervisor.autoresearch.evaluator import run_evaluator_trials
from supervisor.autoresearch.policy_evolution import (
    derive_policy_evolution_proposals_from_report,
    report_contains_derivable_policy_record,
)
from supervisor.autoresearch.report import build_autoresearch_report
from supervisor.autoresearch.schema import AutoresearchAttempt, AutoresearchExperiment
from supervisor.autoresearch.validation import validate_attempt
from supervisor.mergeability_bench import (
    MERGEABILITY_TASK_SCHEMA_VERSION,
    MergeabilityBenchError,
    MergeabilityCandidate,
    grade_mergeability_candidate,
    load_mergeability_candidate,
    load_mergeability_task,
    load_mergeability_tasks,
    result_receipt,
)


BENCH_ROOT = Path("tests/fixtures/mergeability_bench")
EVALUATOR = Path("supervisor/autoresearch/evaluators/mergeability_bench.py")


def _task():
    return load_mergeability_task(BENCH_ROOT, "calculator-addition")


def _candidate(name: str):
    return load_mergeability_candidate(BENCH_ROOT / "candidates" / f"{name}.json")


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_load_mergeability_tasks_reads_typed_fixture_contract():
    tasks = load_mergeability_tasks(BENCH_ROOT)

    task = tasks[0]
    assert task.task_id == "calculator-addition"
    assert task.repo_fixture_ref == "repos/calculator_bug"
    assert task.allowed_mutable_paths == ("app/", "tests/")
    assert task.hidden_test_commands == (("python", "-m", "pytest", "hidden/test_behavior.py", "-q"),)
    assert task.reverse_test_commands == (("python", "-m", "pytest", "tests/test_calculator.py", "-q"),)
    assert task.weighted_secondary_rubric == {
        "maintainability": 0.3,
        "minimal_diff": 0.4,
        "test_clarity": 0.3,
    }
    assert task.task_hash


def test_invalid_mergeability_task_fails_with_actionable_error(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "bad.json").write_text(
        json.dumps({
            "schema_version": MERGEABILITY_TASK_SCHEMA_VERSION,
            "task_id": "bad",
            "repo_fixture_ref": "repos/missing",
            "prompt": "No hidden commands declared here.",
            "allowed_mutable_paths": ["app/"],
        }),
        encoding="utf-8",
    )

    try:
        load_mergeability_tasks(tmp_path)
    except MergeabilityBenchError as exc:
        assert "hidden_test_commands" in str(exc)
    else:  # pragma: no cover - defensive readability
        raise AssertionError("invalid task should fail")


def test_mergeability_result_serializes_replayable_hashes(tmp_path):
    result = grade_mergeability_candidate(
        _task(),
        _candidate("known_good"),
        bench_root=BENCH_ROOT,
        output_dir=tmp_path,
    )

    payload = result.to_payload()
    assert payload["schema_version"] == "supervisor-mergeability-result/v1"
    assert payload["result_sha256"]
    assert payload["artifact_hashes"]["task"] == _task().task_hash
    assert payload["artifact_hashes"]["candidate"] == _candidate("known_good").candidate_hash
    assert (tmp_path / "calculator-addition-known-good.json").exists()
    receipt = result_receipt(result, result_ref="mergeability-result.json")
    assert receipt["source"] == "supervisor"
    assert receipt["evidence_grade"] == "runtime_native"


def test_mergeability_controls_discriminate_noop_known_bad_and_known_good(tmp_path):
    task = _task()
    noop = grade_mergeability_candidate(task, _candidate("noop"), bench_root=BENCH_ROOT, output_dir=tmp_path)
    known_bad = grade_mergeability_candidate(task, _candidate("known_bad"), bench_root=BENCH_ROOT, output_dir=tmp_path)
    known_good = grade_mergeability_candidate(task, _candidate("known_good"), bench_root=BENCH_ROOT, output_dir=tmp_path)

    assert noop.final_score == 0.0
    assert noop.hidden_test_status == "failed"
    assert known_bad.final_score == 0.0
    assert known_bad.hidden_test_status == "failed"
    assert known_bad.reverse_test_status == "passed"
    assert known_good.final_score == 1.0
    assert known_good.blocker_status == "passed"
    assert known_good.hidden_test_status == "passed"
    assert known_good.reverse_test_status == "passed"
    assert known_good.lint_build_status == "passed"


def test_mergeability_candidate_hidden_material_edit_is_rejected():
    candidate = MergeabilityCandidate(
        candidate_id="hidden-edit",
        task_id="calculator-addition",
        changed_files=("hidden/test_behavior.py",),
        files={"hidden/test_behavior.py": "def test_fake():\n    assert True\n"},
    )

    result = grade_mergeability_candidate(_task(), candidate, bench_root=BENCH_ROOT)

    assert result.final_score == 0.0
    assert result.scope_status == "failed"
    assert "scope:protected_path_mutation:hidden/test_behavior.py" in result.failures


def test_mergeability_candidate_mutable_path_escape_is_rejected():
    candidate = MergeabilityCandidate(
        candidate_id="escape",
        task_id="calculator-addition",
        changed_files=("README.md",),
        files={"README.md": "outside allowed surface\n"},
    )

    result = grade_mergeability_candidate(_task(), candidate, bench_root=BENCH_ROOT)

    assert result.final_score == 0.0
    assert result.scope_status == "failed"
    assert "scope:path_outside_allowed_mutable_surface:README.md" in result.failures


def test_reverse_classical_requires_candidate_tests_fail_on_base():
    task = _task()
    candidate = MergeabilityCandidate(
        candidate_id="tautological-test",
        task_id="calculator-addition",
        changed_files=("app/calculator.py", "tests/test_calculator.py"),
        files={
            "app/calculator.py": "def add(left: int, right: int) -> int:\n    return left + right\n",
            "tests/test_calculator.py": (
                "from app.calculator import add\n\n\n"
                "def test_preserves_broken_base_behavior():\n"
                "    assert add(5, 3) == 2\n"
            ),
        },
    )

    result = grade_mergeability_candidate(task, candidate, bench_root=BENCH_ROOT)

    assert result.hidden_test_status == "passed"
    assert result.reverse_test_status == "failed"
    assert result.final_score == 0.0


def test_reverse_classical_rejects_candidate_without_submitted_tests():
    candidate = MergeabilityCandidate(
        candidate_id="fix-without-tests",
        task_id="calculator-addition",
        changed_files=("app/calculator.py",),
        files={
            "app/calculator.py": "def add(left: int, right: int) -> int:\n    return left + right\n",
        },
    )

    result = grade_mergeability_candidate(_task(), candidate, bench_root=BENCH_ROOT)

    assert result.hidden_test_status == "passed"
    assert result.reverse_test_status == "failed"
    assert result.final_score == 0.0
    assert "reverse tests:no_candidate_tests_submitted" in result.failures


def test_autoresearch_mergeability_evaluator_emits_computed_runtime_native_metric(tmp_path):
    attempt_json = tmp_path / "attempt.json"
    attempt_json.write_text(
        json.dumps({
            "task_id": "calculator-addition",
            "patch_ref": "tests/fixtures/mergeability_bench/candidates/known_good.json",
        }),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AUTORESEARCH_SOURCE_ROOT": str(Path.cwd()),
        "MERGEABILITY_BENCH_ROOT": str((Path.cwd() / BENCH_ROOT).resolve()),
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(EVALUATOR),
            "--attempt-worktree",
            str(tmp_path),
            "--trial-index",
            "0",
            "--metric-name",
            "mergeability_score",
            "--attempt-json",
            str(attempt_json),
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["metric_value"] == 1.0
    assert payload["metrics"]["blocker_status"] == "passed"
    assert payload["evidence_refs"][0].startswith("mergeability_result:")
    receipt = payload["runtime_native_receipt"]
    assert receipt["source"] == "supervisor"
    assert receipt["evidence_grade"] == "runtime_native"


def test_autoresearch_mergeability_evaluator_works_with_live_trials(tmp_path):
    experiment = AutoresearchExperiment(
        experiment_id="mergeability-exp",
        task_id="calculator-addition",
        hypothesis="Use the mergeability bench as a report-only evaluator.",
        baseline_ref="baseline:current",
        mutable_paths=("app/", "tests/"),
        immutable_paths=("hidden/", "supervisor/autoresearch/evaluators/"),
        evaluator_ref=EVALUATOR.as_posix(),
        evaluator_hash=_sha256(EVALUATOR),
        metric_name="mergeability_score",
        k_trials=2,
        timeout_s=20.0,
        execution_mode="live",
    )
    attempt = AutoresearchAttempt(
        attempt_id="mergeability-attempt",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="worker-mergeability",
        hypothesis="Known good candidate should pass the bench.",
        changed_files=("app/calculator.py", "tests/test_calculator.py"),
        metric_trials=(),
        metric_before=0.0,
        metric_source="evaluator_execution",
        patch_ref=(BENCH_ROOT / "candidates/known_good.json").as_posix(),
        policy_candidate_changes={
            ".supervisor/policy-overlay.yaml": (BENCH_ROOT / "candidates/known_good.json").as_posix(),
        },
        artifact_hashes={},
        evidence_refs=(),
    )

    execution = run_evaluator_trials(
        experiment=experiment,
        attempt=attempt,
        repo_root=Path.cwd(),
        output_dir=tmp_path,
    )

    assert execution.metric_trials == (1.0, 1.0)
    assert execution.metric_source == "evaluator_execution"
    assert execution.evaluator_run_hash
    assert execution.evidence_refs == (f"evaluator_run:{execution.evaluator_run_ref}",)
    quality = execution.evaluator_quality
    assert quality["source"] == "supervisor_control_execution"
    assert quality["evidence_grade"] == "runtime_native"
    assert quality["verdict"] == "accepted"
    assert quality["controls"]["noop"]["metric_delta"] == 0.0
    assert quality["controls"]["harmful"]["metric_delta"] == 0.0
    assert quality["controls"]["known_good"]["metric_delta"] == 1.0


def test_autoresearch_report_only_invariants_with_mergeability_evaluator(tmp_path):
    execution_ref = "evaluator-runs/mergeability-attempt.json"
    attempt = AutoresearchAttempt(
        attempt_id="mergeability-attempt",
        experiment_id="mergeability-exp",
        task_id="calculator-addition",
        worker_id="worker-mergeability",
        hypothesis="Known good candidate should pass the bench.",
        changed_files=("app/calculator.py", "tests/test_calculator.py"),
        metric_trials=(1.0, 1.0),
        metric_before=0.0,
        metric_after=1.0,
        metric_delta=1.0,
        metric_source="evaluator_execution",
        evaluator_run_ref=execution_ref,
        evaluator_run_hash="runhash",
        patch_ref=(BENCH_ROOT / "candidates/known_good.json").as_posix(),
        artifact_hashes={},
        evidence_refs=(f"evaluator_run:{execution_ref}",),
    )
    experiment = AutoresearchExperiment(
        experiment_id="mergeability-exp",
        task_id="calculator-addition",
        hypothesis="Use the mergeability bench as a report-only evaluator.",
        baseline_ref="baseline:current",
        mutable_paths=("app/", "tests/"),
        immutable_paths=("hidden/", "supervisor/autoresearch/evaluators/"),
        evaluator_ref=EVALUATOR.as_posix(),
        evaluator_hash=_sha256(EVALUATOR),
        metric_name="mergeability_score",
        k_trials=2,
    )

    validation = validate_attempt(experiment=experiment, attempt=attempt, repo_root=tmp_path)
    payload = validation.to_payload()
    report = build_autoresearch_report([validation])

    assert payload["default_change_allowed"] is False
    assert payload["policy_mutated"] is False
    assert payload["gate_advanced"] is False
    assert "zero_variance_trials" in payload["gaming_flags"]
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    ) == []
    assert report_contains_derivable_policy_record(report, repo_root=tmp_path) is False
