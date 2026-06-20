from __future__ import annotations

import json
import os
import shutil
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
    build_mergeability_corpus_manifest,
    grade_mergeability_candidate,
    load_mergeability_candidate,
    load_mergeability_task,
    load_mergeability_tasks,
    result_receipt,
    run_paired_acceptance_pilot,
    validate_mergeability_corpus,
)


BENCH_ROOT = Path("tests/fixtures/mergeability_bench")
EVALUATOR = Path("supervisor/autoresearch/evaluators/mergeability_bench.py")


def _task():
    return load_mergeability_task(BENCH_ROOT, "calculator-addition")


def _candidate(name: str):
    return load_mergeability_candidate(BENCH_ROOT / "candidates" / f"{name}.json")


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _accept_public_review_panel(packet):
    return {
        "decision": "accept" if packet["public_review"]["accept"] else "deny",
        "available": True,
        "reason": "deterministic_fixture_panel",
        "reviewer_ids": ["fixture-reviewer"],
        "accepted_reviewers": ["fixture-reviewer"] if packet["public_review"]["accept"] else [],
    }


def _unavailable_panel(_packet):
    return {
        "decision": "unavailable",
        "available": False,
        "reason": "fixture_panel_unavailable",
        "missing_reviewers": ["fixture-reviewer"],
    }


def _deny_panel(_packet):
    return {
        "decision": "deny",
        "available": True,
        "reason": "deterministic_panel_denial",
        "reviewer_ids": ["fixture-reviewer"],
    }


def test_load_mergeability_tasks_reads_typed_fixture_contract():
    tasks = load_mergeability_tasks(BENCH_ROOT)

    task = tasks[0]
    assert task.task_id == "calculator-addition"
    assert task.split == "held_out"
    assert task.task_class == "arithmetic"
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


def test_mergeability_corpus_manifest_requires_positive_and_negative_controls():
    manifest = build_mergeability_corpus_manifest(
        BENCH_ROOT,
        candidate_paths=(BENCH_ROOT / "candidates" / "known_good.json",),
    )

    task = manifest["tasks"][0]
    assert task["positive_controls"] == ["known-good"]
    assert task["negative_controls"] == []
    assert task["calibration_eligible"] is False
    assert task["excluded_reason"] == "requires_negative_control_and_public_pass_hidden_fail_trap"

    try:
        validate_mergeability_corpus(
            BENCH_ROOT,
            candidate_paths=(BENCH_ROOT / "candidates" / "known_good.json",),
        )
    except MergeabilityBenchError as exc:
        assert "negative_control" in str(exc)
        assert "public_pass_hidden_fail_trap" in str(exc)
    else:  # pragma: no cover - defensive readability
        raise AssertionError("positive-only corpus should not calibrate")


def test_mergeability_calibration_rejects_broken_known_good_control(tmp_path):
    broken = tmp_path / "broken_known_good.json"
    broken.write_text(
        json.dumps({
            "schema_version": "supervisor-mergeability-candidate/v1",
            "candidate_id": "broken-known-good",
            "task_id": "calculator-addition",
            "changed_files": ["app/calculator.py", "tests/test_calculator.py"],
            "files": {
                "app/calculator.py": "def add(left: int, right: int) -> int:\n    return 42\n",
                "tests/test_calculator.py": (
                    "from app.calculator import add\n\n\n"
                    "def test_add_returns_magic_number():\n"
                    "    assert add(20, 22) == 42\n"
                ),
            },
            "generator_metadata": {
                "control": "known_good",
                "expected_outcome": "pass",
                "baseline_accept": True,
            },
        }),
        encoding="utf-8",
    )

    try:
        validate_mergeability_corpus(
            BENCH_ROOT,
            candidate_paths=(BENCH_ROOT / "candidates" / "noop.json", broken),
        )
    except MergeabilityBenchError as exc:
        assert "broken-known-good expected pass but observed fail" in str(exc)
    else:  # pragma: no cover - defensive readability
        raise AssertionError("broken known-good control should fail calibration")


