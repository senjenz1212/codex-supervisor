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
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .mergeability_bench import (
    ORACLE_REVIEW_FORBIDDEN_KEYS,
    ORACLE_REVIEW_FORBIDDEN_TEXT,
    _copy_public_fixture_tree,
    _false_accept_at_matched_true_accept,
    _panel_marginal_delta_at_matched_true_accept,
    _public_input_oracle_refs,
    _rate,
    _run_command,
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


# ---------------------------------------------------------------------------
# Fixture-first executable runner
# ---------------------------------------------------------------------------

SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-fixture-report/v1"
)
SWEBENCH_MERGEABILITY_REVIEWER_PACKET_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-reviewer-packet/v1"
)
SWEBENCH_MERGEABILITY_FROZEN_DECISIONS_SCHEMA_VERSION = (
    "supervisor-swebench-mergeability-frozen-decisions/v1"
)

REVIEWER_PANEL_UNAVAILABLE_REASON = "reviewer_panel_unavailable"
PATCH_APPLY_FAILURE_REASON = "patch_apply_failure"

_DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "hidden/",
    ".mergeability/",
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


def _normalise_protected_paths(value: Sequence[str] | None) -> tuple[str, ...]:
    if not value:
        base = list(_DEFAULT_PROTECTED_PATHS)
    else:
        base = [str(p) for p in value]
    if "hidden/" not in base:
        base.append("hidden/")
    if ".mergeability/" not in base:
        base.append(".mergeability/")
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
        }
    if not isinstance(raw, Mapping):
        raise SwebenchMergeabilityFixtureRunnerError(
            "reviewer_panel result must be a mapping with 'accept' and "
            "'unavailable' keys"
        )
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
    protected_paths: Sequence[str] | None = None,
    timeout_s: float = 30.0,
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

        patch_operations = list(candidate.get("patch_operations") or ())
        patch_apply_status, patch_apply_receipts = _apply_patch_operations(
            patch_operations, public_worktree
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

        baseline_self_report = candidate.get("baseline_self_report")
        if baseline_self_report is None:
            baseline_self_report = True
        baseline_decision = {
            "accept": bool(baseline_self_report),
            "unavailable": False,
            "reason": "candidate_self_report",
        }

        candidate_artifacts[key] = {
            "candidate_id": candidate_id,
            "patch": str(candidate.get("patch") or ""),
        }
        arm_decisions[ARM_BASELINE][key] = {
            "accept": baseline_decision["accept"],
            "unavailable": baseline_decision["unavailable"],
        }
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
            "public_command_status": public_command_status,
            "public_command_receipts": public_command_receipts,
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

    # Oracle phase: execute oracle commands AFTER freeze.
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
        candidate_oracle = _candidate_oracle_commands(candidate)
        outcome, receipts = _run_oracle_phase(
            worktree=public_worktree,
            oracle_commands=candidate_oracle,
            timeout_s=timeout_s,
        )
        oracle_outcomes[key] = outcome
        oracle_phase_payload["rows"].append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "fail_to_pass_status": outcome["fail_to_pass_status"],
            "pass_to_pass_status": outcome["pass_to_pass_status"],
            "oracle_command_receipts": receipts,
        })
        oracle_phase_records.append({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "fail_to_pass_status": outcome["fail_to_pass_status"],
            "pass_to_pass_status": outcome["pass_to_pass_status"],
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
        "frozen_decisions": frozen_decisions_payload,
        "frozen_decisions_path": str(frozen_decisions_path),
        "oracle_outputs_path": str(oracle_outputs_path),
        "public_commands": public_commands_list,
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
