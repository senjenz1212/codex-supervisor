"""Report builders for supervisor AutoResearch validation results."""
from __future__ import annotations

from statistics import median
from typing import Iterable

from .schema import AutoresearchValidationReport, sha256_json


def summarize_metric_trials(values: Iterable[float]) -> dict[str, float | int | bool | list[float] | None]:
    trials = [float(value) for value in values]
    if not trials:
        return {
            "trial_count": 0,
            "metric_trials": [],
            "metric_median": None,
            "metric_iqr": None,
            "quality_unstable_across_trials": False,
        }
    sorted_trials = sorted(trials)
    return {
        "trial_count": len(sorted_trials),
        "metric_trials": trials,
        "metric_median": float(median(sorted_trials)),
        "metric_iqr": _iqr(sorted_trials),
        "quality_unstable_across_trials": len(set(sorted_trials)) > 1,
    }


def build_autoresearch_report(reports: Iterable[AutoresearchValidationReport]) -> dict:
    records = [report.to_payload() for report in reports]
    accepted = [record for record in records if record["validation_status"] == "accepted"]
    rejected = [record for record in records if record["validation_status"] == "rejected"]
    payload = {
        "schema_version": "supervisor-autoresearch-summary/v1",
        "records": records,
        "summary": {
            "attempt_count": len(records),
            "accepted_attempt_count": len(accepted),
            "rejected_attempt_count": len(rejected),
            "gaming_flag_count": sum(len(record["gaming_flags"]) for record in records),
            "report_only": True,
        },
        "recommendation": _recommendation(records),
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "report_only": {
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "config_mutated": False,
            "policy_mutated": False,
            "operator_review_required": True,
        },
    }
    payload["report_sha256"] = sha256_json(_without_report_sha(payload))
    return payload


def _iqr(sorted_trials: list[float]) -> float:
    if len(sorted_trials) < 2:
        return 0.0
    midpoint = len(sorted_trials) // 2
    if len(sorted_trials) % 2:
        lower = sorted_trials[:midpoint]
        upper = sorted_trials[midpoint + 1:]
    else:
        lower = sorted_trials[:midpoint]
        upper = sorted_trials[midpoint:]
    if not lower or not upper:
        return 0.0
    return round(float(median(upper) - median(lower)), 6)


def _recommendation(records: list[dict]) -> dict:
    if not records:
        return {
            "decision": "no_data",
            "reason": "no_autoresearch_attempts",
            "operator_review_required": True,
        }
    if any(record["validation_status"] != "accepted" for record in records):
        return {
            "decision": "review_required",
            "reason": "one_or_more_attempts_failed_validation",
            "operator_review_required": True,
        }
    return {
        "decision": "candidate_evidence_ready",
        "reason": "all_attempts_validated_report_only",
        "operator_review_required": True,
    }


def _without_report_sha(payload: dict) -> dict:
    clone = dict(payload)
    clone.pop("report_sha256", None)
    return clone