def test_mergeability_calibration_covers_seeded_failure_modes(tmp_path):
    summary = validate_mergeability_corpus(BENCH_ROOT, output_dir=tmp_path)

    assert summary["status"] == "accepted"
    assert summary["candidate_count"] >= 12
    assert summary["calibration_metric_applyable"] is True
    assert summary["gaming_flags"] == []
    by_kind = {row["control_kind"]: row for row in summary["results"]}
    assert by_kind["noop"]["observed_outcome"] == "fail"
    assert by_kind["known_bad"]["observed_outcome"] == "fail"
    assert by_kind["known_good"]["observed_outcome"] == "pass"
    assert by_kind["missing_regression_test"]["observed_outcome"] == "fail"
    assert by_kind["tautological_test"]["observed_outcome"] == "fail"
    assert by_kind["protected_path_escape"]["scope_status"] == "failed"
    assert by_kind["scope_escape"]["scope_status"] == "failed"
    assert by_kind["secondary_rubric_only"]["observed_outcome"] == "pass"
    assert (tmp_path / "corpus_manifest.json").exists()
    assert (tmp_path / "calibration_summary.json").exists()


def test_saturated_all_one_results_are_non_applyable(tmp_path):
    bad_but_passing = tmp_path / "bad_but_passing.json"
    bad_but_passing.write_text(
        json.dumps({
            "schema_version": "supervisor-mergeability-candidate/v1",
            "candidate_id": "bad-but-passing",
            "task_id": "calculator-addition",
            "changed_files": ["app/calculator.py", "tests/test_calculator.py"],
            "files": {
                "app/calculator.py": "def add(left: int, right: int) -> int:\n    return left + right\n",
                "tests/test_calculator.py": (
                    "from app.calculator import add\n\n\n"
                    "def test_adds_positive_integers():\n"
                    "    assert add(2, 3) == 5\n"
                ),
            },
            "generator_metadata": {
                "control": "known_bad",
                "expected_outcome": "fail",
                "baseline_accept": True,
            },
        }),
        encoding="utf-8",
    )

    summary = validate_mergeability_corpus(
        BENCH_ROOT,
        strict=False,
        candidate_paths=(BENCH_ROOT / "candidates" / "known_good.json", bad_but_passing),
    )

    assert "saturated_all_ones" in summary["gaming_flags"]
    assert summary["calibration_metric_applyable"] is False
    assert summary["status"] == "rejected"


