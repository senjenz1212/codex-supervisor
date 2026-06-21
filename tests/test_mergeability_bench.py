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
from supervisor import mergeability_bench as mergeability_bench_module
from supervisor.cursor_agent import CursorInvocationRequest, CursorInvocationResult
from supervisor.dual_agent import Outcome, ProbeResult, SpecialistRecord
from supervisor.mergeability_bench import (
    MERGEABILITY_TASK_SCHEMA_VERSION,
    ConfiguredReviewerPanelOptions,
    MergeabilityBenchError,
    MergeabilityCandidate,
    build_configured_reviewer_panel,
    build_mergeability_corpus_manifest,
    grade_mergeability_candidate,
    load_mergeability_candidate,
    load_mergeability_task,
    load_mergeability_tasks,
    result_receipt,
    run_live_mergeability_candidate_generation,
    run_paired_acceptance_pilot,
    run_powered_factorial_mergeability_evaluation,
    validate_mergeability_corpus,
)
from supervisor.reviewer_registry import ReviewerSpec


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


def _factorial_arm_decisions(*, unmatched_tar: bool = False) -> dict[str, dict[str, object]]:
    positives = {"known-good", "secondary-rubric-only", "text-known-good"}
    public_traps = {"hidden-behavior-miss", "text-hidden-behavior-miss"}
    candidate_ids = {
        path.stem: load_mergeability_candidate(path).candidate_id
        for path in BENCH_ROOT.glob("candidates/*.json")
    }

    def accepts(*extra: str) -> dict[str, bool]:
        accepted = positives | set(extra)
        return {
            candidate_id: candidate_id in accepted
            for candidate_id in candidate_ids.values()
        }

    same_model = accepts("hidden-behavior-miss", "text-hidden-behavior-miss")
    if unmatched_tar:
        same_model["known-good"] = False
    return {
        "single_agent_baseline": _produced_baseline_decisions(
            accept_overrides={candidate_id: True for candidate_id in candidate_ids.values()},
        ),
        "same_model_multi_agent": same_model,
        "hetero_multi_reviewer": accepts("hidden-behavior-miss"),
        "runtime_evidence_floor": accepts("hidden-behavior-miss", "text-hidden-behavior-miss"),
        "full_supervisor_stack": accepts(),
    }


def _factorial_reviewer_results() -> dict[str, list[dict]]:
    decisions = _factorial_arm_decisions()["full_supervisor_stack"]
    results: dict[str, list[dict]] = {}
    for candidate_id, accept in decisions.items():
        results[candidate_id] = [
            {"reviewer_id": "claude-reviewer", "decision": "accept" if accept else "deny"},
            {"reviewer_id": "cursor-reviewer", "decision": "accept" if accept else "deny"},
            {
                "reviewer_id": "codex-reviewer",
                "decision": (
                    "accept"
                    if accept or candidate_id == "hidden-behavior-miss"
                    else "deny"
                ),
            },
        ]
    return results


class _FakeGenerator:
    def __init__(self, candidate_name: str, *, cost_usd: float = 0.0, wall_clock_s: float = 0.01):
        self.candidate_name = candidate_name
        self.cost_usd = cost_usd
        self.wall_clock_s = wall_clock_s
        self.calls: list[dict] = []

    def __call__(self, generator_input):
        self.calls.append(generator_input)
        candidate = json.loads((BENCH_ROOT / "candidates" / f"{self.candidate_name}.json").read_text(encoding="utf-8"))
        return {
            "candidate": candidate,
            "cost_usd": self.cost_usd,
            "wall_clock_s": self.wall_clock_s,
            "token_usage": {"input_tokens": 11, "output_tokens": 7},
        }


class _PublicWorktreeReadingGenerator(_FakeGenerator):
    def __call__(self, generator_input):
        public_ref = Path(generator_input["public_worktree_ref"])
        assert public_ref.is_dir()
        assert "return left - right" in (public_ref / "app/calculator.py").read_text(encoding="utf-8")
        assert not (public_ref / "hidden/test_behavior.py").exists()
        assert not (public_ref / ".mergeability").exists()
        return super().__call__(generator_input)


class _FailIfCalled:
    calls = []

    def __call__(self, _generator_input):  # pragma: no cover - failure path
        raise AssertionError("generator must not be invoked")


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


