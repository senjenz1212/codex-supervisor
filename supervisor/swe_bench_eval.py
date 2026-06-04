"""Report-only SWE-bench Pro pilot evaluation helpers."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .config import AgenticLeadCfg
from .dual_agent_lead import CLAUDE_OPUS_UNDERLYING_MODEL


PILOT_SAMPLE_SCHEMA_VERSION = "swe-bench-pro-pilot-sample/v1"
PILOT_RESULTS_SCHEMA_VERSION = "swe-bench-pro-pilot-results/v1"
PILOT_REPORT_SCHEMA_VERSION = "swe-bench-pro-pilot-report/v1"
DEFAULT_DATASET = "ScaleAI/SWE-bench_Pro"
DEFAULT_DATASET_SPLIT = "test"
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_LEAD_MODEL_ALIAS = "opus"
DEFAULT_K = 5
DEFAULT_NOISE_FLOOR = 0.03
EXTERNAL_REFERENCES = {
    "opus48_published_full_set": {
        "score": 0.692,
        "note": "Published reference only; different harness/full-set context.",
    },
}


def load_swe_bench_pilot_sample(path: str | Path) -> dict[str, Any]:
    raw = _load_mapping(path)
    if raw.get("schema_version") != PILOT_SAMPLE_SCHEMA_VERSION:
        raise ValueError("SWE-bench Pro pilot sample schema_version mismatch")
    instance_ids = [
        str(item).strip()
        for item in raw.get("instance_ids") or []
        if str(item).strip()
    ]
    if len(instance_ids) != int(raw.get("sample_size") or 0):
        raise ValueError("SWE-bench Pro pilot sample instance count does not match sample_size")
    if len(instance_ids) != len(set(instance_ids)):
        raise ValueError("SWE-bench Pro pilot sample instance ids must be unique")
    if len(instance_ids) < 1:
        raise ValueError("SWE-bench Pro pilot sample requires instance ids")
    if int(raw.get("k") or 0) <= 0:
        raise ValueError("SWE-bench Pro pilot sample requires positive k")
    return {
        "schema_version": raw["schema_version"],
        "dataset": str(raw.get("dataset") or DEFAULT_DATASET),
        "dataset_split": str(raw.get("dataset_split") or DEFAULT_DATASET_SPLIT),
        "source_instance_count": int(raw.get("source_instance_count") or len(instance_ids)),
        "seed": int(raw.get("seed") or 0),
        "sample_size": int(raw.get("sample_size") or len(instance_ids)),
        "k": int(raw.get("k") or DEFAULT_K),
        "model": str(raw.get("model") or DEFAULT_MODEL),
        "baseline_solver": str(raw.get("baseline_solver") or "mini-swe-agent"),
        "harness_solver": str(raw.get("harness_solver") or "codex-supervisor-dual-agent"),
        "instance_ids": instance_ids,
    }


def load_swe_bench_results(path: str | Path) -> dict[str, Any]:
    raw = _load_mapping(path)
    if raw.get("schema_version") != PILOT_RESULTS_SCHEMA_VERSION:
        raise ValueError("SWE-bench Pro pilot results schema_version mismatch")
    arms = raw.get("arms")
    if not isinstance(arms, dict) or not {"baseline", "harness"}.issubset(arms):
        raise ValueError("SWE-bench Pro pilot results require baseline and harness arms")
    return raw


def build_swe_bench_report(
    *,
    sample: dict[str, Any],
    results: dict[str, Any],
    noise_floor: float = DEFAULT_NOISE_FLOOR,
) -> dict[str, Any]:
    """Build a report-only harness-vs-mini-swe-agent pilot summary."""
    instance_ids = list(sample["instance_ids"])
    k = int(sample["k"])
    arms = {
        arm_name: _summarize_arm(
            arm_name=arm_name,
            arm=results["arms"][arm_name],
            instance_ids=instance_ids,
            k=k,
        )
        for arm_name in ("baseline", "harness")
    }
    delta = arms["harness"]["pass_at_1_mean"] - arms["baseline"]["pass_at_1_mean"]
    delta_ci = _difference_ci(
        arms["harness"]["resolved_count"],
        arms["harness"]["trial_count"],
        arms["baseline"]["resolved_count"],
        arms["baseline"]["trial_count"],
    )
    report = {
        "schema_version": PILOT_REPORT_SCHEMA_VERSION,
        "dataset": sample["dataset"],
        "dataset_split": sample["dataset_split"],
        "model": sample["model"],
        "seed": sample["seed"],
        "k": k,
        "instance_count": len(instance_ids),
        "instance_ids": instance_ids,
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


def build_swe_bench_pilot_plan(
    *,
    sample: dict[str, Any],
    output_dir: str | Path,
    allow_live: bool = False,
    max_budget_usd: float = 0.0,
    python_executable: str = "python",
) -> dict[str, Any]:
    if allow_live and float(max_budget_usd) <= 0:
        raise ValueError("live SWE-bench Pro pilot requires max_budget_usd > 0")
    if sample["model"] != DEFAULT_MODEL:
        raise ValueError(f"SWE-bench Pro pilot requires model {DEFAULT_MODEL}")
    if CLAUDE_OPUS_UNDERLYING_MODEL != DEFAULT_MODEL:
        raise ValueError(
            "codex-supervisor lead Opus route is not configured to claude-opus-4-8"
        )

    output = Path(output_dir)
    predictions_dir = output / "predictions"
    baseline_predictions = predictions_dir / "baseline-mini-swe-agent.jsonl"
    harness_predictions = predictions_dir / "harness-codex-supervisor.jsonl"
    common = [
        python_executable,
        "-m",
        "supervisor.swe_bench_solver",
        "--dataset",
        sample["dataset"],
        "--dataset-split",
        sample["dataset_split"],
        "--model",
        sample["model"],
        "--k",
        str(sample["k"]),
        "--max-budget-usd",
        f"{float(max_budget_usd):.6f}",
    ]
    instance_args = _instance_args(sample["instance_ids"])
    baseline_command = [
        *common,
        "--solver",
        sample["baseline_solver"],
        "--output",
        str(baseline_predictions),
        *instance_args,
    ]
    harness_command = [
        *common,
        "--solver",
        sample["harness_solver"],
        "--lead-model-alias",
        DEFAULT_LEAD_MODEL_ALIAS,
        "--underlying-lead-model",
        CLAUDE_OPUS_UNDERLYING_MODEL,
        "--output",
        str(harness_predictions),
        *instance_args,
    ]
    planned_runs = len(sample["instance_ids"]) * int(sample["k"]) * 2
    agentic_defaults = AgenticLeadCfg().model_dump()
    return {
        "schema_version": "swe-bench-pro-pilot-plan/v1",
        "dataset": sample["dataset"],
        "dataset_split": sample["dataset_split"],
        "model": sample["model"],
        "lead_model_alias": DEFAULT_LEAD_MODEL_ALIAS,
        "underlying_lead_model": CLAUDE_OPUS_UNDERLYING_MODEL,
        "seed": sample["seed"],
        "instance_ids": sample["instance_ids"],
        "k": sample["k"],
        "planned_runs": planned_runs,
        "allow_live": bool(allow_live),
        "per_run_budget_usd": float(max_budget_usd),
        "worst_case_budget_usd": round(planned_runs * float(max_budget_usd), 6),
        "commands": {
            "baseline": baseline_command,
            "harness": harness_command,
        },
        "outputs": {
            "baseline_predictions_jsonl": str(baseline_predictions),
            "harness_predictions_jsonl": str(harness_predictions),
            "pilot_plan_json": str(output / "pilot-plan.json"),
            "report_json": str(output / "report.json"),
        },
        "agentic_lead_defaults_observed": agentic_defaults,
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
    }


def run_swe_bench_pilot_plan(plan: dict[str, Any]) -> list[subprocess.CompletedProcess[str]]:
    if not plan.get("allow_live"):
        raise RuntimeError("refusing to run SWE-bench Pro pilot without allow_live")
    if float(plan.get("per_run_budget_usd") or 0.0) <= 0:
        raise RuntimeError("refusing to run SWE-bench Pro pilot without max_budget_usd")
    results = []
    for arm_name in ("baseline", "harness"):
        command = plan["commands"][arm_name]
        results.append(subprocess.run(command, text=True, capture_output=True, check=False))
    return results


def export_swe_bench_report(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows_path = output / "rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as f:
        for arm_name, arm in report["arms"].items():
            for row in arm["instances"]:
                f.write(json.dumps({"arm": arm_name, **row}, sort_keys=True) + "\n")
    return {
        "report_json": str(report_path),
        "rows_jsonl": str(rows_path),
    }


def _summarize_arm(
    *,
    arm_name: str,
    arm: dict[str, Any],
    instance_ids: list[str],
    k: int,
) -> dict[str, Any]:
    by_instance = {str(row.get("instance_id")): row for row in arm.get("results") or []}
    instances: list[dict[str, Any]] = []
    resolved_count = 0
    trial_count = 0
    for instance_id in instance_ids:
        row = by_instance.get(instance_id)
        if row is None:
            raise ValueError(f"SWE-bench Pro results missing {arm_name} instance {instance_id}")
        attempts = int(row.get("attempts") or k)
        resolved = int(row.get("resolved") or 0)
        if attempts != k:
            raise ValueError(f"SWE-bench Pro instance {instance_id} has attempts={attempts}, expected {k}")
        if resolved < 0 or resolved > attempts:
            raise ValueError(f"SWE-bench Pro instance {instance_id} has invalid resolved={resolved}")
        resolved_count += resolved
        trial_count += attempts
        instances.append({
            "instance_id": instance_id,
            "resolved": resolved,
            "attempts": attempts,
            "pass_at_1": round(resolved / attempts, 6),
            "pass_at_5": resolved > 0,
            "pass_caret_5": resolved == attempts,
        })

    pass_at_1 = resolved_count / max(1, trial_count)
    ci = _wilson_ci(resolved_count, trial_count)
    pass_at_5 = sum(1 for instance in instances if instance["pass_at_5"]) / max(1, len(instances))
    pass_caret_5 = sum(1 for instance in instances if instance["pass_caret_5"]) / max(1, len(instances))
    return {
        "solver": arm.get("solver") or arm_name,
        "instance_count": len(instances),
        "trial_count": trial_count,
        "resolved_count": resolved_count,
        "pass_at_1_mean": round(pass_at_1, 6),
        "pass_at_1_ci95": [round(ci[0], 6), round(ci[1], 6)],
        "pass_at_5": round(pass_at_5, 6),
        "pass_caret_5": round(pass_caret_5, 6),
        "instances": instances,
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


def _instance_args(instance_ids: list[str]) -> list[str]:
    args: list[str] = []
    for instance_id in instance_ids:
        args.extend(["--instance-id", instance_id])
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
