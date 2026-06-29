from __future__ import annotations

import json
from pathlib import Path

from supervisor.auto_evolve_benchmark_ledger import (
    append_auto_evolve_benchmark_event,
    validate_aeb0_real_attempt_artifact,
    validate_auto_evolve_benchmark_ledger_record,
)
from supervisor.auto_evolve_observability_sink import (
    ingest_auto_evolve_observability_sink,
)


AUTHORITY_FLAGS = {
    "metric_applyable": False,
    "improvement_claim_allowed": False,
    "powered_improvement_claim_allowed": False,
    "human_mergeability_claim_allowed": False,
    "policy_mutated": False,
    "gate_advanced": False,
}


def _write_aeb0_artifact(tmp_path: Path, *, blocked: bool = True) -> Path:
    payload = {
        "schema_version": "supervisor-swebench-official-all-arms-diagnostic-report/v1",
        "status": "unavailable" if blocked else "completed",
        "report_sha256": "placeholder",
        "aeb0_artifact_gate": {
            "gate_id": "AEB-0",
            "status": "blocked" if blocked else "completed",
            "blocked_reasons": ["missing_cli_prerequisite:allow_dataset_fetch"] if blocked else [],
            "authority_flags": dict(AUTHORITY_FLAGS),
        },
        "dataset_readiness": {
            "dataset": "SWE-bench/SWE-bench_Pro",
            "claim_scope": "candidate_serious_benchmark",
            "serious_benchmark_claim_allowed": False,
        },
        "attempt_stage": "docker" if blocked else "scoring",
        **AUTHORITY_FLAGS,
    }
    path = tmp_path / "official_all_arms_diagnostic_report.json"
    payload["report_path"] = str(path)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return path


def test_observability_sink_requires_aeb0_artifact_ref(tmp_path):
    report = ingest_auto_evolve_observability_sink(
        aeb0_artifact_path=None,
        output_dir=tmp_path / "sink",
    )

    assert report["status"] == "blocked"
    assert report["blocked_reasons"] == ["aeb0_artifact_ref_required"]
    assert report["trust_path"]["sink_never_in_trust_path"] is True
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["policy_mutated"] is False
    assert (tmp_path / "sink" / "observability_sink_report.json").exists()


def test_observability_sink_preserves_false_authority_flags_from_aeb0(tmp_path):
    aeb0_path = _write_aeb0_artifact(tmp_path)

    report = ingest_auto_evolve_observability_sink(
        aeb0_artifact_path=aeb0_path,
        output_dir=tmp_path / "sink",
        sink_payload={
            "trace_id": "langfuse-trace-1",
            "dashboard_url": "https://langfuse.example/project/demo/traces/1",
        },
    )

    assert report["status"] == "reported"
    assert report["source_artifacts"]["aeb0"]["status"] == "blocked"
    assert report["source_authority_flags"] == AUTHORITY_FLAGS
    assert report["sink_authority_flags"] == AUTHORITY_FLAGS
    assert report["trust_path"]["can_satisfy_evaluator_run_ref"] is False
    assert report["trust_path"]["can_satisfy_policy_derivation"] is False


def test_observability_sink_payload_cannot_set_applyability_or_promotion(tmp_path):
    aeb0_path = _write_aeb0_artifact(tmp_path)

    report = ingest_auto_evolve_observability_sink(
        aeb0_artifact_path=aeb0_path,
        output_dir=tmp_path / "sink",
        sink_payload={
            "metric_applyable": True,
            "improvement_claim_allowed": True,
            "policy_mutated": True,
            "gate_advanced": True,
        },
    )

    rejected = {item["field"] for item in report["rejected_sink_fields"]}
    assert rejected >= {
        "metric_applyable",
        "improvement_claim_allowed",
        "policy_mutated",
        "gate_advanced",
    }
    assert report["sink_authority_flags"] == AUTHORITY_FLAGS
    assert report["blocked_reasons"] == []


def test_observability_sink_annotations_cannot_satisfy_evaluator_or_quality_controls(
    tmp_path,
):
    aeb0_path = _write_aeb0_artifact(tmp_path)

    report = ingest_auto_evolve_observability_sink(
        aeb0_artifact_path=aeb0_path,
        output_dir=tmp_path / "sink",
        sink_payload={
            "trace_id": "trace-1",
            "score": 0.99,
            "annotation": "looks mergeable",
            "evaluator_run_ref": "langfuse:trace-1",
            "evaluator_run_hash": "score-hash",
            "empty_floor_comparison": {"metric_delta": 1.0},
            "candidate_overlay_ref": "langfuse:annotation-1",
        },
    )

    assert report["trust_path"]["can_satisfy_evaluator_run_ref"] is False
    assert report["trust_path"]["can_satisfy_quality_controls"] is False
    assert report["trust_path"]["can_satisfy_empty_floor"] is False
    assert report["trust_path"]["can_satisfy_candidate_overlay"] is False
    assert "sink_payload_cannot_satisfy_evaluator_provenance" in report["warnings"]


