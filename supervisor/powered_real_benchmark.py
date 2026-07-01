"""Definition-of-done checks for scaled powered SWE-bench Pro benchmark artifacts."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping


POWERED_REAL_BENCHMARK_DOD_SCHEMA_VERSION = (
    "supervisor-powered-real-benchmark-definition-of-done/v1"
)
REQUIRED_AUTHORITY_FLAGS = (
    "metric_applyable",
    "improvement_claim_allowed",
    "powered_improvement_claim_allowed",
    "human_mergeability_claim_allowed",
    "default_change_allowed",
    "policy_mutated",
    "gate_advanced",
)
REQUIRED_FAR_TAR_FRR_ARMS = ("baseline", "s_probe", "s_full", "oracle_ceiling")
REQUIRED_RATE_CI_FIELDS = (
    "false_accept_confidence_interval",
    "true_accept_confidence_interval",
    "false_reject_confidence_interval",
)
REPORT_ONLY_ORACLE_KIND = "swe_bench_held_out_test_pass_proxy"
_DISALLOWED_SOURCE_PATH_FRAGMENTS = (
    "tests/fixtures",
    "/fixtures/",
    "fixture",
    "synthetic",
    "pro-gold-predictions",
    "gold-predictions",
    "smoke",
)


class PoweredRealBenchmarkDoDError(AssertionError):
    """Raised when a powered real benchmark artifact does not meet DoD."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _require_false(
    reasons: list[str],
    *,
    payload: Mapping[str, Any],
    path: str,
    key: str,
) -> None:
    if key not in payload:
        reasons.append(f"{path}.{key}_missing")
        return
    if payload.get(key) is not False:
        reasons.append(f"{path}.{key}_not_false")


def _require_true(
    reasons: list[str],
    *,
    payload: Mapping[str, Any],
    path: str,
    key: str,
) -> None:
    if payload.get(key) is not True:
        reasons.append(f"{path}.{key}_not_true")


def _check_authority_flags(
    *,
    powered_report: Mapping[str, Any],
    all_arms_diagnostic_report: Mapping[str, Any],
    reasons: list[str],
) -> dict[str, bool]:
    for flag in REQUIRED_AUTHORITY_FLAGS:
        _require_false(reasons, payload=powered_report, path="powered_report", key=flag)
        _require_false(
            reasons,
            payload=all_arms_diagnostic_report,
            path="all_arms_diagnostic_report",
            key=flag,
        )
        gate_flags = _mapping(
            _nested(all_arms_diagnostic_report, "aeb0_artifact_gate", "authority_flags")
        )
        _require_false(
            reasons,
            payload=gate_flags,
            path="aeb0_artifact_gate.authority_flags",
            key=flag,
        )

    contract = _mapping(powered_report.get("evidence_conversion_power_contract"))
    _require_false(
        reasons,
        payload=contract,
        path="evidence_conversion_power_contract",
        key="policy_mutation_allowed",
    )
    recommendation = _mapping(powered_report.get("recommendation"))
    _require_false(
        reasons,
        payload=recommendation,
        path="recommendation",
        key="applyable_policy_proposal",
    )
    guardrails = _mapping(powered_report.get("promotion_guardrails"))
    _require_false(
        reasons,
        payload=guardrails,
        path="promotion_guardrails",
        key="policy_mutation_allowed",
    )

    report_only = _mapping(all_arms_diagnostic_report.get("report_only"))
    for key in ("default_change_allowed", "config_mutated", "policy_mutated"):
        _require_false(
            reasons,
            payload=report_only,
            path="all_arms_diagnostic_report.report_only",
            key=key,
        )

    return {flag: False for flag in REQUIRED_AUTHORITY_FLAGS}


def _check_source_predictions_path(
    powered_report: Mapping[str, Any],
    *,
    reasons: list[str],
) -> str:
    source = str(powered_report.get("source_predictions_path") or "").strip()
    if not source:
        reasons.append("source_predictions_path_missing")
        return ""
    normalized = PurePosixPath(source.replace("\\", "/")).as_posix().lower()
    for fragment in _DISALLOWED_SOURCE_PATH_FRAGMENTS:
        if fragment in normalized:
            reasons.append(f"source_predictions_path_not_real_scale:{fragment}")
            break
    return source


