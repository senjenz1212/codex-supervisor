from __future__ import annotations

from supervisor.reviewer_registry import evaluate_reviewer_panel
from supervisor.reviewer_registry import load_reviewer_panel_calibration
from supervisor.reviewer_registry import ReviewerSpec


def _accepting_result(reviewer_id: str, *, severity: str = "none") -> dict:
    is_first = reviewer_id.endswith("-0")
    provider_family = "fixture-a" if is_first else "fixture-b"
    model = "fixture-0" if is_first else "fixture-1"
    return {
        "reviewer_id": reviewer_id,
        "verdict_present": True,
        "accepted": True,
        "decision": "accept",
        "severity": severity,
        "confidence": 0.9,
        "runtime": "fixture",
        "model": model,
        "provider_family": provider_family,
        "lineage": [provider_family, model],
        "tool_access": "codebase_tools",
        "assurance_grade": "agentic",
    }


def _real_source(task_id: str, reviewer_id: str) -> dict:
    suffix = f"{task_id}-{reviewer_id}"
    return {
        "source_kind": "workflow_transcript_event",
        "source_trace_path": f"docs/dual-agent/test/{task_id}/transcript.jsonl",
        "source_event_id": f"event-{suffix}",
        "source_payload_sha256": "a" * 64,
        "transcript_sha256": "b" * 64,
        "transcript_refs": [
            {
                "kind": "workflow_transcript_event",
                "ref": f"docs/dual-agent/test/{task_id}/transcript.jsonl#event-{suffix}",
            }
        ],
        "output_sha256": "c" * 64,
    }