def test_paired_pilot_records_independent_supervisor_candidate_review_arm(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    assert report["schema_version"] == "supervisor-mergeability-paired-report/v1"
    assert report["report_label"] == "calibration"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False

    baseline = report["arms"]["baseline"]
    supervisor = report["arms"]["supervisor_candidate_review"]
    oracle_ceiling = report["arms"]["oracle_ceiling"]
    assert baseline["arm_role"] == "baseline_self_report"
    assert baseline["decision_source"] == "candidate_self_report"
    assert baseline["oracle_coupled"] is False
    assert supervisor["arm_role"] == "supervisor_candidate_review"
    assert supervisor["decision_source"] == "supervisor_candidate_review"
    assert supervisor["oracle_coupled"] is False
    assert oracle_ceiling["arm_role"] == "oracle_ceiling"
    assert oracle_ceiling["decision_source"] == "oracle_final_score"
    assert oracle_ceiling["oracle_coupled"] is True
    assert report["arms"]["supervisor"]["legacy_alias_of"] == "supervisor_candidate_review"

    for row in report["per_task_results"]:
        assert row["baseline_decision_source"] == "candidate_self_report"
        assert row["supervisor_decision_source"] == "supervisor_candidate_review"
        assert row["supervisor_review"]["decision_source"] == "supervisor_candidate_review"
        assert row["supervisor_review"]["oracle_coupled"] is False
        assert row["oracle_ceiling_decision_source"] == "oracle_final_score"


def test_paired_report_records_full_gate_arm_with_panel_decision(tmp_path):
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel=_accept_public_review_panel,
    )

    assert "supervisor_candidate_review" in report["arms"]
    full_gate = report["arms"]["supervisor_full_gate"]
    assert full_gate["arm_role"] == "supervisor_full_gate"
    assert full_gate["decision_source"] == "supervisor_candidate_review+independent_reviewer_panel"
    assert full_gate["oracle_coupled"] is False
    assert full_gate["availability_status"] == "available"
    assert full_gate["unavailable_count"] == 0
    assert report["oracle_agreement"]["supervisor_full_gate"]["candidate_count"] == report["candidate_count"]

    for row in report["per_task_results"]:
        assert row["supervisor_full_gate_decision_source"] == "supervisor_full_gate"
        review = row["supervisor_full_gate_review"]
        assert review["schema_version"] == "supervisor-mergeability-full-gate-review/v1"
        assert review["decision_source"] == "supervisor_candidate_review+independent_reviewer_panel"
        assert review["panel_result"]["reason"] == "deterministic_fixture_panel"
        assert review["reviewer_packet_refs"][0]["source"] == "supervisor"
        assert review["reviewer_packet_refs"][0]["evidence_grade"] == "runtime_native"
        assert row["supervisor_full_gate_accept"] == (
            row["supervisor_candidate_review_accept"] and review["panel_decision"] == "accept"
        )


def test_full_gate_reviewer_packet_excludes_oracle_material(tmp_path):
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel=_accept_public_review_panel,
    )

    for row in report["per_task_results"]:
        review = row["supervisor_full_gate_review"]
        assert review["reviewer_packet_sha256"]
        encoded = json.dumps(review["reviewer_packet"], sort_keys=True)
        assert "hidden/test_behavior.py" not in encoded
        assert ".mergeability/" not in encoded
        assert '"hidden_test_commands"' not in encoded
        assert '"expected_outcome"' not in encoded
        assert '"final_score"' not in encoded
        assert '"oracle_accept"' not in encoded
        assert not review["reviewer_packet"].get("oracle_isolation_violations")


def test_full_gate_unavailable_reviewer_does_not_count_as_accept(tmp_path):
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel=_unavailable_panel,
    )

    full_gate = report["arms"]["supervisor_full_gate"]
    assert full_gate["availability_status"] == "unavailable"
    assert full_gate["unavailable_count"] == report["candidate_count"]
    assert full_gate["accepted_count"] == 0
    assert "reviewer_panel_unavailable" in report["gaming_flags"]
    assert any(row["supervisor_candidate_review_accept"] for row in report["per_task_results"])
    for row in report["per_task_results"]:
        assert row["supervisor_full_gate_unavailable"] is True
        assert row["supervisor_full_gate_accept"] is False
        assert row["supervisor_full_gate_review"]["unavailable_reason"] == "fixture_panel_unavailable"


def test_panel_marginal_delta_is_reported_only_when_matched_true_accept_is_computable(tmp_path):
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel=_deny_panel,
    )

    delta = report["panel_marginal_delta_at_matched_true_accept"]
    assert delta["status"] == "not_matched"
    assert delta["reason"] == "public_review_and_full_gate_true_accept_rates_differ"
    assert delta["public_review_true_accept_rate"] != delta["supervisor_full_gate_true_accept_rate"]


def test_full_gate_calibration_report_cannot_create_applyable_policy_claim(tmp_path):
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel=_accept_public_review_panel,
    )

    assert report["report_label"] == "calibration"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path.cwd(),
        affected_gates=("execution", "outcome_review"),
    ) == []


