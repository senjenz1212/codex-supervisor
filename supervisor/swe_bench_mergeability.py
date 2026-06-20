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
from hashlib import sha256
from typing import Any, Mapping

from .mergeability_bench import (
    ORACLE_REVIEW_FORBIDDEN_KEYS,
    ORACLE_REVIEW_FORBIDDEN_TEXT,
    _false_accept_at_matched_true_accept,
    _panel_marginal_delta_at_matched_true_accept,
    _public_input_oracle_refs,
    _rate,
    _summarize_acceptance_arm,
    _wilson_interval,
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
    "test_patch",
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
        for marker in SWEBENCH_PRO_HIDDEN_ORACLE_KEYS:
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


def _decision_for(
    arm_decisions: Mapping[str, Mapping[str, Any]],
    *,
    arm: str,
    instance_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    per_arm = arm_decisions.get(arm) or {}
    raw = per_arm.get((instance_id, candidate_id))
    if raw is None:
        raw = per_arm.get(f"{instance_id}/{candidate_id}")
    if raw is None:
        return {"accept": False, "unavailable": True}
    if isinstance(raw, Mapping):
        accept = bool(raw.get("accept"))
        unavailable = bool(raw.get("unavailable"))
        return {"accept": accept and not unavailable, "unavailable": unavailable}
    return {"accept": bool(raw), "unavailable": False}


def _frozen_decision_row(
    *,
    instance_id: str,
    candidate_id: str,
    public_packet: Mapping[str, Any],
    arm_decisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = _decision_for(
        arm_decisions, arm=ARM_BASELINE, instance_id=instance_id, candidate_id=candidate_id
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
        "pass_to_pass_regression": pass_to_pass == "fail",
    }


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
                "public_packet_sha256": frozen["public_packet_sha256"],
                "decision_phase_sha256": frozen["decision_phase_sha256"],
                "oracle_isolation_ok": packet_isolation_ok,
                "oracle_isolation_refs": leak_refs,
                "baseline_accept": frozen["baseline_accept"],
                "baseline_unavailable": frozen["baseline_unavailable"],
                "s_probe_accept": frozen["s_probe_accept"],
                "s_probe_unavailable": frozen["s_probe_unavailable"],
                "s_full_accept": frozen["s_full_accept"],
                "s_full_unavailable": frozen["s_full_unavailable"],
                "oracle_ceiling_accept": outcome["oracle_accept"],
                "oracle_ceiling_unavailable": False,
                "oracle_accept": outcome["oracle_accept"],
                "fail_to_pass_status": outcome["fail_to_pass_status"],
                "pass_to_pass_status": outcome["pass_to_pass_status"],
                "pass_to_pass_regression": outcome["pass_to_pass_regression"],
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
                oracle_isolation_violations.append({
                    "instance_id": instance_id,
                    "candidate_id": candidate_id,
                    "refs": leak_refs,
                    "reason": "public_packet_contains_hidden_oracle_material",
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

    arm_summaries = {
        arm: _summarize_acceptance_arm(
            rows,
            arm=arm,
            arm_role=arm,
            decision_source=_arm_decision_source(arm),
            oracle_coupled=(arm == ARM_ORACLE_CEILING),
        )
        for arm in ARM_NAMES
    }

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
    )

    no_regression_findings = _no_regression_findings_for_bridge(rows)
    oracle_isolation_ok = not oracle_isolation_violations

    gaming_flags: list[str] = []
    if oracle_isolation_violations:
        gaming_flags.append("oracle_isolation_violation")
    if no_regression_findings:
        gaming_flags.append("pass_to_pass_regression_detected")

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
        "far_tar_frr": {
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
            }
            for arm in ARM_NAMES
        },
        "false_accept_at_matched_true_accept": matched_true_accept,
        "matched_true_accept_status": {
            arm: matched_true_accept[arm]["status"]
            for arm in matched_true_accept
        },
        "panel_marginal_delta_at_matched_true_accept": panel_marginal_delta,
        "no_regression_findings": no_regression_findings,
        "no_regression_sha256": _sha256_json(no_regression_findings),
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
    report["report_sha256"] = _sha256_json(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )
    return report


def _arm_decision_source(arm: str) -> str:
    if arm == ARM_BASELINE:
        return "candidate_self_report"
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
