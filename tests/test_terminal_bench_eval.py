from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from supervisor.config import AgenticLeadCfg
from supervisor.terminal_bench_eval import (
    HARNESS_AGENT_IMPORT_PATH,
    build_terminal_bench_pilot_plan,
    build_terminal_bench_report,
    load_terminal_bench_pilot_sample,
    load_terminal_bench_results,
)
from supervisor.terminal_bench_harbor_agent import CodexSupervisorTerminalBenchAgent


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "terminal_bench"
SAMPLE = FIXTURE_ROOT / "pilot_sample.yaml"
RESULTS = FIXTURE_ROOT / "pilot_results.json"


def test_terminal_bench_pilot_sample_loads_fixed_seed_manifest():
    sample = load_terminal_bench_pilot_sample(SAMPLE)

    assert sample["dataset"] == "terminal-bench/terminal-bench-2-1"
    assert sample["seed"] == 20260603
    assert sample["model"] == "gpt-5.5"
    assert sample["k"] == 5
    assert len(sample["task_ids"]) == 30
    assert len(set(sample["task_ids"])) == 30
    assert sample["task_ids"][0] == "build-cython-ext"
    assert sample["task_ids"][-1] == "winning-avg-corewars"


def test_terminal_bench_report_computes_pass_metrics_and_noise_floor():
    sample = load_terminal_bench_pilot_sample(SAMPLE)
    results = load_terminal_bench_results(RESULTS)

    report = build_terminal_bench_report(sample=sample, results=results)

    assert report["arms"]["baseline"]["success_count"] == 100
    assert report["arms"]["baseline"]["trial_count"] == 150
    assert report["arms"]["baseline"]["pass_at_1_mean"] == pytest.approx(0.666667)
    assert report["arms"]["baseline"]["pass_at_5"] == 1.0
    assert report["arms"]["baseline"]["pass_caret_5"] == 0.1
    assert report["arms"]["harness"]["success_count"] == 108
    assert report["arms"]["harness"]["pass_at_1_mean"] == pytest.approx(0.72)
    assert report["delta"]["harness_minus_baseline_pass_at_1"] == pytest.approx(0.053333)
    assert report["noise_floor"]["point_estimate_clears"] is True
    assert report["noise_floor"]["ci_lower_clears"] is False
    assert report["noise_floor"]["verdict"] == "inconclusive_or_null"
    assert len(report["report_sha256"]) == 64


def test_terminal_bench_report_is_report_only():
    before = AgenticLeadCfg().model_dump()
    report = build_terminal_bench_report(
        sample=load_terminal_bench_pilot_sample(SAMPLE),
        results=load_terminal_bench_results(RESULTS),
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


def test_terminal_bench_harbor_agent_dry_run_records_context(tmp_path):
    class Result:
        stdout = "codex-supervisor-terminal-bench-agent"
        stderr = ""
        return_code = 0

    class FakeEnvironment:
        def __init__(self) -> None:
            self.commands: list[tuple[str, int | None]] = []

        def exec(self, command: str, timeout_sec: int | None = None):
            self.commands.append((command, timeout_sec))
            return Result()

    class Context:
        cost_usd = None
        metadata = None

    env = FakeEnvironment()
    context = Context()
    agent = CodexSupervisorTerminalBenchAgent(
        logs_dir=tmp_path,
        model_name="gpt-5.5",
        dry_run=True,
    )

    asyncio.run(agent.setup(env))
    asyncio.run(agent.run("solve this task", env, context))  # type: ignore[arg-type]

    assert env.commands == [("printf codex-supervisor-terminal-bench-agent", 10)]
    assert context.cost_usd == 0.0
    assert context.metadata["model"] == "gpt-5.5"
    assert context.metadata["workflow_status"] == "dry_run"
    assert (tmp_path / "terminal_bench_agent_run.json").exists()


def test_terminal_bench_pilot_script_refuses_live_without_budget(tmp_path):
    sample = load_terminal_bench_pilot_sample(SAMPLE)

    with pytest.raises(ValueError, match="max_budget_usd"):
        build_terminal_bench_pilot_plan(
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
            "scripts/run_terminal_bench_pilot.py",
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


def test_terminal_bench_pilot_script_builds_harbor_commands(tmp_path):
    sample = load_terminal_bench_pilot_sample(SAMPLE)

    plan = build_terminal_bench_pilot_plan(
        sample=sample,
        output_dir=tmp_path,
        allow_live=False,
        max_budget_usd=0.0,
        harbor_executable="harbor",
    )

    assert plan["planned_runs"] == 300
    assert plan["commands"]["baseline"][:7] == [
        "harbor",
        "run",
        "-d",
        "terminal-bench/terminal-bench-2-1",
        "-m",
        "gpt-5.5",
        "-k",
    ]
    assert "terminus-2" in plan["commands"]["baseline"]
    assert "--agent-import-path" in plan["commands"]["harness"]
    assert HARNESS_AGENT_IMPORT_PATH in plan["commands"]["harness"]
    assert plan["commands"]["baseline"].count("--include-task-name") == 30
    assert plan["commands"]["harness"].count("--include-task-name") == 30
    assert plan["report_only"]["default_change_allowed"] is False