def test_paired_pilot_missing_single_agent_baseline_decisions_unavailable(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    metadata = report["arms"]["metadata_accept_all_baseline"]
    legacy = report["arms"]["baseline"]
    produced = report["arms"]["single_agent_baseline"]
    assert legacy["legacy_alias_of"] == "metadata_accept_all_baseline"
    assert metadata["evidence_kind"] == "metadata_calibration"
    assert produced["arm_role"] == "single_agent_baseline"
    assert produced["decision_source"] == "produced_single_agent_baseline_unavailable"
    assert produced["evidence_kind"] == "missing"
    assert produced["availability_status"] == "unavailable"
    assert produced["unavailable_count"] == report["candidate_count"]
    assert produced["accepted_count"] == 0
    assert produced["rejected_count"] == 0
    assert "baseline_evidence_unavailable" in report["gaming_flags"]

    comparison = report["single_agent_baseline_false_accept_at_matched_true_accept"]
    assert comparison["status"] == "unavailable"
    assert comparison["reason"] == "baseline_arm_unavailable"

    for row in report["per_task_results"]:
        assert row["metadata_accept_all_baseline_accept"] is row["baseline_accept"]
        assert row["metadata_accept_all_baseline_evidence_kind"] == "metadata_calibration"
        assert row["single_agent_baseline_unavailable"] is True
        assert row["single_agent_baseline_accept"] is False
        assert row["single_agent_baseline_unavailable_reason"] == "baseline_decisions_not_supplied"
        assert row["single_agent_baseline_evidence_kind"] == "missing"


def test_paired_pilot_replayed_single_agent_baseline_decisions_populate_separate_far_tar(tmp_path):
    baseline_decisions = _produced_baseline_decisions()
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        single_agent_baseline_decisions=baseline_decisions,
    )

    metadata = report["arms"]["metadata_accept_all_baseline"]
    produced = report["arms"]["single_agent_baseline"]
    assert metadata["evidence_kind"] == "metadata_calibration"
    assert produced["availability_status"] == "available"
    assert produced["unavailable_count"] == 0
    assert produced["decision_source"] == "produced_single_agent_baseline"
    assert produced["evidence_kind"] == "produced_single_agent_baseline"
    assert produced["false_accept_rate"] != metadata["false_accept_rate"]
    assert "baseline_evidence_unavailable" not in report["gaming_flags"]

    by_candidate = {row["candidate_id"]: row for row in report["per_task_results"]}
    for candidate_id, decision in baseline_decisions.items():
        row = by_candidate[candidate_id]
        assert row["single_agent_baseline_candidate_id"] == candidate_id
        assert row["single_agent_baseline_accept"] is bool(decision["accept"])
        assert row["single_agent_baseline_unavailable"] is False
        assert row["single_agent_baseline_decision_source"] == "produced_single_agent_baseline"
        assert (
            row["single_agent_baseline_candidate_artifact_hash"]
            == decision["candidate_artifact_hash"]
        )
        assert row["single_agent_baseline_candidate_artifact_hash"] == row["candidate_hash"]
        assert row["single_agent_baseline_prompt_sha256"] == decision["prompt_sha256"]
        assert row["single_agent_baseline_producer"]["model"] == "fixture-baseline-llm"
        assert row["single_agent_baseline_producer"]["runner_label"] == "single-agent-baseline-replay"
        assert row["single_agent_baseline_unavailable_reason"] == ""

    exported_report = json.loads((tmp_path / "paired_acceptance_report.json").read_text(encoding="utf-8"))
    assert exported_report["arms"]["single_agent_baseline"]["evidence_kind"] == (
        "produced_single_agent_baseline"
    )


def test_paired_pilot_matched_tar_refuses_unavailable_single_agent_baseline(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    produced = report["arms"]["single_agent_baseline"]
    assert produced["availability_status"] == "unavailable"
    assert report["single_agent_baseline_false_accept_at_matched_true_accept"] == {
        "status": "unavailable",
        "reason": "baseline_arm_unavailable",
        "unavailable_count": report["candidate_count"],
    }


def test_paired_pilot_malformed_single_agent_baseline_receipt_is_unavailable(tmp_path):
    baseline_decisions = _produced_baseline_decisions()
    baseline_decisions["known-good"] = dict(baseline_decisions["known-good"])
    baseline_decisions["known-good"].pop("candidate_id")

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        single_agent_baseline_decisions=baseline_decisions,
    )

    row = {
        item["candidate_id"]: item
        for item in report["per_task_results"]
    }["known-good"]
    assert row["single_agent_baseline_accept"] is False
    assert row["single_agent_baseline_unavailable"] is True
    assert row["single_agent_baseline_evidence_kind"] == "malformed"
    assert row["single_agent_baseline_unavailable_reason"] == (
        "malformed_baseline_row_missing_replay_evidence:candidate_id"
    )
    assert "baseline_evidence_unavailable" in report["gaming_flags"]


def test_paired_pilot_self_report_baseline_receipt_is_not_produced_evidence(tmp_path):
    baseline_decisions = _produced_baseline_decisions()
    baseline_decisions["known-good"] = dict(baseline_decisions["known-good"])
    baseline_decisions["known-good"]["decision_source"] = "candidate_self_report"

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        single_agent_baseline_decisions=baseline_decisions,
    )

    row = {
        item["candidate_id"]: item
        for item in report["per_task_results"]
    }["known-good"]
    assert row["single_agent_baseline_accept"] is False
    assert row["single_agent_baseline_unavailable"] is True
    assert row["single_agent_baseline_evidence_kind"] == "malformed"
    assert row["single_agent_baseline_unavailable_reason"] == (
        "malformed_baseline_row_untrusted_decision_source:candidate_self_report"
    )
    assert "baseline_evidence_unavailable" in report["gaming_flags"]


def test_paired_pilot_baseline_only_calibration_creates_no_policy_proposal(tmp_path):
    baseline_decisions = _produced_baseline_decisions()
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        single_agent_baseline_decisions=baseline_decisions,
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


def _fake_outcome(decision: str) -> Outcome:
    severity = "none" if decision == "accept" else "important"
    return Outcome(
        task_id="mergeability-full-gate",
        summary=f"fake reviewer outcome {decision}",
        specialists=[
            SpecialistRecord(name="Independent Reviewer", decision=decision, objection=None)
        ],
        decisions=[decision],
        objections=[],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.9,
        confidence_rationale="fixture",
        confidence_criteria=[],
        claims=[],
        critical_review={"severity": severity},
    )


