"""SWE-bench Pro mergeability bridge.

Public boundary: ``swebench_pro_mergeability_bridge_report``.

This module bridges SWE-bench Pro-shaped instance records and candidate-arm
patches into the mergeability FAR/TAR measurement path. It builds public-only
mergeability packets, freezes baseline / S_probe / S_full decision rows, and
only then attaches held-out FAIL_TO_PASS / PASS_TO_PASS oracle outcomes for
aggregate reporting. All outputs are report-only and cannot derive applyable
policy proposals.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .mergeability_bench import (
    ConfiguredReviewerPanelOptions,
    MERGEABILITY_CANDIDATE_SCHEMA_VERSION,
    MERGEABILITY_RUBRIC_LABELS,
    MERGEABILITY_RUBRIC_SCHEMA_VERSION,
    MERGEABILITY_TASK_SCHEMA_VERSION,
    ORACLE_REVIEW_FORBIDDEN_KEYS,
    ORACLE_REVIEW_FORBIDDEN_TEXT,
    _copy_public_fixture_tree,
    _effective_vote_estimate,
    _false_accept_at_matched_true_accept,
    _generator_disjointness_report,
    _inter_reviewer_agreement,
    _leave_one_reviewer_out_analysis,
    _mergeability_rubric_coverage,
    _normalise_mergeability_reviewer_result,
    _panel_marginal_delta_at_matched_true_accept,
    _per_reviewer_acceptance_arms,
    _producer_family_from_row,
    _public_input_oracle_refs,
    _rate,
    _reviewer_oracle_error_overlap,
    _reviewer_cross_family_claim_status,
    _reviewer_provenance_report,
    _resolve_powered_baseline_decision,
    _run_command,
    _summarize_acceptance_arm,
    _wilson_interval,
    build_configured_reviewer_panel,
    run_powered_factorial_mergeability_evaluation,
)
from .swe_bench_official_oracle import (
    preflight_swe_bench_pro_run_scripts,
    run_swe_bench_pro_oracle,
    swe_bench_pro_oracle_scripts_dir,
)


SWEBENCH_PRO_BRIDGE_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-pro-mergeability-bridge-report/v1"
)
SWEBENCH_PRO_PUBLIC_PACKET_SCHEMA_VERSION = (
    "supervisor-swebench-pro-public-packet/v1"
)
PUBLIC_STATIC_PATCH_PROBE = "public_static_patch_probe"

# Hidden SWE-bench Pro oracle fields that must never leak into public packets,
# reviewer packets, transcripts, or receipts. Layered on top of the general
# ``ORACLE_REVIEW_FORBIDDEN_KEYS`` from the mergeability bench.
SWEBENCH_PRO_HIDDEN_ORACLE_KEYS = frozenset({
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "fail_to_pass",
    "pass_to_pass",
    "test_patch",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
})
SWEBENCH_PRO_HIDDEN_ORACLE_TEXT_MARKERS = frozenset({
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "test_patch",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
})
SWEBENCH_PRO_FORBIDDEN_KEYS = frozenset(
    set(ORACLE_REVIEW_FORBIDDEN_KEYS) | set(SWEBENCH_PRO_HIDDEN_ORACLE_KEYS)
)

# Required arm names.
ARM_BASELINE = "baseline"
ARM_S_PROBE = "s_probe"
ARM_S_FULL = "s_full"
ARM_ORACLE_CEILING = "oracle_ceiling"
ARM_NAMES = (ARM_BASELINE, ARM_S_PROBE, ARM_S_FULL, ARM_ORACLE_CEILING)
PRODUCED_BASELINE_RECEIPT_KEYS = (
    "single_agent_baseline_decision",
    "produced_baseline_decision",
    "baseline_decision",
)
POWERED_FACTORIAL_ARM_DECISION_KEYS = {
    "same_model_multi_agent": "same_model_multi_agent_decision",
    "hetero_multi_reviewer": "hetero_multi_reviewer_decision",
    "runtime_evidence_floor": "runtime_evidence_floor_decision",
    "full_supervisor_stack": "full_supervisor_stack_decision",
}
OFFICIAL_PREDICTION_METADATA_KEYS = (
    "oracle_label",
    "candidate_artifact_hash",
    "model_patch_sha256",
    "diff_sha256",
    "origin",
    "producer",
    "patch_applied",
)
ORACLE_GOOD_LABEL = "oracle-good"
ORACLE_BAD_LABEL = "oracle-bad"

_PUBLIC_PACKET_ALLOWED_INSTANCE_KEYS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "public_checkout_ref",
    "public_checkout_sha256",
    "public_lint_commands",
    "public_build_commands",
)


class SwebenchProBridgeError(RuntimeError):
    """Raised when bridge inputs are invalid or violate isolation invariants."""


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _scan_for_swebench_pro_oracle_refs(value: Any, *, path: str = "") -> list[str]:
    """Scan a value for SWE-bench Pro-specific oracle keys/text.

    Layered on top of the general mergeability oracle-ref scanner so that
    FAIL_TO_PASS / PASS_TO_PASS / test_patch leaks are detected even though
    they are not part of the local mergeability bench schema.
    """
    refs: list[str] = list(_public_input_oracle_refs(value, path=path))
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            if key_text in SWEBENCH_PRO_HIDDEN_ORACLE_KEYS:
                refs.append(nested_path)
            refs.extend(
                _scan_for_swebench_pro_oracle_refs(nested, path=nested_path)
            )
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            refs.extend(
                _scan_for_swebench_pro_oracle_refs(
                    nested, path=f"{path}[{index}]"
                )
            )
    elif isinstance(value, str):
        for marker in SWEBENCH_PRO_HIDDEN_ORACLE_TEXT_MARKERS:
            if marker in value:
                refs.append(path or marker)
                break
    return sorted(set(refs))


def _validate_s_probe_substrate(s_probe_substrate: Any) -> dict[str, Any]:
    if s_probe_substrate is None:
        raise SwebenchProBridgeError(
            "s_probe_substrate is required: pass an explicit "
            f"{PUBLIC_STATIC_PATCH_PROBE} configuration"
        )
    if not isinstance(s_probe_substrate, Mapping):
        raise SwebenchProBridgeError("s_probe_substrate must be a mapping")
    kind = str(s_probe_substrate.get("kind") or "")
    if kind != PUBLIC_STATIC_PATCH_PROBE:
        raise SwebenchProBridgeError(
            "s_probe_substrate.kind must be "
            f"{PUBLIC_STATIC_PATCH_PROBE!r}; got {kind!r}"
        )
    public_lint_commands = [
        [str(part) for part in cmd]
        for cmd in s_probe_substrate.get("public_lint_commands", [])
    ]
    public_build_commands = [
        [str(part) for part in cmd]
        for cmd in s_probe_substrate.get("public_build_commands", [])
    ]
    requires_apply = bool(
        s_probe_substrate.get("requires_patch_applies", True)
    )
    refs = _scan_for_swebench_pro_oracle_refs(
        {
            "public_lint_commands": public_lint_commands,
            "public_build_commands": public_build_commands,
        }
    )
    if refs:
        raise SwebenchProBridgeError(
            "s_probe_substrate references forbidden oracle material: "
            + ", ".join(refs)
        )
    return {
        "kind": PUBLIC_STATIC_PATCH_PROBE,
        "requires_patch_applies": requires_apply,
        "public_lint_commands": public_lint_commands,
        "public_build_commands": public_build_commands,
    }


def build_swe_bench_pro_public_packet(
    *,
    instance: Mapping[str, Any],
    candidate: Mapping[str, Any],
    s_probe_substrate: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a public-only packet from a SWE-bench Pro instance and candidate.

    Excludes all hidden oracle material (FAIL_TO_PASS, PASS_TO_PASS, test_patch,
    final_score, oracle_accept, expected_outcome, hidden command strings).
    """
    substrate = _validate_s_probe_substrate(s_probe_substrate)
    instance_id = str(instance.get("instance_id") or "")
    if not instance_id:
        raise SwebenchProBridgeError("instance.instance_id is required")
    candidate_id = str(candidate.get("candidate_id") or "")
    if not candidate_id:
        raise SwebenchProBridgeError("candidate.candidate_id is required")
    candidate_patch = str(candidate.get("patch") or "")
    candidate_artifact_hash = sha256(candidate_patch.encode("utf-8")).hexdigest()
    public_lint = [list(cmd) for cmd in substrate["public_lint_commands"]]
    public_build = [list(cmd) for cmd in substrate["public_build_commands"]]
    packet = {
        "schema_version": SWEBENCH_PRO_PUBLIC_PACKET_SCHEMA_VERSION,
        "instance_id": instance_id,
        "repo": str(instance.get("repo") or ""),
        "base_commit": str(instance.get("base_commit") or ""),
        "problem_statement": str(instance.get("problem_statement") or ""),
        "public_checkout_ref": str(instance.get("public_checkout_ref") or ""),
        "public_checkout_sha256": str(instance.get("public_checkout_sha256") or ""),
        "candidate_id": candidate_id,
        "candidate_artifact_hash": candidate_artifact_hash,
        "s_probe_substrate": {
            "kind": substrate["kind"],
            "requires_patch_applies": substrate["requires_patch_applies"],
            "public_lint_commands": public_lint,
            "public_build_commands": public_build,
        },
    }
    return packet


def _raw_decision_for(
    arm_decisions: Mapping[str, Mapping[str, Any]],
    *,
    arm: str,
    instance_id: str,
    candidate_id: str,
) -> Any:
    per_arm = arm_decisions.get(arm) or {}
    raw = per_arm.get((instance_id, candidate_id))
    if raw is None:
        raw = per_arm.get(f"{instance_id}/{candidate_id}")
    return raw


