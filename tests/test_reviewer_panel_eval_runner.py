from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.agentic_eval import build_agentic_eval_report
from supervisor.reviewer_registry import ReviewerSpec
from supervisor.state import State


def _reviewers() -> list[ReviewerSpec]:
    return [
        ReviewerSpec(
            reviewer_id="independent-reviewer-0",
            runtime="litellm_structured",
            model="gemini-3.1-pro-preview",
            provider_family="google",
            lineage=("google", "litellm_structured", "gemini-3.1-pro-preview"),
            tool_access="text_only",
            assurance_grade="text_only",
        ),
        ReviewerSpec(
            reviewer_id="independent-reviewer-1",
            runtime="codex_cli",
            model="gpt-5.5",
            provider_family="openai",
            lineage=("openai", "codex_cli", "gpt-5.5"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
    ]


def _task(task_id: str, label: str = "accept_allowed") -> dict:
    return {
        "task_id": task_id,
        "gate": "outcome_review",
        "label": label,
        "input_sha256": f"input-{task_id}",
        "evidence_ref": f"fixtures/{task_id}.json",
    }


def _cassette(
    task_id: str,
    reviewer_id: str,
    *,
    decision: str | None = "accept",
    severity: str = "none",
    confidence: float | None = 0.9,
    cost_usd: float = 0.2,
    latency_ms: int = 100,
    verdict_present: bool | None = None,
) -> dict:
    present = decision in {"accept", "revise", "deny"} if verdict_present is None else verdict_present
    return {
        "task_id": task_id,
        "gate": "outcome_review",
        "reviewer_id": reviewer_id,
        "cassette_id": f"cassette-{task_id}-{reviewer_id}",
        "decision": decision,
        "severity": severity,
        "confidence": confidence,
        "verdict_present": present,
        "runtime": "litellm_structured" if reviewer_id.endswith("-0") else "codex_cli",
        "model": "gemini-3.1-pro-preview" if reviewer_id.endswith("-0") else "gpt-5.5",
        "provider_family": "google" if reviewer_id.endswith("-0") else "openai",
        "lineage": ["google", "litellm_structured", "gemini-3.1-pro-preview"]
        if reviewer_id.endswith("-0") else ["openai", "codex_cli", "gpt-5.5"],
        "tool_access": "text_only" if reviewer_id.endswith("-0") else "codebase_tools",
        "assurance_grade": "text_only" if reviewer_id.endswith("-0") else "agentic",
        "transcript_refs": [{"kind": "fixture", "ref": f"transcripts/{task_id}-{reviewer_id}.jsonl"}],
        "input_sha256": f"input-{task_id}-{reviewer_id}",
        "output_sha256": f"output-{task_id}-{reviewer_id}" if present else None,
        "failure_classification": None if present else "reviewer_infrastructure_unavailable",
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
    }


def _basic_fixture() -> tuple[list[dict], list[dict]]:
    tasks = [
        _task("task-accept-1", "accept_allowed"),
        _task("task-block-1", "block_required"),
        _task("task-accept-2", "accept_allowed"),
    ]
    cassettes = [
        _cassette("task-accept-1", "independent-reviewer-0", decision="accept", cost_usd=0.1, latency_ms=100),
        _cassette("task-accept-1", "independent-reviewer-1", decision="accept", cost_usd=0.2, latency_ms=110),
        _cassette("task-block-1", "independent-reviewer-0", decision="accept", cost_usd=0.2, latency_ms=200),
        _cassette("task-block-1", "independent-reviewer-1", decision="revise", severity="important", cost_usd=0.2, latency_ms=210),
        _cassette("task-accept-2", "independent-reviewer-0", decision="accept", cost_usd=0.3, latency_ms=300),
        _cassette("task-accept-2", "independent-reviewer-1", decision=None, confidence=None, cost_usd=0.0, latency_ms=0),
    ]
    return tasks, cassettes


def test_reviewer_panel_eval_runner_validates_labeled_fixture_schema():
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks, cassettes = _basic_fixture()

    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks[:1],
        reviewers=_reviewers(),
        cassettes=cassettes[:2],
    )

    assert report["schema_version"] == "reviewer-panel-eval/v1"
    assert report["runner"]["public_boundary"] == "reviewer_panel_eval_runner"
    assert report["runner"]["execution_mode"] == "fixture_replay"
    assert report["provenance"]["fixture_row_count"] == 2
    assert report["provenance"]["real_reviewer_output_count"] == 0
    assert report["provenance"]["row_roster_consistency"]["all_rows_match_reviewer_roster"] is True
    assert report["labeled_set"]["schema_version"] == "reviewer-panel-labeled-set/v1"
    assert report["policy_change_allowed"] is False
    assert report["schema_version"] != "agentic-lead-eval/v1"

    with pytest.raises(ValueError, match="invalid label"):
        reviewer_panel_eval_runner(
            labeled_tasks=[_task("bad-task", "maybe_accept")],
            reviewers=_reviewers(),
            cassettes=[],
        )


def test_reviewer_panel_eval_runner_records_all_reviewer_rows():
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks, cassettes = _basic_fixture()
    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=_reviewers(),
        cassettes=cassettes,
    )

    rows = report["rows"]
    assert len(rows) == 6
    assert [(row["task_id"], row["reviewer_id"]) for row in rows] == [
        ("task-accept-1", "independent-reviewer-0"),
        ("task-accept-1", "independent-reviewer-1"),
        ("task-accept-2", "independent-reviewer-0"),
        ("task-accept-2", "independent-reviewer-1"),
        ("task-block-1", "independent-reviewer-0"),
        ("task-block-1", "independent-reviewer-1"),
    ]
    missing = next(row for row in rows if row["reviewer_id"] == "independent-reviewer-1" and row["task_id"] == "task-accept-2")
    assert missing["verdict_present"] is False
    assert missing["decision"] == "missing"
    assert missing["failure_classification"] == "reviewer_infrastructure_unavailable"
    assert missing["cassette_id"]
    assert missing["input_sha256"]
    assert missing["output_sha256"] is None