def _fake_cursor_result(decision: str, *, model: str = "fake-model") -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult("INDEPENDENT_REVIEWER", "green", "fake_ok", {}),
        outcome=_fake_outcome(decision),
        transcript=f"fake reviewer transcript {decision}",
        model=model,
        reviewer_runtime="fake_runtime",
        reviewer_output_mode="cursor_sdk",
        reviewer_assurance="self_reported",
    )


def _unavailable_cursor_result(spec: ReviewerSpec, *, reason: str) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult("INDEPENDENT_REVIEWER", "red", reason, {}),
        outcome=None,
        transcript="",
        model=spec.model,
        reviewer_runtime=spec.runtime,
        reviewer_output_mode="cursor_sdk",
        reviewer_assurance=spec.assurance_grade,
        failure_classification="reviewer_infrastructure",
    )


class _RecordingFakeReviewer:
    def __init__(self, reviewer_id: str, *, result: CursorInvocationResult):
        self.spec = ReviewerSpec(
            reviewer_id=reviewer_id,
            runtime="fake_runtime",
            model="fake-model",
            provider_family="fake",
            lineage=("fake", reviewer_id),
            tool_access="none",
            assurance_grade="self_reported",
        )
        self._result = result
        self.calls: list[CursorInvocationRequest] = []

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        self.calls.append(request)
        return self._result


def _accepting_fake_reviewers() -> tuple[_RecordingFakeReviewer, _RecordingFakeReviewer]:
    return (
        _RecordingFakeReviewer("fake-reviewer-a", result=_fake_cursor_result("accept")),
        _RecordingFakeReviewer("fake-reviewer-b", result=_fake_cursor_result("accept")),
    )


def test_run_paired_acceptance_pilot_uses_configured_panel_when_requested(tmp_path):
    reviewer_a, reviewer_b = _accepting_fake_reviewers()

    report_baseline = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path / "baseline")
    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path / "configured",
        reviewer_panel_mode="configured",
        configured_reviewer_panel_options=ConfiguredReviewerPanelOptions(
            reviewers=(reviewer_a, reviewer_b),
        ),
    )

    assert report["configured_reviewer_panel"]["mode"] == "configured"
    full_gate = report["arms"]["supervisor_full_gate"]
    assert full_gate["availability_status"] == "available"
    assert full_gate["unavailable_count"] == 0

    # The configured roster was actually exercised.
    assert reviewer_a.calls, "reviewer_a should have been invoked"
    assert reviewer_b.calls, "reviewer_b should have been invoked"
    assert all(isinstance(call, CursorInvocationRequest) for call in reviewer_a.calls)

    # Reviewer results were converted via the registry seam and recorded per row.
    for row in report["per_task_results"]:
        review = row["supervisor_full_gate_review"]
        reviewer_results = review["reviewer_results"]
        assert {item["reviewer_id"] for item in reviewer_results} == {
            "fake-reviewer-a",
            "fake-reviewer-b",
        }
        assert all(item["verdict_present"] for item in reviewer_results)
        assert review["panel_decision"] == "accept"
        assert review["panel_result"]["decision"] == "accept"
        assert review["panel_result"]["panel_decision"]["decision"] == "accept"

    # S_probe arm (supervisor_candidate_review) is unchanged across the two runs.
    baseline_probe = report_baseline["arms"]["supervisor_candidate_review"]
    configured_probe = report["arms"]["supervisor_candidate_review"]
    for key in (
        "accepted_count",
        "rejected_count",
        "false_accept_count",
        "true_accept_count",
        "false_accept_rate",
        "true_accept_rate",
    ):
        assert baseline_probe[key] == configured_probe[key], key


def test_configured_panel_unavailable_does_not_count_as_accept(tmp_path):
    unavailable_a = _RecordingFakeReviewer(
        "fake-reviewer-a",
        result=_unavailable_cursor_result(
            ReviewerSpec(reviewer_id="fake-reviewer-a", runtime="fake_runtime"),
            reason="reviewer_infrastructure_down",
        ),
    )
    unavailable_b = _RecordingFakeReviewer(
        "fake-reviewer-b",
        result=_unavailable_cursor_result(
            ReviewerSpec(reviewer_id="fake-reviewer-b", runtime="fake_runtime"),
            reason="reviewer_infrastructure_down",
        ),
    )

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel_mode="configured",
        configured_reviewer_panel_options=ConfiguredReviewerPanelOptions(
            reviewers=(unavailable_a, unavailable_b),
        ),
    )

    full_gate = report["arms"]["supervisor_full_gate"]
    assert full_gate["availability_status"] == "unavailable"
    assert full_gate["accepted_count"] == 0
    assert full_gate["unavailable_count"] == report["candidate_count"]
    assert "reviewer_panel_unavailable" in report["gaming_flags"]
    for row in report["per_task_results"]:
        review = row["supervisor_full_gate_review"]
        assert row["supervisor_full_gate_unavailable"] is True
        assert row["supervisor_full_gate_accept"] is False
        assert review["unavailable_reason"] == "reviewer_panel_unavailable"
        assert review["panel_result"]["available"] is False


