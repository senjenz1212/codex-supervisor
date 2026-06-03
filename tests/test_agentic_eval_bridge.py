from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from supervisor.agentic_eval import REQUIRED_MODES, agentic_eval_runner, load_agentic_eval_dataset
from supervisor.agentic_eval_assembler import (
    assert_equal_arm_total_budgets,
    assemble_agentic_eval_dataset,
    assemble_agentic_eval_task,
    compute_arm_budget_split,
    write_agentic_eval_dataset,
)
from supervisor.agentic_eval_corpus import load_agentic_eval_labeled_set


LABELED_SET = Path("tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml")


def test_agentic_eval_assembler_emits_runner_loadable_dataset(tmp_path):
    corpus = load_agentic_eval_labeled_set(LABELED_SET)
    case = corpus["tasks"][0]
    calls: list[tuple[str, str]] = []

    def fake_runner(*, task, mode, arm):
        calls.append((task["task_id"], mode))
        assert arm["workflow_request"]["agentic_lead_policy"] == {
            "lead_direct": "off",
            "agentic_allowed": "allowed",
            "agentic_required": "required",
        }[mode]
        return _workflow_result(mode)

    task = assemble_agentic_eval_task(
        case=case,
        workflow_runner=fake_runner,
        cassette_dir=tmp_path / "cassettes",
        repo_root=Path.cwd(),
    )
    dataset = {"schema_version": "agentic-lead-eval-dataset/v1", "tasks": [task]}
    dataset_path = write_agentic_eval_dataset(dataset, tmp_path / "dataset.yaml")
    report = agentic_eval_runner(dataset_path=dataset_path)

    assert [mode for _, mode in calls] == list(REQUIRED_MODES)
    assert set(task["arms"]) == set(REQUIRED_MODES)
    assert all(task["arms"][mode]["recording"]["hand_authored"] is False for mode in REQUIRED_MODES)
    assert len(report["rows"]) == 3
    assert {row["mode"] for row in report["rows"]} == set(REQUIRED_MODES)
    assert report["default_change_allowed"] is False


def test_agentic_eval_assembler_enforces_equal_total_budget_and_split(tmp_path):
    corpus = load_agentic_eval_labeled_set(LABELED_SET)
    case = corpus["tasks"][0]

    split = compute_arm_budget_split(
        mode="agentic_required",
        total_tokens=12000,
        total_usd=3.6,
        min_subagents=2,
    )
    assert split["worker_count"] == 2
    assert split["budget_sum_usd"] <= 3.6
    assert split["budget_sum_tokens"] <= 12000
    assert split["lead_budget_usd"] < 3.6
    assert split["worker_budget_usd_total"] > 0

    dataset = assemble_agentic_eval_dataset(
        cases=[case],
        workflow_runner=lambda *, task, mode, arm: _workflow_result(mode),
        cassette_dir=tmp_path / "cassettes",
        repo_root=Path.cwd(),
        min_subagents=2,
    )
    task = dataset["tasks"][0]
    budgets = {
        json.dumps(task["arms"][mode]["budget"], sort_keys=True)
        for mode in REQUIRED_MODES
    }
    assert len(budgets) == 1
    for mode in ("agentic_allowed", "agentic_required"):
        split = task["arms"][mode]["budget_split"]
        assert split["worker_count"] == 2
        assert split["budget_sum_usd"] <= task["arms"][mode]["budget"]["budget_usd_limit"]
        assert task["arms"][mode]["workflow_request"]["budget_usd"] == split["lead_budget_usd"]

    task["arms"]["agentic_required"]["budget"]["token_budget"] += 1
    with pytest.raises(ValueError, match="unequal arm budgets"):
        assert_equal_arm_total_budgets(task)


def test_agentic_eval_bridge_record_replay_is_deterministic(tmp_path):
    corpus = load_agentic_eval_labeled_set(LABELED_SET)
    dataset = assemble_agentic_eval_dataset(
        cases=corpus["tasks"][:2],
        workflow_runner=lambda *, task, mode, arm: _workflow_result(mode),
        cassette_dir=tmp_path / "cassettes",
        repo_root=Path.cwd(),
    )
    dataset_path = write_agentic_eval_dataset(dataset, tmp_path / "dataset.yaml")

    first = agentic_eval_runner(dataset_path=dataset_path, output_dir=tmp_path / "out-1")
    second = agentic_eval_runner(dataset_path=dataset_path, output_dir=tmp_path / "out-2")

    assert first["report_sha256"] == second["report_sha256"]
    loaded = load_agentic_eval_dataset(dataset_path)
    assert loaded["tasks"][0]["arms"]["lead_direct"]["workflow_result"]["status"] == "accepted"