def _check_powered_thresholds(
    powered_report: Mapping[str, Any],
    *,
    reasons: list[str],
) -> dict[str, Any]:
    sample_size = _mapping(powered_report.get("sample_size_sufficiency"))
    paired_power = _mapping(powered_report.get("paired_power"))
    contract = _mapping(powered_report.get("evidence_conversion_power_contract"))
    thresholds = _mapping(contract.get("thresholds"))
    comparisons = _mapping(paired_power.get("comparisons"))
    required_name = str(paired_power.get("required_comparison") or "full_supervisor_stack")
    required = _mapping(comparisons.get(required_name))
    if not required:
        required = _mapping(_nested(contract, "required_comparison"))

    min_good = _int_value(thresholds.get("min_good"), _int_value(sample_size.get("min_good"), 30))
    min_bad = _int_value(thresholds.get("min_bad"), _int_value(sample_size.get("min_bad"), 30))
    min_discordant = _int_value(
        thresholds.get("min_discordant"),
        _int_value(paired_power.get("min_discordant"), 25),
    )
    alpha = _float_value(thresholds.get("alpha"), _float_value(paired_power.get("alpha"), 0.05))
    n_good = _int_value(sample_size.get("n_good"))
    n_bad = _int_value(sample_size.get("n_bad"))
    discordant = _int_value(required.get("discordant_pair_count"))
    p_value = required.get("p_value")
    p = _float_value(p_value, 1.0)

    if sample_size.get("status") != "sufficient":
        reasons.append("sample_size_sufficiency_status_underpowered")
    if n_good < min_good:
        reasons.append("insufficient_oracle_good")
    if n_bad < min_bad:
        reasons.append("insufficient_oracle_bad")
    if paired_power.get("status") != "sufficient":
        reasons.append("paired_power_status_underpowered")
    if required.get("status") != "sufficient":
        reasons.append("required_full_supervisor_stack_comparison_underpowered")
    if discordant < min_discordant:
        reasons.append("insufficient_discordant_pairs")
    if required.get("mcnemar_test_passed") is not True:
        reasons.append("mcnemar_test_not_passed")
    if p_value is None:
        reasons.append("mcnemar_p_value_missing")
    elif p > alpha:
        reasons.append("mcnemar_p_value_above_alpha")
    if contract.get("status") != "qualified":
        reasons.append("evidence_conversion_power_contract_not_qualified")
    _require_true(
        reasons,
        payload=contract,
        path="evidence_conversion_power_contract",
        key="report_only",
    )
    _require_true(
        reasons,
        payload=contract,
        path="evidence_conversion_power_contract",
        key="operator_review_required",
    )

    candidate_count = _int_value(
        powered_report.get("pro_candidate_count"),
        _int_value(powered_report.get("candidate_count")),
    )
    if candidate_count < n_good + n_bad:
        reasons.append("candidate_count_less_than_oracle_labeled_total")

    source_disclosure_counts = _mapping(powered_report.get("source_disclosure_counts"))
    if not source_disclosure_counts:
        reasons.append("source_disclosure_counts_missing")

    return {
        "n_good": n_good,
        "n_bad": n_bad,
        "min_good": min_good,
        "min_bad": min_bad,
        "discordant_pair_count": discordant,
        "min_discordant": min_discordant,
        "mcnemar_p_value": p_value,
        "alpha": alpha,
        "candidate_count": candidate_count,
        "vacuous_pass_to_pass_count": _int_value(
            source_disclosure_counts.get("vacuous_pass_to_pass_count")
        ),
        "rc_nonzero_resolved_count": _int_value(
            source_disclosure_counts.get("rc_nonzero_resolved_count")
        ),
    }


def _check_far_tar_frr(
    all_arms_diagnostic_report: Mapping[str, Any],
    *,
    reasons: list[str],
) -> dict[str, Any]:
    far_tar_frr = _mapping(all_arms_diagnostic_report.get("far_tar_frr"))
    if not far_tar_frr:
        reasons.append("far_tar_frr_missing")
        return {
            "far_tar_frr_arms": [],
            "confidence_interval_paths": [],
        }

    missing_arms = [
        arm for arm in REQUIRED_FAR_TAR_FRR_ARMS if arm not in far_tar_frr
    ]
    for arm in missing_arms:
        reasons.append(f"far_tar_frr.{arm}_missing")

    ci_paths: list[str] = []
    for arm, arm_payload_raw in sorted(far_tar_frr.items()):
        arm_payload = _mapping(arm_payload_raw)
        for ci_field in REQUIRED_RATE_CI_FIELDS:
            ci_path = f"far_tar_frr.{arm}.{ci_field}"
            ci = _mapping(arm_payload.get(ci_field))
            if not ci:
                reasons.append(f"{ci_path}_missing")
                continue
            ci_paths.append(ci_path)
            if ci.get("method") not in {"wilson_score", "rule_of_three"}:
                reasons.append(f"{ci_path}.method_not_wilson_family")
            if _int_value(ci.get("denominator")) <= 0:
                reasons.append(f"{ci_path}.denominator_not_positive")
            if ci.get("lower") is None or ci.get("upper") is None:
                reasons.append(f"{ci_path}.bounds_missing")

    return {
        "far_tar_frr_arms": sorted(str(arm) for arm in far_tar_frr),
        "confidence_interval_paths": ci_paths,
    }


