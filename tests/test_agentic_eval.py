from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from supervisor.agentic_eval import agentic_eval_runner, build_agentic_eval_report, load_agentic_eval_dataset, score_agentic_eval_arm


FIXTURE = "tests/fixtures/agentic_eval/three_arm_tasks.yaml"


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
    assert report["default_change_gate"]["required_modes"] == [
        "lead_direct",
        "agentic_allowed",
        "agentic_required",
    ]


def test_agentic_eval_runner_covers_required_modes():
    report = agentic_eval_runner(dataset_path=FIXTURE)

    rows = report["rows"]
    by_task: dict[str, list[str]] = {}
    for row in rows:
        by_task.setdefault(row["task_id"], []).append(row["mode"])

    assert report["schema_version"] == "agentic-lead-eval/v1"
    assert report["runner"]["execution_mode"] == "fixture_replay"
    assert report["runner"]["live_calls_allowed"] is False
    assert sorted(by_task) == ["resume-drop-catchup", "reviewer-roster-calibration"]
    for modes in by_task.values():
        assert modes == ["lead_direct", "agentic_allowed", "agentic_required"]
    assert report["default_change_gate"]["required_modes"] == [
        "lead_direct",
        "agentic_allowed",
        "agentic_required",
    ]
    assert {row["token_budget"] for row in rows if row["task_id"] == "resume-drop-catchup"} == {12000}
    assert {row["budget_usd_limit"] for row in rows if row["task_id"] == "resume-drop-catchup"} == {3.5}
    for row in rows:
        assert row["workflow_status"] == "accepted"
        assert row["gate_statuses"]["outcome_review"] == "accepted"
        assert {"P1", "P2", "P3"} <= set(row["probe_statuses"])
        assert row["reviewer_panel_decisions"]
        if row["mode"] in {"agentic_allowed", "agentic_required"}:
            assert {"P13", "P14"} <= set(row["probe_statuses"])


def test_agentic_eval_runner_enforces_equal_budget(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    dataset["tasks"][0]["arms"]["agentic_required"]["budget"]["token_budget"] += 1

    with pytest.raises(ValueError, match="unequal arm budgets"):
        # Exercise the public boundary through a temporary JSON fixture because
        # the runner intentionally receives dataset paths.
        path = tmp_path / "unequal.json"
        path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")
        agentic_eval_runner(dataset_path=path)


def test_agentic_eval_runner_requires_gated_replay_shape(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    agentic_arm = dataset["tasks"][0]["arms"]["agentic_allowed"]
    del agentic_arm["workflow_result"]["final_gate_result"]["probes"]["P13"]

    path = tmp_path / "missing-probe.json"
    path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing probes"):
        agentic_eval_runner(dataset_path=path)

    dataset = load_agentic_eval_dataset(FIXTURE)
    lead_arm = dataset["tasks"][0]["arms"]["lead_direct"]
    lead_arm["workflow_result"]["steps"] = [
        step for step in lead_arm["workflow_result"]["steps"] if step["gate"] != "execution"
    ]

    path = tmp_path / "missing-gate.json"
    path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing workflow gates"):
        agentic_eval_runner(dataset_path=path)


def test_agentic_eval_decision_tree_is_deterministic():
    dataset = load_agentic_eval_dataset(FIXTURE)
    task = dataset["tasks"][0]
    arm = task["arms"]["agentic_allowed"]

    first = score_agentic_eval_arm(
        task=task,
        mode="agentic_allowed",
        arm=arm,
        workflow_result=arm["workflow_result"],
    )
    second = score_agentic_eval_arm(
        task=copy.deepcopy(task),
        mode="agentic_allowed",
        arm=copy.deepcopy(arm),
        workflow_result=copy.deepcopy(arm["workflow_result"]),
    )

    assert first == second
    assert first["score"] == 5.0
    assert first["failed_verdict_count"] == 0


def test_agentic_eval_requires_evidence_for_verdict():
    dataset = load_agentic_eval_dataset(FIXTURE)
    task = dataset["tasks"][0]
    arm = copy.deepcopy(task["arms"]["lead_direct"])
    del arm["verdict_evidence"]["no_regression"]

    score = score_agentic_eval_arm(
        task=task,
        mode="lead_direct",
        arm=arm,
        workflow_result=arm["workflow_result"],
    )

    failed = next(item for item in score["verdicts"] if item["verdict_id"] == "no_regression")
    assert failed["status"] == "failed"
    assert failed["reason"] == "missing_evidence"
    assert score["score"] < 5
    assert score["failed_verdict_count"] == 1

    arm = copy.deepcopy(task["arms"]["lead_direct"])
    arm["verdict_evidence"]["no_regression"] = [
        {"kind": "artifact_path", "ref": "tests/does_not_exist.py", "status": "passed"}
    ]

    score = score_agentic_eval_arm(
        task=task,
        mode="lead_direct",
        arm=arm,
        workflow_result=arm["workflow_result"],
    )

    failed = next(item for item in score["verdicts"] if item["verdict_id"] == "no_regression")
    assert failed["status"] == "failed"
    assert failed["reason"] == "evidence_ref_not_resolvable"
    assert score["score"] < 5


def test_agentic_eval_runner_is_report_only(tmp_path):
    output_dir = tmp_path / "agentic-eval"
    report = agentic_eval_runner(dataset_path=FIXTURE, output_dir=output_dir)

    assert report["default_change_allowed"] is False
    assert report["report_only"] == {
        "default_change_allowed": False,
        "config_mutated": False,
        "policy_mutated": False,
    }
    assert report["agentic_lead_policy_snapshot"]["policy"] == "off"
    assert report["agentic_lead_policy_snapshot"]["mutated"] is False
    for key in ("report_json", "evidence_json", "rows_jsonl", "replay_manifest"):
        assert key in report["exports"]
        assert (output_dir / report["exports"][key].split("/")[-1]).exists()
    saved = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["default_change_allowed"] is False
    evidence = json.loads((output_dir / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["records"]


def test_agentic_eval_replay_blocks_live_calls():
    def forbidden_runner(**_kwargs):
        raise AssertionError("live workflow runner should not be called in fixture replay")

    report = agentic_eval_runner(dataset_path=FIXTURE, workflow_runner=forbidden_runner)
    assert report["runner"]["workflow_runner_used"] is False

    with pytest.raises(RuntimeError, match="live workflow execution is disabled"):
        agentic_eval_runner(
            dataset_path=FIXTURE,
            execution_mode="live_workflow",
            workflow_runner=forbidden_runner,
        )
