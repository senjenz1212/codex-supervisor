from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from supervisor.agentic_eval import agentic_eval_runner, build_agentic_eval_report, load_agentic_eval_dataset, score_agentic_eval_arm


FIXTURE = "tests/fixtures/agentic_eval/three_arm_tasks.yaml"
ACCELERATION_REPORT_SHA256 = "5f5239f0bc0dfacd64b1e71f0b3a8abc53d051b2c4d0c7f24d455eb425393e74"


def _write_dataset(tmp_path, dataset: dict, name: str = "dataset.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"schema_version": dataset["schema_version"], "tasks": dataset["tasks"]}), encoding="utf-8")
    return path


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
    assert sorted(by_task) == [
        "affected-path-dependency-trace",
        "multi-file-code-archaeology",
        "resume-drop-catchup",
        "reviewer-roster-calibration",
    ]
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


def test_agentic_eval_runner_reports_acceleration_percentiles():
    report = agentic_eval_runner(dataset_path=FIXTURE)

    rows = {
        (row["task_id"], row["mode"]): row
        for row in report["rows"]
    }
    assert rows[("resume-drop-catchup", "lead_direct")]["acceleration_ratio"] == 1.0
    assert rows[("resume-drop-catchup", "agentic_allowed")]["acceleration_ratio"] == 1.2
    assert rows[("resume-drop-catchup", "agentic_required")]["acceleration_ratio"] == pytest.approx(1.364)

    allowed = report["summary"]["agentic_allowed"]
    required = report["summary"]["agentic_required"]
    assert allowed["acceleration_ratio_p50"] == pytest.approx(1.281)
    assert allowed["acceleration_ratio_p95"] == pytest.approx(1.409)
    assert required["acceleration_ratio_p50"] == pytest.approx(1.372)
    assert required["acceleration_ratio_p95"] == pytest.approx(1.514)
    assert report["summary"]["lead_direct"]["acceleration_ratio_p50"] == 1.0


def test_agentic_eval_quality_gated_win_condition_truth_table():
    lead = {
        "task_id": "truth-table",
        "mode": "lead_direct",
        "wall_clock_s": 120,
        "workflow_status": "accepted",
        "score": 5,
        "missed_issues": 0,
        "rejected_gates": 0,
    }
    base_agentic = {
        "task_id": "truth-table",
        "mode": "agentic_allowed",
        "wall_clock_s": 80,
        "workflow_status": "accepted",
        "score": 5,
        "missed_issues": 0,
        "rejected_gates": 0,
    }

    cases = [
        ("workflow_status_not_accepted", {"workflow_status": "blocked"}),
        ("score_below_lead_direct", {"score": 4}),
        ("missed_issues_above_lead_direct", {"missed_issues": 1}),
        ("rejected_gates_above_lead_direct", {"rejected_gates": 1}),
        ("acceleration_ratio_below_1_2", {"wall_clock_s": 110}),
    ]
    for predicate, mutation in cases:
        agentic = {**base_agentic, **mutation}
        report = build_agentic_eval_report([lead, agentic])
        row = next(row for row in report["rows"] if row["mode"] == "agentic_allowed")
        assert row["qualifies"] is False
        assert predicate in row["qualification_failing_predicates"]

    report = build_agentic_eval_report([lead, base_agentic])
    row = next(row for row in report["rows"] if row["mode"] == "agentic_allowed")
    assert row["qualifies"] is True
    assert row["qualification_failing_predicates"] == []


def test_agentic_eval_blocked_faster_arm_never_qualifies(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    task = dataset["tasks"][0]
    arm = task["arms"]["agentic_allowed"]
    arm["metrics"]["wall_clock_s"] = 10
    arm["workflow_result"]["status"] = "blocked"
    arm["workflow_result"]["steps"][-1]["status"] = "blocked"
    arm["workflow_result"]["final_gate_result"]["status"] = "blocked"
    path = _write_dataset(tmp_path, dataset, "blocked-faster.json")

    report = agentic_eval_runner(dataset_path=path)

    row = next(row for row in report["rows"] if row["task_id"] == task["task_id"] and row["mode"] == "agentic_allowed")
    assert row["acceleration_ratio"] >= 1.2
    assert row["qualifies"] is False
    assert "workflow_status_not_accepted" in row["qualification_failing_predicates"]


def test_agentic_eval_latency_fields_are_values_or_unavailable_reasons(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    task = dataset["tasks"][0]
    task["arms"]["lead_direct"]["metrics"].update({
        "time_to_first_useful_finding": 40,
        "time_to_accepted_outcome": 180,
        "orchestration_overhead_s": 12,
        "reviewer_time_s": 35,
        "worker_idle_wait_s": 0,
    })
    task["arms"]["agentic_allowed"]["metrics"].pop("time_to_first_useful_finding", None)
    path = _write_dataset(tmp_path, dataset, "latency.json")

    report = agentic_eval_runner(dataset_path=path)

    lead = next(row for row in report["rows"] if row["task_id"] == task["task_id"] and row["mode"] == "lead_direct")
    assert lead["time_to_first_useful_finding"] == 40
    assert lead["time_to_first_useful_finding_unavailable_reason"] is None
    assert lead["worker_idle_wait_s"] == 0

    allowed = next(row for row in report["rows"] if row["task_id"] == task["task_id"] and row["mode"] == "agentic_allowed")
    assert allowed["time_to_first_useful_finding"] is None
    assert allowed["time_to_first_useful_finding_unavailable_reason"] == "not_recorded"


def test_agentic_eval_parallelism_corpus_replays_to_stable_report_sha256():
    report = agentic_eval_runner(dataset_path=FIXTURE)

    parallelism_tasks = {
        row["task_id"]
        for row in report["rows"]
        if row["task_id"] in {"multi-file-code-archaeology", "affected-path-dependency-trace"}
    }
    assert parallelism_tasks == {"multi-file-code-archaeology", "affected-path-dependency-trace"}
    for task_id in parallelism_tasks:
        task_rows = [row for row in report["rows"] if row["task_id"] == task_id]
        assert {row["mode"] for row in task_rows} == {"lead_direct", "agentic_allowed", "agentic_required"}
        assert all(row["workflow_status"] == "accepted" for row in task_rows)
    assert report["report_sha256"] == ACCELERATION_REPORT_SHA256


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


def test_agentic_eval_runner_allows_real_early_block_replay(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    lead_arm = dataset["tasks"][0]["arms"]["lead_direct"]
    lead_arm["workflow_result"] = {
        "status": "blocked",
        "steps": [{"gate": "prd_review", "status": "blocked", "attempt_count": 1}],
        "final_gate_result": {
            "gate": "prd_review",
            "status": "blocked",
            "probes": {"P_planning": {"status": "red"}},
        },
    }
    path = tmp_path / "early-block.json"
    path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")

    report = agentic_eval_runner(dataset_path=path)

    row = next(row for row in report["rows"] if row["task_id"] == "resume-drop-catchup" and row["mode"] == "lead_direct")
    assert row["workflow_status"] == "blocked"
    assert row["rejected_gates"] >= 1


def test_agentic_eval_runner_derives_missed_issues_from_verdicts(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    task = dataset["tasks"][0]
    lead_arm = task["arms"]["lead_direct"]
    lead_arm["verdict_evidence"] = {
        verdict["verdict_id"]: [
            {"kind": "artifact_path", "ref": "tests/does_not_exist.py", "status": "passed"}
        ]
        for verdict in task["required_verdicts"]
    }
    lead_arm["metrics"]["missed_issues"] = 0
    lead_arm["metrics"]["wall_clock_s"] = 123.4
    lead_arm["metrics"]["cost_usd"] = 5.6
    path = tmp_path / "quality-mask.json"
    path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")

    report = agentic_eval_runner(dataset_path=path)

    row = next(row for row in report["rows"] if row["task_id"] == "resume-drop-catchup" and row["mode"] == "lead_direct")
    assert row["score"] == 0.0
    assert row["missed_issues"] == len(task["required_verdicts"])
    assert row["reported_missed_issues"] == 0
    assert row["metrics_divergence"] is True
    assert "missed_issues" in row["metrics_divergence_fields"]
    assert row["wall_clock_s"] == 123.4
    assert row["cost_usd"] == 5.6


def test_agentic_eval_runner_derives_rejected_gates_from_workflow(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    lead_arm = dataset["tasks"][0]["arms"]["lead_direct"]
    lead_arm["workflow_result"]["steps"][0]["status"] = "blocked"
    lead_arm["metrics"]["rejected_gates"] = 0
    path = tmp_path / "rejected-gate-mask.json"
    path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")

    report = agentic_eval_runner(dataset_path=path)

    row = next(row for row in report["rows"] if row["task_id"] == "resume-drop-catchup" and row["mode"] == "lead_direct")
    assert row["rejected_gates"] == 1
    assert row["reported_rejected_gates"] == 0
    assert row["metrics_divergence"] is True
    assert "rejected_gates" in row["metrics_divergence_fields"]


def test_agentic_eval_runner_does_not_flag_consistent_quality_metrics(tmp_path):
    dataset = load_agentic_eval_dataset(FIXTURE)
    lead_arm = dataset["tasks"][0]["arms"]["lead_direct"]
    lead_arm["metrics"]["missed_issues"] = 0
    lead_arm["metrics"]["rejected_gates"] = 0
    path = tmp_path / "consistent-quality.json"
    path.write_text(json.dumps({"tasks": dataset["tasks"]}), encoding="utf-8")

    report = agentic_eval_runner(dataset_path=path)

    row = next(row for row in report["rows"] if row["task_id"] == "resume-drop-catchup" and row["mode"] == "lead_direct")
    assert row["missed_issues"] == 0
    assert row["rejected_gates"] == 0
    assert row["metrics_divergence"] is False
    assert row["metrics_divergence_fields"] == []
    assert "reported_missed_issues" not in row
    assert "reported_rejected_gates" not in row


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
    assert report["recommendation"]["report_only"] is True
    assert report["recommendation"]["policy_mutated"] is False
    for key in ("report_json", "evidence_json", "rows_jsonl", "replay_manifest"):
        assert key in report["exports"]
        assert (output_dir / report["exports"][key].split("/")[-1]).exists()
    saved = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["default_change_allowed"] is False
    assert saved["recommendation"]["report_only"] is True
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