def test_reviewer_panel_eval_runner_computes_per_reviewer_metrics():
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks, cassettes = _basic_fixture()
    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=_reviewers(),
        cassettes=cassettes,
    )

    by_reviewer = report["per_reviewer"]
    reviewer_0 = by_reviewer["independent-reviewer-0"]
    assert reviewer_0["task_count"] == 3
    assert reviewer_0["verdict_present_count"] == 3
    assert reviewer_0["decision_counts"] == {"accept": 3, "revise": 0, "deny": 0, "missing": 0}
    assert reviewer_0["false_accept_count"] == 1
    assert reviewer_0["false_accept_rate"] == 1.0
    assert reviewer_0["false_accept_denominator"] == 1
    assert reviewer_0["false_block_count"] == 0
    assert reviewer_0["unavailable_rate"] == 0.0
    assert reviewer_0["total_cost_usd"] == pytest.approx(0.6)
    assert reviewer_0["avg_cost_usd"] == pytest.approx(0.2)
    assert reviewer_0["total_latency_ms"] == 600
    assert reviewer_0["avg_latency_ms"] == pytest.approx(200)

    reviewer_1 = by_reviewer["independent-reviewer-1"]
    assert reviewer_1["decision_counts"] == {"accept": 1, "revise": 1, "deny": 0, "missing": 1}
    assert reviewer_1["false_accept_count"] == 0
    assert reviewer_1["false_block_count"] == 1
    assert reviewer_1["false_block_rate"] == pytest.approx(0.5)
    assert reviewer_1["false_block_denominator"] == 2
    assert reviewer_1["false_block_cause_counts"]["missing_unavailable"] == 1
    assert reviewer_1["missing_count"] == 1
    assert reviewer_1["unavailable_rate"] == pytest.approx(1 / 3)


def test_reviewer_panel_eval_runner_computes_pairwise_dependency_metrics():
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks = [
        _task("agree-accept", "accept_allowed"),
        _task("disagree-block", "block_required"),
        _task("overlap-false-block", "accept_allowed"),
        _task("overlap-false-accept", "block_required"),
    ]
    cassettes = [
        _cassette("agree-accept", "independent-reviewer-0", decision="accept"),
        _cassette("agree-accept", "independent-reviewer-1", decision="accept"),
        _cassette("disagree-block", "independent-reviewer-0", decision="accept"),
        _cassette("disagree-block", "independent-reviewer-1", decision="revise", severity="important"),
        _cassette("overlap-false-block", "independent-reviewer-0", decision=None, confidence=None),
        _cassette("overlap-false-block", "independent-reviewer-1", decision="revise", severity="important"),
        _cassette("overlap-false-accept", "independent-reviewer-0", decision="accept"),
        _cassette("overlap-false-accept", "independent-reviewer-1", decision="accept"),
    ]

    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=_reviewers(),
        cassettes=cassettes,
    )

    pair = report["pairwise"]["independent-reviewer-0__independent-reviewer-1"]
    assert pair["comparable_task_count"] == 4
    assert pair["agreement_count"] == 2
    assert pair["disagreement_count"] == 2
    assert pair["agreement_rate"] == pytest.approx(0.5)
    assert pair["false_accept_overlap"]["count"] == 1
    assert pair["false_accept_overlap"]["jaccard"] == pytest.approx(0.5)
    assert pair["false_block_overlap"]["count"] == 1
    assert pair["false_block_overlap"]["jaccard"] == pytest.approx(1.0)
    assert pair["combined_failure_jaccard"] == pytest.approx(2 / 3)
    assert pair["block_decision_correlation"]["status"] == "ok"
    assert pair["wrong_decision_correlation"]["status"] == "ok"
    assert pair["contingency"]["block_decision"]
    assert pair["contingency"]["wrong_decision"]

    zero_variance_tasks = [_task("all-accept-1"), _task("all-accept-2")]
    zero_variance_cassettes = [
        _cassette("all-accept-1", "independent-reviewer-0", decision="accept"),
        _cassette("all-accept-1", "independent-reviewer-1", decision="accept"),
        _cassette("all-accept-2", "independent-reviewer-0", decision="accept"),
        _cassette("all-accept-2", "independent-reviewer-1", decision="accept"),
    ]
    zero_report = reviewer_panel_eval_runner(
        labeled_tasks=zero_variance_tasks,
        reviewers=_reviewers(),
        cassettes=zero_variance_cassettes,
    )
    zero_pair = zero_report["pairwise"]["independent-reviewer-0__independent-reviewer-1"]
    assert zero_pair["block_decision_correlation"]["status"] == "not_applicable"
    assert "zero variance" in zero_pair["block_decision_correlation"]["reason"]