def _calibration() -> dict:
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    reviewers = [
        ReviewerSpec(
            reviewer_id="independent-reviewer-0",
            runtime="fixture",
            model="fixture-0",
            provider_family="fixture-a",
            lineage=("fixture-a", "fixture-0"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
        ReviewerSpec(
            reviewer_id="independent-reviewer-1",
            runtime="fixture",
            model="fixture-1",
            provider_family="fixture-b",
            lineage=("fixture-b", "fixture-1"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
    ]
    report = reviewer_panel_eval_runner(
        labeled_tasks=[
            {
                "task_id": "severity-calibration-accept",
                "gate": "outcome_review",
                "label": "accept_allowed",
                "input_sha256": "input-accept",
            },
            {
                "task_id": "severity-calibration-block",
                "gate": "outcome_review",
                "label": "block_required",
                "input_sha256": "input-block",
            },
        ],
        reviewers=reviewers,
        cassettes=[
            {
                "task_id": "severity-calibration-accept",
                "gate": "outcome_review",
                "reviewer_id": "independent-reviewer-0",
                "cassette_id": "accept-0",
                "decision": "accept",
                "verdict_present": True,
                "confidence": 0.9,
                **_real_source("severity-calibration-accept", "independent-reviewer-0"),
            },
            {
                "task_id": "severity-calibration-accept",
                "gate": "outcome_review",
                "reviewer_id": "independent-reviewer-1",
                "cassette_id": "accept-1",
                "decision": "accept",
                "verdict_present": True,
                "confidence": 0.9,
                **_real_source("severity-calibration-accept", "independent-reviewer-1"),
            },
            {
                "task_id": "severity-calibration-block",
                "gate": "outcome_review",
                "reviewer_id": "independent-reviewer-0",
                "cassette_id": "block-0",
                "decision": "accept",
                "verdict_present": True,
                "confidence": 0.9,
                **_real_source("severity-calibration-block", "independent-reviewer-0"),
            },
            {
                "task_id": "severity-calibration-block",
                "gate": "outcome_review",
                "reviewer_id": "independent-reviewer-1",
                "cassette_id": "block-1",
                "decision": "revise",
                "severity": "important",
                "verdict_present": True,
                "confidence": 0.9,
                **_real_source("severity-calibration-block", "independent-reviewer-1"),
            },
        ],
        emit_calibration=True,
    )
    return report["calibration"]


def test_evaluate_reviewer_panel_calibrated_accept_applies_severity_multiplier():
    decision = evaluate_reviewer_panel(
        [
            _accepting_result("independent-reviewer-0", severity="important"),
            _accepting_result("independent-reviewer-1", severity="important"),
        ],
        calibration=_calibration(),
    )

    assert decision["aggregation_mode"] == "calibrated_weighted"
    assert decision["decision"] == "escalate"
    assert decision["reason"] == "calibrated_dependency_accept"
    calibrated = decision["calibrated_accept"]
    assert calibrated["aggregate_confidence"] < calibrated["accept_confidence_threshold"]
    assert {
        item["reviewer_id"]: item["severity_multiplier"]
        for item in calibrated["weighted_inputs"]
    } == {
        "independent-reviewer-0": 0.65,
        "independent-reviewer-1": 0.65,
    }


def test_load_reviewer_panel_calibration_rejects_no_lineage_constants(tmp_path):
    calibration_path = tmp_path / "constant-calibration.json"
    calibration_path.write_text(
        """{
  "schema_version": "reviewer-panel-calibration/v1",
  "accept_confidence_threshold": 0.99,
  "reviewer_weights": {
    "independent-reviewer-0": {
      "reviewer_id": "independent-reviewer-0",
      "weight": 0.05,
      "dependency_score": 0.95
    }
  }
}
""",
        encoding="utf-8",
    )

    assert load_reviewer_panel_calibration(calibration_path) is None

    decision = evaluate_reviewer_panel(
        [_accepting_result("independent-reviewer-0")],
        calibration=load_reviewer_panel_calibration(calibration_path),
    )

    assert decision["aggregation_mode"] == "conservative"
    assert decision["decision"] == "accept"


def test_load_reviewer_panel_calibration_rejects_fixture_only_provenance(tmp_path):
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    report = reviewer_panel_eval_runner(
        labeled_tasks=[
            {
                "task_id": "fixture-only",
                "gate": "outcome_review",
                "label": "accept_allowed",
                "input_sha256": "fixture-only-input",
            }
        ],
        reviewers=[
            ReviewerSpec(
                reviewer_id="independent-reviewer-0",
                runtime="fixture",
                provider_family="fixture-a",
            ),
            ReviewerSpec(
                reviewer_id="independent-reviewer-1",
                runtime="fixture",
                provider_family="fixture-b",
            ),
        ],
        cassettes=[
            {
                "task_id": "fixture-only",
                "gate": "outcome_review",
                "reviewer_id": "independent-reviewer-0",
                "cassette_id": "fixture-only-0",
                "decision": "accept",
                "verdict_present": True,
                "confidence": 0.9,
            },
            {
                "task_id": "fixture-only",
                "gate": "outcome_review",
                "reviewer_id": "independent-reviewer-1",
                "cassette_id": "fixture-only-1",
                "decision": "accept",
                "verdict_present": True,
                "confidence": 0.9,
            },
        ],
        output_dir=tmp_path,
        emit_calibration=True,
    )

    assert report["calibration"]["source_provenance"]["fixture_row_count"] == 2
    assert load_reviewer_panel_calibration(report["exports"]["calibration_json"]) is None


def test_load_reviewer_panel_calibration_rejects_formula_inconsistent_weights(tmp_path):
    from supervisor.reviewer_registry import _calibration_sha256

    calibration = _calibration()
    pair_id, pair = next(iter(calibration["pairwise_dependency"].items()))
    pair["dependency_score"] = 1.0
    for signal in pair["signals"]:
        pair["signals"][signal] = 0.0
    pair["signals"]["combined_failure_jaccard"] = 1.0
    for payload in calibration["reviewer_weights"].values():
        payload["dependency_score"] = 1.0
        payload["weight"] = 1.0
        payload["derived_from_pairwise"] = [
            {
                "pair_id": pair_id,
                "dependency_score": 1.0,
                "signals": dict(pair["signals"]),
            }
        ]
    calibration["calibration_sha256"] = _calibration_sha256(calibration)
    calibration_path = tmp_path / "formula-inconsistent-calibration.json"
    calibration_path.write_text(
        __import__("json").dumps(calibration, sort_keys=True),
        encoding="utf-8",
    )

    assert load_reviewer_panel_calibration(calibration_path) is None


def test_load_reviewer_panel_calibration_rejects_mixed_lineage_provenance(tmp_path):
    from supervisor.reviewer_registry import _calibration_sha256

    calibration = _calibration()
    consistency = calibration["source_provenance"]["row_roster_consistency"]
    consistency["all_rows_match_reviewer_roster"] = False
    consistency["mismatched_row_count"] = 1
    consistency["mismatches"] = [{
        "task_id": "mixed-lineage",
        "reviewer_id": "independent-reviewer-0",
        "mismatches": [{"field": "runtime", "row": "cursor_sdk", "roster": "fixture"}],
    }]
    calibration["calibration_sha256"] = _calibration_sha256(calibration)
    calibration_path = tmp_path / "mixed-lineage-calibration.json"
    calibration_path.write_text(
        __import__("json").dumps(calibration, sort_keys=True),
        encoding="utf-8",
    )

    assert load_reviewer_panel_calibration(calibration_path) is None


def test_evaluate_reviewer_panel_partial_calibration_falls_back_to_conservative():
    from supervisor.reviewer_registry import _calibration_sha256

    calibration = _calibration()
    calibration["reviewer_weights"].pop("independent-reviewer-1")
    calibration["calibration_sha256"] = _calibration_sha256(calibration)

    decision = evaluate_reviewer_panel(
        [
            _accepting_result("independent-reviewer-0"),
            _accepting_result("independent-reviewer-1"),
        ],
        calibration=calibration,
    )

    assert decision["aggregation_mode"] == "conservative"
    assert decision["decision"] == "accept"
    assert "calibration" not in decision


def test_evaluate_reviewer_panel_lineage_mismatch_falls_back_to_conservative():
    calibration = _calibration()
    changed_runtime = {
        **_accepting_result("independent-reviewer-0"),
        "runtime": "different-runtime",
        "provider_family": "different-family",
        "lineage": ["different-family", "different-runtime"],
    }

    decision = evaluate_reviewer_panel(
        [
            changed_runtime,
            _accepting_result("independent-reviewer-1"),
        ],
        calibration=calibration,
    )

    assert decision["aggregation_mode"] == "conservative"
    assert decision["decision"] == "accept"
    assert "calibration" not in decision