def test_observability_sink_cannot_change_roster_or_effective_vote(tmp_path):
    aeb0_path = _write_aeb0_artifact(tmp_path)

    report = ingest_auto_evolve_observability_sink(
        aeb0_artifact_path=aeb0_path,
        output_dir=tmp_path / "sink",
        sink_payload={
            "effective_vote_estimate": {"status": "computed", "value": 3.0},
            "roster_selection": {"selected_reviewers": ["only-codex"]},
            "pairwise_oracle_error_overlap": [{"overlap": 0.0}],
        },
    )

    rejected = {item["field"] for item in report["rejected_sink_fields"]}
    assert rejected >= {
        "effective_vote_estimate",
        "roster_selection",
        "pairwise_oracle_error_overlap",
    }
    assert report["trust_path"]["can_change_effective_vote"] is False
    assert report["trust_path"]["can_change_roster_selection"] is False


def test_blocked_downstream_ledger_record_preserves_false_authority_flags(tmp_path):
    ledger_path = tmp_path / "auto_evolve_benchmark_ledger.jsonl"

    record = append_auto_evolve_benchmark_event(
        ledger_path,
        stage="observability_sink",
        promise_ids=("P0", "P1", "P10"),
        status="blocked",
        artifact_path=tmp_path / "missing.json",
        aeb0_artifact_ref="",
        blocked_reasons=("real_official_all_arms_artifact_required",),
        dataset="SWE-bench/SWE-bench_Pro",
        split="test",
        dataset_revision="20260626-pro-recon",
        attempt_stage="recon",
        sink_status="deferred",
        annotation_queue_status="deferred",
    )

    assert record["schema_version"] == "supervisor-auto-evolve-benchmark-ledger/v1"
    assert record["stage"] == "observability_sink"
    assert record["promise_ids"] == ["P0", "P1", "P10"]
    assert record["artifact"]["exists"] is False
    assert record["aeb0_dependency"]["status"] == "missing"
    assert record["operator_review_required"] is True
    assert record["blocked_reasons"] == [
        "real_official_all_arms_artifact_required"
    ]
    assert record["dataset"]["name"] == "SWE-bench/SWE-bench_Pro"
    assert record["dataset"]["split"] == "test"
    assert record["dataset"]["revision"] == "20260626-pro-recon"
    assert record["attempt_stage"] == "recon"
    assert record["sink_status"] == "deferred"
    assert record["annotation_queue_status"] == "deferred"
    for flag, value in AUTHORITY_FLAGS.items():
        assert record["authority_flags"][flag] is value

    rows = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [record]


def test_aeb0_real_attempt_validator_rejects_pre_run_cli_guard_artifact(tmp_path):
    artifact = _write_aeb0_artifact(tmp_path)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["attempt_stage"] = "recon"
    artifact.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    result = validate_aeb0_real_attempt_artifact(artifact)

    assert result["valid"] is False
    assert "pre_run_cli_guard_is_not_real_attempt" in result["reasons"]
    assert "attempt_stage_did_not_reach_dataset_or_docker" in result["reasons"]


def test_aeb0_blocked_artifact_requires_attempt_stage(tmp_path):
    artifact = _write_aeb0_artifact(tmp_path)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload.pop("attempt_stage", None)
    artifact.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    result = validate_aeb0_real_attempt_artifact(artifact)

    assert result["valid"] is False
    assert "attempt_stage_required" in result["reasons"]


def test_ledger_row_without_declared_dataset_is_rejected(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"

    try:
        append_auto_evolve_benchmark_event(
            ledger_path,
            stage="real_official_all_arms_artifact_gate",
            promise_ids=("P0", "P1"),
            status="blocked",
            artifact_path=tmp_path / "missing.json",
            blocked_reasons=("docker_daemon_unavailable",),
            attempt_stage="docker",
        )
    except ValueError as exc:
        assert "dataset.name is required" in str(exc)
    else:  # pragma: no cover - defensive readability
        raise AssertionError("ledger row without dataset must be rejected")


def test_verified_dataset_ledger_requires_override_and_caveat(tmp_path):
    base = {
        "schema_version": "supervisor-auto-evolve-benchmark-ledger/v1",
        "stage": "real_official_all_arms_artifact_gate",
        "promise_ids": ["P0"],
        "status": "blocked",
        "dataset": {
            "name": "SWE-bench/SWE-bench_Verified",
            "split": "test",
            "revision": "main",
            "sha": "",
            "allow_known_contaminated": False,
            "contamination_caveat": "",
        },
        "attempt_stage": "dataset_fetch",
        "artifact": {"path": "", "exists": False, "sha256": ""},
        "aeb0_dependency": {"artifact_ref": "", "status": "missing", "artifact_sha256": ""},
        "blocked_reasons": ["dataset_fetch_failed"],
        "authority_flags": AUTHORITY_FLAGS,
        "operator_review_required": True,
        "sink_status": "deferred",
        "annotation_queue_status": "deferred",
    }

    errors = validate_auto_evolve_benchmark_ledger_record(base)

    assert "known_contaminated_dataset_requires_override_and_caveat" in errors

    base["dataset"]["allow_known_contaminated"] = True
    base["dataset"]["contamination_caveat"] = "Verified is smoke-only."
    assert validate_auto_evolve_benchmark_ledger_record(base) == []