def _check_report_only_proxy(
    all_arms_diagnostic_report: Mapping[str, Any],
    *,
    reasons: list[str],
) -> dict[str, Any]:
    oracle = _mapping(all_arms_diagnostic_report.get("benchmark_oracle"))
    if oracle.get("kind") != REPORT_ONLY_ORACLE_KIND:
        reasons.append("benchmark_oracle_kind_not_test_pass_proxy")
    if oracle.get("maintainer_mergeability_claim_allowed") is not False:
        reasons.append("maintainer_mergeability_claim_allowed_not_false")
    if all_arms_diagnostic_report.get("no_maintainer_mergeability_claim") is not True:
        reasons.append("no_maintainer_mergeability_claim_not_true")

    if all_arms_diagnostic_report.get("all_arms_populated") is not True:
        reasons.append("all_arms_not_populated")
    if all_arms_diagnostic_report.get("diagnostic_ready_for_scale") is not True:
        reasons.append("diagnostic_not_ready_for_scale")

    panel = _mapping(all_arms_diagnostic_report.get("panel_marginal"))
    if not panel:
        panel = _mapping(
            all_arms_diagnostic_report.get(
                "reviewer_marginal_delta_at_matched_true_accept"
            )
        )
    if not panel:
        reasons.append("panel_marginal_missing")

    return {
        "benchmark_oracle_kind": oracle.get("kind"),
        "panel_marginal_status": panel.get("status"),
    }


def validate_powered_real_benchmark_definition_of_done(
    *,
    powered_report: Mapping[str, Any],
    all_arms_diagnostic_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the scaled powered real benchmark Definition of Done.

    This is intentionally a report checker, not a runner. It accepts the powered
    factorial report and the separate all-arms diagnostic status artifact, then
    fails closed unless the paired-power, FAR/TAR/FRR, provenance, and report-only
    gates are all simultaneously satisfied.
    """
    reasons: list[str] = []
    source_predictions_path = _check_source_predictions_path(
        powered_report,
        reasons=reasons,
    )
    powered_evidence = _check_powered_thresholds(powered_report, reasons=reasons)
    far_evidence = _check_far_tar_frr(
        all_arms_diagnostic_report,
        reasons=reasons,
    )
    proxy_evidence = _check_report_only_proxy(
        all_arms_diagnostic_report,
        reasons=reasons,
    )
    authority_flags = _check_authority_flags(
        powered_report=powered_report,
        all_arms_diagnostic_report=all_arms_diagnostic_report,
        reasons=reasons,
    )
    reasons = sorted(set(reasons))
    return {
        "schema_version": POWERED_REAL_BENCHMARK_DOD_SCHEMA_VERSION,
        "status": "passed" if not reasons else "blocked",
        "definition_of_done_met": not reasons,
        "reasons": reasons,
        "evidence": {
            **powered_evidence,
            **far_evidence,
            **proxy_evidence,
            "source_predictions_path": source_predictions_path,
            "real_benchmark_claim_allowed": not reasons,
            "policy_mutation_allowed": False,
        },
        "authority_flags": authority_flags,
    }


def assert_powered_real_benchmark_definition_of_done(
    *,
    powered_report: Mapping[str, Any],
    all_arms_diagnostic_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the DoD verdict or raise with the failing reason codes."""
    verdict = validate_powered_real_benchmark_definition_of_done(
        powered_report=powered_report,
        all_arms_diagnostic_report=all_arms_diagnostic_report,
    )
    if not verdict["definition_of_done_met"]:
        raise PoweredRealBenchmarkDoDError(
            "powered real benchmark Definition of Done failed: "
            + ", ".join(verdict["reasons"])
        )
    return verdict
