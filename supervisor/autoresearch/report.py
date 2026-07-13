"""Report builders for supervisor AutoResearch validation results."""
from __future__ import annotations

from statistics import median
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..claim_gate import (
    ClaimGate,
    EvidenceResolver,
    LedgerVerificationResolver,
    TrustedVerifierAttestors,
)
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


def build_autoresearch_report(
    reports: Iterable[AutoresearchValidationReport],
    *,
    claim_evidence_bundle: Mapping[str, Any] | None = None,
    claim_evidence_root: str | Path | None = None,
    claim_evidence_resolver: EvidenceResolver | None = None,
    ledger_verification_resolver: LedgerVerificationResolver | None = None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None = None,
) -> dict:
    """Build a report whose claim authority is always explicit and verifiable.

    No evidence is the normal report-only default.  That still emits a
    ClaimGate receipt with both managed claim flags set to false, rather than
    leaving downstream code to infer authority from their absence.  Production
    callers may supply evidence only together with the resolvers needed for
    ClaimGate to verify it.
    """
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
    governed = ClaimGate.derive_report(
        payload,
        claim_evidence_bundle,
        evidence_root=claim_evidence_root,
        evidence_resolver=claim_evidence_resolver,
        ledger_verification_resolver=ledger_verification_resolver,
        trusted_verifier_attestors=trusted_verifier_attestors,
    )
    governed["report_sha256"] = autoresearch_report_sha256(governed)
    return governed


def autoresearch_report_sha256(
    report: Mapping[str, Any],
) -> str:
    """Hash the immutable report body used by events and policy proposals."""
    body = dict(report)
    body.pop("report_sha256", None)
    body.pop("event_ids", None)
    body.pop("derived_policy_proposals", None)
    return sha256_json(body)


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