def test_reviewer_panel_eval_runner_exports_replay_and_ledger_artifacts(tmp_path):
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks, cassettes = _basic_fixture()
    output_dir = tmp_path / "eval-artifacts"
    state = State(str(tmp_path / "state.db"))

    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=_reviewers(),
        cassettes=cassettes,
        output_dir=output_dir,
        state=state,
        run_id="reviewer-panel-eval-run",
    )

    exports = report["exports"]
    for key in ("report_json", "report_markdown", "rows_jsonl", "replay_manifest"):
        path = Path(exports[key])
        assert path.exists(), key
        assert path.is_file()
    assert len(report["ledger_event_ids"]) == 1
    assert report["report_sha256"]
    assert report["labeled_set"]["sha256"]
    assert report["cassette_ids"]
    assert report["reviewer_roster"][0]["reviewer_id"] == "independent-reviewer-0"
    assert report["policy_change_allowed"] is False
    assert report["active_weight_changes"] == []

    saved_report = json.loads(Path(exports["report_json"]).read_text(encoding="utf-8"))
    assert saved_report["report_sha256"] == report["report_sha256"]
    manifest = json.loads(Path(exports["replay_manifest"]).read_text(encoding="utf-8"))
    assert manifest["labeled_set_sha256"] == report["labeled_set"]["sha256"]
    assert manifest["report_sha256"] == report["report_sha256"]
    assert manifest["ledger_event_ids"] == report["ledger_event_ids"]

    events = state.read_events_since("reviewer-panel-eval-run", after_event_id=0, limit=10)
    assert events[0]["kind"] == "reviewer_panel_eval_observation"
    assert events[0]["payload"]["policy_change_allowed"] is False


def test_reviewer_panel_eval_runner_emits_calibration_artifact_when_requested(tmp_path):
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks = [
        _task("shared-false-accept", "block_required"),
        _task("shared-accept", "accept_allowed"),
    ]
    cassettes = [
        _cassette("shared-false-accept", "independent-reviewer-0", decision="accept", confidence=0.9),
        _cassette("shared-false-accept", "independent-reviewer-1", decision="accept", confidence=0.9),
        _cassette("shared-accept", "independent-reviewer-0", decision="accept", confidence=0.8),
        _cassette("shared-accept", "independent-reviewer-1", decision="accept", confidence=0.8),
    ]
    output_dir = tmp_path / "eval-artifacts"

    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=_reviewers(),
        cassettes=cassettes,
        output_dir=output_dir,
        emit_calibration=True,
    )

    assert "does_not_emit_active_calibrated_weights" not in report["non_goals"]
    assert report["active_weight_changes"]
    calibration_path = Path(report["exports"]["calibration_json"])
    assert calibration_path.exists()
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    assert calibration["schema_version"] == "reviewer-panel-calibration/v1"
    assert calibration["source_report_sha256"] == report["report_sha256"]
    assert calibration["source_provenance"]["fixture_row_count"] == 4
    assert calibration["source_provenance"]["real_reviewer_output_count"] == 0
    assert calibration["pairwise_dependency"]["independent-reviewer-0__independent-reviewer-1"][
        "dependency_score"
    ] == pytest.approx(1.0)
    assert calibration["reviewer_weights"]["independent-reviewer-0"]["weight"] < 1.0
    assert calibration["reviewer_weights"]["independent-reviewer-1"]["weight"] < 1.0
    assert report["calibration"]["calibration_sha256"] == calibration["calibration_sha256"]
    assert calibration["source_provenance"]["row_roster_consistency"][
        "all_rows_match_reviewer_roster"
    ] is True


