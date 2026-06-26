from __future__ import annotations

import json
from pathlib import Path

from supervisor.autoresearch.benchmark_promotion import (
    promote_benchmark_report_to_autoresearch_report,
)
from supervisor.autoresearch.policy_evolution import (
    derive_policy_evolution_proposals_from_report,
    report_contains_derivable_policy_record,
)


def test_benchmark_backed_evidence_conversion_blocks_without_aeb0_or_evaluator_hash(
    tmp_path,
):
    ledger_path = tmp_path / "auto_evolve_benchmark_ledger.jsonl"

    report = promote_benchmark_report_to_autoresearch_report(
        benchmark_report={
            "schema_version": "supervisor-mergeability-powered-factorial-report/v1",
            "report_sha256": "benchmark-report-sha",
            "evidence_conversion_power_contract": {"status": "qualified"},
        },
        output_dir=tmp_path / "bridge",
        candidate_overlay_ref="candidates/policy-overlay.yaml",
        evaluator_run_ref="runs/evaluator.json",
        evaluator_run_hash="",
        aeb0_artifact_ref="",
        evaluator_quality={"source": "supervisor_control_execution"},
        empty_floor_comparison={"metric_delta": 0.1},
        ledger_path=ledger_path,
    )

    assert report["schema_version"] == "supervisor-autoresearch-summary/v1"
    assert report["recommendation"]["decision"] == "blocked"
    [record] = report["records"]
    assert record["validation_status"] == "blocked"
    assert record["blocked_reasons"] == [
        "missing_aeb0_artifact_ref",
        "missing_evaluator_run_hash",
    ]
    assert record["metric_source"] == "evaluator_execution"
    assert record["default_change_allowed"] is False
    assert record["policy_mutated"] is False
    assert record["gate_advanced"] is False
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report_contains_derivable_policy_record(report, repo_root=tmp_path) is False
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("mergeability",),
    ) == []

    rows = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["stage"] == "benchmark_to_autoresearch_evidence_conversion"
    assert rows[-1]["status"] == "blocked"
    assert rows[-1]["blocked_reasons"] == record["blocked_reasons"]


def test_benchmark_conversion_rejects_missing_empty_floor_without_derivation(
    tmp_path,
):
    aeb0_path = tmp_path / "official_all_arms_diagnostic_report.json"
    aeb0_path.write_text(
        json.dumps({
            "aeb0_artifact_gate": {"status": "completed", "blocked_reasons": []},
            "report_sha256": "aeb0-sha",
        }),
        encoding="utf-8",
    )

    report = promote_benchmark_report_to_autoresearch_report(
        benchmark_report={
            "schema_version": "supervisor-mergeability-powered-factorial-report/v1",
            "report_sha256": "benchmark-report-sha",
            "evidence_conversion_power_contract": {"status": "qualified"},
        },
        output_dir=tmp_path / "bridge",
        candidate_overlay_ref="candidates/policy-overlay.yaml",
        evaluator_run_ref="runs/evaluator.json",
        evaluator_run_hash="evaluator-run-hash",
        aeb0_artifact_ref=aeb0_path,
        evaluator_quality={"source": "supervisor_control_execution"},
    )

    [record] = report["records"]
    assert record["validation_status"] == "blocked"
    assert record["blocked_reasons"] == ["missing_empty_floor_comparison"]
    assert derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("mergeability",),
    ) == []