def test_configured_panel_not_invoked_when_reviewer_packet_contains_oracle_material(
    tmp_path, monkeypatch
):
    reviewer_a, reviewer_b = _accepting_fake_reviewers()

    original_builder = mergeability_bench_module._build_full_gate_reviewer_packet

    def _leaky_builder(**kwargs):
        packet = original_builder(**kwargs)
        # Inject a forbidden oracle marker so the leak detector fires before the panel runs.
        packet["hidden_test_commands"] = ["python -m pytest hidden/test_behavior.py"]
        return packet

    monkeypatch.setattr(
        mergeability_bench_module,
        "_build_full_gate_reviewer_packet",
        _leaky_builder,
    )

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel_mode="configured",
        configured_reviewer_panel_options=ConfiguredReviewerPanelOptions(
            reviewers=(reviewer_a, reviewer_b),
        ),
    )

    # Reviewer adapters MUST NOT be called when the packet leaks oracle material.
    assert reviewer_a.calls == [], "reviewer_a was invoked despite oracle leak"
    assert reviewer_b.calls == [], "reviewer_b was invoked despite oracle leak"

    full_gate = report["arms"]["supervisor_full_gate"]
    assert full_gate["availability_status"] == "unavailable"
    assert full_gate["accepted_count"] == 0
    assert "oracle_isolation_violation" in report["gaming_flags"]
    for row in report["per_task_results"]:
        review = row["supervisor_full_gate_review"]
        assert review["unavailable_reason"] == "oracle_isolation_violation"
        assert row["supervisor_full_gate_accept"] is False
        assert "oracle_isolation_violation" in review["gaming_flags"]


def test_configured_panel_report_records_reviewer_results_and_packet_refs(tmp_path):
    reviewer_a = _RecordingFakeReviewer(
        "fake-reviewer-a", result=_fake_cursor_result("accept")
    )
    reviewer_b = _RecordingFakeReviewer(
        "fake-reviewer-b", result=_fake_cursor_result("deny")
    )

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel_mode="configured",
        configured_reviewer_panel_options=ConfiguredReviewerPanelOptions(
            reviewers=(reviewer_a, reviewer_b),
        ),
    )

    assert report["configured_reviewer_panel"]["mode"] == "configured"
    assert report["configured_reviewer_panel"]["available_full_gate_count"] >= 1

    for row in report["per_task_results"]:
        # Row-level fields exposing replayable evidence.
        assert row["supervisor_full_gate_reviewer_packet_refs"], row
        assert row["supervisor_full_gate_reviewer_packet_refs"][0]["source"] == "supervisor"
        assert row["supervisor_full_gate_reviewer_packet_sha256"]
        results = row["supervisor_full_gate_reviewer_results"]
        reviewer_ids = {item["reviewer_id"] for item in results}
        assert reviewer_ids == {"fake-reviewer-a", "fake-reviewer-b"}
        decision = row["supervisor_full_gate_reviewer_panel_decision"]
        assert decision is not None
        # Conservative aggregation: deny (or revise) wins when any reviewer non-accepts.
        assert decision["decision"] in {"revise", "deny"}
        review = row["supervisor_full_gate_review"]
        assert review["panel_decision"] in {"revise", "deny"}
        assert review["available_reviewers"] == ["fake-reviewer-a", "fake-reviewer-b"]
        # No row should be silently collapsed into the S_probe arm output:
        if row["supervisor_candidate_review_accept"]:
            assert row["supervisor_full_gate_accept"] is False
            assert row["s_probe_vs_s_full_disagreement"] is True

    # S_probe vs S_full disagreement is recorded at report scope.
    assert (
        report["configured_reviewer_panel"]["s_probe_vs_s_full_disagreement_count"] >= 1
    )


def test_configured_panel_calibration_remains_report_only(tmp_path):
    reviewer_a, reviewer_b = _accepting_fake_reviewers()

    report = run_paired_acceptance_pilot(
        BENCH_ROOT,
        output_dir=tmp_path,
        reviewer_panel_mode="configured",
        configured_reviewer_panel_options=ConfiguredReviewerPanelOptions(
            reviewers=(reviewer_a, reviewer_b),
        ),
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


def test_live_generation_requires_allow_live_before_generators_run(tmp_path):
    baseline = _FailIfCalled()
    supervisor = _FailIfCalled()

    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=baseline,
        supervisor_generator=supervisor,
        allow_live=False,
        model="claude-sonnet",
        provider="anthropic",
        budget_usd=1.0,
        timeout_s=30.0,
        output_dir=tmp_path,
    )

    assert report["status"] == "unavailable"
    assert report["unavailable_reason"] == "live_generation_disabled"
    assert report["arms"]["baseline"]["status"] == "not_invoked"
    assert report["arms"]["supervisor"]["status"] == "not_invoked"
    assert report["candidate_artifacts"] == {}
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False


def test_live_generation_requires_budget_matched_arms(tmp_path):
    baseline = _FailIfCalled()
    supervisor = _FailIfCalled()

    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=baseline,
        supervisor_generator=supervisor,
        allow_live=True,
        baseline_config={
            "model": "claude-sonnet",
            "provider": "anthropic",
            "budget_usd": 1.0,
            "timeout_s": 30.0,
        },
        supervisor_config={
            "model": "claude-opus",
            "provider": "anthropic",
            "budget_usd": 2.0,
            "timeout_s": 30.0,
        },
        output_dir=tmp_path,
    )

    assert report["status"] == "unavailable"
    assert report["unavailable_reason"] == "arm_config_mismatch"
    assert sorted(report["config_mismatches"]) == ["budget_usd", "model"]
    assert report["arms"]["baseline"]["status"] == "not_invoked"
    assert report["arms"]["supervisor"]["status"] == "not_invoked"