def _decision_for(
    arm_decisions: Mapping[str, Mapping[str, Any]],
    *,
    arm: str,
    instance_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    raw = _raw_decision_for(
        arm_decisions,
        arm=arm,
        instance_id=instance_id,
        candidate_id=candidate_id,
    )
    if raw is None:
        return {"accept": False, "unavailable": True}
    if isinstance(raw, Mapping):
        accept = bool(raw.get("accept"))
        unavailable = bool(raw.get("unavailable"))
        return {"accept": accept and not unavailable, "unavailable": unavailable}
    return {"accept": bool(raw), "unavailable": False}


def _baseline_decision_for(
    arm_decisions: Mapping[str, Mapping[str, Any]],
    *,
    instance_id: str,
    candidate_id: str,
    expected_candidate_artifact_hash: str,
) -> dict[str, Any]:
    raw = _raw_decision_for(
        arm_decisions,
        arm=ARM_BASELINE,
        instance_id=instance_id,
        candidate_id=candidate_id,
    )
    if (
        isinstance(raw, Mapping)
        and bool(raw.get("unavailable"))
        and "evidence_kind" in raw
        and "unavailable_reason" in raw
        and "decision_source" in raw
    ):
        supplied_candidate_id = str(raw.get("candidate_id") or "")
        if supplied_candidate_id and supplied_candidate_id != candidate_id:
            return _resolve_powered_baseline_decision(
                raw=raw,
                expected_candidate_artifact_hash=expected_candidate_artifact_hash,
                expected_candidate_id=candidate_id,
            )
        return {
            "accept": False,
            "unavailable": True,
            "decision_source": str(
                raw.get("decision_source")
                or "produced_single_agent_baseline_unavailable"
            ),
            "candidate_id": str(raw.get("candidate_id") or candidate_id),
            "candidate_artifact_hash": str(raw.get("candidate_artifact_hash") or ""),
            "producer": (
                dict(raw.get("producer"))
                if isinstance(raw.get("producer"), Mapping)
                else {}
            ),
            "prompt_sha256": str(raw.get("prompt_sha256") or ""),
            "evidence_kind": str(raw.get("evidence_kind") or "missing"),
            "unavailable_reason": str(
                raw.get("unavailable_reason")
                or "baseline_decisions_not_supplied"
            ),
        }
    return _resolve_powered_baseline_decision(
        raw=raw,
        expected_candidate_artifact_hash=expected_candidate_artifact_hash,
        expected_candidate_id=candidate_id,
    )


def _candidate_produced_baseline_receipt(candidate: Mapping[str, Any]) -> Any:
    for key in PRODUCED_BASELINE_RECEIPT_KEYS:
        if key in candidate:
            return candidate.get(key)
    return None


def _frozen_decision_row(
    *,
    instance_id: str,
    candidate_id: str,
    public_packet: Mapping[str, Any],
    arm_decisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = _baseline_decision_for(
        arm_decisions,
        instance_id=instance_id,
        candidate_id=candidate_id,
        expected_candidate_artifact_hash=str(public_packet["candidate_artifact_hash"]),
    )
    s_probe = _decision_for(
        arm_decisions, arm=ARM_S_PROBE, instance_id=instance_id, candidate_id=candidate_id
    )
    s_full = _decision_for(
        arm_decisions, arm=ARM_S_FULL, instance_id=instance_id, candidate_id=candidate_id
    )
    decision_payload = {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "public_packet_sha256": _sha256_json(public_packet),
        "baseline_accept": baseline["accept"],
        "baseline_unavailable": baseline["unavailable"],
        "baseline_decision_source": baseline["decision_source"],
        "baseline_evidence_kind": baseline["evidence_kind"],
        "baseline_candidate_id": baseline["candidate_id"],
        "baseline_candidate_artifact_hash": baseline["candidate_artifact_hash"],
        "baseline_producer": dict(baseline["producer"]),
        "baseline_prompt_sha256": baseline["prompt_sha256"],
        "baseline_unavailable_reason": baseline["unavailable_reason"],
        "s_probe_accept": s_probe["accept"],
        "s_probe_unavailable": s_probe["unavailable"],
        "s_full_accept": s_full["accept"],
        "s_full_unavailable": s_full["unavailable"],
    }
    decision_payload["decision_phase_sha256"] = _sha256_json(decision_payload)
    return decision_payload


def _oracle_outcome_for(
    oracle_outcomes: Mapping[Any, Mapping[str, Any]],
    *,
    instance_id: str,
    candidate_id: str,
) -> Mapping[str, Any]:
    raw = oracle_outcomes.get((instance_id, candidate_id))
    if raw is None:
        raw = oracle_outcomes.get(f"{instance_id}/{candidate_id}")
    if raw is None:
        raise SwebenchProBridgeError(
            f"oracle outcome missing for {instance_id}/{candidate_id}"
        )
    return raw


def _interpret_oracle_outcome(outcome: Mapping[str, Any]) -> dict[str, Any]:
    fail_to_pass = str(outcome.get("fail_to_pass_status") or "").lower()
    pass_to_pass = str(outcome.get("pass_to_pass_status") or "").lower()
    patch_applied = _patch_applied_from_oracle_result(outcome)
    oracle_unavailable = bool(
        outcome.get("oracle_unavailable")
        or outcome.get("unavailable")
        or str(outcome.get("status") or "").lower() == "unavailable"
        or fail_to_pass == "unavailable"
        or pass_to_pass == "unavailable"
        or patch_applied is False
    )
    if oracle_unavailable:
        reason = str(
            outcome.get("oracle_unavailable_reason")
            or outcome.get("unavailable_reason")
            or ("patch_apply_failed" if patch_applied is False else "")
            or "oracle_unavailable"
        )
        return {
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
            "oracle_accept": False,
            "oracle_unavailable": True,
            "oracle_unavailable_reason": reason,
            "pass_to_pass_regression": False,
            "patch_applied": patch_applied,
        }
    if fail_to_pass not in {"pass", "fail"}:
        raise SwebenchProBridgeError(
            "oracle outcome fail_to_pass_status must be 'pass' or 'fail'"
        )
    if pass_to_pass not in {"pass", "fail"}:
        raise SwebenchProBridgeError(
            "oracle outcome pass_to_pass_status must be 'pass' or 'fail'"
        )
    oracle_accept = fail_to_pass == "pass" and pass_to_pass == "pass"
    return {
        "fail_to_pass_status": fail_to_pass,
        "pass_to_pass_status": pass_to_pass,
        "oracle_accept": oracle_accept,
        "oracle_unavailable": False,
        "oracle_unavailable_reason": "",
        "pass_to_pass_regression": pass_to_pass == "fail",
        "patch_applied": patch_applied,
    }


def _patch_applied_from_oracle_result(value: Mapping[str, Any]) -> bool | None:
    raw = value.get("patch_applied")
    if raw is None:
        receipt = value.get("oracle_adapter_receipt")
        if isinstance(receipt, Mapping):
            raw = receipt.get("patch_applied")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalised = raw.strip().lower()
        if normalised in {"true", "1", "yes", "passed"}:
            return True
        if normalised in {"false", "0", "no", "failed"}:
            return False
    return None


def _no_regression_findings_for_bridge(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("pass_to_pass_regression"):
            continue
        findings.append({
            "instance_id": row["instance_id"],
            "candidate_id": row["candidate_id"],
            "fail_to_pass_status": row["fail_to_pass_status"],
            "pass_to_pass_status": row["pass_to_pass_status"],
            "reason": "pass_to_pass_regression",
            "protected_scope": "swe_bench_pro_pass_to_pass_suite",
        })
    return sorted(
        findings,
        key=lambda item: (str(item["instance_id"]), str(item["candidate_id"])),
    )


def swebench_pro_mergeability_bridge_report(
    *,
    instances: list[Mapping[str, Any]],
    candidate_artifacts: Mapping[Any, Mapping[str, Any]],
    s_probe_substrate: Mapping[str, Any],
    arm_decisions: Mapping[str, Mapping[Any, Any]],
    oracle_outcomes: Mapping[Any, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a SWE-bench Pro mergeability FAR/TAR/FRR report.

    Inputs:
      - instances: list of SWE-bench Pro-shaped instance records. May contain
        hidden oracle fields (FAIL_TO_PASS, PASS_TO_PASS, test_patch) which
        will be stripped before public packets are emitted.
      - candidate_artifacts: mapping from (instance_id, candidate_id) -> dict
        with at minimum a ``patch`` string.
      - s_probe_substrate: explicit ``public_static_patch_probe`` configuration.
        Required; absence raises.
      - arm_decisions: mapping arm_name -> {(instance_id, candidate_id) ->
        {accept: bool, unavailable: bool}}. Arms must be at least baseline,
        s_probe, s_full.
      - oracle_outcomes: post-decision FAIL_TO_PASS / PASS_TO_PASS outcomes.

    The report is always ``report_only``: ``metric_applyable``,
    ``improvement_claim_allowed``, ``default_change_allowed``,
    ``policy_mutated``, and ``gate_advanced`` are all False.
    """
    substrate = _validate_s_probe_substrate(s_probe_substrate)

    rows: list[dict[str, Any]] = []
    public_packets: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    oracle_isolation_violations: list[dict[str, Any]] = []
    oracle_unavailable_findings: list[dict[str, Any]] = []
    unavailable_arms_per_row: list[dict[str, Any]] = []

    for instance in instances:
        instance_id = str(instance.get("instance_id") or "")
        if not instance_id:
            raise SwebenchProBridgeError("instance.instance_id is required")
        for key, value in candidate_artifacts.items():
            candidate_instance_id, candidate_id = _split_instance_candidate_key(key)
            if candidate_instance_id != instance_id:
                continue
            candidate = dict(value)
            candidate.setdefault("candidate_id", candidate_id)
            packet = build_swe_bench_pro_public_packet(
                instance=instance,
                candidate=candidate,
                s_probe_substrate=substrate,
            )
            leak_refs = _scan_for_swebench_pro_oracle_refs(packet)
            packet["oracle_isolation_refs"] = leak_refs
            packet_isolation_ok = not leak_refs

            frozen = _frozen_decision_row(
                instance_id=instance_id,
                candidate_id=candidate_id,
                public_packet=packet,
                arm_decisions=arm_decisions,
            )

            outcome = _interpret_oracle_outcome(
                _oracle_outcome_for(
                    oracle_outcomes,
                    instance_id=instance_id,
                    candidate_id=candidate_id,
                )
            )

            row: dict[str, Any] = {
                "instance_id": instance_id,
                "candidate_id": candidate_id,
                "candidate_artifact_hash": str(
                    candidate.get("candidate_artifact_hash")
                    or packet["candidate_artifact_hash"]
                ),
                "model_patch_sha256": str(
                    candidate.get("model_patch_sha256")
                    or sha256(str(candidate.get("patch") or "").encode("utf-8")).hexdigest()
                ),
                "diff_sha256": str(
                    candidate.get("diff_sha256")
                    or sha256(str(candidate.get("patch") or "").encode("utf-8")).hexdigest()
                ),
                "oracle_label": str(candidate.get("oracle_label") or ""),
                "origin": (
                    dict(candidate["origin"])
                    if isinstance(candidate.get("origin"), Mapping)
                    else {}
                ),
                "producer": (
                    dict(candidate["producer"])
                    if isinstance(candidate.get("producer"), Mapping)
                    else {}
                ),
                "public_packet_sha256": frozen["public_packet_sha256"],
                "decision_phase_sha256": frozen["decision_phase_sha256"],
                "oracle_isolation_ok": packet_isolation_ok,
                "oracle_isolation_refs": leak_refs,
                "baseline_accept": frozen["baseline_accept"],
                "baseline_unavailable": frozen["baseline_unavailable"],
                "baseline_decision_source": frozen["baseline_decision_source"],
                "baseline_evidence_kind": frozen["baseline_evidence_kind"],
                "baseline_candidate_id": frozen["baseline_candidate_id"],
                "baseline_candidate_artifact_hash": frozen[
                    "baseline_candidate_artifact_hash"
                ],
                "baseline_producer": dict(frozen["baseline_producer"]),
                "baseline_prompt_sha256": frozen["baseline_prompt_sha256"],
                "baseline_unavailable_reason": frozen["baseline_unavailable_reason"],
                "legacy_baseline_self_report": candidate.get("baseline_self_report"),
                "legacy_baseline_self_report_calibration_only": (
                    "baseline_self_report" in candidate
                ),
                "s_probe_accept": frozen["s_probe_accept"],
                "s_probe_unavailable": frozen["s_probe_unavailable"],
                "s_full_accept": frozen["s_full_accept"],
                "s_full_unavailable": frozen["s_full_unavailable"],
                "oracle_ceiling_accept": outcome["oracle_accept"],
                "oracle_ceiling_unavailable": outcome["oracle_unavailable"],
                "oracle_accept": outcome["oracle_accept"],
                "oracle_unavailable": outcome["oracle_unavailable"],
                "oracle_unavailable_reason": outcome["oracle_unavailable_reason"],
                "fail_to_pass_status": outcome["fail_to_pass_status"],
                "pass_to_pass_status": outcome["pass_to_pass_status"],
                "pass_to_pass_regression": outcome["pass_to_pass_regression"],
                "patch_applied": outcome.get("patch_applied"),
                "s_full_disagrees_with_s_probe": (
                    frozen["s_probe_accept"] != frozen["s_full_accept"]
                    and not frozen["s_full_unavailable"]
                    and not frozen["s_probe_unavailable"]
                ),
            }
            if not packet_isolation_ok:
                for arm_name in (ARM_BASELINE, ARM_S_PROBE, ARM_S_FULL):
                    row[f"{arm_name}_accept"] = False
                    row[f"{arm_name}_unavailable"] = True
                row["baseline_unavailable_reason"] = (
                    "public_packet_contains_hidden_oracle_material"
                )
                oracle_isolation_violations.append({
                    "instance_id": instance_id,
                    "candidate_id": candidate_id,
                    "refs": leak_refs,
                    "reason": "public_packet_contains_hidden_oracle_material",
                })
            if outcome["oracle_unavailable"]:
                for arm_name in ARM_NAMES:
                    row[f"{arm_name}_accept"] = False
                    row[f"{arm_name}_unavailable"] = True
                oracle_unavailable_findings.append({
                    "instance_id": instance_id,
                    "candidate_id": candidate_id,
                    "reason": outcome["oracle_unavailable_reason"],
                })
            rows.append(row)
            public_packets.append(packet)
            decision_rows.append(frozen)
            unavailable_arms_per_row.append({
                "instance_id": instance_id,
                "candidate_id": candidate_id,
                "baseline_unavailable": row["baseline_unavailable"],
                "s_probe_unavailable": row["s_probe_unavailable"],
                "s_full_unavailable": row["s_full_unavailable"],
            })

    arm_summaries: dict[str, dict[str, Any]] = {}
    for arm in ARM_NAMES:
        evidence_kind = None
        decision_source = _arm_decision_source(arm)
        arm_role = arm
        if arm == ARM_BASELINE:
            evidence_kind = _baseline_evidence_kind_for_rows(rows)
            decision_source = _baseline_decision_source_for_rows(rows)
            arm_role = "single_agent_baseline"
        arm_summaries[arm] = _summarize_acceptance_arm(
            rows,
            arm=arm,
            arm_role=arm_role,
            decision_source=decision_source,
            oracle_coupled=(arm == ARM_ORACLE_CEILING),
            evidence_kind=evidence_kind,
        )

    matched_true_accept = {
        arm: _false_accept_at_matched_true_accept(
            baseline=arm_summaries[ARM_BASELINE],
            supervisor=arm_summaries[arm],
        )
        for arm in (ARM_S_PROBE, ARM_S_FULL)
    }
    panel_marginal_delta = _panel_marginal_delta_at_matched_true_accept(
        public_review=arm_summaries[ARM_S_PROBE],
        full_gate=arm_summaries[ARM_S_FULL],
        full_roster_available_count=sum(
            1 for row in rows if not bool(row.get("s_full_unavailable"))
        ),
    )

    no_regression_findings = _no_regression_findings_for_bridge(rows)
    oracle_isolation_ok = not oracle_isolation_violations

    gaming_flags: list[str] = []
    if oracle_isolation_violations:
        gaming_flags.append("oracle_isolation_violation")
    if no_regression_findings:
        gaming_flags.append("pass_to_pass_regression_detected")
    metrics_suppressed = bool(oracle_unavailable_findings)
    metrics_unavailable_reasons = ["oracle_unavailable"] if metrics_suppressed else []

    report = {
        "schema_version": SWEBENCH_PRO_BRIDGE_REPORT_SCHEMA_VERSION,
        "instance_count": len(instances),
        "candidate_count": len(rows),
        "s_probe_substrate": {
            "kind": substrate["kind"],
            "requires_patch_applies": substrate["requires_patch_applies"],
            "public_lint_commands": [
                list(cmd) for cmd in substrate["public_lint_commands"]
            ],
            "public_build_commands": [
                list(cmd) for cmd in substrate["public_build_commands"]
            ],
        },
        "public_packets": public_packets,
        "public_packets_sha256": _sha256_json(public_packets),
        "decision_phase_rows": decision_rows,
        "decision_phase_sha256": _sha256_json(decision_rows),
        "oracle_isolation": {
            "ok": oracle_isolation_ok,
            "violations": oracle_isolation_violations,
            "forbidden_keys_scanned": sorted(SWEBENCH_PRO_FORBIDDEN_KEYS),
            "forbidden_text_markers_scanned": sorted(
                set(ORACLE_REVIEW_FORBIDDEN_TEXT) | set(SWEBENCH_PRO_HIDDEN_ORACLE_KEYS)
            ),
        },
        "arms": arm_summaries,
        "metrics_suppressed": metrics_suppressed,
        "metrics_unavailable_reasons": metrics_unavailable_reasons,
        "false_accept_at_matched_true_accept": matched_true_accept,
        "matched_true_accept_status": {
            arm: matched_true_accept[arm]["status"]
            for arm in matched_true_accept
        },
        "panel_marginal_delta_at_matched_true_accept": panel_marginal_delta,
        "no_regression_findings": no_regression_findings,
        "no_regression_sha256": _sha256_json(no_regression_findings),
        "oracle_unavailable_findings": oracle_unavailable_findings,
        "per_row_unavailable_arms": unavailable_arms_per_row,
        "per_row_results": rows,
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
        "recommendation": {
            "report_only": True,
            "applyable_policy_proposal": False,
            "next_step": (
                "grow oracle-isolated SWE-bench Pro mergeability corpus before "
                "any powered live experiment"
            ),
        },
        "validity_notes": [
            "S_probe substrate is public_static_patch_probe: patch applicability "
            "against a public checkout plus curated public lint/build commands.",
            "This substrate is not equivalent to hidden FAIL_TO_PASS or PASS_TO_PASS "
            "execution and must not be reported as parity.",
            "S_full decisions require both S_probe accept and an available "
            "reviewer-panel accept; unavailable reviewer-panel keeps S_full "
            "unavailable rather than imputing accept.",
            "FAR, TAR, and FRR are computed from post-decision FAIL_TO_PASS and "
            "PASS_TO_PASS oracle labels; decision rows are frozen before oracle "
            "outcomes are read.",
            "Oracle ceiling is oracle-coupled and never reportable as a supervisor "
            "improvement.",
        ],
        "gaming_flags": sorted(set(gaming_flags)),
    }
    if not metrics_suppressed:
        report["far_tar_frr"] = {
            arm: {
                "false_accept_rate": arm_summaries[arm]["false_accept_rate"],
                "true_accept_rate": arm_summaries[arm]["true_accept_rate"],
                "false_reject_rate": arm_summaries[arm]["false_reject_rate"],
                "n_bad": arm_summaries[arm]["n_bad"],
                "n_good": arm_summaries[arm]["n_good"],
                "false_accept_confidence_interval": arm_summaries[arm][
                    "false_accept_confidence_interval"
                ],
                "true_accept_confidence_interval": arm_summaries[arm][
                    "true_accept_confidence_interval"
                ],
                "false_reject_confidence_interval": arm_summaries[arm][
                    "false_reject_confidence_interval"
                ],
            }
            for arm in ARM_NAMES
        }
    report["report_sha256"] = _sha256_json(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )
    return report


def _baseline_evidence_kind_for_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    available = [row for row in rows if not bool(row.get("baseline_unavailable"))]
    if available:
        kinds = {
            str(row.get("baseline_evidence_kind") or "")
            for row in available
        }
        kinds.discard("")
        if len(kinds) == 1:
            return next(iter(kinds))
        if kinds:
            return "mixed_produced_baseline"
        return "produced_single_agent_baseline"
    unavailable_kinds = {
        str(row.get("baseline_evidence_kind") or "missing")
        for row in rows
    }
    if len(unavailable_kinds) == 1:
        return next(iter(unavailable_kinds))
    return "unavailable_mixed"


def _baseline_decision_source_for_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    available = [row for row in rows if not bool(row.get("baseline_unavailable"))]
    if not available:
        return "produced_single_agent_baseline_unavailable"
    sources = {
        str(row.get("baseline_decision_source") or "")
        for row in available
    }
    sources.discard("")
    if len(sources) == 1:
        return next(iter(sources))
    return "mixed_produced_single_agent_baseline"


def _arm_decision_source(arm: str) -> str:
    if arm == ARM_BASELINE:
        return "produced_single_agent_baseline_unavailable"
    if arm == ARM_S_PROBE:
        return PUBLIC_STATIC_PATCH_PROBE
    if arm == ARM_S_FULL:
        return f"{PUBLIC_STATIC_PATCH_PROBE}+independent_reviewer_panel"
    if arm == ARM_ORACLE_CEILING:
        return "swe_bench_pro_fail_to_pass_and_pass_to_pass_oracle"
    return arm


def _split_instance_candidate_key(key: Any) -> tuple[str, str]:
    if isinstance(key, tuple) and len(key) == 2:
        return str(key[0]), str(key[1])
    if isinstance(key, str) and "/" in key:
        instance_id, _, candidate_id = key.partition("/")
        return instance_id, candidate_id
    raise SwebenchProBridgeError(
        "candidate_artifacts key must be (instance_id, candidate_id) tuple or "
        "'instance_id/candidate_id' string"
    )


# ---------------------------------------------------------------------------
# Fixture-first executable runner
# ---------------------------------------------------------------------------

SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-fixture-report/v1"
)
SWEBENCH_MERGEABILITY_REPLAY_MANIFEST_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-replay-manifest/v1"
)
SWEBENCH_MERGEABILITY_REPLAY_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-replay-report/v1"
)
SWEBENCH_MERGEABILITY_LIVE_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-live-report/v1"
)
SWEBENCH_MERGEABILITY_OFFICIAL_REPLAY_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-official-replay-report/v1"
)
SWEBENCH_MERGEABILITY_OFFICIAL_LIVE_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-official-live-report/v1"
)
SWEBENCH_MERGEABILITY_OFFICIAL_ALL_ARMS_DIAGNOSTIC_SCHEMA_VERSION = (
    "supervisor-swebench-official-all-arms-diagnostic-report/v1"
)
SWEBENCH_MERGEABILITY_REVIEWER_PACKET_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-reviewer-packet/v1"
)
SWEBENCH_MERGEABILITY_FROZEN_DECISIONS_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-frozen-decisions/v1"
)

REVIEWER_PANEL_UNAVAILABLE_REASON = "reviewer_panel_unavailable"
PATCH_APPLY_FAILURE_REASON = "patch_apply_failure"
STATIC_LINT_ONLY_SUBSTRATE = "static_lint_only"
PUBLIC_COMMANDS_SUBSTRATE = "public_commands"
DEFAULT_OFFICIAL_ORACLE_ADAPTER_KIND = "official_docker_or_equivalent"
OFFICIAL_ORACLE_RECEIPT_VALIDATION_KINDS = frozenset({
    DEFAULT_OFFICIAL_ORACLE_ADAPTER_KIND,
    "official_equivalent",
})
SUPPORTED_OFFICIAL_REPLAY_ORACLE_ADAPTER_KINDS = (
    OFFICIAL_ORACLE_RECEIPT_VALIDATION_KINDS
)


def _official_replay_should_preflight_pro_scripts(
    *,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    swe_bench_pro_scripts_dir: str | Path | None,
) -> bool:
    if swe_bench_pro_scripts_dir is not None:
        return True
    if os.environ.get("SWEBENCH_PRO_ORACLE_SCRIPTS_DIR"):
        return True
    return oracle_runner is run_swe_bench_pro_oracle

_OFFICIAL_RECORD_HIDDEN_KEYS: tuple[str, ...] = (
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "fail_to_pass",
    "pass_to_pass",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
    "final_score",
    "oracle_accept",
    "expected_outcome",
    "hidden_test_commands",
)

_DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "hidden/",
    ".mergeability/",
    ".git/",
)


class SwebenchMergeabilityFixtureRunnerError(SwebenchProBridgeError):
    """Raised when fixture runner inputs or invariants are violated."""


def _apply_patch_operations(
    operations: Sequence[Mapping[str, Any]],
    worktree: Path,
) -> tuple[str, list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                "candidate patch_operations entries must be mappings"
            )
        rel = str(op.get("path") or "")
        mode = str(op.get("mode") or "create").lower()
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            receipts.append({
                "path": rel,
                "mode": mode,
                "status": "failed",
                "reason": "invalid_relpath",
            })
            continue
        target = worktree / rel
        try:
            if mode == "modify":
                if not target.exists():
                    receipts.append({
                        "path": rel,
                        "mode": mode,
                        "status": "failed",
                        "reason": "modify_missing_target",
                    })
                    continue
                target.write_text(str(op.get("content") or ""), encoding="utf-8")
                receipts.append({"path": rel, "mode": mode, "status": "passed"})
            elif mode == "create":
                if target.exists():
                    receipts.append({
                        "path": rel,
                        "mode": mode,
                        "status": "failed",
                        "reason": "create_existing_target",
                    })
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(op.get("content") or ""), encoding="utf-8")
                receipts.append({"path": rel, "mode": mode, "status": "passed"})
            elif mode == "overwrite":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(op.get("content") or ""), encoding="utf-8")
                receipts.append({"path": rel, "mode": mode, "status": "passed"})
            elif mode == "delete":
                if not target.exists():
                    receipts.append({
                        "path": rel,
                        "mode": mode,
                        "status": "failed",
                        "reason": "delete_missing_target",
                    })
                    continue
                target.unlink()
                receipts.append({"path": rel, "mode": mode, "status": "passed"})
            else:
                receipts.append({
                    "path": rel,
                    "mode": mode,
                    "status": "failed",
                    "reason": f"unknown_mode:{mode}",
                })
        except OSError as exc:
            receipts.append({
                "path": rel,
                "mode": mode,
                "status": "failed",
                "reason": f"os_error:{exc.__class__.__name__}",
            })
    overall = "passed" if receipts and all(
        r["status"] == "passed" for r in receipts
    ) else "failed"
    return overall, receipts


def _candidate_patch_text(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("model_patch") or candidate.get("patch") or "")


def _apply_model_patch_text(
    candidate: Mapping[str, Any],
    worktree: Path,
    *,
    timeout_s: float,
) -> tuple[str, list[dict[str, Any]]]:
    patch_text = _candidate_patch_text(candidate)
    patch_sha256 = sha256(patch_text.encode("utf-8")).hexdigest()
    if not patch_text.strip():
        return "failed", [{
            "mode": "model_patch",
            "status": "failed",
            "reason": "empty_model_patch",
            "patch_sha256": patch_sha256,
        }]

    patch_path = worktree.parent / "model.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    check_result = _run_command(
        ("git", "apply", "--check", str(patch_path)),
        cwd=worktree,
        timeout_s=timeout_s,
        name="model_patch_apply_check",
    )
    check_receipt = _command_receipt(check_result)
    if check_result.status != "passed":
        return "failed", [{
            "mode": "model_patch",
            "status": "failed",
            "reason": "git_apply_check_failed",
            "patch_sha256": patch_sha256,
            "check": check_receipt,
        }]

    apply_result = _run_command(
        ("git", "apply", str(patch_path)),
        cwd=worktree,
        timeout_s=timeout_s,
        name="model_patch_apply",
    )
    apply_receipt = _command_receipt(apply_result)
    if apply_result.status != "passed":
        return "failed", [{
            "mode": "model_patch",
            "status": "failed",
            "reason": "git_apply_failed",
            "patch_sha256": patch_sha256,
            "check": check_receipt,
            "apply": apply_receipt,
        }]
    return "passed", [{
        "mode": "model_patch",
        "status": "passed",
        "reason": "",
        "patch_sha256": patch_sha256,
        "check": check_receipt,
        "apply": apply_receipt,
    }]


def _apply_candidate_patch(
    candidate: Mapping[str, Any],
    worktree: Path,
    *,
    timeout_s: float,
) -> tuple[str, list[dict[str, Any]]]:
    patch_operations = list(candidate.get("patch_operations") or ())
    if patch_operations:
        return _apply_patch_operations(patch_operations, worktree)
    return _apply_model_patch_text(candidate, worktree, timeout_s=timeout_s)


def _command_receipt(result: Any) -> dict[str, Any]:
    payload = result.to_payload()
    return {
        "name": payload["name"],
        "argv": payload["argv"],
        "status": payload["status"],
        "returncode": payload["returncode"],
        "stdout_sha256": payload["stdout_sha256"],
        "stderr_sha256": payload["stderr_sha256"],
        "expected_failure": payload["expected_failure"],
    }


def _receipt_group_status(
    receipts: Sequence[Mapping[str, Any]],
    *,
    name: str,
    empty_status: str,
) -> str:
    matching = [
        receipt for receipt in receipts
        if str(receipt.get("name") or "") == name
    ]
    if not matching:
        return empty_status
    return "passed" if all(str(receipt.get("status") or "") == "passed" for receipt in matching) else "failed"


def _swebench_public_execution_evidence(
    *,
    public_packet: Mapping[str, Any],
    patch_apply_status: str,
    patch_apply_receipts: Sequence[Mapping[str, Any]],
    public_command_receipts: Sequence[Mapping[str, Any]],
    protected_paths: tuple[str, ...],
) -> dict[str, Any]:
    substrate = (
        dict(public_packet.get("s_probe_substrate") or {})
        if isinstance(public_packet.get("s_probe_substrate"), Mapping)
        else {}
    )
    public_command_status = _receipt_group_status(
        public_command_receipts,
        name="public_probe",
        empty_status="not_configured",
    )
    return {
        "schema_version": "supervisor-swebench-public-execution-evidence/v1",
        "evidence_boundary": "public_only",
        "patch_apply_check": {
            "status": patch_apply_status,
            "receipt_count": len(patch_apply_receipts),
            "receipts": [dict(receipt) for receipt in patch_apply_receipts],
        },
        "public_probe_commands": {
            "status": public_command_status,
            "receipt_count": len(public_command_receipts),
            "receipts": [dict(receipt) for receipt in public_command_receipts],
        },
        "public_ci_lint": {
            "status": public_command_status,
            "configured_lint_command_count": len(substrate.get("public_lint_commands") or []),
            "configured_build_command_count": len(substrate.get("public_build_commands") or []),
        },
        "reverse_classical_test_quality": {
            "status": "not_configured",
            "reason": "swe_bench_replay_packets_do_not_include_candidate_submitted_public_tests",
        },
        "scope_locality": {
            "status": "passed",
            "public_checkout_ref": str(public_packet.get("public_checkout_ref") or ""),
        },
        "protected_path_exclusion": {
            "status": "passed",
            "protected_path_content_included": False,
            "protected_path_count": len(protected_paths),
            "protected_paths_sha256": sha256(
                json.dumps(
                    sorted(protected_paths),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        },
        "hidden_oracle_exclusion": {
            "status": "passed",
            "hidden_oracle_material_included": False,
            "excluded_material_categories": [
                "held_out_fail_to_pass_tests",
                "held_out_pass_to_pass_tests",
                "answer_key_test_delta",
                "answer_key_acceptance_labels",
                "protected_path_content",
            ],
        },
    }


def _build_reviewer_packet(
    *,
    instance: Mapping[str, Any],
    candidate: Mapping[str, Any],
    public_packet: Mapping[str, Any],
    patch_apply_status: str,
    patch_apply_receipts: Sequence[Mapping[str, Any]],
    public_command_receipts: Sequence[Mapping[str, Any]],
    protected_paths: tuple[str, ...],
) -> dict[str, Any]:
    diff_text = str(candidate.get("patch") or "")
    diff_sha256 = sha256(diff_text.encode("utf-8")).hexdigest()
    public_task = {
        "instance_id": public_packet["instance_id"],
        "repo": public_packet["repo"],
        "base_commit": public_packet["base_commit"],
        "problem_statement": public_packet["problem_statement"],
        "public_checkout_ref": public_packet["public_checkout_ref"],
        "public_checkout_sha256": public_packet["public_checkout_sha256"],
    }
    packet: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_REVIEWER_PACKET_SCHEMA_VERSION,
        "public_task": public_task,
        "candidate_id": public_packet["candidate_id"],
        "candidate_artifact_hash": public_packet["candidate_artifact_hash"],
        "diff_sha256": diff_sha256,
        "patch_apply_status": patch_apply_status,
        "patch_apply_receipts": [dict(receipt) for receipt in patch_apply_receipts],
        "public_command_receipts": [dict(receipt) for receipt in public_command_receipts],
        "public_execution_evidence": _swebench_public_execution_evidence(
            public_packet=public_packet,
            patch_apply_status=patch_apply_status,
            patch_apply_receipts=patch_apply_receipts,
            public_command_receipts=public_command_receipts,
            protected_paths=protected_paths,
        ),
        "mergeability_rubric": {
            "schema_version": MERGEABILITY_RUBRIC_SCHEMA_VERSION,
            "allowed_labels": list(MERGEABILITY_RUBRIC_LABELS),
            "criteria": [
                "behavioral_correctness_from_public_evidence",
                "regression_risk_from_public_evidence",
                "test_quality_from_public_evidence",
                "scope_locality",
                "maintainability",
                "safety_or_suspicious_behavior",
                "unverifiable_from_public_evidence",
            ],
            "hidden_oracle_fail_is_not_public_reviewer_criterion": True,
            "labels_are_descriptive_not_scoring_authority": True,
        },
        "path_policy": {
            "protected_path_count": len(protected_paths),
            "protected_paths_sha256": sha256(
                json.dumps(
                    sorted(protected_paths),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        },
        "s_probe_substrate": dict(public_packet["s_probe_substrate"]),
    }
    return packet


def _classify_oracle_results(
    results: Sequence[Any],
) -> str:
    if not results:
        return "pass"
    return "pass" if all(r.status == "passed" for r in results) else "fail"


def _ensure_no_oracle_artifacts_yet(
    *,
    output_dir: Path,
    worktree: Path,
    protected_paths: tuple[str, ...],
) -> list[str]:
    refs: list[str] = []
    oracle_outputs_path = output_dir / "oracle_outputs.json"
    if oracle_outputs_path.exists():
        refs.append(f"oracle_outputs.json:exists_before_freeze")
    for protected in protected_paths:
        candidate_dir = worktree / protected.rstrip("/")
        if candidate_dir.exists():
            refs.append(f"public_worktree:{protected}")
    return refs


def _run_oracle_phase(
    *,
    worktree: Path,
    oracle_commands: Mapping[str, Sequence[Sequence[str]]],
    timeout_s: float,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    fail_to_pass_cmds = list(oracle_commands.get("fail_to_pass", ()) or ())
    pass_to_pass_cmds = list(oracle_commands.get("pass_to_pass", ()) or ())
    fail_to_pass_results = []
    for cmd in fail_to_pass_cmds:
        result = _run_command(
            tuple(str(part) for part in cmd),
            cwd=worktree,
            timeout_s=timeout_s,
            name="oracle_fail_to_pass",
        )
        fail_to_pass_results.append(result)
        receipts.append(_command_receipt(result))
    pass_to_pass_results = []
    for cmd in pass_to_pass_cmds:
        result = _run_command(
            tuple(str(part) for part in cmd),
            cwd=worktree,
            timeout_s=timeout_s,
            name="oracle_pass_to_pass",
        )
        pass_to_pass_results.append(result)
        receipts.append(_command_receipt(result))
    outcome = {
        "fail_to_pass_status": _classify_oracle_results(fail_to_pass_results),
        "pass_to_pass_status": _classify_oracle_results(pass_to_pass_results),
    }
    return outcome, receipts


def _normalise_oracle_adapter_outcome(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "oracle_runner result must be a mapping"
        )
    outcome = _interpret_oracle_outcome(raw)
    receipt = raw.get("oracle_adapter_receipt")
    if not isinstance(receipt, Mapping):
        receipt = {
            "status": "passed",
            "adapter_reported": True,
        }
    return {
        "fail_to_pass_status": outcome["fail_to_pass_status"],
        "pass_to_pass_status": outcome["pass_to_pass_status"],
        "oracle_unavailable": outcome["oracle_unavailable"],
        "oracle_unavailable_reason": outcome["oracle_unavailable_reason"],
        "patch_applied": outcome.get("patch_applied"),
    }, dict(receipt)


def _candidate_prediction_metadata(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in OFFICIAL_PREDICTION_METADATA_KEYS:
        if key not in candidate:
            continue
        value = candidate[key]
        if isinstance(value, Mapping):
            metadata[key] = dict(value)
        elif isinstance(value, list):
            metadata[key] = list(value)
        else:
            metadata[key] = value
    return metadata


def _normalise_protected_paths(value: Sequence[str] | None) -> tuple[str, ...]:
    if not value:
        base = list(_DEFAULT_PROTECTED_PATHS)
    else:
        base = [str(p) for p in value]
    for default_path in _DEFAULT_PROTECTED_PATHS:
        if default_path not in base:
            base.append(default_path)
    return tuple(sorted(set(base)))


def _candidate_oracle_commands(
    candidate: Mapping[str, Any],
) -> dict[str, list[list[str]]]:
    raw = candidate.get("oracle_commands") or {}
    if not isinstance(raw, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "candidate.oracle_commands must be a mapping with "
            "'fail_to_pass' and 'pass_to_pass' keys"
        )
    parsed: dict[str, list[list[str]]] = {"fail_to_pass": [], "pass_to_pass": []}
    for key in ("fail_to_pass", "pass_to_pass"):
        commands = raw.get(key) or ()
        for cmd in commands:
            parsed[key].append([str(part) for part in cmd])
    return parsed


def _strict_panel_decision(
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if raw is None:
        return {
            "accept": False,
            "unavailable": True,
            "reason": REVIEWER_PANEL_UNAVAILABLE_REASON,
            "reviewer_id": "",
            "reviewer_notes": "",
            "reviewer_ids": [],
            "available_reviewers": [],
            "missing_reviewers": [],
            "reviewer_results": [],
            "panel_decision": None,
            "full_roster_available": False,
            "failure_classification": "uninvoked",
        }
    if not isinstance(raw, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "reviewer_panel result must be a mapping with 'accept' and "
            "'unavailable' keys"
        )
    if "decision" in raw or "available" in raw:
        decision = str(raw.get("decision") or "").lower()
        available = bool(raw.get("available"))
        reviewer_ids = [str(item) for item in raw.get("reviewer_ids") or []]
        available_reviewers = [
            str(item) for item in raw.get("available_reviewers") or []
        ]
        missing_reviewers = [
            str(item) for item in raw.get("missing_reviewers") or []
        ]
        reviewer_results = _public_reviewer_result_summaries(
            raw.get("reviewer_results") or []
        )
        full_roster_available = bool(
            available
            and reviewer_ids
            and not missing_reviewers
            and set(available_reviewers) >= set(reviewer_ids)
        )
        unavailable = (not full_roster_available) or decision == "unavailable"
        accept = bool(full_roster_available and decision == "accept")
        failure_classification = _configured_panel_failure_classification(
            available=available,
            decision=decision,
            reviewer_ids=reviewer_ids,
            missing_reviewers=missing_reviewers,
            reviewer_results=reviewer_results,
            full_roster_available=full_roster_available,
            reason=str(raw.get("reason") or ""),
        )
        reviewer_id = (
            available_reviewers[0]
            if available_reviewers
            else (reviewer_ids[0] if reviewer_ids else "")
        )
        return {
            "accept": accept,
            "unavailable": unavailable,
            "reason": (
                failure_classification
                if unavailable
                else str(raw.get("reason") or "")
            ),
            "reviewer_id": reviewer_id,
            "reviewer_notes": str(raw.get("reviewer_notes") or ""),
            "reviewer_ids": reviewer_ids,
            "available_reviewers": available_reviewers,
            "missing_reviewers": missing_reviewers,
            "reviewer_results": reviewer_results,
            "panel_decision": raw.get("panel_decision"),
            "full_roster_available": full_roster_available,
            "failure_classification": failure_classification,
        }
    unavailable = bool(raw.get("unavailable"))
    accept = bool(raw.get("accept")) and not unavailable
    return {
        "accept": accept,
        "unavailable": unavailable,
        "reason": (
            REVIEWER_PANEL_UNAVAILABLE_REASON
            if unavailable
            else str(raw.get("reason") or "")
        ),
        "reviewer_id": str(raw.get("reviewer_id") or ""),
        "reviewer_notes": str(raw.get("reviewer_notes") or ""),
        "reviewer_ids": [str(raw.get("reviewer_id") or "")] if raw.get("reviewer_id") else [],
        "available_reviewers": [str(raw.get("reviewer_id") or "")] if raw.get("reviewer_id") and not unavailable else [],
        "missing_reviewers": [],
        "reviewer_results": [],
        "panel_decision": None,
        "full_roster_available": not unavailable,
        "failure_classification": (
            REVIEWER_PANEL_UNAVAILABLE_REASON if unavailable
            else ("available" if accept else "quality_reject")
        ),
    }


def _public_reviewer_result_summaries(raw_results: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
        return summaries
    for item in raw_results:
        if not isinstance(item, Mapping):
            continue
        classification = item.get("failure_classification")
        if classification is None and not bool(item.get("verdict_present")):
            classification = "reviewer_missing_verdict"
        summaries.append({
            "reviewer_id": str(item.get("reviewer_id") or "unknown-reviewer"),
            "runtime": str(item.get("runtime") or "unknown"),
            "model": item.get("model"),
            "provider_family": item.get("provider_family"),
            "lineage": list(item.get("lineage") or []),
            "tool_access": item.get("tool_access"),
            "tool_backed_command_evidence": bool(
                item.get("tool_backed_command_evidence")
            ),
            "assurance_grade": item.get("assurance_grade"),
            "verdict_present": bool(item.get("verdict_present")),
            "decision": str(item.get("decision") or ""),
            "accepted": bool(
                item.get("accepted", str(item.get("decision") or "") == "accept")
            ),
            "severity": str(item.get("severity") or ""),
            "mergeability_label": item.get("mergeability_label"),
            "mergeability_rubric": (
                dict(item.get("mergeability_rubric"))
                if isinstance(item.get("mergeability_rubric"), Mapping)
                else {}
            ),
            "abstention": bool(item.get("abstention")),
            "failure_classification": (
                str(classification) if classification is not None else None
            ),
            "transcript_sha256": item.get("transcript_sha256"),
            "output_sha256": item.get("output_sha256"),
        })
    return summaries


def _configured_panel_failure_classification(
    *,
    available: bool,
    decision: str,
    reviewer_ids: Sequence[str],
    missing_reviewers: Sequence[str],
    reviewer_results: Sequence[Mapping[str, Any]],
    full_roster_available: bool,
    reason: str,
) -> str:
    if not reviewer_ids:
        return "missing_reviewer_roster"
    if missing_reviewers:
        return "missing_reviewer_verdict"
    infrastructure_failures = [
        str(result.get("failure_classification") or "")
        for result in reviewer_results
        if result.get("failure_classification")
    ]
    if infrastructure_failures and not full_roster_available:
        return "reviewer_infrastructure_unavailable"
    if not available or decision == "unavailable":
        return reason or REVIEWER_PANEL_UNAVAILABLE_REASON
    if full_roster_available and decision in {"revise", "deny"}:
        return "quality_reject"
    if full_roster_available and decision == "accept":
        return "available"
    return REVIEWER_PANEL_UNAVAILABLE_REASON


def _swebench_reviewer_panel_preflight(
    reviewer_results: Sequence[Mapping[str, Any]],
    *,
    reviewer_panel_mode: str,
) -> dict[str, Any]:
    per_candidate = [dict(result) for result in reviewer_results]
    configured = reviewer_panel_mode == "configured"
    available_reviewers = sorted({
        reviewer
        for result in per_candidate
        for reviewer in (result.get("available_reviewers") or [])
    })
    missing_reviewers = sorted({
        reviewer
        for result in per_candidate
        for reviewer in (result.get("missing_reviewers") or [])
    })
    classifications = sorted({
        str(result.get("failure_classification") or "")
        for result in per_candidate
        if str(result.get("failure_classification") or "")
    })
    transcript_hashes = sorted({
        str(summary.get("transcript_sha256") or "")
        for result in per_candidate
        for summary in (result.get("reviewer_results") or [])
        if str(summary.get("transcript_sha256") or "")
    })
    output_hashes = sorted({
        str(summary.get("output_sha256") or "")
        for result in per_candidate
        for summary in (result.get("reviewer_results") or [])
        if str(summary.get("output_sha256") or "")
    })
    full_roster_available_count = sum(
        1 for result in per_candidate if bool(result.get("full_roster_available"))
    )
    unavailable_count = sum(
        1 for result in per_candidate if bool(result.get("unavailable"))
    )
    if not configured:
        aggregate = "uninvoked"
    elif any(c == "missing_reviewer_roster" for c in classifications):
        aggregate = "missing_reviewer_roster"
    elif missing_reviewers:
        aggregate = "missing_reviewer_verdict"
    elif any(c == "reviewer_infrastructure_unavailable" for c in classifications):
        aggregate = "reviewer_infrastructure_unavailable"
    elif per_candidate and full_roster_available_count == len(per_candidate):
        aggregate = (
            "quality_reject"
            if any(c == "quality_reject" for c in classifications)
            else "available"
        )
    else:
        aggregate = REVIEWER_PANEL_UNAVAILABLE_REASON
    return {
        "schema_version": "supervisor-swebench-reviewer-panel-preflight/v1",
        "mode": reviewer_panel_mode,
        "configured_panel_invoked": configured,
        "candidate_count": len(per_candidate),
        "full_roster_available": bool(
            configured and per_candidate and full_roster_available_count == len(per_candidate)
        ),
        "full_roster_available_count": full_roster_available_count,
        "unavailable_count": unavailable_count,
        "available_reviewers": available_reviewers,
        "missing_reviewers": missing_reviewers,
        "failure_classification": aggregate,
        "failure_classifications": classifications,
        "transcript_sha256s": transcript_hashes,
        "output_sha256s": output_hashes,
        "reviewer_results": per_candidate,
    }


def swebench_mergeability_fixture_runner(
    *,
    fixture_root: Path,
    instance: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    s_probe_substrate: Mapping[str, Any],
    public_commands: Sequence[Sequence[str]],
    output_dir: Path,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "custom",
    protected_paths: Sequence[str] | None = None,
    timeout_s: float = 30.0,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    oracle_adapter_kind: str = "candidate_oracle_commands",
) -> dict[str, Any]:
    """Run a fixture-first executable SWE-bench mergeability evaluation.

    The runner copies only public fixture material into a fresh public
    worktree per candidate, applies the candidate's ``patch_operations``,
    executes the configured public probe commands, builds a public-only
    reviewer packet, optionally calls ``reviewer_panel`` for an independent
    S_full decision, freezes baseline / S_probe / S_full decisions to disk,
    and only then runs the candidate's deterministic local oracle commands.
    Frozen decisions and oracle outcomes are passed to
    :func:`swebench_pro_mergeability_bridge_report` to produce a report-only
    FAR/TAR report. All artifacts are written under ``output_dir``.
    """
    fixture_root = Path(fixture_root)
    output_dir = Path(output_dir)
    if not fixture_root.exists():
        raise SwebenchMergeabilityFixtureRunnerError(
            f"fixture_root does not exist: {fixture_root}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    if not candidates:
        raise SwebenchMergeabilityFixtureRunnerError(
            "candidates must be a non-empty sequence"
        )

    instance_id = str(instance.get("instance_id") or "")
    if not instance_id:
        raise SwebenchMergeabilityFixtureRunnerError(
            "instance.instance_id is required"
        )
    protected_paths_tuple = _normalise_protected_paths(protected_paths)

    substrate = _validate_s_probe_substrate(s_probe_substrate)

    public_commands_list = [[str(part) for part in cmd] for cmd in public_commands]
    s_probe_execution_substrate = {
        "execution_label": (
            PUBLIC_COMMANDS_SUBSTRATE
            if public_commands_list
            else STATIC_LINT_ONLY_SUBSTRATE
        ),
        "public_command_count": len(public_commands_list),
        "hidden_oracle_excluded": True,
    }

    candidate_artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    arm_decisions: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
        ARM_BASELINE: {},
        ARM_S_PROBE: {},
        ARM_S_FULL: {},
    }
    oracle_outcomes: dict[tuple[str, str], dict[str, str]] = {}

    per_candidate_artifacts: list[dict[str, Any]] = []
    reviewer_packets: list[dict[str, Any]] = []
    reviewer_results: list[dict[str, Any]] = []
    freeze_phase_records: list[dict[str, Any]] = []
    oracle_phase_records: list[dict[str, Any]] = []
    worktree_isolation_records: list[dict[str, Any]] = []

    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            raise SwebenchMergeabilityFixtureRunnerError(
                "candidate.candidate_id is required"
            )
        key = (instance_id, candidate_id)
        per_candidate_dir = output_dir / "candidates" / candidate_id
        per_candidate_dir.mkdir(parents=True, exist_ok=True)
        public_worktree = per_candidate_dir / "public_worktree"
        public_worktree.mkdir(parents=True, exist_ok=True)

        _copy_public_fixture_tree(
            fixture_root,
            public_worktree,
            protected_paths=protected_paths_tuple,
        )

        protected_in_worktree: list[str] = []
        for protected in protected_paths_tuple:
            inside = public_worktree / protected.rstrip("/")
            if inside.exists():
                protected_in_worktree.append(protected)
        if protected_in_worktree:
            raise SwebenchMergeabilityFixtureRunnerError(
                "public worktree contains protected paths: "
                + ", ".join(protected_in_worktree)
            )

        patch_apply_status, patch_apply_receipts = _apply_candidate_patch(
            candidate,
            public_worktree,
            timeout_s=timeout_s,
        )

        if patch_apply_status == "passed" and public_commands_list:
            public_command_results = [
                _run_command(
                    tuple(cmd),
                    cwd=public_worktree,
                    timeout_s=timeout_s,
                    name="public_probe",
                )
                for cmd in public_commands_list
            ]
            public_command_receipts = [
                _command_receipt(result) for result in public_command_results
            ]
            public_command_status = (
                "passed"
                if all(r.status == "passed" for r in public_command_results)
                else "failed"
            )
        elif patch_apply_status == "passed":
            public_command_results = []
            public_command_receipts = []
            public_command_status = "passed"
        else:
            public_command_results = []
            public_command_receipts = []
            public_command_status = "not_executed"

        if substrate["requires_patch_applies"] and patch_apply_status != "passed":
            s_probe_decision = {
                "accept": False,
                "unavailable": False,
                "reason": PATCH_APPLY_FAILURE_REASON,
            }
        elif public_command_status == "passed":
            s_probe_decision = {
                "accept": True,
                "unavailable": False,
                "reason": "public_probe_passed",
            }
        else:
            s_probe_decision = {
                "accept": False,
                "unavailable": False,
                "reason": "public_probe_failed",
            }

        public_packet = build_swe_bench_pro_public_packet(
            instance=instance,
            candidate=candidate,
            s_probe_substrate=substrate,
        )

        reviewer_packet = _build_reviewer_packet(
            instance=instance,
            candidate=candidate,
            public_packet=public_packet,
            patch_apply_status=patch_apply_status,
            patch_apply_receipts=patch_apply_receipts,
            public_command_receipts=public_command_receipts,
            protected_paths=protected_paths_tuple,
        )
        packet_leak_refs = _scan_for_swebench_pro_oracle_refs(reviewer_packet)
        if packet_leak_refs:
            raise SwebenchMergeabilityFixtureRunnerError(
                "reviewer packet references forbidden oracle material: "
                + ", ".join(packet_leak_refs)
            )
        reviewer_packet_path = per_candidate_dir / "reviewer_packet.json"
        reviewer_packet_path.write_text(
            json.dumps(reviewer_packet, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        reviewer_packet_sha256 = _sha256_json(reviewer_packet)
        reviewer_packets.append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "reviewer_packet_path": str(reviewer_packet_path),
            "reviewer_packet_sha256": reviewer_packet_sha256,
        })

        if reviewer_panel is None:
            panel_raw = None
        else:
            panel_raw = reviewer_panel(reviewer_packet)
        panel_decision = _strict_panel_decision(panel_raw)
        reviewer_results.append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "reviewer_packet_sha256": reviewer_packet_sha256,
            "accept": panel_decision["accept"],
            "unavailable": panel_decision["unavailable"],
            "reason": panel_decision["reason"],
            "reviewer_id": panel_decision["reviewer_id"],
            "reviewer_notes": panel_decision["reviewer_notes"],
            "reviewer_ids": list(panel_decision["reviewer_ids"]),
            "available_reviewers": list(panel_decision["available_reviewers"]),
            "missing_reviewers": list(panel_decision["missing_reviewers"]),
            "reviewer_results": list(panel_decision["reviewer_results"]),
            "panel_decision": panel_decision["panel_decision"],
            "full_roster_available": panel_decision["full_roster_available"],
            "failure_classification": panel_decision["failure_classification"],
        })

        if panel_decision["unavailable"]:
            s_full_decision = {
                "accept": False,
                "unavailable": True,
                "reason": REVIEWER_PANEL_UNAVAILABLE_REASON,
            }
        else:
            # S_full requires BOTH S_probe accept AND panel accept; never imputed.
            s_full_decision = {
                "accept": bool(s_probe_decision["accept"] and panel_decision["accept"]),
                "unavailable": False,
                "reason": (
                    "public_probe_and_independent_reviewer_accept"
                    if s_probe_decision["accept"] and panel_decision["accept"]
                    else "public_probe_or_independent_reviewer_reject"
                ),
            }

        baseline_decision = _resolve_powered_baseline_decision(
            raw=_candidate_produced_baseline_receipt(candidate),
            expected_candidate_artifact_hash=str(
                public_packet["candidate_artifact_hash"]
            ),
            expected_candidate_id=candidate_id,
        )

        candidate_artifacts[key] = {
            "candidate_id": candidate_id,
            "patch": str(candidate.get("patch") or ""),
            "baseline_self_report": candidate.get("baseline_self_report"),
            **_candidate_prediction_metadata(candidate),
        }
        arm_decisions[ARM_BASELINE][key] = dict(baseline_decision)
        arm_decisions[ARM_S_PROBE][key] = {
            "accept": s_probe_decision["accept"],
            "unavailable": s_probe_decision["unavailable"],
        }
        arm_decisions[ARM_S_FULL][key] = {
            "accept": s_full_decision["accept"],
            "unavailable": s_full_decision["unavailable"],
        }
        per_candidate_artifacts.append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "patch_apply_status": patch_apply_status,
            "patch_apply_receipts": patch_apply_receipts,
            **_candidate_prediction_metadata(candidate),
            "public_command_status": public_command_status,
            "public_command_receipts": public_command_receipts,
            "s_probe_execution_substrate": dict(s_probe_execution_substrate),
            "baseline_decision": baseline_decision,
            "s_probe_decision": s_probe_decision,
            "s_full_decision": s_full_decision,
            "public_packet_sha256": _sha256_json(public_packet),
            "reviewer_packet_sha256": reviewer_packet_sha256,
            "public_worktree": str(public_worktree),
        })

    # Decision-freeze phase: write frozen decisions BEFORE any oracle execution.
    frozen_at = time.time()
    frozen_decisions_payload: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_FROZEN_DECISIONS_SCHEMA_VERSION,
        "instance_id": instance_id,
        "frozen_at_epoch_s": frozen_at,
        "rows": [
            {
                "instance_id": entry["instance_id"],
                "candidate_id": entry["candidate_id"],
                "public_packet_sha256": entry["public_packet_sha256"],
                "reviewer_packet_sha256": entry["reviewer_packet_sha256"],
                "baseline_accept": entry["baseline_decision"]["accept"],
                "baseline_unavailable": entry["baseline_decision"]["unavailable"],
                "baseline_decision_source": entry["baseline_decision"][
                    "decision_source"
                ],
                "baseline_evidence_kind": entry["baseline_decision"][
                    "evidence_kind"
                ],
                "baseline_candidate_id": entry["baseline_decision"][
                    "candidate_id"
                ],
                "baseline_candidate_artifact_hash": entry["baseline_decision"][
                    "candidate_artifact_hash"
                ],
                "baseline_producer": dict(entry["baseline_decision"]["producer"]),
                "baseline_prompt_sha256": entry["baseline_decision"]["prompt_sha256"],
                "baseline_unavailable_reason": entry["baseline_decision"][
                    "unavailable_reason"
                ],
                "s_probe_accept": entry["s_probe_decision"]["accept"],
                "s_probe_unavailable": entry["s_probe_decision"]["unavailable"],
                "s_probe_reason": entry["s_probe_decision"]["reason"],
                "s_full_accept": entry["s_full_decision"]["accept"],
                "s_full_unavailable": entry["s_full_decision"]["unavailable"],
                "s_full_reason": entry["s_full_decision"]["reason"],
            }
            for entry in per_candidate_artifacts
        ],
    }
    frozen_decisions_payload["frozen_decisions_sha256"] = _sha256_json(
        frozen_decisions_payload["rows"]
    )
    frozen_decisions_path = output_dir / "frozen_decisions.json"
    frozen_decisions_path.write_text(
        json.dumps(frozen_decisions_payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    freeze_phase_records.append({
        "frozen_decisions_path": str(frozen_decisions_path),
        "frozen_decisions_sha256": frozen_decisions_payload[
            "frozen_decisions_sha256"
        ],
        "frozen_at_epoch_s": frozen_at,
    })

    # Oracle phase: execute oracle commands or adapter AFTER freeze.
    oracle_outputs_path = output_dir / "oracle_outputs.json"
    if oracle_outputs_path.exists():
        raise SwebenchMergeabilityFixtureRunnerError(
            "oracle outputs already exist before decision freeze; refusing to "
            "overwrite to preserve freeze-before-oracle invariant"
        )

    oracle_phase_payload: dict[str, Any] = {
        "instance_id": instance_id,
        "rows": [],
    }
    for entry in per_candidate_artifacts:
        candidate_id = entry["candidate_id"]
        key = (instance_id, candidate_id)
        candidate = next(c for c in candidates if str(c.get("candidate_id")) == candidate_id)
        per_candidate_dir = output_dir / "candidates" / candidate_id
        public_worktree = Path(entry["public_worktree"])
        if oracle_runner is None:
            candidate_oracle = _candidate_oracle_commands(candidate)
            outcome, receipts = _run_oracle_phase(
                worktree=public_worktree,
                oracle_commands=candidate_oracle,
                timeout_s=timeout_s,
            )
            adapter_receipt: dict[str, Any] = {}
        else:
            adapter_context = {
                "instance_id": instance_id,
                "candidate_id": candidate_id,
                "public_worktree": str(public_worktree),
                "frozen_decisions_path": str(frozen_decisions_path),
                "frozen_decisions_sha256": frozen_decisions_payload[
                    "frozen_decisions_sha256"
                ],
                "oracle_adapter_kind": str(oracle_adapter_kind),
                "FAIL_TO_PASS": list(
                    candidate.get("FAIL_TO_PASS")
                    or instance.get("FAIL_TO_PASS")
                    or []
                ),
                "PASS_TO_PASS": list(
                    candidate.get("PASS_TO_PASS")
                    or instance.get("PASS_TO_PASS")
                    or []
                ),
                "fail_to_pass": _official_test_list(
                    candidate.get("fail_to_pass")
                    or instance.get("fail_to_pass")
                    or candidate.get("FAIL_TO_PASS")
                    or instance.get("FAIL_TO_PASS")
                    or []
                ),
                "pass_to_pass": _official_test_list(
                    candidate.get("pass_to_pass")
                    or instance.get("pass_to_pass")
                    or candidate.get("PASS_TO_PASS")
                    or instance.get("PASS_TO_PASS")
                    or []
                ),
                "before_repo_set_cmd": str(
                    candidate.get("before_repo_set_cmd")
                    or instance.get("before_repo_set_cmd")
                    or ""
                ),
                "selected_test_files_to_run": _official_test_list(
                    candidate.get("selected_test_files_to_run")
                    or instance.get("selected_test_files_to_run")
                    or []
                ),
                "dockerhub_tag": str(
                    candidate.get("dockerhub_tag")
                    or instance.get("dockerhub_tag")
                    or ""
                ),
                "swe_bench_pro_scripts_dir": str(
                    candidate.get("swe_bench_pro_scripts_dir")
                    or instance.get("swe_bench_pro_scripts_dir")
                    or ""
                ),
                "repo": str(instance.get("repo") or ""),
                "base_commit": str(instance.get("base_commit") or ""),
                "test_patch": str(
                    candidate.get("test_patch")
                    or instance.get("test_patch")
                    or ""
                ),
                "official_patch": str(
                    candidate.get("official_patch")
                    or instance.get("patch")
                    or ""
                ),
                "model_patch": _candidate_patch_text(candidate),
                "model_patch_ref": str(candidate.get("model_patch_ref") or ""),
                "model_patch_sha256": sha256(
                    _candidate_patch_text(candidate).encode("utf-8")
                ).hexdigest(),
            }
            outcome, adapter_receipt = _normalise_oracle_adapter_outcome(
                oracle_runner(adapter_context)
            )
            receipts = [{
                "name": "official_oracle_adapter",
                "status": "passed",
                "oracle_adapter_kind": str(oracle_adapter_kind),
                "receipt": adapter_receipt,
            }]
        oracle_outcomes[key] = outcome
        oracle_phase_payload["rows"].append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "fail_to_pass_status": outcome["fail_to_pass_status"],
            "pass_to_pass_status": outcome["pass_to_pass_status"],
            "patch_applied": outcome.get("patch_applied"),
            "oracle_unavailable": bool(outcome.get("oracle_unavailable")),
            "oracle_unavailable_reason": str(
                outcome.get("oracle_unavailable_reason") or ""
            ),
            "oracle_adapter_kind": str(oracle_adapter_kind),
            "oracle_command_receipts": receipts,
        })
        oracle_phase_records.append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "fail_to_pass_status": outcome["fail_to_pass_status"],
            "pass_to_pass_status": outcome["pass_to_pass_status"],
            "patch_applied": outcome.get("patch_applied"),
            "oracle_unavailable": bool(outcome.get("oracle_unavailable")),
            "oracle_unavailable_reason": str(
                outcome.get("oracle_unavailable_reason") or ""
            ),
        })

    oracle_outputs_path.write_text(
        json.dumps(oracle_phase_payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    bridge_report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts=candidate_artifacts,
        s_probe_substrate=substrate,
        arm_decisions=arm_decisions,
        oracle_outcomes=oracle_outcomes,
    )

    runner_report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION,
        "instance_id": instance_id,
        "reviewer_panel_mode": reviewer_panel_mode,
        "configured_reviewer_panel_preflight": _swebench_reviewer_panel_preflight(
            reviewer_results,
            reviewer_panel_mode=reviewer_panel_mode,
        ),
        "frozen_decisions": frozen_decisions_payload,
        "frozen_decisions_path": str(frozen_decisions_path),
        "oracle_outputs_path": str(oracle_outputs_path),
        "public_commands": public_commands_list,
        "oracle_adapter_kind": str(oracle_adapter_kind),
        "s_probe_execution_substrate": s_probe_execution_substrate,
        "protected_paths": list(protected_paths_tuple),
        "per_candidate_artifacts": per_candidate_artifacts,
        "reviewer_packets": reviewer_packets,
        "independent_reviewer_results": reviewer_results,
        "freeze_phase_records": freeze_phase_records,
        "oracle_phase_records": oracle_phase_records,
        "worktree_isolation_records": worktree_isolation_records,
        "bridge_report": bridge_report,
    }
    runner_report["report_sha256"] = _sha256_json({
        key: value
        for key, value in runner_report.items()
        if key not in ("bridge_report", "report_sha256")
    })

    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(runner_report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    runner_report["report_path"] = str(report_path)
    return runner_report


def load_swebench_mergeability_replay_manifest(
    manifest_path: str | Path,
) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"could not read replay manifest {path}: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "replay manifest must be a JSON object"
        )
    instances_raw = raw.get("instances")
    if not isinstance(instances_raw, Sequence) or isinstance(instances_raw, (str, bytes)):
        raise SwebenchMergeabilityFixtureRunnerError(
            "replay manifest instances must be a non-empty list"
        )

    base_dir = path.parent
    instances: list[dict[str, Any]] = []
    for raw_entry in instances_raw:
        if not isinstance(raw_entry, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                "replay manifest instance entries must be objects"
            )
        public_bundle = str(raw_entry.get("public_bundle") or "")
        if not public_bundle:
            raise SwebenchMergeabilityFixtureRunnerError(
                "replay manifest instance.public_bundle is required"
            )
        bundle_path = (base_dir / public_bundle).resolve()
        if not bundle_path.exists():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"replay public bundle does not exist: {bundle_path}"
            )
        instance = dict(raw_entry.get("instance") or {})
        if not instance:
            for key in _PUBLIC_PACKET_ALLOWED_INSTANCE_KEYS:
                if key in raw_entry:
                    instance[key] = raw_entry[key]
            for hidden_key in SWEBENCH_PRO_HIDDEN_ORACLE_KEYS:
                if hidden_key in raw_entry:
                    instance[hidden_key] = raw_entry[hidden_key]
            if "hidden_test_commands" in raw_entry:
                instance["hidden_test_commands"] = raw_entry["hidden_test_commands"]

        candidates_raw = raw_entry.get("candidates")
        if not isinstance(candidates_raw, Sequence) or isinstance(candidates_raw, (str, bytes)):
            raise SwebenchMergeabilityFixtureRunnerError(
                "replay manifest instance.candidates must be a non-empty list"
            )
        candidates: list[dict[str, Any]] = []
        for raw_candidate in candidates_raw:
            if not isinstance(raw_candidate, Mapping):
                raise SwebenchMergeabilityFixtureRunnerError(
                    "replay manifest candidate entries must be objects"
                )
            candidate = dict(raw_candidate)
            model_patch_path = (
                candidate.get("model_patch_path")
                or candidate.get("patch_path")
                or candidate.get("patch_ref")
            )
            if model_patch_path:
                patch_path = (base_dir / str(model_patch_path)).resolve()
                try:
                    candidate["model_patch"] = patch_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise SwebenchMergeabilityFixtureRunnerError(
                        f"could not read model patch artifact {patch_path}: {exc}"
                    ) from exc
                candidate["model_patch_ref"] = str(patch_path)
            if "patch" not in candidate and "model_patch" in candidate:
                candidate["patch"] = candidate["model_patch"]
            candidates.append(candidate)

        raw_substrate = raw_entry.get("s_probe_substrate")
        if raw_substrate is None:
            raw_substrate = {
                "kind": PUBLIC_STATIC_PATCH_PROBE,
                "requires_patch_applies": True,
                "public_lint_commands": raw_entry.get("public_lint_commands") or [],
                "public_build_commands": raw_entry.get("public_build_commands") or [],
            }
        substrate = _validate_s_probe_substrate(raw_substrate)
        public_commands = raw_entry.get("public_commands")
        if public_commands is None:
            public_commands = (
                list(substrate["public_lint_commands"])
                + list(substrate["public_build_commands"])
            )

        instances.append({
            "instance": instance,
            "public_bundle": str(bundle_path),
            "protected_paths": list(raw_entry.get("protected_paths") or _DEFAULT_PROTECTED_PATHS),
            "s_probe_substrate": substrate,
            "public_commands": [
                [str(part) for part in cmd] for cmd in (public_commands or [])
            ],
            "candidates": candidates,
        })

    if not instances:
        raise SwebenchMergeabilityFixtureRunnerError(
            "replay manifest contains no instances"
        )
    payload = {
        "schema_version": str(
            raw.get("schema_version") or SWEBENCH_MERGEABILITY_REPLAY_MANIFEST_SCHEMA_VERSION
        ),
        "manifest_path": str(path),
        "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
        "instances": instances,
    }
    return payload


def swebench_mergeability_replay_runner(
    *,
    manifest_path: str | Path,
    output_dir: str | Path,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "custom",
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
    timeout_s: float = 30.0,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    oracle_adapter_kind: str = "candidate_oracle_commands",
) -> dict[str, Any]:
    if reviewer_panel_mode not in {"custom", "configured"}:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"unknown reviewer_panel_mode: {reviewer_panel_mode!r}"
        )
    if reviewer_panel_mode == "configured" and reviewer_panel is None:
        reviewer_panel = build_configured_reviewer_panel(
            configured_reviewer_panel_options
        )

    manifest = load_swebench_mergeability_replay_manifest(manifest_path)
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    instance_reports: list[dict[str, Any]] = []
    combined_instances: list[Mapping[str, Any]] = []
    combined_candidate_artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    combined_arm_decisions: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
        ARM_BASELINE: {},
        ARM_S_PROBE: {},
        ARM_S_FULL: {},
    }
    combined_oracle_outcomes: dict[tuple[str, str], dict[str, str]] = {}
    aggregate_public_command_count = 0

    for item in manifest["instances"]:
        instance = dict(item["instance"])
        instance_id = str(instance.get("instance_id") or "")
        if not instance_id:
            raise SwebenchMergeabilityFixtureRunnerError(
                "replay instance.instance_id is required"
            )
        instance_output_dir = output_path / "instances" / instance_id
        instance_report = swebench_mergeability_fixture_runner(
            fixture_root=Path(item["public_bundle"]),
            instance=instance,
            candidates=item["candidates"],
            s_probe_substrate=item["s_probe_substrate"],
            public_commands=item["public_commands"],
            output_dir=instance_output_dir,
            reviewer_panel=reviewer_panel,
            reviewer_panel_mode=reviewer_panel_mode,
            protected_paths=item["protected_paths"],
            timeout_s=timeout_s,
            oracle_runner=oracle_runner,
            oracle_adapter_kind=oracle_adapter_kind,
        )
        instance_reports.append(instance_report)
        combined_instances.append(instance)
        aggregate_public_command_count += int(
            instance_report["s_probe_execution_substrate"]["public_command_count"]
        )

        candidates_by_id = {
            str(candidate.get("candidate_id") or ""): candidate
            for candidate in item["candidates"]
        }
        for frozen_row in instance_report["frozen_decisions"]["rows"]:
            candidate_id = str(frozen_row["candidate_id"])
            key = (instance_id, candidate_id)
            candidate = candidates_by_id[candidate_id]
            combined_candidate_artifacts[key] = {
                "candidate_id": candidate_id,
                "patch": _candidate_patch_text(candidate),
                "baseline_self_report": candidate.get("baseline_self_report"),
            }
            combined_arm_decisions[ARM_BASELINE][key] = {
                "accept": bool(frozen_row["baseline_accept"]),
                "unavailable": bool(frozen_row["baseline_unavailable"]),
                "decision_source": str(frozen_row["baseline_decision_source"]),
                "evidence_kind": str(frozen_row["baseline_evidence_kind"]),
                "candidate_id": str(frozen_row["baseline_candidate_id"]),
                "candidate_artifact_hash": str(
                    frozen_row["baseline_candidate_artifact_hash"]
                ),
                "producer": dict(frozen_row["baseline_producer"]),
                "prompt_sha256": str(frozen_row["baseline_prompt_sha256"]),
                "unavailable_reason": str(
                    frozen_row["baseline_unavailable_reason"]
                ),
            }
            combined_arm_decisions[ARM_S_PROBE][key] = {
                "accept": bool(frozen_row["s_probe_accept"]),
                "unavailable": bool(frozen_row["s_probe_unavailable"]),
            }
            combined_arm_decisions[ARM_S_FULL][key] = {
                "accept": bool(frozen_row["s_full_accept"]),
                "unavailable": bool(frozen_row["s_full_unavailable"]),
            }
        for oracle_row in json.loads(
            Path(instance_report["oracle_outputs_path"]).read_text(encoding="utf-8")
        )["rows"]:
            key = (str(oracle_row["instance_id"]), str(oracle_row["candidate_id"]))
            combined_oracle_outcomes[key] = {
                "fail_to_pass_status": str(oracle_row["fail_to_pass_status"]),
                "pass_to_pass_status": str(oracle_row["pass_to_pass_status"]),
                "patch_applied": oracle_row.get("patch_applied"),
                "oracle_unavailable": bool(oracle_row.get("oracle_unavailable")),
                "oracle_unavailable_reason": str(
                    oracle_row.get("oracle_unavailable_reason") or ""
                ),
            }

    combined_substrate = {
        "kind": PUBLIC_STATIC_PATCH_PROBE,
        "requires_patch_applies": True,
        "public_lint_commands": [],
        "public_build_commands": [],
    }
    bridge_report = swebench_pro_mergeability_bridge_report(
        instances=list(combined_instances),
        candidate_artifacts=combined_candidate_artifacts,
        s_probe_substrate=combined_substrate,
        arm_decisions=combined_arm_decisions,
        oracle_outcomes=combined_oracle_outcomes,
    )
    s_probe_execution_substrate = {
        "execution_label": (
            PUBLIC_COMMANDS_SUBSTRATE
            if aggregate_public_command_count
            else STATIC_LINT_ONLY_SUBSTRATE
        ),
        "public_command_count": aggregate_public_command_count,
        "hidden_oracle_excluded": True,
    }
    combined_reviewer_results = [
        result
        for instance_report in instance_reports
        for result in (instance_report.get("independent_reviewer_results") or [])
    ]
    report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_REPLAY_REPORT_SCHEMA_VERSION,
        "manifest_path": manifest["manifest_path"],
        "manifest_sha256": manifest["manifest_sha256"],
        "instance_count": len(combined_instances),
        "candidate_count": len(combined_candidate_artifacts),
        "s_probe_execution_substrate": s_probe_execution_substrate,
        "reviewer_panel_mode": reviewer_panel_mode,
        "configured_reviewer_panel_preflight": _swebench_reviewer_panel_preflight(
            combined_reviewer_results,
            reviewer_panel_mode=reviewer_panel_mode,
        ),
        "oracle_adapter_kind": str(oracle_adapter_kind),
        "instance_reports": instance_reports,
        "bridge_report": bridge_report,
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "allow_live": False,
        "live_fetch_used": False,
        "live_generation_used": False,
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path = output_path / "report.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def swebench_mergeability_official_replay_runner(
    *,
    dataset: str,
    dataset_split: str,
    predictions_path: str | Path,
    output_dir: str | Path,
    allow_dataset_fetch: bool = False,
    dataset_loader: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
    repo_materializer: Callable[..., str | Path] | None = None,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    oracle_adapter_kind: str = DEFAULT_OFFICIAL_ORACLE_ADAPTER_KIND,
    instance_ids: Sequence[str] | None = None,
    limit: int | None = None,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "custom",
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
    timeout_s: float = 30.0,
    swe_bench_pro_scripts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Replay official-style SWE-bench records through mergeability machinery.

    This adapter intentionally sits above ``swebench_mergeability_replay_runner``:
    it loads official rows, materializes public-only bundles, resolves model
    patches from predictions JSONL, and delegates packet isolation plus FAR/TAR
    aggregation to the existing replay/bridge path. Hidden official fields are
    given only to ``oracle_runner`` after decision freeze.
    """
    started = time.monotonic()
    if dataset_loader is None and not allow_dataset_fetch:
        raise SwebenchMergeabilityFixtureRunnerError(
            "refusing official SWE-bench replay without allow_dataset_fetch "
            "(--allow-dataset-fetch) or an injected dataset_loader"
        )
    if oracle_runner is None:
        raise SwebenchMergeabilityFixtureRunnerError(
            "oracle_runner is required for official SWE-bench replay"
        )
    _validate_official_replay_oracle_adapter_kind(str(oracle_adapter_kind))

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    loader = dataset_loader or _default_official_dataset_loader
    materializer = repo_materializer or _default_official_repo_materializer
    raw_records = loader(dataset=dataset, split=dataset_split)
    if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
        raw_records = list(raw_records)  # type: ignore[arg-type]
    official_records = [dict(record) for record in raw_records]
    if not official_records:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official SWE-bench replay dataset loader returned no records"
        )

    requested_instance_ids = [str(i) for i in (instance_ids or [])]
    requested_limit = int(limit) if limit else 0
    selection_filter: dict[str, Any] = {
        "instance_ids_requested": list(requested_instance_ids),
        "limit_requested": requested_limit,
    }
    if requested_instance_ids:
        wanted = set(requested_instance_ids)
        available = {str(r.get("instance_id") or "") for r in official_records}
        missing = sorted(wanted - available)
        if missing:
            raise SwebenchMergeabilityFixtureRunnerError(
                "requested --instance-id values missing from dataset: "
                + ", ".join(missing)
            )
        official_records = [
            r for r in official_records
            if str(r.get("instance_id") or "") in wanted
        ]
    if requested_limit > 0:
        official_records = sorted(
            official_records,
            key=lambda r: str(r.get("instance_id") or ""),
        )[:requested_limit]
    selected_instance_ids = [
        str(r.get("instance_id") or "") for r in official_records
    ]
    selection_filter["selected_instance_ids"] = list(selected_instance_ids)
    if not official_records:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official SWE-bench replay selection filter excluded all records"
        )
    run_scripts_preflight: dict[str, Any] = {}
    if _official_replay_should_preflight_pro_scripts(
        oracle_runner=oracle_runner,
        swe_bench_pro_scripts_dir=swe_bench_pro_scripts_dir,
    ):
        scripts_dir = (
            Path(swe_bench_pro_scripts_dir).expanduser()
            if swe_bench_pro_scripts_dir is not None
            else swe_bench_pro_oracle_scripts_dir()
        )
        run_scripts_preflight = preflight_swe_bench_pro_run_scripts(
            selected_instance_ids,
            scripts_dir=scripts_dir,
        )
        selection_filter["swe_bench_pro_scripts_dir"] = str(
            run_scripts_preflight["scripts_dir"]
        )
        selection_filter["swe_bench_pro_run_scripts_preflight"] = dict(
            run_scripts_preflight
        )
        if not run_scripts_preflight["ok"]:
            raise SwebenchMergeabilityFixtureRunnerError(
                str(run_scripts_preflight["reason"])
            )

    predictions = _load_official_predictions(predictions_path)
    selected_set = set(selected_instance_ids)
    predictions = {
        key: value
        for key, value in predictions.items()
        if key in selected_set
    }

    manifest_instances: list[dict[str, Any]] = []
    patch_dir = output_path / "official-patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    materialized_dir = output_path / "official-public-bundles"
    materialized_dir.mkdir(parents=True, exist_ok=True)

    for record in official_records:
        instance_id = str(record.get("instance_id") or "")
        if not instance_id:
            raise SwebenchMergeabilityFixtureRunnerError(
                "official record.instance_id is required"
            )
        record_predictions = predictions.get(instance_id) or []
        if not record_predictions:
            raise SwebenchMergeabilityFixtureRunnerError(
                f"prediction missing for official instance {instance_id}"
            )

        public_record = _official_public_record(record)
        public_bundle = materializer(
            record=public_record,
            output_dir=materialized_dir / _safe_artifact_fragment(instance_id),
        )
        public_bundle_path = Path(public_bundle).expanduser().resolve()
        if not public_bundle_path.exists():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"repo_materializer returned missing public bundle: {public_bundle_path}"
            )
        _ensure_public_bundle_has_no_git(
            public_bundle_path,
            context=f"official replay public bundle {instance_id}",
        )

        hidden = _official_hidden_oracle(record)
        candidates: list[dict[str, Any]] = []
        for prediction in record_predictions:
            candidate_id = str(prediction["candidate_id"])
            model_patch = str(prediction["model_patch"])
            patch_path = patch_dir / _safe_artifact_filename(
                instance_id,
                candidate_id,
                suffix=".patch",
            )
            patch_path.write_text(model_patch, encoding="utf-8")
            candidate: dict[str, Any] = {
                "candidate_id": candidate_id,
                "model_patch_path": str(patch_path),
                "FAIL_TO_PASS": list(hidden["FAIL_TO_PASS"]),
                "PASS_TO_PASS": list(hidden["PASS_TO_PASS"]),
                "fail_to_pass": list(hidden["fail_to_pass"]),
                "pass_to_pass": list(hidden["pass_to_pass"]),
                "before_repo_set_cmd": str(hidden["before_repo_set_cmd"]),
                "selected_test_files_to_run": list(
                    hidden["selected_test_files_to_run"]
                ),
                "dockerhub_tag": str(hidden["dockerhub_tag"]),
                "test_patch": str(hidden["test_patch"]),
                "official_patch": str(hidden["patch"]),
            }
            if run_scripts_preflight:
                candidate["swe_bench_pro_scripts_dir"] = str(
                    run_scripts_preflight["scripts_dir"]
                )
            for key in PRODUCED_BASELINE_RECEIPT_KEYS:
                if key in prediction and isinstance(prediction[key], Mapping):
                    candidate[key] = dict(prediction[key])
                    break
            for key in OFFICIAL_PREDICTION_METADATA_KEYS:
                if key not in prediction:
                    continue
                value = prediction[key]
                if isinstance(value, Mapping):
                    candidate[key] = dict(value)
                elif isinstance(value, list):
                    candidate[key] = list(value)
                else:
                    candidate[key] = value
            candidates.append(candidate)

        manifest_instances.append({
            **public_record,
            "FAIL_TO_PASS": list(hidden["FAIL_TO_PASS"]),
            "PASS_TO_PASS": list(hidden["PASS_TO_PASS"]),
            "fail_to_pass": list(hidden["fail_to_pass"]),
            "pass_to_pass": list(hidden["pass_to_pass"]),
            "before_repo_set_cmd": str(hidden["before_repo_set_cmd"]),
            "selected_test_files_to_run": list(hidden["selected_test_files_to_run"]),
            "dockerhub_tag": str(hidden["dockerhub_tag"]),
            "test_patch": str(hidden["test_patch"]),
            "swe_bench_pro_scripts_dir": str(
                run_scripts_preflight.get("scripts_dir") or ""
            ),
            "public_bundle": str(public_bundle_path),
            "protected_paths": list(_DEFAULT_PROTECTED_PATHS),
            "s_probe_substrate": {
                "kind": PUBLIC_STATIC_PATCH_PROBE,
                "requires_patch_applies": True,
                "public_lint_commands": list(
                    public_record.get("public_lint_commands") or []
                ),
                "public_build_commands": list(
                    public_record.get("public_build_commands") or []
                ),
            },
            "public_commands": list(
                public_record.get("public_commands")
                or public_record.get("public_lint_commands")
                or []
            ),
            "candidates": candidates,
        })

    generated_manifest = {
        "schema_version": SWEBENCH_MERGEABILITY_REPLAY_MANIFEST_SCHEMA_VERSION,
        "official_replay_dataset": str(dataset),
        "official_replay_split": str(dataset_split),
        "selection_filter": dict(selection_filter),
        "instances": manifest_instances,
    }
    generated_manifest_path = output_path / "official_replay_manifest.json"
    generated_manifest_path.write_text(
        json.dumps(generated_manifest, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )

    replay_report = swebench_mergeability_replay_runner(
        manifest_path=generated_manifest_path,
        output_dir=output_path / "replay",
        reviewer_panel=reviewer_panel,
        reviewer_panel_mode=reviewer_panel_mode,
        configured_reviewer_panel_options=configured_reviewer_panel_options,
        timeout_s=timeout_s,
        oracle_runner=oracle_runner,
        oracle_adapter_kind=oracle_adapter_kind,
    )
    receipt_validation = _validate_official_oracle_receipts(
        oracle_adapter_kind=str(oracle_adapter_kind),
        replay_report=replay_report,
    )
    label_validation = _validate_official_equivalent_labels(
        oracle_adapter_kind=str(oracle_adapter_kind),
        replay_report=replay_report,
    )
    leak_check = _scan_official_replay_public_artifacts_for_leaks(
        replay_report=replay_report,
    )
    bridge_metrics_suppressed = bool(
        (replay_report.get("bridge_report") or {}).get("metrics_suppressed")
    )
    report_status = "completed"
    if not receipt_validation["validated"]:
        report_status = "unavailable"
    if not label_validation["validated"]:
        report_status = "unavailable"
    if leak_check["refs"]:
        report_status = "unavailable"
    if bridge_metrics_suppressed:
        report_status = "unavailable"
    unavailable_reasons = _official_replay_unavailable_reasons(
        receipt_validation=receipt_validation,
        label_validation=label_validation,
        leak_check=leak_check,
        replay_report=replay_report,
    )
    replay_report_for_output = (
        _suppress_unavailable_official_replay_metrics(
            replay_report=replay_report,
            unavailable_reasons=unavailable_reasons,
        )
        if report_status == "unavailable"
        else replay_report
    )
    report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_OFFICIAL_REPLAY_REPORT_SCHEMA_VERSION,
        "status": report_status,
        "dataset": str(dataset),
        "dataset_split": str(dataset_split),
        "allow_dataset_fetch": bool(allow_dataset_fetch),
        "official_replay_used": True,
        "live_fetch_used": False,
        "live_generation_used": False,
        "predictions_path": str(Path(predictions_path).expanduser()),
        "generated_replay_manifest_path": str(generated_manifest_path),
        "generated_replay_manifest_sha256": sha256(
            generated_manifest_path.read_bytes()
        ).hexdigest(),
        "oracle_adapter_kind": str(oracle_adapter_kind),
        "instance_count": len(manifest_instances),
        "candidate_count": sum(
            len(entry["candidates"]) for entry in manifest_instances
        ),
        "selection_filter": dict(selection_filter),
        "swe_bench_pro_run_scripts_preflight": dict(run_scripts_preflight),
        "oracle_receipt_validation": receipt_validation,
        "label_validation": label_validation,
        "hidden_field_leak_check": leak_check,
        "metrics_unavailable_reasons": unavailable_reasons,
        "configured_reviewer_panel_preflight": replay_report_for_output.get(
            "configured_reviewer_panel_preflight",
            {},
        ),
        "replay_report": replay_report_for_output,
        "bridge_report": replay_report_for_output["bridge_report"],
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "plumbing_smoke_only": True,
        "powered_improvement_claim_allowed": False,
        "human_mergeability_claim_allowed": False,
        "smoke_caveats": [
            "swebench_verified_is_test_pass_proxy_not_human_mergeability",
            "swebench_verified_contamination_possible",
        ],
        "wall_clock_s": round(time.monotonic() - started, 6),
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path = output_path / "official_replay_report.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def swebench_mergeability_official_all_arms_diagnostic_runner(
    *,
    dataset: str,
    dataset_split: str,
    predictions_path: str | Path,
    output_dir: str | Path,
    allow_dataset_fetch: bool = False,
    dataset_loader: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
    repo_materializer: Callable[..., str | Path] | None = None,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    oracle_adapter_kind: str = DEFAULT_OFFICIAL_ORACLE_ADAPTER_KIND,
    instance_ids: Sequence[str] | None = None,
    limit: int | None = None,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "configured",
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
    timeout_s: float = 30.0,
    min_good: int = 1,
    min_bad: int = 1,
    swe_bench_pro_scripts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run a small official-oracle diagnostic and require all arms to populate.

    This is still diagnostic/report-only. It proves that official replay,
    produced baseline receipts, S_probe, configured S_full, and oracle ceiling
    can all emit rows before any powered benchmark claim is considered.
    """
    output_path = Path(output_dir).expanduser().resolve()
    effective_reviewer_panel = reviewer_panel
    if (
        reviewer_panel_mode == "configured"
        and effective_reviewer_panel is None
        and configured_reviewer_panel_options is None
    ):
        # Diagnostic mode must fail closed without accidentally launching live
        # reviewer infrastructure from a missing injection.
        effective_reviewer_panel = _official_all_arms_unavailable_reviewer_panel
    try:
        official_report = swebench_mergeability_official_replay_runner(
            dataset=dataset,
            dataset_split=dataset_split,
            predictions_path=predictions_path,
            output_dir=output_path,
            allow_dataset_fetch=allow_dataset_fetch,
            dataset_loader=dataset_loader,
            repo_materializer=repo_materializer,
            oracle_runner=oracle_runner,
            oracle_adapter_kind=oracle_adapter_kind,
            instance_ids=instance_ids,
            limit=limit,
            reviewer_panel=effective_reviewer_panel,
            reviewer_panel_mode=reviewer_panel_mode,
            configured_reviewer_panel_options=configured_reviewer_panel_options,
            timeout_s=timeout_s,
            swe_bench_pro_scripts_dir=swe_bench_pro_scripts_dir,
        )
    except SwebenchMergeabilityFixtureRunnerError as exc:
        if "forbidden oracle material" not in str(exc):
            raise
        official_report = _official_all_arms_leak_unavailable_official_report(
            dataset=dataset,
            dataset_split=dataset_split,
            oracle_adapter_kind=oracle_adapter_kind,
            reason=str(exc),
        )
    report = _build_official_all_arms_diagnostic_report(
        official_report=official_report,
        min_good=min_good,
        min_bad=min_bad,
    )
    report_path = output_path / "official_all_arms_diagnostic_report.json"
    report["report_path"] = str(report_path)
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def _official_all_arms_leak_unavailable_official_report(
    *,
    dataset: str,
    dataset_split: str,
    oracle_adapter_kind: str,
    reason: str,
) -> dict[str, Any]:
    leak_check = {
        "ok": False,
        "refs": [reason],
    }
    bridge_report = {
        "schema_version": SWEBENCH_PRO_BRIDGE_REPORT_SCHEMA_VERSION,
        "status": "unavailable",
        "instance_count": 0,
        "candidate_count": 0,
        "arms": {},
        "per_row_results": [],
        "metrics_suppressed": True,
        "metrics_unavailable_reasons": ["hidden_field_leak_detected"],
        "oracle_isolation": {
            "ok": False,
            "violations": [{
                "reason": "hidden_field_leak_detected",
                "refs": [reason],
            }],
        },
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }
    return {
        "status": "unavailable",
        "dataset": str(dataset),
        "dataset_split": str(dataset_split),
        "oracle_adapter_kind": str(oracle_adapter_kind),
        "instance_count": 0,
        "candidate_count": 0,
        "report_path": "",
        "report_sha256": "",
        "hidden_field_leak_check": leak_check,
        "configured_reviewer_panel_preflight": {},
        "metrics_unavailable_reasons": ["hidden_field_leak_detected"],
        "bridge_report": bridge_report,
    }


def _official_all_arms_unavailable_reviewer_panel(
    _packet: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "decision": "unavailable",
        "available": False,
        "reason": "reviewer_panel_not_configured_for_diagnostic",
        "reviewer_ids": ["configured-reviewer-panel"],
        "available_reviewers": [],
        "missing_reviewers": ["configured-reviewer-panel"],
        "reviewer_results": [
            {
                "reviewer_id": "configured-reviewer-panel",
                "runtime": "configured",
                "model": None,
                "verdict_present": False,
                "failure_classification": "reviewer_panel_not_configured_for_diagnostic",
            }
        ],
    }


def write_swebench_official_all_arms_blocked_artifact(
    *,
    output_dir: str | Path,
    blocked_reasons: Sequence[str],
    dataset: str = "",
    dataset_split: str = "",
    predictions_path: str | Path | None = None,
    oracle_adapter_kind: str = "",
    min_good: int = 1,
    min_bad: int = 1,
    attempt_stage: str = "recon",
    attempt_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the AEB-0 artifact when a pre-run blocker prevents replay."""
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    reasons = [str(reason) for reason in blocked_reasons if str(reason)]
    if not reasons:
        reasons = ["real_official_all_arms_artifact_required"]
    dataset_readiness = _aeb0_dataset_readiness(dataset)
    bridge_report = _unavailable_official_bridge_report(
        unavailable_reasons=reasons,
    )
    report: dict[str, Any] = {
        "schema_version": (
            SWEBENCH_MERGEABILITY_OFFICIAL_ALL_ARMS_DIAGNOSTIC_SCHEMA_VERSION
        ),
        "status": "unavailable",
        "dataset": str(dataset or ""),
        "dataset_split": str(dataset_split or ""),
        "predictions_path": str(Path(predictions_path).expanduser())
        if predictions_path
        else "",
        "official_replay_report_path": "",
        "official_replay_report_sha256": "",
        "official_replay_status": "not_started",
        "attempt_stage": str(attempt_stage or "recon"),
        "attempt_evidence": dict(attempt_evidence or {}),
        "oracle_adapter_kind": str(oracle_adapter_kind or ""),
        "official_replay_used": False,
        "diagnostic_only": True,
        "plumbing_smoke_only": True,
        "instance_count": 0,
        "candidate_count": 0,
        "n_good": 0,
        "n_bad": 0,
        "min_good": int(min_good),
        "min_bad": int(min_bad),
        "arms_present": [],
        "missing_arms": list(ARM_NAMES),
        "all_arms_populated": False,
        "baseline_available": False,
        "s_probe_available": False,
        "s_full_available": False,
        "oracle_ceiling_available": False,
        "configured_reviewer_panel_preflight": {},
        "hidden_field_leak_check": {},
        "matched_true_accept_status": {
            ARM_S_PROBE: "unavailable",
            ARM_S_FULL: "unavailable",
        },
        "supervisor_full_gate_matched_true_accept": {},
        "false_accept_reduction_at_matched_true_accept": {},
        "s_probe_vs_s_full": {},
        "reviewer_marginal_delta_at_matched_true_accept": {},
        "far_tar_frr": None,
        "benchmark_oracle": {
            "kind": "swe_bench_held_out_test_pass_proxy",
            "oracle_adapter_kind": str(oracle_adapter_kind or ""),
            "scoring_authority": "held_out_fail_to_pass_and_pass_to_pass_tests",
            "maintainer_mergeability_claim_allowed": False,
            "limitation_note": (
                "SWE-bench reports held-out test-pass behavior; it is not a "
                "maintainer would-merge judgment."
            ),
        },
        "swe_bench_oracle_limitation_note": (
            "SWE-bench held-out tests are a test-pass proxy, not human "
            "maintainer mergeability."
        ),
        "no_maintainer_mergeability_claim": True,
        "decision_freeze": {},
        "candidate_generation": {},
        "reviewer_provenance": {},
        "abstention_coverage": {},
        "mergeability_rubric_coverage": {},
        "generator_disjointness": {},
        "self_preference_warnings": [],
        "metrics_unavailable_reasons": list(reasons),
        "all_arms_populated_is_not_success_without_matched_tar": True,
        "diagnostic_ready_for_scale": False,
        "dataset_readiness": dataset_readiness,
        "aeb0_artifact_gate": _aeb0_artifact_gate(
            status="unavailable",
            blocked_reasons=reasons,
            dataset_readiness=dataset_readiness,
        ),
        "real_evidence_claim_blocked_reasons": _aeb0_real_claim_blocked_reasons(
            blocked_reasons=reasons,
            dataset_readiness=dataset_readiness,
        ),
        "bridge_report": bridge_report,
        **_aeb0_authority_flags(),
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
    }
    report_path = output_path / "official_all_arms_diagnostic_report.json"
    report["report_path"] = str(report_path)
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def _build_official_all_arms_diagnostic_report(
    *,
    official_report: Mapping[str, Any],
    min_good: int,
    min_bad: int,
) -> dict[str, Any]:
    bridge = official_report.get("bridge_report") or {}
    arms = bridge.get("arms") if isinstance(bridge, Mapping) else {}
    if not isinstance(arms, Mapping):
        arms = {}
    rows = bridge.get("per_row_results") if isinstance(bridge, Mapping) else []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        rows = []
    missing_arms = [arm for arm in ARM_NAMES if arm not in arms]
    baseline = arms.get(ARM_BASELINE) if isinstance(arms.get(ARM_BASELINE), Mapping) else {}
    s_probe = arms.get(ARM_S_PROBE) if isinstance(arms.get(ARM_S_PROBE), Mapping) else {}
    s_full = arms.get(ARM_S_FULL) if isinstance(arms.get(ARM_S_FULL), Mapping) else {}
    oracle_ceiling = (
        arms.get(ARM_ORACLE_CEILING)
        if isinstance(arms.get(ARM_ORACLE_CEILING), Mapping) else {}
    )
    matched_all = (
        bridge.get("false_accept_at_matched_true_accept")
        if isinstance(bridge, Mapping) else {}
    )
    if not isinstance(matched_all, Mapping):
        matched_all = {}
    matched_status = {
        ARM_S_PROBE: str(
            (matched_all.get(ARM_S_PROBE) or {}).get("status")
            if isinstance(matched_all.get(ARM_S_PROBE), Mapping)
            else "unavailable"
        ),
        ARM_S_FULL: str(
            (matched_all.get(ARM_S_FULL) or {}).get("status")
            if isinstance(matched_all.get(ARM_S_FULL), Mapping)
            else "unavailable"
        ),
    }
    n_good = sum(
        1
        for row in rows
        if isinstance(row, Mapping)
        and bool(row.get("oracle_accept"))
        and not bool(row.get("oracle_unavailable"))
    )
    n_bad = sum(
        1
        for row in rows
        if isinstance(row, Mapping)
        and not bool(row.get("oracle_accept"))
        and not bool(row.get("oracle_unavailable"))
    )
    preflight = official_report.get("configured_reviewer_panel_preflight") or {}
    if not isinstance(preflight, Mapping):
        preflight = {}
    baseline_available = (
        bool(baseline)
        and baseline.get("availability_status") == "available"
        and int(baseline.get("unavailable_count") or 0) == 0
    )
    s_probe_available = (
        bool(s_probe)
        and s_probe.get("availability_status") == "available"
        and int(s_probe.get("unavailable_count") or 0) == 0
    )
    s_full_available = (
        bool(s_full)
        and s_full.get("availability_status") == "available"
        and int(s_full.get("unavailable_count") or 0) == 0
    )
    oracle_ceiling_available = (
        bool(oracle_ceiling)
        and oracle_ceiling.get("availability_status") == "available"
        and int(oracle_ceiling.get("unavailable_count") or 0) == 0
    )
    arm_execution_populated = bool(
        not missing_arms
        and baseline_available
        and s_probe_available
        and s_full_available
        and oracle_ceiling_available
    )
    reviewer_analysis_rows = _official_reviewer_analysis_rows(
        official_report=official_report,
    )
    reviewer_provenance = _reviewer_provenance_report(reviewer_analysis_rows)
    generator_disjointness = _generator_disjointness_report(reviewer_analysis_rows)
    reviewer_roster_cross_family_verification = (
        _official_reviewer_roster_cross_family_verification(reviewer_analysis_rows)
    )

    unavailable_reasons: list[str] = []
    if official_report.get("status") != "completed":
        unavailable_reasons.append("official_replay_unavailable")
    if missing_arms:
        unavailable_reasons.append("missing_arms:" + ",".join(missing_arms))
    if not baseline_available:
        unavailable_reasons.append("baseline_unavailable")
    if not s_probe_available:
        unavailable_reasons.append("s_probe_unavailable")
    if not s_full_available:
        unavailable_reasons.append("s_full_unavailable")
    if not oracle_ceiling_available:
        unavailable_reasons.append("oracle_ceiling_unavailable")
    if not bool(preflight.get("full_roster_available")):
        unavailable_reasons.append("reviewer_panel_full_roster_unavailable")
    if n_good < int(min_good):
        unavailable_reasons.append("insufficient_oracle_good")
    if n_bad < int(min_bad):
        unavailable_reasons.append("insufficient_oracle_bad")
    leak_check = official_report.get("hidden_field_leak_check") or {}
    if isinstance(leak_check, Mapping) and not bool(leak_check.get("ok", True)):
        unavailable_reasons.append("hidden_field_leak_detected")
    if matched_status[ARM_S_FULL] != "computed":
        unavailable_reasons.append(
            "matched_true_accept_not_computed:" + matched_status[ARM_S_FULL]
        )
    if isinstance(bridge, Mapping) and bridge.get("metrics_suppressed"):
        unavailable_reasons.extend(
            str(reason)
            for reason in (bridge.get("metrics_unavailable_reasons") or [])
            if str(reason)
        )
    if not bool(reviewer_roster_cross_family_verification.get("verified")):
        unavailable_reasons.append("reviewer_roster_not_verified_cross_family")
        unavailable_reasons.extend(
            str(reason)
            for reason in (
                reviewer_roster_cross_family_verification.get("reasons") or []
            )
            if str(reason)
        )
    status = "completed" if not unavailable_reasons else "unavailable"
    blocked_reasons = sorted(set(unavailable_reasons))
    dataset_readiness = _aeb0_dataset_readiness(str(official_report.get("dataset") or ""))
    reviewer_independence = _official_reviewer_independence_metrics(
        reviewer_analysis_rows,
        full_stack=s_full,
        roster_verified=bool(reviewer_roster_cross_family_verification.get("verified")),
    )
    abstention_coverage = _mergeability_rubric_coverage(reviewer_analysis_rows)
    panel_marginal = (
        dict(bridge.get("panel_marginal_delta_at_matched_true_accept"))
        if isinstance(bridge, Mapping)
        and isinstance(bridge.get("panel_marginal_delta_at_matched_true_accept"), Mapping)
        else {}
    )
    false_accept_reduction = (
        dict(matched_all.get(ARM_S_FULL))
        if isinstance(matched_all.get(ARM_S_FULL), Mapping) else {}
    )
    s_probe_vs_s_full = _official_s_probe_vs_s_full_summary(
        s_probe=s_probe,
        s_full=s_full,
    )
    decision_freeze = _official_decision_freeze_summary(
        official_report=official_report,
        bridge=bridge,
    )
    candidate_generation = _official_candidate_generation_summary(
        rows=rows,
        official_report=official_report,
    )
    attempt_stage = _official_all_arms_attempt_stage(official_report)
    return {
        "schema_version": (
            SWEBENCH_MERGEABILITY_OFFICIAL_ALL_ARMS_DIAGNOSTIC_SCHEMA_VERSION
        ),
        "status": status,
        "dataset": str(official_report.get("dataset") or ""),
        "dataset_split": str(official_report.get("dataset_split") or ""),
        "official_replay_report_path": str(official_report.get("report_path") or ""),
        "official_replay_report_sha256": str(official_report.get("report_sha256") or ""),
        "official_replay_status": str(official_report.get("status") or ""),
        "attempt_stage": attempt_stage,
        "oracle_adapter_kind": str(official_report.get("oracle_adapter_kind") or ""),
        "official_replay_used": True,
        "diagnostic_only": True,
        "plumbing_smoke_only": True,
        "instance_count": int(official_report.get("instance_count") or 0),
        "candidate_count": int(official_report.get("candidate_count") or 0),
        "candidate_pool_sha256": reviewer_independence["candidate_pool_sha256"],
        "independence_metric_candidate_pool_sha256": (
            reviewer_independence["metric_candidate_pool_sha256"]
        ),
        "n_good": n_good,
        "n_bad": n_bad,
        "min_good": int(min_good),
        "min_bad": int(min_bad),
        "arms_present": sorted(str(arm) for arm in arms),
        "missing_arms": missing_arms,
        "arm_execution_populated": arm_execution_populated,
        "all_arms_populated": (
            arm_execution_populated
            and bool(reviewer_roster_cross_family_verification.get("verified"))
        ),
        "baseline_available": baseline_available,
        "s_probe_available": s_probe_available,
        "s_full_available": s_full_available,
        "oracle_ceiling_available": oracle_ceiling_available,
        "configured_reviewer_panel_preflight": dict(preflight),
        "hidden_field_leak_check": (
            dict(leak_check) if isinstance(leak_check, Mapping) else {}
        ),
        "matched_true_accept_status": matched_status,
        "supervisor_full_gate_matched_true_accept": (
            dict(matched_all.get(ARM_S_FULL))
            if isinstance(matched_all.get(ARM_S_FULL), Mapping) else {}
        ),
        "false_accept_reduction_at_matched_true_accept": false_accept_reduction,
        "s_probe_vs_s_full": s_probe_vs_s_full,
        "reviewer_marginal_delta_at_matched_true_accept": panel_marginal,
        "far_tar_frr": (
            dict(bridge.get("far_tar_frr"))
            if isinstance(bridge, Mapping)
            and isinstance(bridge.get("far_tar_frr"), Mapping)
            else None
        ),
        "benchmark_oracle": {
            "kind": "swe_bench_held_out_test_pass_proxy",
            "oracle_adapter_kind": str(official_report.get("oracle_adapter_kind") or ""),
            "scoring_authority": "held_out_fail_to_pass_and_pass_to_pass_tests",
            "maintainer_mergeability_claim_allowed": False,
            "limitation_note": (
                "SWE-bench reports held-out test-pass behavior; it is not a "
                "maintainer would-merge judgment."
            ),
        },
        "swe_bench_oracle_limitation_note": (
            "SWE-bench held-out tests are a test-pass proxy, not human "
            "maintainer mergeability."
        ),
        "no_maintainer_mergeability_claim": True,
        "decision_freeze": decision_freeze,
        "candidate_generation": candidate_generation,
        "reviewer_provenance": reviewer_provenance,
        "reviewer_roster_cross_family_verification": (
            reviewer_roster_cross_family_verification
        ),
        "inter_reviewer_agreement": reviewer_independence["inter_reviewer_agreement"],
        "leave_one_reviewer_out": reviewer_independence["leave_one_reviewer_out"],
        "effective_vote_estimate": reviewer_independence["effective_vote_estimate"],
        "abstention_coverage": abstention_coverage,
        "mergeability_rubric_coverage": abstention_coverage,
        "generator_disjointness": generator_disjointness,
        "self_preference_warnings": list(
            generator_disjointness.get("self_preference_warnings") or []
        ),
        "metrics_unavailable_reasons": blocked_reasons,
        "all_arms_populated_is_not_success_without_matched_tar": True,
        "diagnostic_ready_for_scale": status == "completed",
        "dataset_readiness": dataset_readiness,
        "aeb0_artifact_gate": _aeb0_artifact_gate(
            status=status,
            blocked_reasons=blocked_reasons,
            dataset_readiness=dataset_readiness,
        ),
        "real_evidence_claim_blocked_reasons": _aeb0_real_claim_blocked_reasons(
            blocked_reasons=blocked_reasons,
            dataset_readiness=dataset_readiness,
        ),
        "bridge_report": dict(bridge) if isinstance(bridge, Mapping) else {},
        **_aeb0_authority_flags(),
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
        },
    }


def _official_all_arms_attempt_stage(official_report: Mapping[str, Any]) -> str:
    if official_report.get("status") == "completed":
        return "scoring"
    if isinstance(official_report.get("replay_report"), Mapping):
        return "harness"
    if int(official_report.get("instance_count") or 0) > 0:
        return "harness"
    if str(official_report.get("dataset") or ""):
        return "dataset_fetch"
    return "recon"


def _aeb0_authority_flags() -> dict[str, bool]:
    return {
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "powered_improvement_claim_allowed": False,
        "human_mergeability_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }


def _aeb0_dataset_readiness(dataset: str) -> dict[str, Any]:
    dataset_text = str(dataset or "")
    normalized = dataset_text.lower().replace("_", "-")
    verified_smoke_only = "swe-bench-verified" in normalized or (
        "verified" in normalized and "swe-bench" in normalized
    )
    pro_or_held_out = (
        "swe-bench-pro" in normalized
        or normalized.endswith("/pro")
        or "held-out" in normalized
        or "heldout" in normalized
    )
    if verified_smoke_only:
        claim_scope = "smoke_only"
    elif pro_or_held_out:
        claim_scope = "candidate_serious_benchmark"
    else:
        claim_scope = "fixture_or_unknown_smoke"
    return {
        "dataset": dataset_text,
        "claim_scope": claim_scope,
        "verified_smoke_only": verified_smoke_only,
        "pro_or_held_out_equivalent": pro_or_held_out,
        "serious_benchmark_claim_allowed": False,
        "reason": (
            "swe_bench_verified_is_plumbing_smoke_only"
            if verified_smoke_only
            else (
                "serious_claim_still_requires_human_review_and_powered_evidence"
                if pro_or_held_out
                else "dataset_not_pinned_to_pro_or_held_out_equivalent"
            )
        ),
    }


def _aeb0_artifact_gate(
    *,
    status: str,
    blocked_reasons: Sequence[str],
    dataset_readiness: Mapping[str, Any],
) -> dict[str, Any]:
    blocked = [str(reason) for reason in blocked_reasons if str(reason)]
    dataset_blocked_reason = _aeb0_dataset_blocked_reason(dataset_readiness)
    if dataset_blocked_reason and dataset_blocked_reason not in blocked:
        blocked.append(dataset_blocked_reason)
    gate_status = "completed" if status == "completed" and not blocked else "blocked"
    return {
        "gate_id": "AEB-0",
        "name": "Real Official All-Arms Artifact Gate",
        "status": gate_status,
        "blocked_reasons": blocked,
        "real_official_all_arms_artifact_required": True,
        "real_or_blocked_artifact": "real" if gate_status == "completed" else "blocked",
        "real_benchmark_claim_allowed": gate_status == "completed",
        "authority_flags": _aeb0_authority_flags(),
        "dataset_claim_scope": str(dataset_readiness.get("claim_scope") or ""),
    }


def _aeb0_real_claim_blocked_reasons(
    *,
    blocked_reasons: Sequence[str],
    dataset_readiness: Mapping[str, Any],
) -> list[str]:
    reasons = [str(reason) for reason in blocked_reasons if str(reason)]
    dataset_reason = _aeb0_dataset_blocked_reason(dataset_readiness)
    if dataset_reason and dataset_reason not in reasons:
        reasons.append(dataset_reason)
    return reasons


def _aeb0_dataset_blocked_reason(dataset_readiness: Mapping[str, Any]) -> str:
    if bool(dataset_readiness.get("pro_or_held_out_equivalent")):
        return ""
    return str(
        dataset_readiness.get("reason")
        or "dataset_not_pinned_to_pro_or_held_out_equivalent"
    )


def _official_reviewer_analysis_rows(
    *,
    official_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    replay_report = official_report.get("replay_report")
    if not isinstance(replay_report, Mapping):
        return []
    bridge = official_report.get("bridge_report")
    bridge_rows = (
        bridge.get("per_row_results")
        if isinstance(bridge, Mapping)
        and isinstance(bridge.get("per_row_results"), Sequence)
        and not isinstance(bridge.get("per_row_results"), (str, bytes))
        else []
    )
    bridge_by_key = {
        (str(row.get("instance_id") or ""), str(row.get("candidate_id") or "")): row
        for row in bridge_rows
        if isinstance(row, Mapping)
    }
    rows: list[dict[str, Any]] = []
    for instance_report in replay_report.get("instance_reports") or []:
        if not isinstance(instance_report, Mapping):
            continue
        for panel_result in instance_report.get("independent_reviewer_results") or []:
            if not isinstance(panel_result, Mapping):
                continue
            instance_id = str(panel_result.get("instance_id") or "")
            candidate_id = str(panel_result.get("candidate_id") or "")
            bridge_row = bridge_by_key.get((instance_id, candidate_id), {})
            panel_decision = (
                dict(panel_result.get("panel_decision"))
                if isinstance(panel_result.get("panel_decision"), Mapping)
                else {}
            )
            rows.append({
                "task_id": instance_id,
                "candidate_id": candidate_id,
                "candidate_hash": bridge_row.get("baseline_candidate_artifact_hash", ""),
                "control_kind": "swe_bench_official_replay",
                "oracle_accept": bool(bridge_row.get("oracle_accept")),
                "oracle_unavailable": bool(
                    bridge_row.get("oracle_unavailable", not bool(bridge_row))
                ),
                "s_probe_accept": bool(bridge_row.get("s_probe_accept")),
                "single_agent_baseline_producer": (
                    dict(bridge_row.get("baseline_producer"))
                    if isinstance(bridge_row.get("baseline_producer"), Mapping)
                    else {}
                ),
                "supervisor_full_gate_reviewer_results": list(
                    panel_result.get("reviewer_results") or []
                ),
                "supervisor_full_gate_panel_decision": panel_decision,
                "panel_aggregation_mode": str(
                    panel_decision.get("aggregation_mode") or ""
                ),
        })
    return rows


def _official_reviewer_roster_cross_family_verification(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    proven_provider_families: set[str] = set()
    proven_reviewer_families: dict[str, set[str]] = {}
    available_reviewer_ids: set[str] = set()
    unproven_reviewers: dict[tuple[str, str, str], dict[str, Any]] = {}
    baseline_family_conflicts: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    non_robust_panel_results: dict[tuple[str, str, str], dict[str, Any]] = {}
    known_baseline_family_row_count = 0

    for row in rows:
        panel_aggregation_mode = str(row.get("panel_aggregation_mode") or "").strip()
        if panel_aggregation_mode != "geometric_median":
            key = (
                str(row.get("task_id") or ""),
                str(row.get("candidate_id") or ""),
                panel_aggregation_mode or "missing",
            )
            non_robust_panel_results[key] = {
                "task_id": key[0],
                "candidate_id": key[1],
                "aggregation_mode": key[2],
                "reason": "reviewer_panel_not_robustly_aggregated",
            }
        baseline_family = _producer_family_from_row(row)
        baseline_family_known = baseline_family not in {"", "unknown"}
        if baseline_family_known:
            known_baseline_family_row_count += 1
        for raw_result in row.get("supervisor_full_gate_reviewer_results") or []:
            if not isinstance(raw_result, Mapping):
                continue
            normalised = _normalise_mergeability_reviewer_result(raw_result)
            if not normalised["available"]:
                continue
            reviewer_id = str(normalised["reviewer_id"])
            available_reviewer_ids.add(reviewer_id)
            provider_family = str(
                normalised.get("provider_family") or "unknown"
            ).strip().lower()
            status, counts_as_proven = _reviewer_cross_family_claim_status(normalised)
            if provider_family == "mixed":
                status = "unproven_provider_family"
                counts_as_proven = False
            if not counts_as_proven:
                unproven_reviewers[(reviewer_id, provider_family, status)] = {
                    "reviewer_id": reviewer_id,
                    "provider_family": provider_family,
                    "runtime": normalised.get("runtime"),
                    "model": normalised.get("model"),
                    "cross_family_claim_status": status,
                }
                continue
            proven_provider_families.add(provider_family)
            proven_reviewer_families.setdefault(reviewer_id, set()).add(provider_family)
            if baseline_family_known and provider_family == baseline_family:
                conflict_key = (
                    str(row.get("task_id") or ""),
                    str(row.get("candidate_id") or ""),
                    reviewer_id,
                    provider_family,
                )
                baseline_family_conflicts[conflict_key] = {
                    "task_id": conflict_key[0],
                    "candidate_id": conflict_key[1],
                    "baseline_family": baseline_family,
                    "reviewer_id": reviewer_id,
                    "reviewer_family": provider_family,
                    "reason": "same_family_generator_reviewer_decisive_vote",
                }

    unproven = sorted(
        unproven_reviewers.values(),
        key=lambda item: (
            str(item["reviewer_id"]),
            str(item["provider_family"]),
            str(item["cross_family_claim_status"]),
        ),
    )
    conflicts = sorted(
        baseline_family_conflicts.values(),
        key=lambda item: (
            str(item["task_id"]),
            str(item["candidate_id"]),
            str(item["reviewer_id"]),
        ),
    )
    non_robust = sorted(
        non_robust_panel_results.values(),
        key=lambda item: (
            str(item["task_id"]),
            str(item["candidate_id"]),
            str(item["aggregation_mode"]),
        ),
    )
    reasons: list[str] = []
    if not available_reviewer_ids:
        reasons.append("reviewer_roster_empty")
    if len(proven_provider_families) < 2:
        reasons.append("insufficient_proven_reviewer_provider_families")
    if unproven:
        statuses = {
            str(item["cross_family_claim_status"])
            for item in unproven
        }
        if "unproven_default_model" in statuses:
            reasons.append("cursor_default_provider_family_unproven")
        if "unproven_provider_family" in statuses:
            reasons.append("unproven_provider_family")
        if "operator_asserted_provider_family_unverified" in statuses:
            reasons.append("operator_asserted_provider_family_unverified")
        if "unverified_provider_family" in statuses:
            reasons.append("unverified_provider_family")
    if conflicts:
        reasons.append("same_family_generator_reviewer_decisive_vote")
    if non_robust:
        reasons.append("reviewer_panel_not_robustly_aggregated")
    verified = not reasons
    return {
        "schema_version": (
            "supervisor-swebench-reviewer-roster-cross-family-verification/v1"
        ),
        "status": "verified" if verified else "blocked",
        "verified": verified,
        "reason": "" if verified else "reviewer_roster_not_verified_cross_family",
        "available_reviewer_count": len(available_reviewer_ids),
        "proven_provider_families": sorted(proven_provider_families),
        "proven_reviewer_families": {
            reviewer_id: sorted(families)
            for reviewer_id, families in sorted(proven_reviewer_families.items())
        },
        "unproven_reviewers": unproven,
        "baseline_family_conflicts": conflicts,
        "non_robust_panel_results": non_robust,
        "known_baseline_family_row_count": known_baseline_family_row_count,
        "reasons": reasons,
    }


def _official_reviewer_independence_metrics(
    rows: list[dict[str, Any]],
    *,
    full_stack: Mapping[str, Any],
    roster_verified: bool = True,
) -> dict[str, Any]:
    metric_rows = [
        row for row in rows
        if not bool(row.get("oracle_unavailable"))
    ]
    candidate_pool_sha256 = _sha256_json([
        {
            "task_id": str(row.get("task_id") or ""),
            "candidate_id": str(row.get("candidate_id") or ""),
            "candidate_hash": str(row.get("candidate_hash") or ""),
        }
        for row in metric_rows
    ])
    if not roster_verified:
        reason = "reviewer_roster_not_verified_cross_family"
        unavailable = {
            "status": "unavailable",
            "reason": reason,
            "candidate_pool_sha256": candidate_pool_sha256,
        }
        return {
            "candidate_pool_sha256": candidate_pool_sha256,
            "metric_candidate_pool_sha256": {
                "inter_reviewer_agreement": candidate_pool_sha256,
                "leave_one_reviewer_out": candidate_pool_sha256,
                "effective_vote_estimate": candidate_pool_sha256,
            },
            "inter_reviewer_agreement": {
                **unavailable,
                "reviewer_pairs": [],
            },
            "leave_one_reviewer_out": dict(unavailable),
            "effective_vote_estimate": dict(unavailable),
        }
    per_reviewer_arms = _per_reviewer_acceptance_arms(metric_rows)
    pairwise_error_overlap = _reviewer_oracle_error_overlap(metric_rows)
    leave_one_rows: list[dict[str, Any]] = []
    reviewer_panel_results: dict[str, list[Mapping[str, Any]]] = {}
    for row in metric_rows:
        unique_candidate_id = f"{row['task_id']}:{row['candidate_id']}"
        filtered_results = [
            result
            for result in row.get("supervisor_full_gate_reviewer_results") or []
            if isinstance(result, Mapping) and bool(result.get("verdict_present"))
        ]
        if filtered_results:
            reviewer_panel_results[unique_candidate_id] = filtered_results
        leave_row = dict(row)
        leave_row["candidate_id"] = unique_candidate_id
        leave_row["runtime_evidence_floor_accept"] = bool(row.get("s_probe_accept"))
        leave_one_rows.append(leave_row)

    return {
        "candidate_pool_sha256": candidate_pool_sha256,
        "metric_candidate_pool_sha256": {
            "inter_reviewer_agreement": candidate_pool_sha256,
            "leave_one_reviewer_out": candidate_pool_sha256,
            "effective_vote_estimate": candidate_pool_sha256,
        },
        "inter_reviewer_agreement": _inter_reviewer_agreement(metric_rows),
        "leave_one_reviewer_out": _leave_one_reviewer_out_analysis(
            leave_one_rows,
            reviewer_panel_results=reviewer_panel_results,
            full_stack=full_stack,
        ),
        "effective_vote_estimate": _effective_vote_estimate(
            per_reviewer_arms=per_reviewer_arms,
            pairwise_error_overlap=pairwise_error_overlap,
        ),
    }


def _official_s_probe_vs_s_full_summary(
    *,
    s_probe: Mapping[str, Any],
    s_full: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "s_probe_false_accept_rate": s_probe.get("false_accept_rate"),
        "s_full_false_accept_rate": s_full.get("false_accept_rate"),
        "false_accept_rate_delta": (
            round(
                float(s_full.get("false_accept_rate") or 0.0)
                - float(s_probe.get("false_accept_rate") or 0.0),
                6,
            )
            if s_probe and s_full
            else None
        ),
        "s_probe_true_accept_rate": s_probe.get("true_accept_rate"),
        "s_full_true_accept_rate": s_full.get("true_accept_rate"),
        "true_accept_rate_delta": (
            round(
                float(s_full.get("true_accept_rate") or 0.0)
                - float(s_probe.get("true_accept_rate") or 0.0),
                6,
            )
            if s_probe and s_full
            else None
        ),
    }


def _official_decision_freeze_summary(
    *,
    official_report: Mapping[str, Any],
    bridge: Mapping[str, Any],
) -> dict[str, Any]:
    replay_report = official_report.get("replay_report")
    instance_reports = (
        replay_report.get("instance_reports")
        if isinstance(replay_report, Mapping)
        and isinstance(replay_report.get("instance_reports"), Sequence)
        and not isinstance(replay_report.get("instance_reports"), (str, bytes))
        else []
    )
    frozen_paths = [
        str(instance_report.get("frozen_decisions_path") or "")
        for instance_report in instance_reports
        if isinstance(instance_report, Mapping)
        and instance_report.get("frozen_decisions_path")
    ]
    oracle_paths = [
        str(instance_report.get("oracle_outputs_path") or "")
        for instance_report in instance_reports
        if isinstance(instance_report, Mapping)
        and instance_report.get("oracle_outputs_path")
    ]
    decision_rows = bridge.get("decision_phase_rows") if isinstance(bridge, Mapping) else []
    if not isinstance(decision_rows, Sequence) or isinstance(decision_rows, (str, bytes)):
        decision_rows = []
    if not decision_rows:
        decision_rows = _official_frozen_decision_rows_from_instance_reports(
            instance_reports
        )
    decision_phase_sha256 = str(bridge.get("decision_phase_sha256") or "")
    if not decision_phase_sha256 and decision_rows:
        decision_phase_sha256 = _sha256_json(decision_rows)
    return {
        "oracle_after_reviewer_decisions": bool(frozen_paths and oracle_paths),
        "frozen_decision_row_count": len(decision_rows),
        "decision_phase_sha256": decision_phase_sha256,
        "frozen_decisions_paths": frozen_paths,
        "oracle_outputs_paths": oracle_paths,
    }


def _official_frozen_decision_rows_from_instance_reports(
    instance_reports: Sequence[Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for instance_report in instance_reports:
        if not isinstance(instance_report, Mapping):
            continue
        frozen = instance_report.get("frozen_decisions")
        if not isinstance(frozen, Mapping):
            continue
        frozen_rows = frozen.get("rows")
        if not isinstance(frozen_rows, Sequence) or isinstance(
            frozen_rows,
            (str, bytes),
        ):
            continue
        rows.extend(dict(row) for row in frozen_rows if isinstance(row, Mapping))
    return rows


def _official_candidate_generation_summary(
    *,
    rows: Sequence[Any],
    official_report: Mapping[str, Any],
) -> dict[str, Any]:
    producer_families = sorted({
        str((row.get("baseline_producer") or {}).get("provider_family")
            or (row.get("baseline_producer") or {}).get("provider")
            or "unknown")
        for row in rows
        if isinstance(row, Mapping)
    })
    return {
        "baseline": {
            "source": "produced_single_agent_baseline_receipts",
            "candidate_count": len([row for row in rows if isinstance(row, Mapping)]),
            "provider_families": producer_families,
            "producer_family_count": len(producer_families),
        },
        "supervisor": {
            "source": "replayed_model_patch_artifacts",
            "candidate_count": len([row for row in rows if isinstance(row, Mapping)]),
            "live_generation_used": bool(official_report.get("live_generation_used")),
        },
        "matched_model_budget": {
            "required_when_live_generation_used": True,
            "status": (
                "recorded"
                if bool(official_report.get("live_generation_used"))
                else "not_applicable_replay"
            ),
        },
    }


def _official_replay_unavailable_reasons(
    *,
    receipt_validation: Mapping[str, Any],
    label_validation: Mapping[str, Any],
    leak_check: Mapping[str, Any],
    replay_report: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    bridge_reasons = (replay_report.get("bridge_report") or {}).get(
        "metrics_unavailable_reasons"
    ) or []
    for reason in bridge_reasons:
        reason_text = str(reason)
        if reason_text and reason_text not in reasons:
            reasons.append(reason_text)
    if not receipt_validation.get("validated", True):
        reasons.append("oracle_receipt_validation_failed")
    if not label_validation.get("validated", True):
        reasons.append("label_validation_failed")
    if leak_check.get("refs"):
        reasons.append("hidden_field_leak_detected")
    return reasons


def _validate_official_replay_oracle_adapter_kind(oracle_adapter_kind: str) -> None:
    if oracle_adapter_kind in SUPPORTED_OFFICIAL_REPLAY_ORACLE_ADAPTER_KINDS:
        return
    allowed = ", ".join(sorted(SUPPORTED_OFFICIAL_REPLAY_ORACLE_ADAPTER_KINDS))
    raise SwebenchMergeabilityFixtureRunnerError(
        "unsupported official SWE-bench oracle adapter kind "
        f"{oracle_adapter_kind!r}; expected one of: {allowed}"
    )


def _suppress_unavailable_official_replay_metrics(
    *,
    replay_report: Mapping[str, Any],
    unavailable_reasons: Sequence[str],
) -> dict[str, Any]:
    suppressed_bridge = _unavailable_official_bridge_report(
        unavailable_reasons=unavailable_reasons
    )
    redacted = dict(replay_report)
    redacted["status"] = "unavailable"
    redacted["metrics_suppressed"] = True
    redacted["metrics_unavailable_reasons"] = list(unavailable_reasons)
    redacted["bridge_report"] = suppressed_bridge
    original_report_path = redacted.get("report_path")
    redacted.pop("report_path", None)
    instance_reports: list[dict[str, Any]] = []
    for instance_report in replay_report.get("instance_reports") or []:
        if not isinstance(instance_report, Mapping):
            continue
        redacted_instance = dict(instance_report)
        redacted_instance["status"] = "unavailable"
        redacted_instance["metrics_suppressed"] = True
        redacted_instance["metrics_unavailable_reasons"] = list(
            unavailable_reasons
        )
        redacted_instance["bridge_report"] = suppressed_bridge
        original_instance_report_path = redacted_instance.get("report_path")
        redacted_instance.pop("report_path", None)
        _rewrite_json_report(
            original_instance_report_path,
            redacted_instance,
        )
        instance_reports.append(redacted_instance)
    redacted["instance_reports"] = instance_reports
    _rewrite_json_report(original_report_path, redacted)
    return redacted


def _rewrite_json_report(path_value: Any, payload: Mapping[str, Any]) -> None:
    if not path_value:
        return
    path = Path(str(path_value))
    if not path.exists():
        return
    path.write_text(
        json.dumps(dict(payload), sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )


def _unavailable_official_bridge_report(
    *,
    unavailable_reasons: Sequence[str],
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "metrics_suppressed": True,
        "metrics_unavailable_reasons": list(unavailable_reasons),
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }


def _validate_official_oracle_receipts(
    *,
    oracle_adapter_kind: str,
    replay_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Require proof that official-labeled oracle adapters are not bare labels."""
    if oracle_adapter_kind not in OFFICIAL_ORACLE_RECEIPT_VALIDATION_KINDS:
        return {"required": False, "validated": True, "mismatches": []}

    mismatches: list[dict[str, Any]] = []
    for instance_report in replay_report.get("instance_reports") or []:
        oracle_path = instance_report.get("oracle_outputs_path")
        if not oracle_path:
            mismatches.append({
                "instance_id": instance_report.get("instance_id"),
                "reason": "missing_oracle_outputs",
            })
            continue
        oracle_payload = json.loads(
            Path(oracle_path).read_text(encoding="utf-8")
        )
        expected_frozen_sha256 = str(
            (instance_report.get("frozen_decisions") or {}).get(
                "frozen_decisions_sha256"
            )
            or ""
        )
        expected_patch_sha256_by_candidate = {
            str(artifact.get("candidate_id") or ""): _candidate_artifact_patch_sha256(
                artifact
            )
            for artifact in instance_report.get("per_candidate_artifacts") or []
            if isinstance(artifact, Mapping)
        }
        for row in oracle_payload.get("rows") or []:
            adapter_receipt = _official_oracle_adapter_receipt(row)
            if not isinstance(adapter_receipt, Mapping):
                mismatches.append({
                    "instance_id": row.get("instance_id"),
                    "candidate_id": row.get("candidate_id"),
                    "reason": "missing_official_oracle_adapter_receipt",
                })
                continue
            mismatches.extend(
                _official_oracle_receipt_mismatches(
                    row=row,
                    receipt=adapter_receipt,
                    expected_frozen_decisions_sha256=expected_frozen_sha256,
                    expected_model_patch_sha256=expected_patch_sha256_by_candidate.get(
                        str(row.get("candidate_id") or ""),
                        "",
                    ),
                )
            )
    return {
        "required": True,
        "validated": len(mismatches) == 0,
        "mismatches": mismatches,
    }


def _official_oracle_adapter_receipt(
    row: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    for receipt in row.get("oracle_command_receipts") or []:
        if (
            isinstance(receipt, Mapping)
            and receipt.get("name") == "official_oracle_adapter"
        ):
            inner = receipt.get("receipt")
            if isinstance(inner, Mapping):
                return inner
            return None
    return None


def _candidate_artifact_patch_sha256(artifact: Mapping[str, Any]) -> str:
    for receipt in artifact.get("patch_apply_receipts") or []:
        if isinstance(receipt, Mapping):
            patch_sha256 = str(receipt.get("patch_sha256") or "")
            if patch_sha256:
                return patch_sha256
    baseline_decision = artifact.get("baseline_decision")
    if isinstance(baseline_decision, Mapping):
        candidate_hash = str(baseline_decision.get("candidate_artifact_hash") or "")
        if candidate_hash:
            return candidate_hash
    patch = artifact.get("patch")
    if patch is not None:
        return sha256(str(patch).encode("utf-8")).hexdigest()
    return ""


def _official_oracle_receipt_mismatches(
    *,
    row: Mapping[str, Any],
    receipt: Mapping[str, Any],
    expected_frozen_decisions_sha256: str = "",
    expected_model_patch_sha256: str = "",
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    base = {
        "instance_id": row.get("instance_id"),
        "candidate_id": row.get("candidate_id"),
    }
    row_unavailable = _oracle_status_unavailable(row)
    for key in ("command", "stdout_sha256", "stderr_sha256", "evaluator_version"):
        value = receipt.get(key)
        if not value:
            mismatches.append({
                **base,
                "field": key,
                "reason": "missing_required_receipt_field",
            })
    command = receipt.get("command")
    if command and (
        isinstance(command, (str, bytes))
        or not isinstance(command, Sequence)
    ):
        mismatches.append({
            **base,
            "field": "command",
            "reason": "invalid_required_receipt_field",
        })
    return_code = receipt.get("return_code", receipt.get("returncode"))
    if not isinstance(return_code, int) or (not row_unavailable and return_code != 0):
        mismatches.append({
            **base,
            "field": "return_code",
            "reason": "invalid_return_code",
        })
    artifact_paths = receipt.get("artifact_paths")
    if not isinstance(artifact_paths, Mapping) or (not row_unavailable and not artifact_paths):
        mismatches.append({
            **base,
            "field": "artifact_paths",
            "reason": "missing_required_receipt_field",
        })
    if not _official_harness_metadata_present(receipt):
        mismatches.append({
            **base,
            "field": "harness",
            "reason": "missing_official_harness_metadata",
        })
    for key in ("fail_to_pass_status", "pass_to_pass_status"):
        expected = str(row.get(key) or "").lower()
        observed = str(receipt.get(key) or "").lower()
        if observed not in {"pass", "fail", "unavailable"} or observed != expected:
            mismatches.append({
                **base,
                "field": key,
                "receipt_value": receipt.get(key),
                "adapter_reported": row.get(key),
                "reason": "receipt_status_mismatch",
            })
    if row_unavailable and not str(receipt.get("unavailable_reason") or ""):
        mismatches.append({
            **base,
            "field": "unavailable_reason",
            "reason": "missing_required_receipt_field",
        })
    for field, expected in (
        ("frozen_decisions_sha256", expected_frozen_decisions_sha256),
        ("model_patch_sha256", expected_model_patch_sha256),
    ):
        if not expected:
            continue
        observed = str(receipt.get(field) or "")
        if not observed:
            mismatches.append({
                **base,
                "field": field,
                "reason": "missing_required_receipt_field",
            })
        elif observed != expected:
            mismatches.append({
                **base,
                "field": field,
                "receipt_value": observed,
                "expected_value": expected,
                "reason": "receipt_hash_mismatch",
            })
    return mismatches


def _oracle_status_unavailable(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("oracle_unavailable")
        or row.get("unavailable")
        or str(row.get("fail_to_pass_status") or "").lower() == "unavailable"
        or str(row.get("pass_to_pass_status") or "").lower() == "unavailable"
    )


def _official_harness_metadata_present(receipt: Mapping[str, Any]) -> bool:
    for key in ("harness", "harness_metadata", "docker", "docker_metadata"):
        value = receipt.get(key)
        if isinstance(value, Mapping) and value:
            return True
    docker_image = receipt.get("docker_image")
    return bool(isinstance(docker_image, str) and docker_image.strip())


def _validate_official_equivalent_labels(
    *,
    oracle_adapter_kind: str,
    replay_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Cross-check official_equivalent adapter labels against reported status."""
    if oracle_adapter_kind != "official_equivalent":
        return {"required": False, "validated": True, "mismatches": []}
    mismatches: list[dict[str, Any]] = []
    for instance_report in replay_report.get("instance_reports") or []:
        oracle_path = instance_report.get("oracle_outputs_path")
        if not oracle_path:
            mismatches.append({
                "instance_id": instance_report.get("instance_id"),
                "reason": "missing_oracle_outputs",
            })
            continue
        oracle_payload = json.loads(
            Path(oracle_path).read_text(encoding="utf-8")
        )
        for row in oracle_payload.get("rows") or []:
            adapter_receipt = _official_oracle_adapter_receipt(row)
            labels = (
                adapter_receipt.get("official_equivalent_labels")
                if isinstance(adapter_receipt, Mapping)
                else None
            )
            if not isinstance(labels, Mapping):
                mismatches.append({
                    "instance_id": row.get("instance_id"),
                    "candidate_id": row.get("candidate_id"),
                    "reason": "missing_official_equivalent_labels",
                })
                continue
            for key in ("fail_to_pass_status", "pass_to_pass_status"):
                expected = str(labels.get(key) or "").lower()
                reported = str(row.get(key) or "").lower()
                if expected not in {"pass", "fail"} or expected != reported:
                    mismatches.append({
                        "instance_id": row.get("instance_id"),
                        "candidate_id": row.get("candidate_id"),
                        "field": key,
                        "expected_official_equivalent": labels.get(key),
                        "adapter_reported": row.get(key),
                        "reason": "label_mismatch",
                    })
                    break
    return {
        "required": True,
        "validated": len(mismatches) == 0,
        "mismatches": mismatches,
    }


def _scan_official_replay_public_artifacts_for_leaks(
    *,
    replay_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Scan public artifacts for SWE-bench Pro hidden oracle field leaks."""
    refs: list[str] = []
    for instance_report in replay_report.get("instance_reports") or []:
        instance_id = str(instance_report.get("instance_id") or "")
        frozen_path = instance_report.get("frozen_decisions_path")
        if frozen_path and Path(frozen_path).exists():
            content = Path(frozen_path).read_text(encoding="utf-8")
            for ref in _scan_for_swebench_pro_oracle_refs(content):
                refs.append(f"frozen_decisions[{instance_id}]:{ref}")
        for packet in instance_report.get("reviewer_packets") or []:
            packet_path = packet.get("reviewer_packet_path") if isinstance(
                packet, Mapping
            ) else None
            if packet_path and Path(packet_path).exists():
                content = Path(packet_path).read_text(encoding="utf-8")
                for ref in _scan_for_swebench_pro_oracle_refs(content):
                    refs.append(f"reviewer_packet[{instance_id}]:{ref}")
        for gen_input in instance_report.get("generator_inputs") or []:
            input_path = gen_input.get("generator_input_path") if isinstance(
                gen_input, Mapping
            ) else None
            if input_path and Path(input_path).exists():
                content = Path(input_path).read_text(encoding="utf-8")
                for ref in _scan_for_swebench_pro_oracle_refs(content):
                    refs.append(f"generator_input[{instance_id}]:{ref}")
        for artifact in instance_report.get("per_candidate_artifacts") or []:
            public_worktree = (
                artifact.get("public_worktree")
                if isinstance(artifact, Mapping)
                else None
            )
            if public_worktree and (Path(public_worktree) / ".git").exists():
                candidate_id = str(artifact.get("candidate_id") or "")
                refs.append(
                    f"public_worktree[{instance_id}/{candidate_id}]:.git"
                )
    public_packets = (replay_report.get("bridge_report") or {}).get(
        "public_packets"
    ) or {}
    for ref in _scan_for_swebench_pro_oracle_refs(public_packets):
        refs.append(f"public_packets:{ref}")
    return {"ok": not refs, "refs": refs}


def _ensure_public_bundle_has_no_git(path: Path, *, context: str) -> None:
    git_path = path / ".git"
    if not git_path.exists():
        return
    raise SwebenchMergeabilityFixtureRunnerError(
        f"{context} contains .git metadata; public bundles must be detached trees"
    )


def _official_public_record(record: Mapping[str, Any]) -> dict[str, Any]:
    public = {
        "instance_id": str(record.get("instance_id") or ""),
        "repo": str(record.get("repo") or ""),
        "base_commit": str(record.get("base_commit") or ""),
        "problem_statement": str(record.get("problem_statement") or ""),
        "public_checkout_ref": str(
            record.get("public_checkout_ref")
            or record.get("base_commit")
            or ""
        ),
        "public_checkout_sha256": str(record.get("public_checkout_sha256") or ""),
    }
    for key in ("public_lint_commands", "public_build_commands", "public_commands"):
        if key in record:
            public[key] = record[key]
    for hidden_key in _OFFICIAL_RECORD_HIDDEN_KEYS:
        public.pop(hidden_key, None)
    leaks = _scan_for_swebench_pro_oracle_refs(public)
    if leaks:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official public record references forbidden oracle material: "
            + ", ".join(leaks)
        )
    return public


def _official_hidden_oracle(record: Mapping[str, Any]) -> dict[str, Any]:
    fail_to_pass = _official_test_list(record.get("fail_to_pass"))
    pass_to_pass = _official_test_list(record.get("pass_to_pass"))
    upper_fail_to_pass = _official_test_list(record.get("FAIL_TO_PASS"))
    upper_pass_to_pass = _official_test_list(record.get("PASS_TO_PASS"))
    return {
        "patch": str(record.get("patch") or ""),
        "test_patch": str(record.get("test_patch") or ""),
        "FAIL_TO_PASS": upper_fail_to_pass or fail_to_pass,
        "PASS_TO_PASS": upper_pass_to_pass or pass_to_pass,
        "fail_to_pass": fail_to_pass or upper_fail_to_pass,
        "pass_to_pass": pass_to_pass or upper_pass_to_pass,
        "before_repo_set_cmd": str(record.get("before_repo_set_cmd") or ""),
        "selected_test_files_to_run": _official_test_list(
            record.get("selected_test_files_to_run")
        ),
        "dockerhub_tag": str(record.get("dockerhub_tag") or ""),
    }


def _official_test_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, Sequence) and not isinstance(parsed, (bytes, bytearray)):
            return [str(item) for item in parsed]
        return [str(parsed)]
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        return [str(item) for item in raw]
    return [str(raw)]


def _normalise_prediction_oracle_label(raw: Any) -> str:
    label = str(raw or "").strip().lower().replace("_", "-")
    if not label:
        return ""
    if label in {"good", "pass", "resolved", ORACLE_GOOD_LABEL}:
        return ORACLE_GOOD_LABEL
    if label in {"bad", "fail", "nonresolving", "non-resolving", ORACLE_BAD_LABEL}:
        return ORACLE_BAD_LABEL
    raise SwebenchMergeabilityFixtureRunnerError(
        "prediction oracle_label must be 'oracle-good' or 'oracle-bad'"
    )


def _normalise_prediction_mapping(raw: Any, *, field: str) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    raise SwebenchMergeabilityFixtureRunnerError(
        f"prediction {field} must be a JSON object"
    )


def _required_prediction_mapping(raw: Any, *, field: str) -> dict[str, Any]:
    value = _normalise_prediction_mapping(raw, field=field)
    if not value:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"generated candidate {field} provenance is required"
        )
    return value


def _prediction_hash(
    *,
    raw: Mapping[str, Any],
    key: str,
    model_patch_sha256: str,
    line_number: int,
) -> str:
    supplied = str(raw.get(key) or "").strip()
    if not supplied:
        return model_patch_sha256
    if supplied != model_patch_sha256:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"prediction line {line_number} {key} does not match model_patch sha256"
        )
    return supplied


def _load_official_predictions(predictions_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    path = Path(predictions_path).expanduser().resolve()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"could not read predictions JSONL {path}: {exc}"
        ) from exc
    by_instance: dict[str, list[dict[str, Any]]] = {}
    seen_candidate_hashes: dict[str, str] = {}
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SwebenchMergeabilityFixtureRunnerError(
                f"invalid predictions JSONL at line {index}: {exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                f"prediction line {index} must be a JSON object"
            )
        instance_id = str(raw.get("instance_id") or "")
        if not instance_id:
            raise SwebenchMergeabilityFixtureRunnerError(
                f"prediction line {index} missing instance_id"
            )
        model_patch = str(
            raw.get("model_patch")
            or raw.get("prediction")
            or raw.get("patch")
            or ""
        )
        if not model_patch.strip():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"prediction line {index} missing model_patch"
            )
        patch_sha256 = sha256(model_patch.encode("utf-8")).hexdigest()
        candidate_artifact_hash = _prediction_hash(
            raw=raw,
            key="candidate_artifact_hash",
            model_patch_sha256=patch_sha256,
            line_number=index,
        )
        model_patch_sha256 = _prediction_hash(
            raw=raw,
            key="model_patch_sha256",
            model_patch_sha256=patch_sha256,
            line_number=index,
        )
        diff_sha256 = _prediction_hash(
            raw=raw,
            key="diff_sha256",
            model_patch_sha256=patch_sha256,
            line_number=index,
        )
        candidate_id = str(raw.get("candidate_id") or f"prediction-{index}")
        oracle_label = _normalise_prediction_oracle_label(
            raw.get("oracle_label") or raw.get("label") or raw.get("oracle_status")
        )
        if oracle_label:
            duplicate = seen_candidate_hashes.get(candidate_artifact_hash)
            if duplicate is not None:
                raise SwebenchMergeabilityFixtureRunnerError(
                    "prediction candidate_artifact_hash duplicated at line "
                    f"{index}: {candidate_artifact_hash} already used by {duplicate}"
                )
            seen_candidate_hashes[candidate_artifact_hash] = (
                f"{instance_id}/{candidate_id}"
            )
        candidate = {
            "candidate_id": candidate_id,
            "model_patch": model_patch,
            "oracle_label": oracle_label,
            "candidate_artifact_hash": candidate_artifact_hash,
            "model_patch_sha256": model_patch_sha256,
            "diff_sha256": diff_sha256,
            "origin": _normalise_prediction_mapping(raw.get("origin"), field="origin"),
            "producer": _normalise_prediction_mapping(
                raw.get("producer"),
                field="producer",
            ),
        }
        patch_applied = _patch_applied_from_oracle_result(raw)
        if patch_applied is not None:
            candidate["patch_applied"] = patch_applied
        for key in PRODUCED_BASELINE_RECEIPT_KEYS:
            if key in raw and isinstance(raw[key], Mapping):
                candidate[key] = dict(raw[key])
        for key in POWERED_FACTORIAL_ARM_DECISION_KEYS.values():
            if key in raw and isinstance(raw[key], Mapping):
                candidate[key] = dict(raw[key])
        reviewer_panel_results = raw.get("reviewer_panel_results")
        if (
            isinstance(reviewer_panel_results, Sequence)
            and not isinstance(reviewer_panel_results, (str, bytes, bytearray))
        ):
            candidate["reviewer_panel_results"] = [
                dict(item)
                for item in reviewer_panel_results
                if isinstance(item, Mapping)
            ]
        by_instance.setdefault(instance_id, []).append(candidate)
    if not by_instance:
        raise SwebenchMergeabilityFixtureRunnerError(
            "predictions JSONL contains no candidates"
        )
    return by_instance


def _candidate_corpus_summary_from_predictions(
    predictions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    rows = [
        candidate
        for candidates in predictions.values()
        for candidate in candidates
        if isinstance(candidate, Mapping)
    ]
    n_good = sum(
        1 for candidate in rows if candidate.get("oracle_label") == ORACLE_GOOD_LABEL
    )
    n_bad = sum(
        1 for candidate in rows if candidate.get("oracle_label") == ORACLE_BAD_LABEL
    )
    unlabeled = len(rows) - n_good - n_bad
    return {
        "candidate_count": len(rows),
        "n_good": n_good,
        "n_bad": n_bad,
        "false_accept_denominator": n_bad,
        "far_degenerate": n_bad == 0,
        "unlabeled_count": unlabeled,
    }


def summarize_swe_bench_pro_candidate_corpus(
    predictions_path: str | Path,
) -> dict[str, Any]:
    """Summarize report-only FAR denominator readiness for a Pro corpus."""
    predictions = _load_official_predictions(predictions_path)
    return _candidate_corpus_summary_from_predictions(predictions)


def swebench_mergeability_powered_factorial_runner(
    *,
    predictions_path: str | Path,
    output_dir: str | Path,
    min_good: int = 30,
    min_bad: int = 30,
    min_discordant: int = 25,
    alpha: float = 0.05,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run the powered factorial evaluator over an oracle-labeled Pro corpus.

    Loads the SWE-bench Pro predictions JSONL at ``predictions_path``, adapts
    every row's explicit ``oracle_label``, ``candidate_artifact_hash``, trusted
    ``single_agent_baseline_decision`` receipt, ``reviewer_panel_results``, and
    the four required arm decisions
    (``same_model_multi_agent_decision``, ``hetero_multi_reviewer_decision``,
    ``runtime_evidence_floor_decision``, ``full_supervisor_stack_decision``)
    into ``run_powered_factorial_mergeability_evaluation``. ``min_good`` /
    ``min_bad`` drive raw oracle sample-size sufficiency; ``min_discordant`` /
    ``alpha`` drive the paired McNemar gate over the full supervisor stack vs
    the single-agent baseline arm. The returned report is augmented with the
    report-only ``evidence_conversion_power_contract`` block; every authority
    flag (``metric_applyable``, ``powered_improvement_claim_allowed``,
    ``human_mergeability_claim_allowed``, ``improvement_claim_allowed``,
    ``default_change_allowed``, ``policy_mutated``, ``gate_advanced``) is
    held false. ``powered_metric_applyable`` reflects the raw factorial
    evaluator status for diagnostics only.
    """
    predictions_file = Path(predictions_path).expanduser().resolve()
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    predictions = _load_official_predictions(predictions_file)
    candidates = _powered_factorial_flatten_predictions(predictions)
    arm_decisions = _powered_factorial_arm_decisions(candidates)
    reviewer_panel_results = _powered_factorial_reviewer_panel_results(candidates)
    public_reviews = _powered_factorial_public_reviews(candidates)
    adapter_root = output_path / "pro_powered_factorial_adapter"
    adapter_manifest = _write_powered_factorial_adapter_corpus(candidates, adapter_root)
    thresholds = {
        "min_good": int(min_good),
        "min_bad": int(min_bad),
        "min_discordant": int(min_discordant),
        "alpha": float(alpha),
    }
    report = run_powered_factorial_mergeability_evaluation(
        adapter_root,
        output_dir=output_path,
        timeout_s=timeout_s,
        strict_calibration=False,
        arm_decisions=arm_decisions,
        reviewer_panel_results=reviewer_panel_results,
        powered_thresholds=thresholds,
        precomputed_calibration=_powered_factorial_precomputed_calibration(
            candidates,
            adapter_manifest=adapter_manifest,
        ),
        public_reviews=public_reviews,
        candidate_artifact_hashes={
            str(candidate["candidate_id"]): str(candidate["candidate_artifact_hash"])
            for candidate in candidates
        },
    )
    core_metric_applyable = bool(report.get("metric_applyable"))
    report.update({
        "source_predictions_path": str(predictions_file),
        "source_predictions_sha256": sha256(predictions_file.read_bytes()).hexdigest(),
        "pro_candidate_count": len(candidates),
        "pro_instance_count": len(predictions),
        "adapter_corpus_root": str(adapter_root),
        "adapter_manifest_path": str(adapter_manifest["adapter_manifest_path"]),
        "adapter_manifest_sha256": adapter_manifest["adapter_manifest_sha256"],
        "powered_metric_applyable": core_metric_applyable,
        "metric_applyable": False,
        "powered_improvement_claim_allowed": False,
        "human_mergeability_claim_allowed": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    })
    report["evidence_conversion_power_contract"] = _evidence_conversion_power_contract(
        report,
        thresholds=thresholds,
    )
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path = output_path / "powered_factorial_report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _powered_factorial_flatten_predictions(
    predictions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for instance_id, rows in sorted(predictions.items()):
        for candidate in rows:
            if not isinstance(candidate, Mapping):
                continue
            copied = dict(candidate)
            copied.setdefault("instance_id", instance_id)
            candidates.append(copied)
    if not candidates:
        raise SwebenchMergeabilityFixtureRunnerError(
            "powered factorial Pro corpus contains no candidates"
        )
    candidate_id_counts = Counter(
        str(candidate.get("candidate_id") or "") for candidate in candidates
    )
    duplicate_ids = sorted(cid for cid, count in candidate_id_counts.items() if count > 1)
    if duplicate_ids:
        raise SwebenchMergeabilityFixtureRunnerError(
            "powered factorial Pro corpus requires globally unique candidate_id values: "
            + ", ".join(duplicate_ids)
        )
    unlabeled = [
        str(candidate.get("candidate_id") or "")
        for candidate in candidates
        if candidate.get("oracle_label") not in {ORACLE_GOOD_LABEL, ORACLE_BAD_LABEL}
    ]
    if unlabeled:
        raise SwebenchMergeabilityFixtureRunnerError(
            "powered factorial Pro corpus requires oracle labels for every candidate: "
            + ", ".join(unlabeled)
        )
    missing_hashes = [
        str(candidate.get("candidate_id") or "")
        for candidate in candidates
        if not str(candidate.get("candidate_artifact_hash") or "").strip()
    ]
    if missing_hashes:
        raise SwebenchMergeabilityFixtureRunnerError(
            "powered factorial Pro corpus requires candidate_artifact_hash for every candidate: "
            + ", ".join(missing_hashes)
        )
    missing_provenance = [
        str(candidate.get("candidate_id") or "")
        for candidate in candidates
        if not isinstance(candidate.get("origin"), Mapping)
        or not candidate.get("origin")
        or not isinstance(candidate.get("producer"), Mapping)
        or not candidate.get("producer")
    ]
    if missing_provenance:
        raise SwebenchMergeabilityFixtureRunnerError(
            "powered factorial Pro corpus requires origin and producer provenance for every candidate: "
            + ", ".join(missing_provenance)
        )
    return candidates


def _write_powered_factorial_adapter_corpus(
    candidates: Sequence[Mapping[str, Any]],
    adapter_root: Path,
) -> dict[str, Any]:
    if adapter_root.exists():
        shutil.rmtree(adapter_root)
    tasks_dir = adapter_root / "tasks"
    candidates_dir = adapter_root / "candidates"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    instance_ids = sorted({str(candidate.get("instance_id") or "") for candidate in candidates})
    task_paths: list[str] = []
    candidate_paths: list[str] = []
    for instance_id in instance_ids:
        task_path = tasks_dir / f"{_safe_powered_fragment(instance_id)}.json"
        task_payload = {
            "schema_version": MERGEABILITY_TASK_SCHEMA_VERSION,
            "task_id": instance_id,
            "split": "held_out",
            "task_class": "swe_bench_pro",
            "repo_fixture_ref": f"pro-adapter/{_safe_powered_fragment(instance_id)}",
            "prompt": f"SWE-bench Pro powered factorial adapter for {instance_id}",
            "allowed_mutable_paths": ["patches/"],
            "hidden_test_commands": [["python", "-c", "raise SystemExit(0)"]],
            "protected_paths": ["hidden/", ".mergeability/"],
        }
        task_path.write_text(
            json.dumps(task_payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        task_paths.append(str(task_path))
    for candidate in candidates:
        instance_id = str(candidate.get("instance_id") or "")
        candidate_id = str(candidate.get("candidate_id") or "")
        oracle_label = str(candidate.get("oracle_label") or "")
        patch_path = f"patches/{_safe_powered_fragment(candidate_id)}.diff"
        candidate_path = candidates_dir / f"{_safe_powered_fragment(candidate_id)}.json"
        candidate_payload = {
            "schema_version": MERGEABILITY_CANDIDATE_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "task_id": instance_id,
            "changed_files": [patch_path],
            "files": {patch_path: str(candidate.get("model_patch") or "")},
            "provenance": {
                "kind": "swe_bench_pro_powered_factorial_adapter",
                "origin": dict(candidate.get("origin") or {}),
                "producer": dict(candidate.get("producer") or {}),
                "candidate_artifact_hash": str(candidate.get("candidate_artifact_hash") or ""),
                "oracle_label": oracle_label,
            },
            "generator_metadata": {
                "control": "known_good" if oracle_label == ORACLE_GOOD_LABEL else "known_bad",
                "expected_outcome": "pass" if oracle_label == ORACLE_GOOD_LABEL else "fail",
                "source": "swe_bench_pro_oracle_label",
            },
        }
        candidate_path.write_text(
            json.dumps(candidate_payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        candidate_paths.append(str(candidate_path))
    manifest = {
        "schema_version": "supervisor-swebench-pro-powered-factorial-adapter/v1",
        "adapter_root": str(adapter_root),
        "task_paths": task_paths,
        "candidate_paths": candidate_paths,
        "source": "swe_bench_pro_predictions_jsonl",
    }
    manifest["adapter_manifest_sha256"] = _sha256_json(manifest)
    manifest_path = adapter_root / "adapter_manifest.json"
    manifest["adapter_manifest_path"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _powered_factorial_arm_decisions(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    arms: dict[str, dict[str, Any]] = {"single_agent_baseline": {}}
    for arm in POWERED_FACTORIAL_ARM_DECISION_KEYS:
        arms[arm] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        baseline = candidate.get("single_agent_baseline_decision")
        if not isinstance(baseline, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                f"powered factorial candidate {candidate_id} missing trusted baseline receipt"
            )
        resolved_baseline = _resolve_powered_baseline_decision(
            raw=baseline,
            expected_candidate_artifact_hash=str(candidate.get("candidate_artifact_hash") or ""),
            expected_candidate_id=candidate_id,
        )
        if resolved_baseline["unavailable"]:
            raise SwebenchMergeabilityFixtureRunnerError(
                "powered factorial candidate "
                f"{candidate_id} baseline receipt is not trusted: "
                f"{resolved_baseline['unavailable_reason']}"
            )
        arms["single_agent_baseline"][candidate_id] = dict(baseline)
        for arm, key in POWERED_FACTORIAL_ARM_DECISION_KEYS.items():
            raw = candidate.get(key)
            if not isinstance(raw, Mapping):
                raise SwebenchMergeabilityFixtureRunnerError(
                    f"powered factorial candidate {candidate_id} missing {key}"
                )
            if not isinstance(raw.get("accept"), bool):
                raise SwebenchMergeabilityFixtureRunnerError(
                    f"powered factorial candidate {candidate_id} {key}.accept must be boolean"
                )
            if "unavailable" in raw and not isinstance(raw.get("unavailable"), bool):
                raise SwebenchMergeabilityFixtureRunnerError(
                    f"powered factorial candidate {candidate_id} {key}.unavailable must be boolean"
                )
            decision_source = str(raw.get("decision_source") or "").strip()
            if not decision_source:
                raise SwebenchMergeabilityFixtureRunnerError(
                    f"powered factorial candidate {candidate_id} {key}.decision_source is required"
                )
            arms[arm][candidate_id] = {
                "accept": bool(raw.get("accept")),
                "unavailable": bool(raw.get("unavailable")),
                "decision_source": decision_source,
            }
    return arms


def _powered_factorial_reviewer_panel_results(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    return {
        str(candidate.get("candidate_id") or ""): list(candidate.get("reviewer_panel_results") or [])
        for candidate in candidates
    }


def _powered_factorial_public_reviews(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    public_reviews: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        raw = candidate.get("runtime_evidence_floor_decision")
        if not isinstance(raw, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                f"powered factorial candidate {candidate_id} missing runtime_evidence_floor_decision"
            )
        public_reviews[candidate_id] = {
            "accept": bool(raw.get("accept")),
            "unavailable": bool(raw.get("unavailable")),
            "decision_source": str(
                raw.get("decision_source") or "swe_bench_pro_runtime_evidence_floor"
            ),
            "reason": str(raw.get("reason") or ""),
        }
    return public_reviews


def _powered_factorial_precomputed_calibration(
    candidates: Sequence[Mapping[str, Any]],
    *,
    adapter_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        instance_id = str(candidate.get("instance_id") or "")
        oracle_good = candidate.get("oracle_label") == ORACLE_GOOD_LABEL
        receipt = {
            "receipt_id": f"swebench-pro-oracle-label:{instance_id}:{candidate_id}",
            "kind": "swe_bench_pro_oracle_label",
            "source": "swe_bench_pro_predictions_jsonl",
            "status": "passed" if oracle_good else "failed",
            "task_id": instance_id,
            "candidate_id": candidate_id,
            "final_score": 1.0 if oracle_good else 0.0,
            "candidate_artifact_hash": str(candidate.get("candidate_artifact_hash") or ""),
        }
        results.append({
            "task_id": instance_id,
            "candidate_id": candidate_id,
            "oracle_label": str(candidate.get("oracle_label") or ""),
            "control_kind": "known_good" if oracle_good else "known_bad",
            "expected_outcome": "pass" if oracle_good else "fail",
            "observed_outcome": "pass" if oracle_good else "fail",
            "final_score": 1.0 if oracle_good else 0.0,
            "blocker_status": "passed" if oracle_good else "failed",
            "hidden_test_status": "precomputed",
            "reverse_test_status": "precomputed",
            "scope_status": "precomputed",
            "lint_build_status": "precomputed",
            "failures": [] if oracle_good else ["swe_bench_pro_oracle_bad"],
            "receipt_id": receipt["receipt_id"],
            "receipt": receipt,
        })
    summary = {
        "schema_version": "supervisor-mergeability-calibration/v1",
        "status": "accepted",
        "bench_root": str(adapter_manifest.get("adapter_root") or ""),
        "manifest_sha256": str(adapter_manifest.get("adapter_manifest_sha256") or ""),
        "task_count": len({result["task_id"] for result in results}),
        "candidate_count": len(results),
        "included_task_ids": sorted({str(result["task_id"]) for result in results}),
        "excluded_task_ids": [],
        "required_control_kinds": [],
        "observed_control_kinds": sorted({str(result["control_kind"]) for result in results}),
        "control_coverage_by_task_class": {},
        "results": results,
        "receipt_ids": [str(result["receipt_id"]) for result in results],
        "gaming_flags": [],
        "calibration_metric_applyable": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "errors": [],
    }
    summary["summary_sha256"] = _sha256_json(summary)
    return summary


def _evidence_conversion_power_contract(
    report: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    sample_size = dict(report.get("sample_size_sufficiency") or {})
    paired_power = dict(report.get("paired_power") or {})
    comparisons = paired_power.get("comparisons") if isinstance(paired_power.get("comparisons"), Mapping) else {}
    required = dict((comparisons or {}).get("full_supervisor_stack") or {})
    reasons: list[str] = []
    if sample_size.get("status") != "sufficient":
        reasons.append("sample_size_underpowered")
    if paired_power.get("status") != "sufficient":
        reasons.append("paired_power_underpowered")
    if required and required.get("mcnemar_test_passed") is not True:
        reason_values = [
            str(reason)
            for reason in required.get("reasons") or []
            if str(reason)
        ]
        reasons.extend(reason_values or ["mcnemar_test_not_significant"])
    reasons = sorted(set(reasons))
    return {
        "schema_version": "supervisor-evidence-conversion-power-contract/v1",
        "status": "qualified" if not reasons else "underpowered",
        "thresholds": {
            "min_good": int(thresholds.get("min_good", 30)),
            "min_bad": int(thresholds.get("min_bad", 30)),
            "min_discordant": int(thresholds.get("min_discordant", 25)),
            "alpha": float(thresholds.get("alpha", 0.05)),
        },
        "sample_size_status": str(sample_size.get("status") or "missing"),
        "paired_power_status": str(paired_power.get("status") or "missing"),
        "required_comparison": required,
        "reasons": reasons,
        "report_only": True,
        "operator_review_required": True,
        "policy_mutation_allowed": False,
    }


def _safe_powered_fragment(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    sanitized = text.strip("._") or "item"
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{sanitized}-{digest}"


def build_swe_bench_pro_candidate_corpus(
    *,
    attempts: Sequence[Mapping[str, Any]],
    output_path: str | Path,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Write oracle-labeled Pro prediction rows from generated solver attempts."""
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for index, attempt in enumerate(attempts, start=1):
        instance_id = str(attempt.get("instance_id") or "")
        candidate_id = str(attempt.get("candidate_id") or f"attempt-{index}")
        model_patch = str(
            attempt.get("model_patch")
            or attempt.get("patch")
            or attempt.get("prediction")
            or ""
        )
        if not instance_id or not model_patch.strip():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"attempt {index} missing instance_id or model_patch"
            )
        raw_outcome = oracle_runner(attempt)
        outcome = _interpret_oracle_outcome(raw_outcome)
        if outcome["oracle_unavailable"]:
            excluded.append({
                "instance_id": instance_id,
                "candidate_id": candidate_id,
                "reason": outcome["oracle_unavailable_reason"],
            })
            continue
        if outcome.get("patch_applied") is not True:
            excluded.append({
                "instance_id": instance_id,
                "candidate_id": candidate_id,
                "reason": "patch_applied_evidence_missing_or_false",
            })
            continue
        oracle_label = (
            ORACLE_GOOD_LABEL if outcome["oracle_accept"] else ORACLE_BAD_LABEL
        )
        patch_sha256 = sha256(model_patch.encode("utf-8")).hexdigest()
        if patch_sha256 in seen_hashes:
            raise SwebenchMergeabilityFixtureRunnerError(
                f"duplicate generated candidate hash for {candidate_id}: {patch_sha256}"
            )
        seen_hashes.add(patch_sha256)
        row = {
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "model_patch": model_patch,
            "oracle_label": oracle_label,
            "patch_applied": True,
            "candidate_artifact_hash": patch_sha256,
            "model_patch_sha256": patch_sha256,
            "diff_sha256": patch_sha256,
            "origin": _required_prediction_mapping(
                attempt.get("origin"),
                field="origin",
            ),
            "producer": _required_prediction_mapping(
                attempt.get("producer"),
                field="producer",
            ),
        }
        for key in PRODUCED_BASELINE_RECEIPT_KEYS:
            if key in attempt and isinstance(attempt[key], Mapping):
                row[key] = dict(attempt[key])
        rows.append(row)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary = _candidate_corpus_summary_from_predictions({"generated": rows})
    return {
        "schema_version": "supervisor-swebench-pro-candidate-corpus-report/v1",
        "status": "completed" if rows else "unavailable",
        "output_path": str(path),
        "rows": rows,
        "excluded": excluded,
        "summary": summary,
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "powered_improvement_claim_allowed": False,
        "human_mergeability_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }


def _default_official_dataset_loader(*, dataset: str, split: str) -> Sequence[Mapping[str, Any]]:
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official SWE-bench replay requires the optional datasets package "
            "or an injected dataset_loader"
        ) from exc
    loaded = load_dataset(dataset, split=split)
    return list(loaded)


def _default_official_repo_materializer(
    *,
    record: Mapping[str, Any],
    output_dir: str | Path,
) -> Path:
    repo = str(record.get("repo") or "")
    base_commit = str(record.get("base_commit") or "")
    if not repo or not base_commit:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official repo materializer requires repo and base_commit"
        )
    output_path = Path(output_dir).expanduser().resolve()
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clone_url = (
        repo
        if repo.startswith(("https://", "git@", "file://")) or Path(repo).expanduser().exists()
        else f"https://github.com/{repo}.git"
    )
    clone_result = _run_command(
        ("git", "clone", "--no-checkout", clone_url, str(output_path)),
        cwd=output_path.parent,
        timeout_s=600.0,
        name="official_repo_clone",
    )
    if clone_result.status != "passed":
        raise SwebenchMergeabilityFixtureRunnerError(
            "official repo clone failed for "
            f"{repo}: {clone_result.stderr_tail or clone_result.stdout_tail}"
        )
    checkout_result = _run_command(
        ("git", "checkout", base_commit),
        cwd=output_path,
        timeout_s=300.0,
        name="official_repo_checkout",
    )
    if checkout_result.status != "passed":
        raise SwebenchMergeabilityFixtureRunnerError(
            "official repo checkout failed for "
            f"{repo}@{base_commit}: "
            f"{checkout_result.stderr_tail or checkout_result.stdout_tail}"
        )
    git_metadata = output_path / ".git"
    if git_metadata.is_dir():
        shutil.rmtree(git_metadata)
    elif git_metadata.exists():
        git_metadata.unlink()
    return output_path


def swebench_mergeability_official_live_runner(
    *,
    dataset: str,
    dataset_split: str,
    output_dir: str | Path,
    baseline_generator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    supervisor_generator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    allow_live: bool,
    max_budget_usd: float,
    model: str,
    provider: str,
    allow_dataset_fetch: bool = False,
    dataset_loader: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
    repo_materializer: Callable[..., str | Path] | None = None,
    oracle_runner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    oracle_adapter_kind: str = DEFAULT_OFFICIAL_ORACLE_ADAPTER_KIND,
    timeout_s: float = 30.0,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "custom",
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
    swe_bench_pro_scripts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Generate matched live candidates, then evaluate via official replay."""
    if not allow_live:
        raise SwebenchMergeabilityFixtureRunnerError(
            "refusing official SWE-bench live run without allow_live=true"
        )
    budget = float(max_budget_usd or 0.0)
    if budget <= 0:
        raise SwebenchMergeabilityFixtureRunnerError(
            "refusing official SWE-bench live run without max_budget_usd > 0"
        )
    if dataset_loader is None and not allow_dataset_fetch:
        raise SwebenchMergeabilityFixtureRunnerError(
            "refusing official SWE-bench live run without allow_dataset_fetch "
            "or an injected dataset_loader"
        )
    if oracle_runner is None:
        raise SwebenchMergeabilityFixtureRunnerError(
            "oracle_runner is required for official SWE-bench live run"
        )

    started = time.monotonic()
    output_path = Path(output_dir).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)
    loader = dataset_loader or _default_official_dataset_loader
    materializer = repo_materializer or _default_official_repo_materializer
    raw_records = loader(dataset=dataset, split=dataset_split)
    if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
        raw_records = list(raw_records)  # type: ignore[arg-type]
    official_records = [dict(record) for record in raw_records]
    if not official_records:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official SWE-bench live loader returned no records"
        )

    arm_config = {
        "model": str(model or ""),
        "provider": str(provider or ""),
        "budget_usd": budget,
        "timeout_s": float(timeout_s),
    }
    live_arms = {
        "baseline": _empty_live_generation_arm("baseline", arm_config),
        "supervisor": _empty_live_generation_arm("supervisor", arm_config),
    }
    patch_dir = output_path / "official-live-patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    materialized_dir = output_path / "official-live-public-bundles"
    materialized_dir.mkdir(parents=True, exist_ok=True)
    predictions: list[dict[str, Any]] = []

    for record in official_records:
        public_record = _official_public_record(record)
        instance_id = str(public_record.get("instance_id") or "")
        public_bundle = materializer(
            record=public_record,
            output_dir=materialized_dir / _safe_artifact_fragment(instance_id),
        )
        public_bundle_path = Path(public_bundle).expanduser().resolve()
        if not public_bundle_path.exists():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"repo_materializer returned missing public bundle: {public_bundle_path}"
            )
        _ensure_public_bundle_has_no_git(
            public_bundle_path,
            context=f"official live public bundle {instance_id}",
        )

        for arm, generator in (
            ("baseline", baseline_generator),
            ("supervisor", supervisor_generator),
        ):
            generator_input = _build_official_live_generator_input(
                public_record=public_record,
                public_bundle=public_bundle_path,
                arm=arm,
                config=arm_config,
                public_root=(
                    output_path
                    / "official-live-generator-inputs"
                    / _safe_artifact_fragment(instance_id)
                    / arm
                ),
            )
            generation_started = time.monotonic()
            raw_result = generator(generator_input)
            measured_wall_clock = time.monotonic() - generation_started
            generation = _normalise_swebench_live_generation_result(
                raw_result,
                default_candidate_id=f"{arm}-{instance_id}",
                measured_wall_clock_s=measured_wall_clock,
            )
            record_payload = _live_generation_record(
                arm=arm,
                instance_id=instance_id,
                config=arm_config,
                generator_input=generator_input,
                generation=generation,
                patch_dir=patch_dir,
            )
            live_arms[arm]["candidates"].append(record_payload)
            live_arms[arm]["cost_usd"] = round(
                float(live_arms[arm]["cost_usd"])
                + float(record_payload["cost_usd"]),
                6,
            )
            live_arms[arm]["wall_clock_s"] = round(
                float(live_arms[arm]["wall_clock_s"])
                + float(record_payload["wall_clock_s"]),
                6,
            )
            live_arms[arm]["prompt_hash"] = (
                live_arms[arm]["prompt_hash"] or record_payload["prompt_hash"]
            )
            live_arms[arm]["generator_input_hash"] = (
                live_arms[arm]["generator_input_hash"]
                or record_payload["generator_input_hash"]
            )
            live_arms[arm]["token_usage"] = (
                live_arms[arm]["token_usage"]
                or dict(record_payload["token_usage"])
            )
            live_arms[arm]["candidate_artifact_hash"] = record_payload[
                "candidate_artifact_hash"
            ]

            if float(live_arms[arm]["cost_usd"]) > budget:
                live_arms[arm]["status"] = "unavailable"
                live_arms[arm]["accepted"] = False
                live_arms[arm]["unavailable_reason"] = "budget_exceeded"
                report = _official_live_unavailable_report(
                    dataset=dataset,
                    dataset_split=dataset_split,
                    output_path=output_path,
                    started=started,
                    reason="budget_exceeded",
                    arms=live_arms,
                    arm_config=arm_config,
                    gaming_flags=("budget_exceeded",),
                )
                _write_official_live_report(output_path, report)
                return report

            prediction: dict[str, Any] = {
                "instance_id": instance_id,
                "candidate_id": record_payload["candidate_id"],
                "model_patch": generation["model_patch"],
            }
            if arm == "baseline":
                explicit_baseline_receipt = generation.get("single_agent_baseline_decision")
                if not isinstance(explicit_baseline_receipt, Mapping):
                    explicit_baseline_receipt = generation.get("baseline_decision")
                if isinstance(explicit_baseline_receipt, Mapping):
                    prediction["single_agent_baseline_decision"] = dict(
                        explicit_baseline_receipt
                    )
                elif isinstance(generation.get("accept"), bool):
                    prediction["single_agent_baseline_decision"] = {
                        "candidate_id": record_payload["candidate_id"],
                        "accept": bool(generation["accept"]),
                        "decision_source": "single_agent_candidate_generation",
                        "candidate_artifact_hash": record_payload[
                            "candidate_artifact_hash"
                        ],
                        "producer": {
                            "model": arm_config["model"],
                            "provider": arm_config["provider"],
                            "runner_label": "swebench-official-live-baseline-generator",
                        },
                        "prompt_sha256": record_payload["prompt_hash"],
                    }
            predictions.append(prediction)

    predictions_path = output_path / "official_live_predictions.jsonl"
    predictions_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in predictions) + "\n",
        encoding="utf-8",
    )

    official_replay_report = swebench_mergeability_official_replay_runner(
        dataset=dataset,
        dataset_split=dataset_split,
        predictions_path=predictions_path,
        output_dir=output_path / "official-replay",
        allow_dataset_fetch=allow_dataset_fetch,
        dataset_loader=lambda **_kwargs: official_records,
        repo_materializer=materializer,
        oracle_runner=oracle_runner,
        oracle_adapter_kind=oracle_adapter_kind,
        reviewer_panel=reviewer_panel,
        reviewer_panel_mode=reviewer_panel_mode,
        configured_reviewer_panel_options=configured_reviewer_panel_options,
        timeout_s=timeout_s,
        swe_bench_pro_scripts_dir=swe_bench_pro_scripts_dir,
    )
    for arm in live_arms.values():
        arm["status"] = "completed"
        arm["accepted"] = False
        arm["evaluated_by_official_replay"] = True

    report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_OFFICIAL_LIVE_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "unavailable_reason": "",
        "dataset": str(dataset),
        "dataset_split": str(dataset_split),
        "allow_live": True,
        "allow_dataset_fetch": bool(allow_dataset_fetch),
        "official_replay_used": True,
        "live_generation_used": True,
        "live_fetch_used": False,
        "generated_predictions_path": str(predictions_path),
        "generated_predictions_sha256": sha256(
            predictions_path.read_bytes()
        ).hexdigest(),
        "instance_count": len(official_records),
        "candidate_count": len(predictions),
        "model": arm_config["model"],
        "provider": arm_config["provider"],
        "max_budget_usd": budget,
        "timeout_s": float(timeout_s),
        "live_generation_arms": live_arms,
        "total_cost_usd": round(
            sum(float(arm["cost_usd"]) for arm in live_arms.values()),
            6,
        ),
        "oracle_adapter_kind": str(oracle_adapter_kind),
        "official_replay_report": official_replay_report,
        "bridge_report": official_replay_report["bridge_report"],
        "evaluator_hash": official_replay_report["report_sha256"],
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "gaming_flags": [],
        "wall_clock_s": round(time.monotonic() - started, 6),
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    _write_official_live_report(output_path, report)
    return report


def _build_official_live_generator_input(
    *,
    public_record: Mapping[str, Any],
    public_bundle: Path,
    arm: str,
    config: Mapping[str, Any],
    public_root: Path,
) -> dict[str, Any]:
    if public_root.exists():
        shutil.rmtree(public_root)
    _copy_public_fixture_tree(
        public_bundle,
        public_root,
        protected_paths=_DEFAULT_PROTECTED_PATHS,
    )
    public_manifest = _public_tree_manifest(public_root)
    stable_prompt = {
        "instance_id": public_record["instance_id"],
        "repo": public_record.get("repo", ""),
        "base_commit": public_record.get("base_commit", ""),
        "problem_statement": public_record.get("problem_statement", ""),
        "public_checkout_ref": public_record.get("public_checkout_ref", ""),
        "public_checkout_sha256": public_record.get("public_checkout_sha256", ""),
        "public_worktree_manifest": public_manifest,
    }
    generator_input = {
        "schema_version": "supervisor-swebench-official-live-generator-input/v1",
        "arm": arm,
        "instance_id": public_record["instance_id"],
        "repo": public_record.get("repo", ""),
        "base_commit": public_record.get("base_commit", ""),
        "problem_statement": public_record.get("problem_statement", ""),
        "public_checkout_ref": public_record.get("public_checkout_ref", ""),
        "public_checkout_sha256": public_record.get("public_checkout_sha256", ""),
        "public_worktree_ref": str(public_root),
        "public_worktree_sha256": _sha256_json(public_manifest),
        "public_worktree_manifest": public_manifest,
        "protected_path_policy_count": len(_DEFAULT_PROTECTED_PATHS),
        "protected_path_policy_sha256": _sha256_json(list(_DEFAULT_PROTECTED_PATHS)),
        "generation_config": dict(config),
        "prompt_hash": _sha256_json(stable_prompt),
        "generator_input_hash": _sha256_json({
            **stable_prompt,
            "arm": arm,
            "generation_config": dict(config),
        }),
    }
    leaks = _scan_for_swebench_pro_oracle_refs(generator_input)
    if leaks:
        raise SwebenchMergeabilityFixtureRunnerError(
            "official live generator input references forbidden oracle material: "
            + ", ".join(leaks)
        )
    return generator_input


def _official_live_unavailable_report(
    *,
    dataset: str,
    dataset_split: str,
    output_path: Path,
    started: float,
    reason: str,
    arms: Mapping[str, Mapping[str, Any]],
    arm_config: Mapping[str, Any],
    gaming_flags: Sequence[str] = (),
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_OFFICIAL_LIVE_REPORT_SCHEMA_VERSION,
        "status": "unavailable",
        "unavailable_reason": reason,
        "dataset": str(dataset),
        "dataset_split": str(dataset_split),
        "allow_live": True,
        "official_replay_used": False,
        "live_generation_used": True,
        "live_fetch_used": False,
        "instance_count": 0,
        "candidate_count": sum(
            len(arm.get("candidates") or []) for arm in arms.values()
        ),
        "model": str(arm_config.get("model") or ""),
        "provider": str(arm_config.get("provider") or ""),
        "max_budget_usd": float(arm_config.get("budget_usd") or 0.0),
        "timeout_s": float(arm_config.get("timeout_s") or 0.0),
        "live_generation_arms": {key: dict(value) for key, value in arms.items()},
        "total_cost_usd": round(
            sum(float(arm.get("cost_usd") or 0.0) for arm in arms.values()),
            6,
        ),
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "gaming_flags": sorted(set(gaming_flags)),
        "wall_clock_s": round(time.monotonic() - started, 6),
        "report_path": str(output_path / "official_live_report.json"),
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    return report


def _write_official_live_report(output_path: Path, report: Mapping[str, Any]) -> None:
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "official_live_report.json").write_text(
        json.dumps(dict(report), sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )


def swebench_mergeability_live_runner(
    *,
    manifest_path: str | Path,
    output_dir: str | Path,
    baseline_generator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    supervisor_generator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    allow_live: bool,
    max_budget_usd: float,
    model: str,
    provider: str,
    timeout_s: float = 30.0,
    reviewer_panel: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    reviewer_panel_mode: str = "custom",
    configured_reviewer_panel_options: ConfiguredReviewerPanelOptions | None = None,
) -> dict[str, Any]:
    """Run budget-gated live generation, then delegate evaluation to replay."""
    if not allow_live:
        raise SwebenchMergeabilityFixtureRunnerError(
            "refusing live SWE-bench mergeability run without allow_live=true"
        )
    budget = float(max_budget_usd or 0.0)
    if budget <= 0:
        raise SwebenchMergeabilityFixtureRunnerError(
            "refusing live SWE-bench mergeability run without max_budget_usd > 0"
        )
    started = time.monotonic()
    output_path = Path(output_dir).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)
    source = _load_live_source_manifest(manifest_path)
    arm_config = {
        "model": str(model or ""),
        "provider": str(provider or ""),
        "budget_usd": budget,
        "timeout_s": float(timeout_s),
    }
    live_arms = {
        "baseline": _empty_live_generation_arm("baseline", arm_config),
        "supervisor": _empty_live_generation_arm("supervisor", arm_config),
    }
    generated_instances: list[dict[str, Any]] = []
    patch_dir = output_path / "generated-patches"
    patch_dir.mkdir(parents=True, exist_ok=True)

    for source_instance in source["instances"]:
        replay_entry = {
            **{
                key: value
                for key, value in source_instance["instance"].items()
                if key in _PUBLIC_PACKET_ALLOWED_INSTANCE_KEYS
                or key in SWEBENCH_PRO_HIDDEN_ORACLE_KEYS
                or key == "hidden_test_commands"
            },
            "public_bundle": source_instance["public_bundle"],
            "protected_paths": list(source_instance["protected_paths"]),
            "s_probe_substrate": dict(source_instance["s_probe_substrate"]),
            "public_commands": [list(cmd) for cmd in source_instance["public_commands"]],
            "candidates": [],
        }
        instance_id = str(source_instance["instance"]["instance_id"])
        for arm, generator in (
            ("baseline", baseline_generator),
            ("supervisor", supervisor_generator),
        ):
            generator_input = _build_swebench_live_generator_input(
                source_instance=source_instance,
                arm=arm,
                config=arm_config,
                public_root=output_path / "live-public-worktrees" / instance_id / arm,
            )
            generation_started = time.monotonic()
            raw_result = generator(generator_input)
            measured_wall_clock = time.monotonic() - generation_started
            generation = _normalise_swebench_live_generation_result(
                raw_result,
                default_candidate_id=f"{arm}-{instance_id}",
                measured_wall_clock_s=measured_wall_clock,
            )
            record = _live_generation_record(
                arm=arm,
                instance_id=instance_id,
                config=arm_config,
                generator_input=generator_input,
                generation=generation,
                patch_dir=patch_dir,
            )
            live_arms[arm]["candidates"].append(record)
            live_arms[arm]["cost_usd"] = round(
                float(live_arms[arm]["cost_usd"]) + float(record["cost_usd"]),
                6,
            )
            live_arms[arm]["wall_clock_s"] = round(
                float(live_arms[arm]["wall_clock_s"]) + float(record["wall_clock_s"]),
                6,
            )
            if not live_arms[arm]["prompt_hash"]:
                live_arms[arm]["prompt_hash"] = record["prompt_hash"]
            if not live_arms[arm]["generator_input_hash"]:
                live_arms[arm]["generator_input_hash"] = record["generator_input_hash"]
            if not live_arms[arm]["token_usage"]:
                live_arms[arm]["token_usage"] = dict(record["token_usage"])

            if float(record["cost_usd"]) > budget:
                live_arms[arm]["status"] = "unavailable"
                live_arms[arm]["accepted"] = False
                live_arms[arm]["unavailable_reason"] = "budget_exceeded"
                report = _live_unavailable_report(
                    source=source,
                    output_path=output_path,
                    started=started,
                    reason="budget_exceeded",
                    arms=live_arms,
                    arm_config=arm_config,
                    gaming_flags=("budget_exceeded",),
                )
                _write_live_report(output_path, report)
                return report

            candidate = {
                "candidate_id": record["candidate_id"],
                "model_patch_path": record["model_patch_path"],
                "oracle_commands": source_instance["oracle_commands"],
                "live_generation": {
                    "arm": arm,
                    "model": arm_config["model"],
                    "provider": arm_config["provider"],
                    "budget_usd": arm_config["budget_usd"],
                    "cost_usd": record["cost_usd"],
                    "wall_clock_s": record["wall_clock_s"],
                    "token_usage": dict(record["token_usage"]),
                    "prompt_hash": record["prompt_hash"],
                    "candidate_artifact_hash": record["candidate_artifact_hash"],
                },
            }
            if arm == "baseline":
                explicit_baseline_receipt = generation.get("single_agent_baseline_decision")
                if not isinstance(explicit_baseline_receipt, Mapping):
                    explicit_baseline_receipt = generation.get("baseline_decision")
                if isinstance(explicit_baseline_receipt, Mapping):
                    candidate["single_agent_baseline_decision"] = dict(
                        explicit_baseline_receipt
                    )
                elif isinstance(generation.get("accept"), bool):
                    candidate["single_agent_baseline_decision"] = {
                        "candidate_id": record["candidate_id"],
                        "accept": bool(generation["accept"]),
                        "decision_source": "single_agent_candidate_generation",
                        "candidate_artifact_hash": record["candidate_artifact_hash"],
                        "producer": {
                            "model": arm_config["model"],
                            "provider": arm_config["provider"],
                            "runner_label": "swebench-live-baseline-generator",
                        },
                        "prompt_sha256": record["prompt_hash"],
                    }
            replay_entry["candidates"].append(candidate)
        generated_instances.append(replay_entry)

    generated_manifest = {
        "schema_version": SWEBENCH_MERGEABILITY_REPLAY_MANIFEST_SCHEMA_VERSION,
        "instances": generated_instances,
    }
    generated_manifest_path = output_path / "generated_replay_manifest.json"
    generated_manifest_path.write_text(
        json.dumps(generated_manifest, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    replay_report = swebench_mergeability_replay_runner(
        manifest_path=generated_manifest_path,
        output_dir=output_path / "replay",
        reviewer_panel=reviewer_panel,
        reviewer_panel_mode=reviewer_panel_mode,
        configured_reviewer_panel_options=configured_reviewer_panel_options,
        timeout_s=timeout_s,
    )
    for arm in live_arms.values():
        arm["status"] = "completed"
        arm["accepted"] = False
        arm["evaluated_by_replay"] = True
    report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_LIVE_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "unavailable_reason": "",
        "allow_live": True,
        "live_generation_used": True,
        "live_fetch_used": False,
        "manifest_path": source["manifest_path"],
        "manifest_sha256": source["manifest_sha256"],
        "generated_replay_manifest_path": str(generated_manifest_path),
        "generated_replay_manifest_sha256": sha256(
            generated_manifest_path.read_bytes()
        ).hexdigest(),
        "instance_count": len(source["instances"]),
        "candidate_count": sum(
            len(entry["candidates"]) for entry in generated_instances
        ),
        "model": arm_config["model"],
        "provider": arm_config["provider"],
        "max_budget_usd": budget,
        "timeout_s": float(timeout_s),
        "live_generation_arms": live_arms,
        "total_cost_usd": round(
            sum(float(arm["cost_usd"]) for arm in live_arms.values()),
            6,
        ),
        "replay_report": replay_report,
        "bridge_report": replay_report["bridge_report"],
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "gaming_flags": [],
        "wall_clock_s": round(time.monotonic() - started, 6),
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    _write_live_report(output_path, report)
    return report


def _load_live_source_manifest(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SwebenchMergeabilityFixtureRunnerError(
            f"could not read live manifest {path}: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "live manifest must be a JSON object"
        )
    entries = raw.get("instances")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise SwebenchMergeabilityFixtureRunnerError(
            "live manifest instances must be a non-empty list"
        )
    base_dir = path.parent
    instances: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                "live manifest instance entries must be objects"
            )
        public_bundle = str(entry.get("public_bundle") or "")
        if not public_bundle:
            raise SwebenchMergeabilityFixtureRunnerError(
                "live manifest instance.public_bundle is required"
            )
        bundle_path = (base_dir / public_bundle).resolve()
        if not bundle_path.exists():
            raise SwebenchMergeabilityFixtureRunnerError(
                f"live public bundle does not exist: {bundle_path}"
            )
        instance = {
            key: entry[key]
            for key in _PUBLIC_PACKET_ALLOWED_INSTANCE_KEYS
            if key in entry
        }
        for hidden_key in SWEBENCH_PRO_HIDDEN_ORACLE_KEYS:
            if hidden_key in entry:
                instance[hidden_key] = entry[hidden_key]
        instance_id = str(instance.get("instance_id") or "")
        if not instance_id:
            raise SwebenchMergeabilityFixtureRunnerError(
                "live manifest instance.instance_id is required"
            )
        substrate = _validate_s_probe_substrate(
            entry.get("s_probe_substrate")
            or {
                "kind": PUBLIC_STATIC_PATCH_PROBE,
                "requires_patch_applies": True,
                "public_lint_commands": entry.get("public_lint_commands") or [],
                "public_build_commands": entry.get("public_build_commands") or [],
            }
        )
        public_commands = entry.get("public_commands")
        if public_commands is None:
            public_commands = (
                list(substrate["public_lint_commands"])
                + list(substrate["public_build_commands"])
            )
        oracle_commands = entry.get("oracle_commands")
        if not isinstance(oracle_commands, Mapping):
            raise SwebenchMergeabilityFixtureRunnerError(
                "live manifest instance.oracle_commands is required"
            )
        instances.append({
            "instance": instance,
            "public_bundle": str(bundle_path),
            "protected_paths": list(entry.get("protected_paths") or _DEFAULT_PROTECTED_PATHS),
            "s_probe_substrate": substrate,
            "public_commands": [
                [str(part) for part in cmd] for cmd in (public_commands or [])
            ],
            "oracle_commands": _normalise_oracle_commands(oracle_commands),
        })
    if not instances:
        raise SwebenchMergeabilityFixtureRunnerError(
            "live manifest contains no instances"
        )
    return {
        "schema_version": str(raw.get("schema_version") or ""),
        "manifest_path": str(path),
        "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
        "instances": instances,
    }


def _normalise_oracle_commands(
    raw: Mapping[str, Any],
) -> dict[str, list[list[str]]]:
    parsed = {"fail_to_pass": [], "pass_to_pass": []}
    for key in parsed:
        for cmd in raw.get(key) or []:
            parsed[key].append([str(part) for part in cmd])
    return parsed


def _build_swebench_live_generator_input(
    *,
    source_instance: Mapping[str, Any],
    arm: str,
    config: Mapping[str, Any],
    public_root: Path,
) -> dict[str, Any]:
    public_bundle = Path(str(source_instance["public_bundle"]))
    protected_paths = _normalise_protected_paths(source_instance.get("protected_paths"))
    if public_root.exists():
        shutil.rmtree(public_root)
    _copy_public_fixture_tree(
        public_bundle,
        public_root,
        protected_paths=protected_paths,
    )
    public_manifest = _public_tree_manifest(public_root)
    instance = source_instance["instance"]
    stable_prompt = {
        "instance_id": instance["instance_id"],
        "repo": instance.get("repo", ""),
        "base_commit": instance.get("base_commit", ""),
        "problem_statement": instance.get("problem_statement", ""),
        "public_checkout_ref": instance.get("public_checkout_ref", ""),
        "public_checkout_sha256": instance.get("public_checkout_sha256", ""),
        "public_worktree_manifest": public_manifest,
        "s_probe_substrate": dict(source_instance["s_probe_substrate"]),
    }
    generator_input = {
        "schema_version": "supervisor-swebench-mergeability-live-generator-input/v1",
        "arm": arm,
        "instance_id": instance["instance_id"],
        "repo": instance.get("repo", ""),
        "base_commit": instance.get("base_commit", ""),
        "problem_statement": instance.get("problem_statement", ""),
        "public_checkout_ref": instance.get("public_checkout_ref", ""),
        "public_checkout_sha256": instance.get("public_checkout_sha256", ""),
        "public_worktree_ref": str(public_root),
        "public_worktree_sha256": _sha256_json(public_manifest),
        "public_worktree_manifest": public_manifest,
        "s_probe_substrate": dict(source_instance["s_probe_substrate"]),
        "protected_path_policy_count": len(protected_paths),
        "protected_path_policy_sha256": _sha256_json(list(protected_paths)),
        "generation_config": dict(config),
        "prompt_hash": _sha256_json(stable_prompt),
        "generator_input_hash": _sha256_json({
            **stable_prompt,
            "arm": arm,
            "generation_config": dict(config),
        }),
    }
    leaks = _scan_for_swebench_pro_oracle_refs(generator_input)
    if leaks:
        raise SwebenchMergeabilityFixtureRunnerError(
            "live generator input references forbidden oracle material: "
            + ", ".join(leaks)
        )
    return generator_input


def _public_tree_manifest(root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        entries.append({
            "path": rel,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    return entries


def _normalise_swebench_live_generation_result(
    raw_result: Mapping[str, Any],
    *,
    default_candidate_id: str,
    measured_wall_clock_s: float,
) -> dict[str, Any]:
    if not isinstance(raw_result, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "live generator must return a mapping"
        )
    candidate = raw_result.get("candidate")
    payload = dict(candidate) if isinstance(candidate, Mapping) else dict(raw_result)
    patch_text = str(payload.get("model_patch") or payload.get("patch") or "")
    if not patch_text.strip():
        raise SwebenchMergeabilityFixtureRunnerError(
            "live generator result must include model_patch or patch text"
        )
    token_usage = payload.get("token_usage")
    if not isinstance(token_usage, Mapping):
        token_usage = {}
    return {
        "candidate_id": str(payload.get("candidate_id") or default_candidate_id),
        "model_patch": patch_text,
        "cost_usd": float(payload.get("cost_usd") or 0.0),
        "wall_clock_s": float(payload.get("wall_clock_s") or measured_wall_clock_s),
        "token_usage": dict(token_usage),
        "accept": payload.get("accept"),
        "single_agent_baseline_decision": payload.get("single_agent_baseline_decision"),
        "baseline_decision": payload.get("baseline_decision"),
    }


def _empty_live_generation_arm(
    arm: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "arm": arm,
        "status": "not_invoked",
        "accepted": False,
        "unavailable_reason": "",
        "model": str(config.get("model") or ""),
        "provider": str(config.get("provider") or ""),
        "budget_usd": float(config.get("budget_usd") or 0.0),
        "timeout_s": float(config.get("timeout_s") or 0.0),
        "cost_usd": 0.0,
        "wall_clock_s": 0.0,
        "token_usage": {},
        "prompt_hash": "",
        "generator_input_hash": "",
        "candidates": [],
    }


def _live_generation_record(
    *,
    arm: str,
    instance_id: str,
    config: Mapping[str, Any],
    generator_input: Mapping[str, Any],
    generation: Mapping[str, Any],
    patch_dir: Path,
) -> dict[str, Any]:
    candidate_id = str(generation["candidate_id"])
    patch_text = str(generation["model_patch"])
    patch_hash = sha256(patch_text.encode("utf-8")).hexdigest()
    patch_path = patch_dir / _safe_artifact_filename(
        instance_id,
        candidate_id,
        suffix=".patch",
    )
    patch_path.write_text(patch_text, encoding="utf-8")
    return {
        "arm": arm,
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "status": "completed",
        "accepted": False,
        "unavailable_reason": "",
        "model": str(config.get("model") or ""),
        "provider": str(config.get("provider") or ""),
        "budget_usd": float(config.get("budget_usd") or 0.0),
        "timeout_s": float(config.get("timeout_s") or 0.0),
        "cost_usd": round(float(generation.get("cost_usd") or 0.0), 6),
        "wall_clock_s": round(float(generation.get("wall_clock_s") or 0.0), 6),
        "token_usage": dict(generation.get("token_usage") or {}),
        "prompt_hash": str(generator_input.get("prompt_hash") or ""),
        "generator_input_hash": str(generator_input.get("generator_input_hash") or ""),
        "public_worktree_ref": str(generator_input.get("public_worktree_ref") or ""),
        "public_worktree_sha256": str(generator_input.get("public_worktree_sha256") or ""),
        "candidate_artifact_hash": patch_hash,
        "model_patch_path": str(patch_path),
    }


def _safe_artifact_fragment(value: str) -> str:
    safe = []
    for char in str(value):
        safe.append(char if char.isalnum() or char in {"-", "_"} else "_")
    return "".join(safe).strip("_") or "artifact"


def _safe_artifact_filename(
    *fragments: str,
    suffix: str = "",
    max_length: int = 180,
) -> str:
    clean_suffix = str(suffix or "")
    stem = "-".join(_safe_artifact_fragment(fragment) for fragment in fragments)
    if len(stem) + len(clean_suffix) <= max_length:
        return f"{stem}{clean_suffix}"

    digest = sha256(
        "\0".join(str(fragment) for fragment in fragments).encode("utf-8")
    ).hexdigest()[:16]
    prefix_limit = max(1, max_length - len(clean_suffix) - len(digest) - 1)
    prefix = stem[:prefix_limit].rstrip("-_") or "artifact"
    return f"{prefix}-{digest}{clean_suffix}"


def _live_unavailable_report(
    *,
    source: Mapping[str, Any],
    output_path: Path,
    started: float,
    reason: str,
    arms: Mapping[str, Mapping[str, Any]],
    arm_config: Mapping[str, Any],
    gaming_flags: Sequence[str] = (),
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": SWEBENCH_MERGEABILITY_LIVE_REPORT_SCHEMA_VERSION,
        "status": "unavailable",
        "unavailable_reason": reason,
        "allow_live": True,
        "live_generation_used": True,
        "live_fetch_used": False,
        "manifest_path": str(source.get("manifest_path") or ""),
        "manifest_sha256": str(source.get("manifest_sha256") or ""),
        "instance_count": len(source.get("instances") or []),
        "candidate_count": sum(
            len(arm.get("candidates") or []) for arm in arms.values()
        ),
        "model": str(arm_config.get("model") or ""),
        "provider": str(arm_config.get("provider") or ""),
        "max_budget_usd": float(arm_config.get("budget_usd") or 0.0),
        "timeout_s": float(arm_config.get("timeout_s") or 0.0),
        "live_generation_arms": {key: dict(value) for key, value in arms.items()},
        "total_cost_usd": round(
            sum(float(arm.get("cost_usd") or 0.0) for arm in arms.values()),
            6,
        ),
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "gaming_flags": sorted(set(gaming_flags)),
        "wall_clock_s": round(time.monotonic() - started, 6),
        "report_path": str(output_path / "live_report.json"),
    }
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    return report


def _write_live_report(output_path: Path, report: Mapping[str, Any]) -> None:
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "live_report.json").write_text(
        json.dumps(dict(report), sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