def test_supervisor_candidate_review_input_excludes_oracle_material(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    for row in report["per_task_results"]:
        review = row["supervisor_review"]
        payload = review["public_input_payload"]
        encoded = json.dumps(payload, sort_keys=True)
        assert "hidden/test_behavior.py" not in encoded
        assert '"hidden_test_commands"' not in encoded
        assert '"expected_outcome"' not in encoded
        assert '"final_score"' not in encoded
        assert '"oracle_accept"' not in encoded
        assert review["candidate_review_worktree_hash"]
        assert review["public_input_hash"]
        assert not review["protected_paths_present_in_review_worktree"]


def test_oracle_isolation_violation_blocks_metric_applyability(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    row = next(row for row in report["per_task_results"] if row["candidate_id"] == "hidden-edit")
    assert row["candidate_id"] == "hidden-edit"
    assert row["supervisor_accept"] is False
    assert "oracle_isolation_violation" in row["supervisor_review"]["gaming_flags"]
    assert "oracle_isolation_violation" in report["gaming_flags"]
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_supervisor_candidate_review_preserves_disagreement_with_oracle(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    disagreements = [
        row for row in report["per_task_results"]
        if row["supervisor_accept"] != row["oracle_accept"]
    ]
    assert disagreements
    assert any(row["supervisor_false_accept"] for row in disagreements)
    assert all(row["reason"] == "supervisor_candidate_review_disagreed_with_oracle" for row in report["disagreements"])


def test_paired_report_surfaces_false_accept_true_accept_false_reject_rates(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    baseline = report["arms"]["baseline"]
    supervisor = report["arms"]["supervisor_candidate_review"]
    oracle_ceiling = report["arms"]["oracle_ceiling"]

    assert baseline["false_accept_count"] >= 1
    assert baseline["false_accept_rate"] > 0.0
    assert supervisor["false_accept_count"] >= 1
    assert supervisor["false_accept_rate"] > 0.0
    assert "false_reject_rate" in supervisor
    assert "true_accept_rate" in supervisor
    assert oracle_ceiling["false_accept_count"] == 0
    assert oracle_ceiling["false_accept_rate"] == 0.0
    assert oracle_ceiling["true_accept_rate"] == 1.0
    assert report["false_accept_at_matched_true_accept"]["status"] in {"computed", "insufficient_candidate_pool"}
    assert report["positive_control_count"] >= 1
    assert report["negative_control_count"] >= 1


def test_paired_acceptance_pilot_uses_identical_candidate_pool_for_both_arms(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)
    manifest = build_mergeability_corpus_manifest(BENCH_ROOT)
    task_hashes = {entry["task_id"]: entry["task_hash"] for entry in manifest["tasks"]}
    candidate_hashes = {
        (entry["task_id"], entry["candidate_id"]): entry["candidate_hash"]
        for entry in manifest["candidates"]
    }
    expected_pool_hash = _sha256_json([
        {
            "task_id": row["task_id"],
            "task_hash": task_hashes[row["task_id"]],
            "candidate_id": row["candidate_id"],
            "candidate_hash": candidate_hashes[(row["task_id"], row["candidate_id"])],
        }
        for row in report["per_task_results"]
    ])

    candidate_count = len(report["per_task_results"])
    assert report["arms"]["baseline"]["candidate_count"] == candidate_count
    assert report["arms"]["supervisor_candidate_review"]["candidate_count"] == candidate_count
    assert report["arms"]["supervisor"]["candidate_count"] == candidate_count
    assert report["candidate_pool_sha256"] == expected_pool_hash
    assert report["disagreements"]


def test_perfect_oracle_agreement_tripwire_blocks_improvement_claim(tmp_path):
    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir()
    source_candidates = {
        "known_good": "known_good",
        "secondary_rubric_only": "secondary_rubric_only",
        "noop": "noop",
        "known_bad": "known_bad",
        "missing_regression_test": "missing_regression_test",
        "tautological_test": "tautological_test",
        "hidden_behavior_miss": "hidden_behavior_miss",
        "hidden_edit": "protected_path_escape",
        "mutable_escape": "scope_escape",
    }
    paths = []
    for source_name, control in source_candidates.items():
        payload = json.loads((BENCH_ROOT / "candidates" / f"{source_name}.json").read_text(encoding="utf-8"))
        if control in {
            "noop",
            "known_bad",
            "hidden_behavior_miss",
            "missing_regression_test",
            "tautological_test",
            "protected_path_escape",
            "scope_escape",
        }:
            payload["changed_files"] = ["hidden/test_behavior.py"]
            payload["files"] = {"hidden/test_behavior.py": "# protected path change\n"}
            payload["generator_metadata"]["expected_outcome"] = "fail"
        payload["generator_metadata"]["control"] = control
        target = candidate_dir / f"{source_name}.json"
        target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        paths.append(target)

    report = run_paired_acceptance_pilot(BENCH_ROOT, candidate_paths=tuple(paths), output_dir=tmp_path / "out")

    assert "perfect_oracle_agreement_tripwire" in report["gaming_flags"]
    assert report["oracle_agreement"]["supervisor_candidate_review"]["agreement_rate"] == 1.0
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_mergeability_calibration_report_only_invariants_remain_false(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    assert (tmp_path / "corpus_manifest.json").exists()
    assert (tmp_path / "calibration_summary.json").exists()
    report_path = tmp_path / "paired_acceptance_report.json"
    assert report_path.exists()
    rows_path = tmp_path / "per_task_results.jsonl"
    assert rows_path.exists()

    exported_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exported_report["report_label"] == "calibration"
    assert exported_report["metric_applyable"] is False
    assert exported_report["improvement_claim_allowed"] is False
    assert exported_report["arms"]["oracle_ceiling"]["arm_role"] == "oracle_ceiling"
    assert exported_report["arms"]["oracle_ceiling"]["decision_source"] == "oracle_final_score"
    assert exported_report["arms"]["oracle_ceiling"]["oracle_coupled"] is True
    assert exported_report["arms"]["supervisor_candidate_review"]["arm_role"] == "supervisor_candidate_review"
    assert exported_report["arms"]["supervisor_candidate_review"]["oracle_coupled"] is False
    assert exported_report["arms"]["baseline"]["oracle_coupled"] is False
    assert exported_report["candidate_pool_sha256"] == report["candidate_pool_sha256"]

    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == report["candidate_count"]
    assert all(row["receipt"]["source"] == "supervisor" for row in rows)
    assert all(row["receipt"]["evidence_grade"] == "runtime_native" for row in rows)
    assert all(row["baseline_decision_source"] == "candidate_self_report" for row in rows)
    assert all(row["supervisor_decision_source"] == "supervisor_candidate_review" for row in rows)
    assert all(row["oracle_ceiling_decision_source"] == "oracle_final_score" for row in rows)

    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert report["recommendation"]["applyable_policy_proposal"] is False


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


def _assert_autoresearch_mergeability_evaluator_works_with_live_trials(tmp_path):
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


def test_autoresearch_mergeability_evaluator_works_with_live_trials(tmp_path):
    _assert_autoresearch_mergeability_evaluator_works_with_live_trials(tmp_path)


def test_existing_mergeability_evaluator_quality_checks_remain_green(tmp_path):
    _assert_autoresearch_mergeability_evaluator_works_with_live_trials(tmp_path)


def _assert_autoresearch_report_only_invariants_with_mergeability_evaluator(tmp_path):
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


def test_autoresearch_report_only_invariants_with_mergeability_evaluator(tmp_path):
    _assert_autoresearch_report_only_invariants_with_mergeability_evaluator(tmp_path)


def test_paired_acceptance_report_cannot_create_applyable_policy_claim(tmp_path):
    _assert_autoresearch_report_only_invariants_with_mergeability_evaluator(tmp_path)


def test_existing_autoresearch_report_only_invariants_remain_green(tmp_path):
    _assert_autoresearch_report_only_invariants_with_mergeability_evaluator(tmp_path)

    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert report["recommendation"]["applyable_policy_proposal"] is False
    assert report["metric_applyable"] is False


def _build_regression_candidate_paths(tmp_path: Path) -> tuple[Path, ...]:
    """Build a compact candidate pool with positives, negatives, and traps."""

    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir()
    sources = [
        "known_good",
        "noop",
        "known_bad",
        "missing_regression_test",
        "tautological_test",
        "hidden_edit",
        "mutable_escape",
        "partial_fix_regression",
    ]
    paths: list[Path] = []
    for source in sources:
        src = BENCH_ROOT / "candidates" / f"{source}.json"
        dst = candidate_dir / f"{source}.json"
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        paths.append(dst)
    return tuple(paths)


def _bench_with_calculator_candidates(tmp_path: Path, candidate_names: tuple[str, ...]) -> Path:
    bench_root = tmp_path / "bench"
    (bench_root / "tasks").mkdir(parents=True)
    (bench_root / "candidates").mkdir()
    shutil.copytree(
        BENCH_ROOT / "repos" / "calculator_bug",
        bench_root / "repos" / "calculator_bug",
    )
    task_src = BENCH_ROOT / "tasks" / "calculator_addition.json"
    (bench_root / "tasks" / "calculator_addition.json").write_text(
        task_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for candidate_name in candidate_names:
        src = BENCH_ROOT / "candidates" / f"{candidate_name}.json"
        (bench_root / "candidates" / f"{candidate_name}.json").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return bench_root


def test_validate_mergeability_corpus_rejects_task_without_positive_control(tmp_path):
    bench_root = _bench_with_calculator_candidates(tmp_path, ("known_bad",))

    summary = validate_mergeability_corpus(bench_root, strict=False)

    errors = " | ".join(summary["errors"])
    assert "task calculator-addition missing held-out controls" in errors
    assert "positive_control" in errors


def test_validate_mergeability_corpus_rejects_task_without_negative_control(tmp_path):
    bench_root = _bench_with_calculator_candidates(tmp_path, ("known_good",))

    summary = validate_mergeability_corpus(bench_root, strict=False)

    errors = " | ".join(summary["errors"])
    assert "task calculator-addition missing held-out controls" in errors
    assert "negative_control" in errors


def test_validate_mergeability_corpus_rejects_task_without_false_accept_trap(tmp_path):
    bench_root = _bench_with_calculator_candidates(tmp_path, ("known_good", "noop"))

    summary = validate_mergeability_corpus(bench_root, strict=False)

    errors = " | ".join(summary["errors"])
    assert "task calculator-addition missing held-out controls" in errors
    assert "public_pass_hidden_fail_trap" in errors


def test_paired_report_records_heldout_task_class_coverage(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    coverage = report["heldout_coverage"]
    assert coverage["split"] == "held_out"
    by_class = coverage["by_task_class"]
    task_classes = set(by_class)
    assert len(task_classes) >= 2, by_class
    assert "arithmetic" in task_classes
    assert "text_processing" in task_classes

    for task_class, entry in by_class.items():
        assert entry["task_class"] == task_class
        assert entry["split"] == "held_out"
        assert entry["task_count"] >= 1
        assert entry["candidate_count"] >= 2
        assert entry["positive_control_count"] >= 1
        assert entry["negative_control_count"] >= 1
        assert entry["false_accept_trap_count"] >= 1

    public_surfaces = {
        "heldout_coverage": report["heldout_coverage"],
        "supervisor_public_inputs": [
            row["supervisor_review"]["public_input_payload"]
            for row in report["per_task_results"]
        ],
        "full_gate_reviewer_packets": [
            row["supervisor_full_gate_review"]["reviewer_packet"]
            for row in report["per_task_results"]
        ],
    }
    serialized = json.dumps(public_surfaces, sort_keys=True, default=str)
    for forbidden in (
        "expected_outcome",
        "final_score",
        "oracle_accept",
        "hidden_test_commands",
        "hidden/test_behavior.py",
        ".mergeability/",
    ):
        assert forbidden not in serialized, forbidden


def test_paired_report_separates_heldout_metrics_and_confidence_intervals(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    assert report["split_policy"]["reporting_split"] == "held_out"
    assert report["split_policy"]["heldout_excluded_from_variant_selection"] is True
    metric_splits = report["metric_splits"]
    assert metric_splits["held_out"]["status"] == "reported"
    assert metric_splits["held_out"]["row_count"] == report["candidate_count"]
    assert metric_splits["dev"]["status"] == "not_reported"

    for arm_name in (
        "baseline",
        "supervisor_candidate_review",
        "supervisor_full_gate",
        "oracle_ceiling",
    ):
        arm = report["arms"][arm_name]
        assert arm["n_bad"] == arm["false_accept_denominator"]
        assert arm["n_good"] == arm["true_accept_denominator"]
        false_accept_ci = arm["false_accept_confidence_interval"]
        true_accept_ci = arm["true_accept_confidence_interval"]
        assert false_accept_ci["method"] == "wilson_score"
        assert true_accept_ci["method"] == "wilson_score"
        assert false_accept_ci["label"] == "approximate_binary_proportion_interval"
        assert false_accept_ci["denominator"] == arm["false_accept_denominator"]
        assert true_accept_ci["denominator"] == arm["true_accept_denominator"]


def test_validate_mergeability_corpus_requires_controls_per_task_class(tmp_path):
    bench_root = tmp_path / "bench"
    (bench_root / "tasks").mkdir(parents=True)
    (bench_root / "candidates").mkdir(parents=True)
    repo_root = bench_root / "repos" / "calculator_bug"
    repo_root.mkdir(parents=True)
    shutil_src = BENCH_ROOT / "repos" / "calculator_bug"
    for src in shutil_src.rglob("*"):
        target = repo_root / src.relative_to(shutil_src)
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src.read_bytes())

    base_task = json.loads(
        (BENCH_ROOT / "tasks" / "calculator_addition.json").read_text(encoding="utf-8")
    )
    base_task["task_class"] = "arithmetic"
    (bench_root / "tasks" / "calculator_addition.json").write_text(
        json.dumps(base_task, sort_keys=True), encoding="utf-8"
    )
    secondary_task = dict(base_task)
    secondary_task["task_id"] = "arith-positive-only"
    secondary_task["task_class"] = "positive_only"
    (bench_root / "tasks" / "positive_only.json").write_text(
        json.dumps(secondary_task, sort_keys=True), encoding="utf-8"
    )

    for source_name in (
        "known_good",
        "noop",
        "known_bad",
        "missing_regression_test",
        "tautological_test",
        "hidden_edit",
        "mutable_escape",
    ):
        payload = json.loads(
            (BENCH_ROOT / "candidates" / f"{source_name}.json").read_text(encoding="utf-8")
        )
        (bench_root / "candidates" / f"{source_name}.json").write_text(
            json.dumps(payload, sort_keys=True), encoding="utf-8"
        )

    second_class_good = json.loads(
        (BENCH_ROOT / "candidates" / "known_good.json").read_text(encoding="utf-8")
    )
    second_class_good["candidate_id"] = "positive-only-known-good"
    second_class_good["task_id"] = "arith-positive-only"
    (bench_root / "candidates" / "positive_only_known_good.json").write_text(
        json.dumps(second_class_good, sort_keys=True), encoding="utf-8"
    )

    summary = validate_mergeability_corpus(bench_root, strict=False)
    errors = summary["errors"]
    joined = " | ".join(errors)
    assert "task_class" in joined.lower()
    assert "positive_only" in joined
    assert any("control" in error.lower() for error in errors)
    by_class = summary["control_coverage_by_task_class"]
    assert "positive_only" in by_class
    positive_only_entry = by_class["positive_only"]
    assert positive_only_entry["missing_control_kinds"]


def test_paired_report_catches_no_regression_failure(tmp_path):
    candidate_paths = _build_regression_candidate_paths(tmp_path)

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        candidate_paths=candidate_paths,
        output_dir=tmp_path / "out",
        reviewer_panel=_deny_panel,
    )

    findings = report["no_regression_findings"]
    assert findings, "expected at least one no-regression failure to be surfaced"
    regression_ids = {entry["candidate_id"] for entry in findings}
    assert "known-good" in regression_ids
    assert "partial-fix-regression" not in regression_ids

    regression_row = next(
        row
        for row in report["per_task_results"]
        if row["candidate_id"] == "known-good"
    )
    assert regression_row["is_no_regression_failure"] is True
    assert regression_row["baseline_accept"] is True
    assert regression_row["oracle_accept"] is True

    prior_false_accept_row = next(
        row
        for row in report["per_task_results"]
        if row["candidate_id"] == "partial-fix-regression"
    )
    assert prior_false_accept_row["baseline_accept"] is True
    assert prior_false_accept_row["oracle_accept"] is False
    assert prior_false_accept_row["is_no_regression_failure"] is False

    flags = set(report["gaming_flags"])
    assert "no_regression_failure_detected" in flags


def test_no_regression_blocks_prior_true_positive_rejects_not_false_accepts(tmp_path):
    candidate_paths = _build_regression_candidate_paths(tmp_path)

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        candidate_paths=candidate_paths,
        output_dir=tmp_path / "out",
        reviewer_panel=_deny_panel,
    )

    findings_by_id = {
        finding["candidate_id"]: finding
        for finding in report["no_regression_findings"]
    }
    assert findings_by_id["known-good"]["reason"] == (
        "prior_true_positive_accept_rejected_by_supervisor_full_gate"
    )
    assert findings_by_id["known-good"]["protected_scope"] == (
        "baseline_accepted_oracle_positive_cases"
    )
    assert "partial-fix-regression" not in findings_by_id


def test_heldout_no_regression_report_remains_non_applyable(tmp_path):
    candidate_paths = _build_regression_candidate_paths(tmp_path)

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        candidate_paths=candidate_paths,
        output_dir=tmp_path / "out",
        reviewer_panel=_deny_panel,
    )

    assert report["no_regression_findings"], "fixture must exercise the no-regression path"
    assert report["heldout_coverage"]["split"] == "held_out"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert report["recommendation"]["applyable_policy_proposal"] is False

    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    ) == []
    assert report_contains_derivable_policy_record(report, repo_root=tmp_path) is False


def test_best_of_k_peak_cannot_be_labeled_heldout_improvement(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    guard = report["heldout_reporting"]["best_of_k_in_sample"]
    assert guard["present"] is False
    assert guard["label_allowed_as_heldout_improvement"] is False
    assert report["heldout_reporting"]["heldout_improvement_claim_allowed"] is False
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_no_regression_and_heldout_artifacts_export_replayable_hashes(tmp_path):
    candidate_paths = _build_regression_candidate_paths(tmp_path)
    output_dir = tmp_path / "out"

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        candidate_paths=candidate_paths,
        output_dir=output_dir,
    )

    expected_coverage_hash = _sha256_json(report["heldout_coverage"])
    expected_no_regression_hash = _sha256_json(report["no_regression_findings"])
    assert report["heldout_coverage_sha256"] == expected_coverage_hash
    assert report["no_regression_sha256"] == expected_no_regression_hash

    report_path = output_dir / "paired_acceptance_report.json"
    assert report_path.exists()
    exported_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exported_report["heldout_coverage_sha256"] == expected_coverage_hash
    assert exported_report["no_regression_sha256"] == expected_no_regression_hash
    assert exported_report["heldout_coverage"] == report["heldout_coverage"]
    assert exported_report["no_regression_findings"] == report["no_regression_findings"]
    assert exported_report["report_sha256"] == report["report_sha256"]
    assert report["improvement_claim_allowed"] is False