def test_live_generation_excludes_hidden_oracle_material_from_generator_inputs(tmp_path):
    baseline = _PublicWorktreeReadingGenerator("known_good")
    supervisor = _PublicWorktreeReadingGenerator("known_bad")

    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=baseline,
        supervisor_generator=supervisor,
        allow_live=True,
        model="claude-sonnet",
        provider="anthropic",
        budget_usd=1.0,
        timeout_s=30.0,
        output_dir=tmp_path,
    )

    assert report["status"] == "completed"
    for generator in (baseline, supervisor):
        assert len(generator.calls) == 1
        encoded = json.dumps(generator.calls[0], sort_keys=True)
        assert "hidden/test_behavior.py" not in encoded
        assert ".mergeability/" not in encoded
        assert '"hidden_test_commands"' not in encoded
        assert '"expected_outcome"' not in encoded
        assert '"final_score"' not in encoded
        assert '"oracle_accept"' not in encoded
        assert generator.calls[0]["public_worktree_ref"]
        assert generator.calls[0]["public_worktree_hash"]
        assert generator.calls[0]["visible_commands"]["reverse_test_commands"]


def test_live_generation_records_stable_candidate_artifact_hashes(tmp_path):
    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=_FakeGenerator("known_good"),
        supervisor_generator=_FakeGenerator("known_good"),
        allow_live=True,
        model="claude-sonnet",
        provider="anthropic",
        budget_usd=1.0,
        timeout_s=30.0,
        output_dir=tmp_path,
    )

    baseline = report["arms"]["baseline"]
    supervisor = report["arms"]["supervisor"]
    assert baseline["candidate_artifact_hash"]
    assert supervisor["candidate_artifact_hash"]
    assert baseline["candidate_artifact_hash"] == baseline["candidate_artifact_hash_recomputed"]
    assert supervisor["candidate_artifact_hash"] == supervisor["candidate_artifact_hash_recomputed"]
    assert baseline["prompt_hash"] == supervisor["prompt_hash"]
    assert baseline["evaluator_hash"] == supervisor["evaluator_hash"]
    assert baseline["wall_clock_s"] >= 0.0
    assert baseline["cost_usd"] == 0.0
    assert baseline["token_usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert report["candidate_artifacts"]["baseline"]["candidate_artifact_hash"] == baseline["candidate_artifact_hash"]
    assert (tmp_path / "live_candidate_generation_report.json").exists()


def test_live_generation_evaluates_both_arms_with_same_heldout_oracle(tmp_path):
    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=_FakeGenerator("known_good"),
        supervisor_generator=_FakeGenerator("known_bad"),
        allow_live=True,
        model="claude-sonnet",
        provider="anthropic",
        budget_usd=1.0,
        timeout_s=30.0,
        output_dir=tmp_path,
    )

    baseline = report["arms"]["baseline"]
    supervisor = report["arms"]["supervisor"]
    assert baseline["evaluator_hash"] == supervisor["evaluator_hash"]
    assert baseline["oracle_result"]["final_score"] == 1.0
    assert baseline["accepted"] is True
    assert supervisor["oracle_result"]["final_score"] == 0.0
    assert supervisor["accepted"] is False
    assert report["heldout_oracle"]["decision_source"] == "grade_mergeability_candidate"
    assert report["metric_applyable"] is False


