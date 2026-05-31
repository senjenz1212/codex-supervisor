from __future__ import annotations

from supervisor.agentic_eval import build_agentic_eval_report


def test_agentic_eval_report_compares_required_modes():
    report = build_agentic_eval_report([
        {
            "task_id": "historical-1",
            "mode": "lead_direct",
            "wall_clock_s": 120,
            "cost_usd": 4.2,
            "retries": 1,
            "rejected_gates": 0,
            "missed_issues": 1,
            "operator_interventions": 0,
        },
        {
            "task_id": "historical-1",
            "mode": "agentic_allowed",
            "wall_clock_s": 80,
            "cost_usd": 5.1,
            "retries": 1,
            "rejected_gates": 1,
            "missed_issues": 1,
            "operator_interventions": 0,
        },
        {
            "task_id": "historical-1",
            "mode": "agentic_required",
            "wall_clock_s": 95,
            "cost_usd": 5.8,
            "retries": 0,
            "rejected_gates": 1,
            "missed_issues": 0,
            "operator_interventions": 1,
        },
    ])

    assert report["schema_version"] == "agentic-lead-eval/v1"
    assert [row["mode"] for row in report["rows"]] == [
        "lead_direct",
        "agentic_allowed",
        "agentic_required",
    ]
    assert report["summary"]["lead_direct"]["task_count"] == 1
    assert report["summary"]["agentic_required"]["missed_issues"] == 0
    assert report["default_change_allowed"] is False