def test_reviewer_panel_calibration_rejects_mixed_lineage_rows(tmp_path):
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    tasks = [_task("mixed-lineage", "accept_allowed")]
    cassettes = [
        {
            **_cassette("mixed-lineage", "independent-reviewer-0", decision="accept"),
            "runtime": "cursor_sdk",
            "model": "composer-2.5",
            "provider_family": "cursor",
            "lineage": ["cursor", "cursor_sdk", "composer-2.5"],
            "tool_access": "codebase_tools",
            "assurance_grade": "agentic",
        },
        _cassette("mixed-lineage", "independent-reviewer-1", decision="accept"),
    ]

    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=_reviewers(),
        cassettes=cassettes,
    )
    consistency = report["provenance"]["row_roster_consistency"]
    assert consistency["all_rows_match_reviewer_roster"] is False
    assert consistency["mismatched_row_count"] == 1

    with pytest.raises(ValueError, match="calibration rows must match reviewer roster metadata"):
        reviewer_panel_eval_runner(
            labeled_tasks=tasks,
            reviewers=_reviewers(),
            cassettes=cassettes,
            output_dir=tmp_path,
            emit_calibration=True,
        )


def test_reviewer_panel_calibration_is_deterministic_and_data_derived(tmp_path):
    from supervisor.reviewer_panel_eval import (
        build_reviewer_panel_calibration,
        reviewer_panel_eval_runner,
    )

    independent_tasks = [_task("clean-accept", "accept_allowed"), _task("clean-block", "block_required")]
    independent_cassettes = [
        _cassette("clean-accept", "independent-reviewer-0", decision="accept", confidence=0.9),
        _cassette("clean-accept", "independent-reviewer-1", decision="accept", confidence=0.9),
        _cassette("clean-block", "independent-reviewer-0", decision="accept", confidence=0.9),
        _cassette("clean-block", "independent-reviewer-1", decision="revise", severity="important", confidence=0.9),
    ]
    correlated_tasks = [_task("bad-accept", "block_required"), _task("ok-accept", "accept_allowed")]
    correlated_cassettes = [
        _cassette("bad-accept", "independent-reviewer-0", decision="accept", confidence=0.9),
        _cassette("bad-accept", "independent-reviewer-1", decision="accept", confidence=0.9),
        _cassette("ok-accept", "independent-reviewer-0", decision="accept", confidence=0.9),
        _cassette("ok-accept", "independent-reviewer-1", decision="accept", confidence=0.9),
    ]

    independent_report = reviewer_panel_eval_runner(
        labeled_tasks=independent_tasks,
        reviewers=_reviewers(),
        cassettes=independent_cassettes,
    )
    correlated_report = reviewer_panel_eval_runner(
        labeled_tasks=correlated_tasks,
        reviewers=_reviewers(),
        cassettes=correlated_cassettes,
    )
    first = build_reviewer_panel_calibration(independent_report)
    second = build_reviewer_panel_calibration(independent_report)
    correlated = build_reviewer_panel_calibration(correlated_report)

    assert first["calibration_sha256"] == second["calibration_sha256"]
    assert first["reviewer_weights"]["independent-reviewer-0"]["weight"] != (
        correlated["reviewer_weights"]["independent-reviewer-0"]["weight"]
    )
    assert first["reviewer_weights"]["independent-reviewer-1"]["weight"] != (
        correlated["reviewer_weights"]["independent-reviewer-1"]["weight"]
    )
    assert first["calibration_sha256"] != correlated["calibration_sha256"]


def test_reviewer_panel_eval_runner_is_distinct_from_agentic_eval_report():
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner

    lead_report = build_agentic_eval_report([
        {
            "task_id": "historical-1",
            "mode": "lead_direct",
            "wall_clock_s": 120,
            "cost_usd": 4.2,
            "retries": 1,
            "rejected_gates": 0,
            "missed_issues": 1,
            "operator_interventions": 0,
        }
    ])
    tasks, cassettes = _basic_fixture()
    panel_report = reviewer_panel_eval_runner(
        labeled_tasks=tasks[:1],
        reviewers=_reviewers(),
        cassettes=cassettes[:2],
    )

    assert lead_report["schema_version"] == "agentic-lead-eval/v1"
    assert lead_report["default_change_allowed"] is False
    assert "reviewer_roster" not in lead_report
    assert panel_report["schema_version"] == "reviewer-panel-eval/v1"
    assert panel_report["runner"]["public_boundary"] == "reviewer_panel_eval_runner"
    assert "summary" not in panel_report or "lead_direct" not in panel_report.get("summary", {})