def test_candidate_affects_evaluated_path_false_for_non_evaluated_change(tmp_path):
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
        k_trials=1,
        timeout_s=20.0,
        execution_mode="live",
    )
    attempt = AutoresearchAttempt(
        attempt_id="mergeability-doc-only",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="worker-mergeability",
        hypothesis="Documentation-only candidate should not count as evaluated behavior.",
        changed_files=("docs/readme.md",),
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

    assert execution.evaluator_quality["candidate_affects_evaluated_path"] is False
    assert execution.evaluator_quality["evaluated_path_derivation"]["reason"] == "no_evaluated_path_delta"


def test_candidate_affects_evaluated_path_true_for_evaluated_delta(tmp_path):
    execution_dir = tmp_path / "execution"
    _assert_autoresearch_mergeability_evaluator_works_with_live_trials(execution_dir)
    manifest = json.loads(
        (execution_dir / "evaluator-quality" / "mergeability-attempt" / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["candidate_affects_evaluated_path"] is True
    assert manifest["evaluated_path_derivation"]["reason"] == "changed_evaluated_path"
    assert "app/calculator.py" in manifest["evaluated_path_derivation"]["matching_changed_files"]


def test_candidate_affects_evaluated_path_true_for_metric_delta_without_path_match(tmp_path):
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
        k_trials=1,
        timeout_s=20.0,
        execution_mode="live",
    )
    attempt = AutoresearchAttempt(
        attempt_id="mergeability-metric-delta-only",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="worker-mergeability",
        hypothesis="Candidate-level metric deltas should count even when changed paths are coarse.",
        changed_files=("docs/readme.md",),
        metric_trials=(),
        metric_before=0.0,
        metric_after=1.0,
        metric_delta=1.0,
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

    assert execution.evaluator_quality["candidate_affects_evaluated_path"] is True
    derivation = execution.evaluator_quality["evaluated_path_derivation"]
    assert derivation["reason"] == "candidate_metric_delta"
    assert derivation["candidate_metric_delta"] == 1.0
    assert derivation["matching_changed_files"] == []


def test_live_generation_budget_overrun_is_unavailable_not_accepted(tmp_path):
    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=_FakeGenerator("known_good", cost_usd=1.5),
        supervisor_generator=_FakeGenerator("known_good"),
        allow_live=True,
        model="claude-sonnet",
        provider="anthropic",
        budget_usd=1.0,
        timeout_s=30.0,
        output_dir=tmp_path,
    )

    assert report["status"] == "unavailable"
    assert "budget_exceeded" in report["gaming_flags"]
    assert report["arms"]["baseline"]["status"] == "unavailable"
    assert report["arms"]["baseline"]["accepted"] is False
    assert report["arms"]["baseline"]["unavailable_reason"] == "budget_exceeded"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_live_generation_report_cannot_create_policy_proposal(tmp_path):
    report = run_live_mergeability_candidate_generation(
        BENCH_ROOT,
        task_id="calculator-addition",
        baseline_generator=_FakeGenerator("known_good"),
        supervisor_generator=_FakeGenerator("known_good"),
        allow_live=True,
        model="claude-sonnet",
        provider="anthropic",
        budget_usd=1.0,
        timeout_s=30.0,
        output_dir=tmp_path,
    )

    assert report["report_label"] == "live_generation_calibration"
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


def test_powered_factorial_report_includes_all_labeled_arms(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    assert report["schema_version"] == "supervisor-mergeability-powered-factorial-report/v1"
    assert set(report["arms"]) == {
        "single_agent_baseline",
        "same_model_multi_agent",
        "hetero_multi_reviewer",
        "runtime_evidence_floor",
        "full_supervisor_stack",
        "oracle_ceiling",
    }
    assert report["arms"]["full_supervisor_stack"]["arm_role"] == "full_supervisor_stack"
    assert report["arms"]["oracle_ceiling"]["oracle_coupled"] is True
    assert report["report_label"] == "powered_factorial_evaluation"


def test_powered_factorial_uses_same_candidate_pool_across_arms(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
    )

    pool_hashes = report["candidate_pool_by_arm_sha256"]
    assert len(set(pool_hashes.values())) == 1
    assert report["same_candidate_pool"] is True

    broken = _factorial_arm_decisions()
    broken["same_model_multi_agent"].pop("known-good")
    try:
        run_powered_factorial_mergeability_evaluation(
            BENCH_ROOT,
            output_dir=tmp_path / "broken",
            arm_decisions=broken,
            reviewer_panel_results=_factorial_reviewer_results(),
        )
    except MergeabilityBenchError as exc:
        assert "candidate pool mismatch" in str(exc)
    else:  # pragma: no cover - defensive readability
        raise AssertionError("factorial arms must share one candidate pool")


def test_matched_tar_refuses_unmatched_comparisons(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(unmatched_tar=True),
        reviewer_panel_results=_factorial_reviewer_results(),
    )

    comparison = report["matched_true_accept"]["same_model_multi_agent"]
    assert comparison["status"] == "not_matched"
    assert comparison["baseline_true_accept_rate"] != comparison["supervisor_true_accept_rate"]


def test_powered_factorial_records_far_tar_frr_confidence_and_discordance(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
    )

    arm = report["arms"]["full_supervisor_stack"]
    assert "false_accept_rate" in arm
    assert "true_accept_rate" in arm
    assert "false_reject_rate" in arm
    assert arm["false_accept_confidence_interval"]["method"] == "wilson_score"
    assert arm["true_accept_confidence_interval"]["method"] == "wilson_score"
    discordance = report["paired_discordant_counts"]["full_supervisor_stack"]
    assert discordance["candidate_count"] == report["candidate_count"]
    assert discordance["left_accept_right_reject"] >= 1


def test_oracle_ceiling_cannot_be_reported_as_supervisor_improvement(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    oracle = report["arms"]["oracle_ceiling"]
    assert oracle["oracle_coupled"] is True
    assert oracle["improvement_claim_allowed"] is False
    assert report["promotion_guardrails"]["oracle_ceiling_supervisor_claim_allowed"] is False


def test_leave_one_reviewer_out_records_marginal_effects_and_correlation(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
    )

    analysis = report["leave_one_reviewer_out"]
    assert analysis["status"] == "computed"
    assert {entry["reviewer_id"] for entry in analysis["reviewer_effects"]} == {
        "claude-reviewer",
        "cursor-reviewer",
        "codex-reviewer",
    }
    assert analysis["reviewer_correlation"]["pairwise_agreement"]


def test_reviewer_unavailable_blocks_full_stack_claim(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions={
            key: value
            for key, value in _factorial_arm_decisions().items()
            if key != "full_supervisor_stack"
        },
        reviewer_panel_results=None,
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    assert report["arms"]["full_supervisor_stack"]["availability_status"] == "unavailable"
    assert report["matched_true_accept"]["full_supervisor_stack"]["status"] == "unavailable"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["recommendation"]["applyable_policy_proposal"] is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path.cwd(),
        affected_gates=("execution", "outcome_review"),
    ) == []


def test_gaming_flagged_factorial_run_creates_no_applyable_proposal(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
        gaming_flags=("synthetic_gaming_detected",),
    )

    assert "synthetic_gaming_detected" in report["gaming_flags"]
    assert report["metric_applyable"] is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path.cwd(),
        affected_gates=("execution", "outcome_review"),
    ) == []


def test_powered_threshold_unmet_keeps_metric_non_applyable(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 999, "min_good": 999},
    )

    assert report["sample_size_sufficiency"]["status"] == "underpowered"
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_powered_threshold_met_may_allow_metric_but_never_mutates_policy(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    assert report["sample_size_sufficiency"]["status"] == "sufficient"
    assert report["metric_applyable"] is True
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert report["recommendation"]["applyable_policy_proposal"] is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path.cwd(),
        affected_gates=("execution", "outcome_review"),
    ) == []


def test_powered_factorial_exports_replayable_artifacts_and_trend_row(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    report_path = tmp_path / "powered_factorial_report.json"
    rows_path = tmp_path / "powered_factorial_per_task_results.jsonl"
    trends_path = tmp_path / "powered_factorial_trend_rows.json"
    assert report_path.exists()
    assert rows_path.exists()
    assert trends_path.exists()
    exported_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exported_report["report_sha256"] == report["report_sha256"]
    trend_rows = json.loads(trends_path.read_text(encoding="utf-8"))
    assert trend_rows == report["trend_rows"]
    assert any(row["gate"] == "powered_factorial_eval" for row in trend_rows)


def _produced_baseline_decisions(
    *,
    accept_overrides: dict[str, bool] | None = None,
    hash_overrides: dict[str, str] | None = None,
    candidate_filter: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    """Build a replayable produced-baseline arm input for mergeability tests."""
    manifest = build_mergeability_corpus_manifest(BENCH_ROOT)
    candidate_hashes = {
        entry["candidate_id"]: entry["candidate_hash"]
        for entry in manifest["candidates"]
    }
    positives = {"known-good", "secondary-rubric-only", "text-known-good"}
    rows: dict[str, dict[str, object]] = {}
    for candidate_id, real_hash in candidate_hashes.items():
        if candidate_filter is not None and candidate_id not in candidate_filter:
            continue
        accept = (accept_overrides or {}).get(candidate_id, candidate_id in positives)
        artifact_hash = (hash_overrides or {}).get(candidate_id, real_hash)
        rows[candidate_id] = {
            "candidate_id": candidate_id,
            "accept": accept,
            "candidate_artifact_hash": artifact_hash,
            "decision_source": "produced_single_agent_baseline",
            "producer": {
                "agent": "single-agent-baseline-replay",
                "runner_label": "single-agent-baseline-replay",
                "model": "fixture-baseline-llm",
                "provider": "fixture",
                "budget_usd": 0.0,
            },
            "prompt_sha256": sha256(f"baseline:{candidate_id}".encode("utf-8")).hexdigest(),
        }
    return rows


def _factorial_arm_decisions_without_baseline() -> dict[str, dict[str, bool]]:
    decisions = _factorial_arm_decisions()
    decisions.pop("single_agent_baseline", None)
    return decisions


def test_powered_factorial_requires_explicit_baseline_decisions(tmp_path):
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=_factorial_arm_decisions_without_baseline(),
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    baseline = report["arms"]["single_agent_baseline"]
    assert baseline["accepted_count"] == 0
    assert baseline["unavailable_count"] == report["candidate_count"]
    assert baseline["availability_status"] == "unavailable"
    assert "baseline_evidence_unavailable" in report["gaming_flags"]
    for row in report["per_task_results"]:
        assert row["single_agent_baseline_unavailable"] is True
        assert row["single_agent_baseline_accept"] is False
        assert row["single_agent_baseline_unavailable_reason"] == (
            "baseline_decisions_not_supplied"
        )
        assert row["single_agent_baseline_evidence_kind"] == "missing"


def test_powered_factorial_consumes_replayable_baseline_decisions(tmp_path):
    baseline_decisions = _produced_baseline_decisions()
    arm_decisions = _factorial_arm_decisions_without_baseline()
    arm_decisions["single_agent_baseline"] = baseline_decisions
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=arm_decisions,
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    baseline = report["arms"]["single_agent_baseline"]
    assert baseline["unavailable_count"] == 0
    assert baseline["availability_status"] == "available"
    assert baseline["decision_source"] == "produced_single_agent_baseline"
    assert baseline["evidence_kind"] == "produced_single_agent_baseline"

    by_candidate = {row["candidate_id"]: row for row in report["per_task_results"]}
    for candidate_id, decision in baseline_decisions.items():
        row = by_candidate[candidate_id]
        assert row["single_agent_baseline_accept"] is bool(decision["accept"])
        assert row["single_agent_baseline_unavailable"] is False
        assert (
            row["single_agent_baseline_decision_source"]
            == "produced_single_agent_baseline"
        )
        assert (
            row["single_agent_baseline_candidate_artifact_hash"]
            == decision["candidate_artifact_hash"]
        )
        assert row["single_agent_baseline_candidate_artifact_hash"] == row["candidate_hash"]
        producer = row["single_agent_baseline_producer"]
        assert producer["agent"] == "single-agent-baseline-replay"
        assert producer["model"] == "fixture-baseline-llm"
        assert row["single_agent_baseline_prompt_sha256"] == decision["prompt_sha256"]
        assert row["single_agent_baseline_evidence_kind"] == "produced_single_agent_baseline"

    exported_rows_path = tmp_path / "powered_factorial_per_task_results.jsonl"
    exported = [
        json.loads(line)
        for line in exported_rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sample = exported[0]
    assert "single_agent_baseline_producer" in sample
    assert "single_agent_baseline_candidate_artifact_hash" in sample
    assert "single_agent_baseline_prompt_sha256" in sample


def test_powered_factorial_baseline_hash_mismatch_is_unavailable(tmp_path):
    bogus_hash = sha256(b"not-the-real-candidate-bytes").hexdigest()
    baseline_decisions = _produced_baseline_decisions(
        hash_overrides={"known-good": bogus_hash},
    )
    arm_decisions = _factorial_arm_decisions_without_baseline()
    arm_decisions["single_agent_baseline"] = baseline_decisions
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=arm_decisions,
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    by_candidate = {row["candidate_id"]: row for row in report["per_task_results"]}
    bad_row = by_candidate["known-good"]
    assert bad_row["single_agent_baseline_unavailable"] is True
    assert bad_row["single_agent_baseline_accept"] is False
    assert (
        bad_row["single_agent_baseline_unavailable_reason"]
        == "candidate_artifact_hash_mismatch"
    )
    assert bad_row["single_agent_baseline_evidence_kind"] == "hash_mismatch"
    assert bad_row["single_agent_baseline_candidate_artifact_hash"] == bogus_hash
    assert bad_row["candidate_hash"] != bogus_hash
    assert "baseline_evidence_unavailable" in report["gaming_flags"]


def test_powered_factorial_baseline_missing_replay_fields_is_unavailable(tmp_path):
    manifest = build_mergeability_corpus_manifest(BENCH_ROOT)
    baseline_decisions = {
        entry["candidate_id"]: {
            "candidate_artifact_hash": entry["candidate_hash"],
        }
        for entry in manifest["candidates"]
    }
    arm_decisions = _factorial_arm_decisions_without_baseline()
    arm_decisions["single_agent_baseline"] = baseline_decisions
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=arm_decisions,
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    baseline = report["arms"]["single_agent_baseline"]
    assert baseline["unavailable_count"] == report["candidate_count"]
    assert baseline["accepted_count"] == 0
    assert baseline["rejected_count"] == 0
    assert baseline["false_reject_count"] == 0
    assert baseline["true_reject_count"] == 0
    assert "baseline_evidence_unavailable" in report["gaming_flags"]
    for row in report["per_task_results"]:
        assert row["single_agent_baseline_unavailable"] is True
        assert row["single_agent_baseline_evidence_kind"] == "malformed"
        assert row["single_agent_baseline_unavailable_reason"] == (
            "malformed_baseline_row_missing_replay_evidence:"
            "accept,decision_source,producer,prompt_sha256"
        )


def test_powered_factorial_unavailable_baseline_rows_do_not_count_as_rejects(tmp_path):
    bogus_hash = sha256(b"divergent-bytes").hexdigest()
    # All supplied rows are accept=True (positive-control candidates); the hash
    # mismatch makes one row unavailable. Missing candidates from the filter
    # are absent baseline rows. Any non-zero rejected_count would prove an
    # unavailable row leaked into reject accounting.
    decisions = _produced_baseline_decisions(
        hash_overrides={"known-good": bogus_hash},
        candidate_filter={
            "known-good",
            "secondary-rubric-only",
            "text-known-good",
        },
    )
    arm_decisions = _factorial_arm_decisions_without_baseline()
    arm_decisions["single_agent_baseline"] = decisions
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=arm_decisions,
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    baseline = report["arms"]["single_agent_baseline"]
    candidate_ids = {row["candidate_id"] for row in report["per_task_results"]}
    expected_unavailable_count = len(candidate_ids - set(decisions)) + 1  # +1 for hash mismatch
    assert baseline["unavailable_count"] == expected_unavailable_count
    assert baseline["unavailable_count"] > 0
    assert baseline["rejected_count"] == 0
    assert baseline["false_reject_count"] == 0
    assert baseline["true_reject_count"] == 0
    assert baseline["false_reject_denominator"] == sum(
        1
        for row in report["per_task_results"]
        if row["oracle_accept"] and not row["single_agent_baseline_unavailable"]
    )


def test_legacy_metadata_baseline_is_labeled_not_real_baseline(tmp_path):
    report = run_paired_acceptance_pilot(BENCH_ROOT, output_dir=tmp_path)

    baseline_arm = report["arms"]["baseline"]
    assert baseline_arm["evidence_kind"] == "metadata_calibration"
    assert baseline_arm["evidence_kind"] != "produced_single_agent_baseline"
    assert report["baseline_evidence_kind"] == "metadata_calibration"
    assert report["report_label"] == "calibration"
    for row in report["per_task_results"]:
        assert row["baseline_evidence_kind"] == "metadata_calibration"
        assert row["baseline_evidence_kind"] != "produced_single_agent_baseline"


def test_real_baseline_reports_remain_report_only(tmp_path):
    baseline_decisions = _produced_baseline_decisions()
    arm_decisions = _factorial_arm_decisions_without_baseline()
    arm_decisions["single_agent_baseline"] = baseline_decisions
    report = run_powered_factorial_mergeability_evaluation(
        BENCH_ROOT,
        output_dir=tmp_path,
        arm_decisions=arm_decisions,
        reviewer_panel_results=_factorial_reviewer_results(),
        powered_thresholds={"min_bad": 1, "min_good": 1},
    )

    baseline = report["arms"]["single_agent_baseline"]
    assert baseline["unavailable_count"] == 0
    assert baseline["evidence_kind"] == "produced_single_agent_baseline"
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False
    assert report["recommendation"]["report_only"] is True
    assert report["recommendation"]["applyable_policy_proposal"] is False
    assert report["promotion_guardrails"]["policy_mutation_allowed"] is False
    assert report["promotion_guardrails"]["oracle_ceiling_supervisor_claim_allowed"] is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path.cwd(),
        affected_gates=("execution", "outcome_review"),
    ) == []
