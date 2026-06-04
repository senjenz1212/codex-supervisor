from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from supervisor.config import AgenticLeadCfg
from supervisor.dual_agent_lead import CLAUDE_OPUS_UNDERLYING_MODEL
from supervisor.swe_bench_eval import (
    build_swe_bench_pilot_plan,
    build_swe_bench_report,
    load_swe_bench_pilot_sample,
    load_swe_bench_results,
)
from supervisor.swe_bench_solver import (
    SweBenchInstance,
    capture_model_patch,
    evaluator_patch_row,
    write_model_patch_jsonl,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "swe_bench_pro"
SAMPLE = FIXTURE_ROOT / "pilot_sample.yaml"
RESULTS = FIXTURE_ROOT / "pilot_results.json"


def test_swe_bench_sample_loads_fixed_seed_manifest():
    sample = load_swe_bench_pilot_sample(SAMPLE)

    assert sample["dataset"] == "ScaleAI/SWE-bench_Pro"
    assert sample["dataset_split"] == "test"
    assert sample["source_instance_count"] == 731
    assert sample["seed"] == 20260603
    assert sample["model"] == "claude-opus-4-8"
    assert sample["k"] == 5
    assert len(sample["instance_ids"]) == 30
    assert len(set(sample["instance_ids"])) == 30
    assert sample["instance_ids"][0].startswith("instance_NodeBB__NodeBB-397835")
    assert sample["instance_ids"][-1].startswith("instance_qutebrowser__qutebrowser-36ade")


def test_swe_bench_report_computes_pass_metrics_and_noise_floor():
    report = build_swe_bench_report(
        sample=load_swe_bench_pilot_sample(SAMPLE),
        results=load_swe_bench_results(RESULTS),
    )

    assert report["arms"]["baseline"]["resolved_count"] == 96
    assert report["arms"]["baseline"]["trial_count"] == 150
    assert report["arms"]["baseline"]["pass_at_1_mean"] == pytest.approx(0.64)
    assert report["arms"]["baseline"]["pass_at_5"] == 1.0
    assert report["arms"]["baseline"]["pass_caret_5"] == 0.2
    assert report["arms"]["harness"]["resolved_count"] == 105
    assert report["arms"]["harness"]["pass_at_1_mean"] == pytest.approx(0.7)
    assert report["delta"]["harness_minus_baseline_pass_at_1"] == pytest.approx(0.06)
    assert report["noise_floor"]["point_estimate_clears"] is True
    assert report["noise_floor"]["ci_lower_clears"] is False
    assert report["noise_floor"]["verdict"] == "inconclusive_or_null"
    assert report["external_references"]["opus48_published_full_set"]["score"] == 0.692
    assert len(report["report_sha256"]) == 64


def test_swe_bench_report_is_report_only():
    before = AgenticLeadCfg().model_dump()
    report = build_swe_bench_report(
        sample=load_swe_bench_pilot_sample(SAMPLE),
        results=load_swe_bench_results(RESULTS),
    )
    after = AgenticLeadCfg().model_dump()

    assert after == before
    assert report["default_change_allowed"] is False
    assert report["report_only"] == {
        "default_change_allowed": False,
        "config_mutated": False,
        "policy_mutated": False,
    }
    assert report["recommendation"]["policy_mutated"] is False


def test_swe_bench_solver_captures_model_patch_jsonl(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    (repo / "module.py").write_text("value = 2\n", encoding="utf-8")

    patch = capture_model_patch(repo)
    out = tmp_path / "predictions.jsonl"
    row = write_model_patch_jsonl(
        out,
        instance_id="instance_demo__repo-123",
        model_patch=patch,
    )

    assert row["instance_id"] == "instance_demo__repo-123"
    assert "model_patch" in row
    assert "+value = 2" in row["model_patch"]
    assert json.loads(out.read_text(encoding="utf-8").strip()) == row
    assert evaluator_patch_row(row, prefix="harness") == {
        "instance_id": "instance_demo__repo-123",
        "patch": row["model_patch"],
        "prefix": "harness",
    }


def test_swe_bench_solver_rejects_missing_instance_id(tmp_path):
    with pytest.raises(ValueError, match="instance_id"):
        write_model_patch_jsonl(tmp_path / "predictions.jsonl", instance_id="", model_patch="diff")


def test_swe_bench_pilot_plan_uses_same_model_budget_and_opus48_route(tmp_path):
    sample = load_swe_bench_pilot_sample(SAMPLE)

    plan = build_swe_bench_pilot_plan(
        sample=sample,
        output_dir=tmp_path,
        allow_live=False,
        max_budget_usd=2.5,
        python_executable="python",
    )

    assert CLAUDE_OPUS_UNDERLYING_MODEL == "claude-opus-4-8"
    assert plan["planned_runs"] == 300
    assert plan["model"] == "claude-opus-4-8"
    assert plan["lead_model_alias"] == "opus"
    assert plan["underlying_lead_model"] == "claude-opus-4-8"
    assert plan["per_run_budget_usd"] == 2.5
    assert plan["commands"]["baseline"][0:3] == ["python", "-m", "supervisor.swe_bench_solver"]
    assert "mini-swe-agent" in plan["commands"]["baseline"]
    assert "codex-supervisor-dual-agent" in plan["commands"]["harness"]
    assert plan["commands"]["baseline"].count("--instance-id") == 30
    assert plan["commands"]["harness"].count("--instance-id") == 30
    assert plan["report_only"]["default_change_allowed"] is False


def test_swe_bench_pilot_script_refuses_live_without_budget(tmp_path):
    sample = load_swe_bench_pilot_sample(SAMPLE)

    with pytest.raises(ValueError, match="max_budget_usd"):
        build_swe_bench_pilot_plan(
            sample=sample,
            output_dir=tmp_path,
            allow_live=True,
            max_budget_usd=0.0,
        )

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/run_swe_bench_pro_pilot.py",
            "--sample",
            str(SAMPLE),
            "--output-dir",
            str(tmp_path),
            "--run-live",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "without --allow-live" in result.stderr


def test_swe_bench_pilot_script_builds_replay_report(tmp_path):
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/run_swe_bench_pro_pilot.py",
            "--sample",
            str(SAMPLE),
            "--results",
            str(RESULTS),
            "--output-dir",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "reported"
    assert summary["planned_runs"] == 300
    assert summary["noise_floor"]["verdict"] == "inconclusive_or_null"
    assert (tmp_path / "pilot-plan.json").exists()
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "rows.jsonl").exists()
