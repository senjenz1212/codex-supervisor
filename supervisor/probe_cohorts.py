"""Deterministic aggregation for repeated live probe trials."""
from __future__ import annotations

from collections import Counter
from typing import Any


def summarize_probe_cohort(
    *,
    cohort_id: str,
    trial_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize N probe trials without changing supervisor policy."""
    trials = [_trial_summary(index, trial) for index, trial in enumerate(trial_summaries, start=1)]
    status_counts = Counter(str(trial["status"]) for trial in trials)
    mast_counts = Counter(
        str(trial["mast_code"])
        for trial in trials
        if trial.get("mast_code")
    )
    total_cost = round(sum(float(trial.get("cost_usd") or 0.0) for trial in trials), 6)
    totals = {
        "cost_usd": total_cost,
        "tokens_in": sum(int(trial.get("tokens_in") or 0) for trial in trials),
        "tokens_out": sum(int(trial.get("tokens_out") or 0) for trial in trials),
    }
    return {
        "schema_version": "dual-agent-probe-cohort/v1",
        "cohort_id": cohort_id,
        "trial_count": len(trials),
        "classification": _classify(status_counts, len(trials)),
        "status_counts": dict(sorted(status_counts.items())),
        "blocked_as_expected_count": status_counts.get("blocked_as_expected", 0),
        "unexpected_count": status_counts.get("unexpected", 0),
        "failure_counts_by_mast_code": dict(sorted(mast_counts.items())),
        "totals": totals,
        "worst_case_observed": {
            "cost_usd": max((float(trial.get("cost_usd") or 0.0) for trial in trials), default=0.0),
            "tokens_in": max((int(trial.get("tokens_in") or 0) for trial in trials), default=0),
            "tokens_out": max((int(trial.get("tokens_out") or 0) for trial in trials), default=0),
        },
        "trials": trials,
    }


def _trial_summary(index: int, trial: dict[str, Any]) -> dict[str, Any]:
    claude = trial.get("claude") if isinstance(trial.get("claude"), dict) else {}
    cursor = trial.get("cursor") if isinstance(trial.get("cursor"), dict) else {}
    artifact_export = trial.get("artifact_export") if isinstance(trial.get("artifact_export"), dict) else {}
    failure_taxonomy = trial.get("failure_taxonomy") if isinstance(trial.get("failure_taxonomy"), dict) else {}
    artifact_dir = str(artifact_export.get("output_dir") or trial.get("artifact_dir") or "")
    return {
        "trial_index": index,
        "trial_id": str(trial.get("trial_id") or trial.get("task_id") or f"trial-{index}"),
        "task_id": str(trial.get("task_id") or trial.get("trial_id") or ""),
        "run_id": str(trial.get("run_id") or trial.get("trial_id") or ""),
        "status": str(trial.get("status") or "unknown"),
        "mast_code": failure_taxonomy.get("mast_code"),
        "artifact_dir": artifact_dir,
        "manifest_path": f"{artifact_dir}/replay/manifest.json" if artifact_dir else "",
        "fixture_dir": str(trial.get("fixture_dir") or ""),
        "cost_usd": float(claude.get("cost_usd") or 0.0),
        "tokens_in": int(claude.get("tokens_in") or 0),
        "tokens_out": int(claude.get("tokens_out") or 0),
        "cursor_accepted": cursor.get("accepted"),
    }


def _classify(status_counts: Counter[str], trial_count: int) -> str:
    if trial_count == 0:
        return "EMPTY"
    if len(status_counts) == 1:
        return "STABLE"
    majority = max(status_counts.values())
    if trial_count - majority == 1:
        return "DRIFT-1"
    return "FLIPPING"
