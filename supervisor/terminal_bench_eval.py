"""Report-only Terminal-Bench pilot evaluation helpers."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import yaml


PILOT_SAMPLE_SCHEMA_VERSION = "terminal-bench-pilot-sample/v1"
PILOT_RESULTS_SCHEMA_VERSION = "terminal-bench-pilot-results/v1"
PILOT_REPORT_SCHEMA_VERSION = "terminal-bench-pilot-report/v1"
DEFAULT_DATASET = "terminal-bench/terminal-bench-2-1"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_K = 5
DEFAULT_NOISE_FLOOR = 0.03
HARNESS_AGENT_IMPORT_PATH = (
    "supervisor.terminal_bench_harbor_agent:CodexSupervisorTerminalBenchAgent"
)
EXTERNAL_REFERENCES = {
    "terminal_bench_2_1_gpt55_codex": {
        "score": 0.834,
        "note": "Published reference only; different harness/full-set context.",
    },
    "terminal_bench_2_1_opus48": {
        "score": 0.789,
        "note": "Published reference only; not a pilot baseline.",
    },
}


def load_terminal_bench_pilot_sample(path: str | Path) -> dict[str, Any]:
    raw = _load_mapping(path)
    if raw.get("schema_version") != PILOT_SAMPLE_SCHEMA_VERSION:
        raise ValueError("terminal-bench pilot sample schema_version mismatch")
    task_ids = [str(item).strip() for item in raw.get("task_ids") or [] if str(item).strip()]
    if len(task_ids) != int(raw.get("sample_size") or 0):
        raise ValueError("terminal-bench pilot sample task count does not match sample_size")
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("terminal-bench pilot sample task ids must be unique")
    if len(task_ids) < 1:
        raise ValueError("terminal-bench pilot sample requires task ids")
    if int(raw.get("k") or 0) <= 0:
        raise ValueError("terminal-bench pilot sample requires positive k")
    return {
        "schema_version": raw["schema_version"],
        "dataset": str(raw.get("dataset") or DEFAULT_DATASET),
        "dataset_version": str(raw.get("dataset_version") or ""),
        "source_task_count": int(raw.get("source_task_count") or len(task_ids)),
        "seed": int(raw.get("seed") or 0),
        "sample_size": int(raw.get("sample_size") or len(task_ids)),
        "k": int(raw.get("k") or DEFAULT_K),
        "model": str(raw.get("model") or DEFAULT_MODEL),
        "baseline_agent": str(raw.get("baseline_agent") or "terminus-2"),
        "harness_agent_import_path": str(
            raw.get("harness_agent_import_path") or HARNESS_AGENT_IMPORT_PATH
        ),
        "task_ids": task_ids,
    }


def load_terminal_bench_results(path: str | Path) -> dict[str, Any]:
    raw = _load_mapping(path)
    if raw.get("schema_version") != PILOT_RESULTS_SCHEMA_VERSION:
        raise ValueError("terminal-bench pilot results schema_version mismatch")
    arms = raw.get("arms")
    if not isinstance(arms, dict) or not {"baseline", "harness"}.issubset(arms):
        raise ValueError("terminal-bench pilot results require baseline and harness arms")
    return raw


def build_terminal_bench_report(
    *,
    sample: dict[str, Any],
    results: dict[str, Any],
    noise_floor: float = DEFAULT_NOISE_FLOOR,
) -> dict[str, Any]:
    """Build a report-only harness-vs-Terminus2 pilot summary."""
    task_ids = list(sample["task_ids"])
    k = int(sample["k"])
    arms = {
        arm_name: _summarize_arm(
            arm_name=arm_name,
            arm=results["arms"][arm_name],
            task_ids=task_ids,
            k=k,
        )
        for arm_name in ("baseline", "harness")
    }
    delta = arms["harness"]["pass_at_1_mean"] - arms["baseline"]["pass_at_1_mean"]
    delta_ci = _difference_ci(
        arms["harness"]["success_count"],
        arms["harness"]["trial_count"],
        arms["baseline"]["success_count"],
        arms["baseline"]["trial_count"],
    )
    report = {
        "schema_version": PILOT_REPORT_SCHEMA_VERSION,
        "dataset": sample["dataset"],
        "model": sample["model"],
        "seed": sample["seed"],
        "k": k,
        "task_count": len(task_ids),
        "task_ids": task_ids,
        "arms": arms,
        "delta": {
            "harness_minus_baseline_pass_at_1": round(delta, 6),
            "ci95": [round(delta_ci[0], 6), round(delta_ci[1], 6)],
        },
        "noise_floor": {
            "threshold": noise_floor,
            "point_estimate_clears": delta >= noise_floor,
            "ci_lower_clears": delta_ci[0] >= noise_floor,
            "verdict": (
                "clears_noise_floor"
                if delta_ci[0] >= noise_floor
                else "inconclusive_or_null"
            ),
        },
        "external_references": EXTERNAL_REFERENCES,
        "recommendation": {
            "report_only": True,
            "scale_to_full_set": bool(delta_ci[0] >= noise_floor),
            "policy_mutated": False,
        },
        "default_change_allowed": False,
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
    }
    report["report_sha256"] = _sha256_json(_without_report_sha(report))
    return report


def build_terminal_bench_pilot_plan(
    *,
    sample: dict[str, Any],
    output_dir: str | Path,
    allow_live: bool = False,
    max_budget_usd: float = 0.0,
    harbor_executable: str = "harbor",
) -> dict[str, Any]:
    if allow_live and float(max_budget_usd) <= 0:
        raise ValueError("live Terminal-Bench pilot requires max_budget_usd > 0")
    output = Path(output_dir)
    baseline_dir = output / "harbor-jobs" / "baseline-terminus-2"
    harness_dir = output / "harbor-jobs" / "harness-codex-supervisor"
    common = [
        harbor_executable,
        "run",
        "-d",
        sample["dataset"],
        "-m",
        sample["model"],
        "-k",
        str(sample["k"]),
    ]
    includes = _include_task_args(sample["task_ids"])
    baseline_command = [
        *common,
        "-a",
        sample["baseline_agent"],
        "--jobs-dir",
        str(baseline_dir),
        *includes,
    ]
    harness_command = [
        *common,
        "--agent-import-path",
        sample["harness_agent_import_path"],
        "--agent-kwarg",
        "dry_run=false",
        "--agent-kwarg",
        f"max_budget_usd={float(max_budget_usd):.6f}",
        "--jobs-dir",
        str(harness_dir),
        *includes,
    ]
    planned_runs = len(sample["task_ids"]) * int(sample["k"]) * 2
    return {
        "schema_version": "terminal-bench-pilot-plan/v1",
        "dataset": sample["dataset"],
        "model": sample["model"],
        "seed": sample["seed"],
        "task_ids": sample["task_ids"],
        "k": sample["k"],
        "planned_runs": planned_runs,
        "allow_live": bool(allow_live),
        "max_budget_usd": float(max_budget_usd),
        "worst_case_budget_usd": round(planned_runs * float(max_budget_usd), 6),
        "commands": {
            "baseline": baseline_command,
            "harness": harness_command,
        },
        "outputs": {
            "baseline_jobs_dir": str(baseline_dir),
            "harness_jobs_dir": str(harness_dir),
            "plan_json": str(output / "pilot-plan.json"),
            "report_json": str(output / "report.json"),
        },
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
    }


def run_terminal_bench_pilot_plan(plan: dict[str, Any]) -> list[subprocess.CompletedProcess[str]]:
    if not plan.get("allow_live"):
        raise RuntimeError("refusing to run Terminal-Bench pilot without allow_live")
    if float(plan.get("max_budget_usd") or 0.0) <= 0:
        raise RuntimeError("refusing to run Terminal-Bench pilot without max_budget_usd")
    results = []
    for arm_name in ("baseline", "harness"):
        command = plan["commands"][arm_name]
        results.append(subprocess.run(command, text=True, capture_output=True, check=False))
    return results


def export_terminal_bench_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows_path = output / "rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as f:
        for arm_name, arm in report["arms"].items():
            for row in arm["tasks"]:
                f.write(json.dumps({"arm": arm_name, **row}, sort_keys=True) + "\n")
    return {
        "report_json": str(report_path),
        "rows_jsonl": str(rows_path),
    }


def _summarize_arm(
    *,
    arm_name: str,
    arm: dict[str, Any],
    task_ids: list[str],
    k: int,
) -> dict[str, Any]:
    by_task = {str(row.get("task_id")): row for row in arm.get("results") or []}
    tasks: list[dict[str, Any]] = []
    success_count = 0
    trial_count = 0
    for task_id in task_ids:
        row = by_task.get(task_id)
        if row is None:
            raise ValueError(f"terminal-bench results missing {arm_name} task {task_id}")
        attempts = int(row.get("attempts") or k)
        successes = int(row.get("successes") or 0)
        if attempts != k:
            raise ValueError(f"terminal-bench task {task_id} has attempts={attempts}, expected {k}")
        if successes < 0 or successes > attempts:
            raise ValueError(f"terminal-bench task {task_id} has invalid successes={successes}")
        success_count += successes
        trial_count += attempts
        tasks.append({
            "task_id": task_id,
            "successes": successes,
            "attempts": attempts,
            "pass_at_1": round(successes / attempts, 6),
            "pass_at_5": successes > 0,
            "pass_caret_5": successes == attempts,
        })

    pass_at_1 = success_count / max(1, trial_count)
    ci = _wilson_ci(success_count, trial_count)
    pass_at_5 = sum(1 for task in tasks if task["pass_at_5"]) / max(1, len(tasks))
    pass_caret_5 = sum(1 for task in tasks if task["pass_caret_5"]) / max(1, len(tasks))
    return {
        "agent": arm.get("agent") or arm_name,
        "task_count": len(tasks),
        "trial_count": trial_count,
        "success_count": success_count,
        "pass_at_1_mean": round(pass_at_1, 6),
        "pass_at_1_ci95": [round(ci[0], 6), round(ci[1], 6)],
        "pass_at_5": round(pass_at_5, 6),
        "pass_caret_5": round(pass_caret_5, 6),
        "tasks": tasks,
    }


def _wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _difference_ci(
    harness_successes: int,
    harness_total: int,
    baseline_successes: int,
    baseline_total: int,
    z: float = 1.96,
) -> tuple[float, float]:
    ph = harness_successes / max(1, harness_total)
    pb = baseline_successes / max(1, baseline_total)
    delta = ph - pb
    se = math.sqrt(
        (ph * (1 - ph) / max(1, harness_total))
        + (pb * (1 - pb) / max(1, baseline_total))
    )
    return (max(-1.0, delta - z * se), min(1.0, delta + z * se))


def _include_task_args(task_ids: list[str]) -> list[str]:
    args: list[str] = []
    for task_id in task_ids:
        args.extend(["--include-task-name", task_id])
    return args


def _load_mapping(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) if file_path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{file_path} must contain an object")
    return raw


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _without_report_sha(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "report_sha256"}