def test_agentic_eval_live_cli_refuses_without_allow_live_calls(tmp_path):
    output_dir = tmp_path / "live"
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/run_agentic_eval_live.py",
            "--labeled-set",
            str(LABELED_SET),
            "--output-dir",
            str(output_dir),
            "--limit",
            "1",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    assert "refusing live workflow execution" in proc.stderr
    assert not (output_dir / "agentic_eval_three_arm_dataset.yaml").exists()


def test_agentic_eval_bridge_replay_does_not_call_live_runner(tmp_path):
    corpus = load_agentic_eval_labeled_set(LABELED_SET)
    dataset = assemble_agentic_eval_dataset(
        cases=corpus["tasks"][:1],
        workflow_runner=lambda *, task, mode, arm: _workflow_result(mode),
        cassette_dir=tmp_path / "cassettes",
        repo_root=Path.cwd(),
    )
    dataset_path = write_agentic_eval_dataset(dataset, tmp_path / "dataset.yaml")

    def forbidden_runner(**_kwargs):
        raise AssertionError("replay must not call live workflow runner")

    report = agentic_eval_runner(dataset_path=dataset_path, workflow_runner=forbidden_runner)

    assert report["runner"]["execution_mode"] == "fixture_replay"
    assert report["runner"]["workflow_runner_used"] is False


def test_agentic_eval_bridge_report_only_policy_snapshot(tmp_path):
    corpus = load_agentic_eval_labeled_set(LABELED_SET)
    dataset = assemble_agentic_eval_dataset(
        cases=corpus["tasks"][:1],
        workflow_runner=lambda *, task, mode, arm: _workflow_result(mode),
        cassette_dir=tmp_path / "cassettes",
        repo_root=Path.cwd(),
    )
    dataset_path = write_agentic_eval_dataset(dataset, tmp_path / "dataset.yaml")
    report = agentic_eval_runner(dataset_path=dataset_path, output_dir=tmp_path / "report")

    assert report["default_change_allowed"] is False
    assert report["agentic_lead_policy_snapshot"] == {
        "policy": "off",
        "mutated": False,
        "source": "report_only_runner_invariant",
    }
    assert report["report_only"] == {
        "default_change_allowed": False,
        "config_mutated": False,
        "policy_mutated": False,
    }


def test_agentic_eval_bridge_expected_accept_requires_terminal_accept(tmp_path):
    corpus = load_agentic_eval_labeled_set(LABELED_SET)
    clean_accept_case = corpus["tasks"][0]
    dataset = assemble_agentic_eval_dataset(
        cases=[clean_accept_case],
        workflow_runner=lambda *, task, mode, arm: _workflow_result(mode, status="blocked"),
        cassette_dir=tmp_path / "cassettes",
        repo_root=Path.cwd(),
    )
    dataset_path = write_agentic_eval_dataset(dataset, tmp_path / "dataset.yaml")

    report = agentic_eval_runner(dataset_path=dataset_path)

    assert {row["score"] for row in report["rows"]} == {0.0}
    assert {row["missed_issues"] for row in report["rows"]} == {len(clean_accept_case["required_verdicts"])}


def _workflow_result(mode: str, *, status: str = "accepted") -> dict:
    probes = {
        "P1": {"status": "green"},
        "P2": {"status": "green"},
        "P3": {"status": "green"},
    }
    if mode in {"agentic_allowed", "agentic_required"}:
        probes.update({
            "P13": {"status": "green"},
            "P14": {"status": "green"},
        })
    return {
        "status": status,
        "steps": [
            {"gate": "prd_review", "status": "accepted", "attempt_count": 1},
            {"gate": "issues_review", "status": "accepted", "attempt_count": 1},
            {"gate": "tdd_review", "status": "accepted", "attempt_count": 1},
            {"gate": "implementation_plan", "status": "accepted", "attempt_count": 1},
            {"gate": "execution", "status": "accepted", "attempt_count": 1},
            {"gate": "outcome_review", "status": "accepted", "attempt_count": 1},
        ],
        "final_gate_result": {
            "gate": "outcome_review",
            "status": status,
            "codex_decision": "accept",
            "claude_decision": "accept",
            "cursor_decision": "accept",
            "independent_reviewer_results": [
                {"reviewer_id": "independent-reviewer-0", "decision": "accept"},
                {"reviewer_id": "independent-reviewer-1", "decision": "accept"},
            ],
            "probes": probes,
        },
        "metrics": {
            "wall_clock_s": 12.0,
            "cost_usd": 0.25,
            "retries": 0,
            "operator_interventions": 0,
        },
    }
