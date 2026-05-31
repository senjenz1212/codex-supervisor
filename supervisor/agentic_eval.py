"""Evaluation reports for making agentic lead policy changes empirical."""
from __future__ import annotations

from typing import Any


_NUMERIC_FIELDS = (
    "wall_clock_s",
    "cost_usd",
    "retries",
    "rejected_gates",
    "missed_issues",
    "operator_interventions",
)


def build_agentic_eval_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate historical lead-direct versus agentic policy runs.

    The report is deliberately conservative: it never authorizes a default
    policy change by itself. Operators need explicit review over the summary and
    the raw rows before moving agentic modes beyond opt-in.
    """
    normalized_rows = [_normalise_row(row) for row in rows]
    summary: dict[str, dict[str, Any]] = {}
    for row in normalized_rows:
        mode = str(row.get("mode") or "unknown")
        bucket = summary.setdefault(mode, {
            "task_count": 0,
            "wall_clock_s": 0.0,
            "cost_usd": 0.0,
            "retries": 0,
            "rejected_gates": 0,
            "missed_issues": 0,
            "operator_interventions": 0,
        })
        bucket["task_count"] += 1
        for field in _NUMERIC_FIELDS:
            bucket[field] += row[field]

    for bucket in summary.values():
        task_count = max(1, int(bucket["task_count"]))
        bucket["avg_wall_clock_s"] = bucket["wall_clock_s"] / task_count
        bucket["avg_cost_usd"] = bucket["cost_usd"] / task_count

    return {
        "schema_version": "agentic-lead-eval/v1",
        "rows": normalized_rows,
        "summary": summary,
        "default_change_allowed": False,
        "default_change_gate": {
            "required_modes": ["lead_direct", "agentic_allowed", "agentic_required"],
            "required_review": "operator_accepts_eval_report",
        },
    }


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        "task_id": str(row.get("task_id") or ""),
        "mode": str(row.get("mode") or "unknown"),
    }
    for field in _NUMERIC_FIELDS:
        result[field] = _number(row.get(field))
    return result


def _number(value: Any) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0
    return int(parsed) if parsed.is_integer() else parsed
