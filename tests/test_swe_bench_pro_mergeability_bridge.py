"""Public-boundary tests for the SWE-bench Pro mergeability bridge."""
from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.config import AgenticLeadCfg
from supervisor.swe_bench_eval import (
    build_swe_bench_report,
    load_swe_bench_pilot_sample,
    load_swe_bench_results,
)
from supervisor.swe_bench_mergeability import (
    ARM_BASELINE,
    ARM_ORACLE_CEILING,
    ARM_S_FULL,
    ARM_S_PROBE,
    PUBLIC_STATIC_PATCH_PROBE,
    SWEBENCH_PRO_FORBIDDEN_KEYS,
    SwebenchProBridgeError,
    build_swe_bench_pro_public_packet,
    swebench_pro_mergeability_bridge_report,
)
from supervisor.autoresearch.policy_evolution import (
    derive_policy_evolution_proposals_from_report,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "swe_bench_pro"
SAMPLE = FIXTURE_ROOT / "pilot_sample.yaml"
RESULTS = FIXTURE_ROOT / "pilot_results.json"


def _substrate() -> dict:
    return {
        "kind": PUBLIC_STATIC_PATCH_PROBE,
        "requires_patch_applies": True,
        "public_lint_commands": [["ruff", "check", "."]],
        "public_build_commands": [["python", "-m", "compileall", "."]],
    }


def _instance(instance_id: str, *, with_hidden: bool = True) -> dict:
    record = {
        "instance_id": instance_id,
        "repo": "octocat/example",
        "base_commit": "deadbeef" * 5,
        "problem_statement": "Fix the parser",
        "public_checkout_ref": "refs/heads/main",
        "public_checkout_sha256": "a" * 64,
    }
    if with_hidden:
        record["FAIL_TO_PASS"] = ["tests/test_parser.py::test_hidden"]
        record["PASS_TO_PASS"] = ["tests/test_parser.py::test_existing"]
        record["test_patch"] = "diff --git a/tests/test_parser.py..."
        record["hidden_test_commands"] = [["pytest", "-q", "tests/test_parser.py"]]
    return record


def _candidate(candidate_id: str, *, patch: str = "diff --git a/parser.py...") -> dict:
    return {"candidate_id": candidate_id, "patch": patch}


def _arm_decisions(
    instance_id: str,
    candidate_id: str,
    *,
    baseline: bool = True,
    s_probe: bool | None = True,
    s_full: bool | None = True,
    s_probe_unavailable: bool = False,
    s_full_unavailable: bool = False,
) -> dict:
    return {
        ARM_BASELINE: {
            (instance_id, candidate_id): {"accept": baseline, "unavailable": False},
        },
        ARM_S_PROBE: {
            (instance_id, candidate_id): {
                "accept": bool(s_probe) if s_probe is not None else False,
                "unavailable": s_probe_unavailable,
            },
        },
        ARM_S_FULL: {
            (instance_id, candidate_id): {
                "accept": bool(s_full) if s_full is not None else False,
                "unavailable": s_full_unavailable,
            },
        },
    }


# ---------------------------------------------------------------------------
# Slice 1: Public packet construction + oracle isolation
# ---------------------------------------------------------------------------


def test_public_packet_excludes_hidden_oracle_material():
    instance = _instance("instance_demo__repo-001")
    candidate = _candidate("cand-a")
    packet = build_swe_bench_pro_public_packet(
        instance=instance,
        candidate=candidate,
        s_probe_substrate=_substrate(),
    )

    assert packet["instance_id"] == "instance_demo__repo-001"
    assert packet["repo"] == "octocat/example"
    assert packet["base_commit"].startswith("deadbeef")
    assert packet["problem_statement"] == "Fix the parser"
    assert packet["public_checkout_ref"] == "refs/heads/main"
    assert packet["public_checkout_sha256"] == "a" * 64
    assert packet["candidate_artifact_hash"]
    assert packet["s_probe_substrate"]["kind"] == PUBLIC_STATIC_PATCH_PROBE
    assert packet["s_probe_substrate"]["public_lint_commands"] == [
        ["ruff", "check", "."]
    ]
    assert packet["s_probe_substrate"]["public_build_commands"] == [
        ["python", "-m", "compileall", "."]
    ]
    for forbidden in SWEBENCH_PRO_FORBIDDEN_KEYS:
        assert forbidden not in packet
    for forbidden in (
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "test_patch",
        "final_score",
        "oracle_accept",
        "expected_outcome",
        "hidden_test_commands",
    ):
        assert forbidden not in packet


def test_oracle_material_in_public_packet_triggers_isolation_failure():
    instance = _instance("instance_demo__repo-002")
    # Inject FAIL_TO_PASS into the problem statement string as a leak.
    leaky_instance = dict(instance)
    leaky_instance["problem_statement"] = (
        "FAIL_TO_PASS: tests/test_parser.py::test_hidden"
    )
    candidate = _candidate("cand-b")
    report = swebench_pro_mergeability_bridge_report(
        instances=[leaky_instance],
        candidate_artifacts={("instance_demo__repo-002", "cand-b"): candidate},
        s_probe_substrate=_substrate(),
        arm_decisions=_arm_decisions(
            "instance_demo__repo-002", "cand-b",
            baseline=True, s_probe=True, s_full=True,
        ),
        oracle_outcomes={
            ("instance_demo__repo-002", "cand-b"): {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            }
        },
    )

    assert report["oracle_isolation"]["ok"] is False
    assert report["oracle_isolation"]["violations"]
    violation = report["oracle_isolation"]["violations"][0]
    assert violation["instance_id"] == "instance_demo__repo-002"
    assert violation["candidate_id"] == "cand-b"
    assert violation["refs"]
    row = report["per_row_results"][0]
    assert row["oracle_isolation_ok"] is False
    assert row["s_probe_unavailable"] is True
    assert row["s_full_unavailable"] is True
    assert row["baseline_unavailable"] is True
    # Even though arm_decisions said accept, leaked row contributes 0 accepts.
    assert row["s_probe_accept"] is False
    assert row["s_full_accept"] is False
    assert report["arms"][ARM_S_PROBE]["accepted_count"] == 0
    assert report["arms"][ARM_S_FULL]["accepted_count"] == 0
    assert "oracle_isolation_violation" in report["gaming_flags"]
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


# ---------------------------------------------------------------------------
# Slice 1 + 2: S_probe substrate validation
# ---------------------------------------------------------------------------


def test_s_probe_substrate_is_explicit_and_required():
    instance = _instance("instance_demo__repo-003")
    candidate = _candidate("cand-c")
    with pytest.raises(SwebenchProBridgeError):
        swebench_pro_mergeability_bridge_report(
            instances=[instance],
            candidate_artifacts={("instance_demo__repo-003", "cand-c"): candidate},
            s_probe_substrate=None,  # type: ignore[arg-type]
            arm_decisions=_arm_decisions(
                "instance_demo__repo-003", "cand-c",
                baseline=True, s_probe=True, s_full=True,
            ),
            oracle_outcomes={
                ("instance_demo__repo-003", "cand-c"): {
                    "fail_to_pass_status": "pass",
                    "pass_to_pass_status": "pass",
                }
            },
        )

    report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts={("instance_demo__repo-003", "cand-c"): candidate},
        s_probe_substrate=_substrate(),
        arm_decisions=_arm_decisions(
            "instance_demo__repo-003", "cand-c",
            baseline=True, s_probe=True, s_full=True,
        ),
        oracle_outcomes={
            ("instance_demo__repo-003", "cand-c"): {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            }
        },
    )
    substrate = report["s_probe_substrate"]
    assert substrate["kind"] == PUBLIC_STATIC_PATCH_PROBE
    assert substrate["requires_patch_applies"] is True
    assert substrate["public_lint_commands"] == [["ruff", "check", "."]]
    assert substrate["public_build_commands"] == [["python", "-m", "compileall", "."]]
    for packet in report["public_packets"]:
        assert packet["s_probe_substrate"]["kind"] == PUBLIC_STATIC_PATCH_PROBE


# ---------------------------------------------------------------------------
# Slice 2: Frozen arm decisions before oracle attachment
# ---------------------------------------------------------------------------


def test_arm_decisions_are_recorded_before_oracle_results():
    instance = _instance("instance_demo__repo-004")
    candidate = _candidate("cand-d")
    report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts={("instance_demo__repo-004", "cand-d"): candidate},
        s_probe_substrate=_substrate(),
        arm_decisions=_arm_decisions(
            "instance_demo__repo-004", "cand-d",
            baseline=True, s_probe=True, s_full=False,
        ),
        oracle_outcomes={
            ("instance_demo__repo-004", "cand-d"): {
                "fail_to_pass_status": "fail",
                "pass_to_pass_status": "pass",
            }
        },
    )

    assert len(report["decision_phase_rows"]) == 1
    decision_row = report["decision_phase_rows"][0]
    # Decision-phase fields are present and do not include oracle outcome data.
    assert decision_row["instance_id"] == "instance_demo__repo-004"
    assert decision_row["candidate_id"] == "cand-d"
    assert decision_row["s_probe_accept"] is True
    assert decision_row["s_full_accept"] is False
    assert decision_row["baseline_accept"] is True
    assert "oracle_accept" not in decision_row
    assert "fail_to_pass_status" not in decision_row
    assert "pass_to_pass_status" not in decision_row
    assert len(decision_row["decision_phase_sha256"]) == 64
    assert len(decision_row["public_packet_sha256"]) == 64
    # Same per-row payload should still reflect the public packet hash.
    row = report["per_row_results"][0]
    assert row["public_packet_sha256"] == decision_row["public_packet_sha256"]
    assert row["decision_phase_sha256"] == decision_row["decision_phase_sha256"]


# ---------------------------------------------------------------------------
# Slice 2: S_full unavailability not imputed as accept
# ---------------------------------------------------------------------------


def test_full_gate_reviewer_unavailable_is_not_imputed_as_accept():
    instance = _instance("instance_demo__repo-005")
    candidate = _candidate("cand-e")
    report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts={("instance_demo__repo-005", "cand-e"): candidate},
        s_probe_substrate=_substrate(),
        arm_decisions=_arm_decisions(
            "instance_demo__repo-005", "cand-e",
            baseline=True,
            s_probe=True,
            s_full=False,
            s_full_unavailable=True,
        ),
        oracle_outcomes={
            ("instance_demo__repo-005", "cand-e"): {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            }
        },
    )

    row = report["per_row_results"][0]
    assert row["s_full_unavailable"] is True
    assert row["s_full_accept"] is False
    full_arm = report["arms"][ARM_S_FULL]
    assert full_arm["unavailable_count"] == 1
    assert full_arm["accepted_count"] == 0
    assert full_arm["availability_status"] == "unavailable"
    # Report-only invariants remain false.
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False


# ---------------------------------------------------------------------------
# Slice 2: S_full can disagree with S_probe; marginal delta is reportable
# ---------------------------------------------------------------------------


def test_full_gate_can_disagree_with_s_probe_and_records_delta():
    # Two candidates so matched-true-accept denominator >= 2.
    rows = []
    candidate_artifacts: dict = {}
    arm_decisions: dict = {ARM_BASELINE: {}, ARM_S_PROBE: {}, ARM_S_FULL: {}}
    oracle_outcomes: dict = {}
    for i, (s_probe, s_full, oracle_pass) in enumerate(
        [(True, False, True), (True, True, True)]
    ):
        instance_id = f"instance_demo__repo-disagree-{i}"
        candidate_id = f"cand-{i}"
        rows.append(_instance(instance_id))
        candidate_artifacts[(instance_id, candidate_id)] = _candidate(candidate_id)
        arm_decisions[ARM_BASELINE][(instance_id, candidate_id)] = {
            "accept": True, "unavailable": False,
        }
        arm_decisions[ARM_S_PROBE][(instance_id, candidate_id)] = {
            "accept": s_probe, "unavailable": False,
        }
        arm_decisions[ARM_S_FULL][(instance_id, candidate_id)] = {
            "accept": s_full, "unavailable": False,
        }
        oracle_outcomes[(instance_id, candidate_id)] = {
            "fail_to_pass_status": "pass" if oracle_pass else "fail",
            "pass_to_pass_status": "pass",
        }

    report = swebench_pro_mergeability_bridge_report(
        instances=rows,
        candidate_artifacts=candidate_artifacts,
        s_probe_substrate=_substrate(),
        arm_decisions=arm_decisions,
        oracle_outcomes=oracle_outcomes,
    )

    disagreement_rows = [
        row for row in report["per_row_results"]
        if row["s_full_disagrees_with_s_probe"]
    ]
    assert len(disagreement_rows) == 1
    assert disagreement_rows[0]["instance_id"].endswith("disagree-0")
    panel = report["panel_marginal_delta_at_matched_true_accept"]
    # Either reportable status; status string must exist.
    assert panel["status"] in {
        "computed", "not_matched", "insufficient_candidate_pool", "unavailable"
    }


# ---------------------------------------------------------------------------
# Slice 3: PASS_TO_PASS regression flows into no-regression reporting
# ---------------------------------------------------------------------------


def test_pass_to_pass_regression_contributes_to_no_regression_status():
    instance = _instance("instance_demo__repo-007")
    candidate = _candidate("cand-g")
    report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts={("instance_demo__repo-007", "cand-g"): candidate},
        s_probe_substrate=_substrate(),
        arm_decisions=_arm_decisions(
            "instance_demo__repo-007", "cand-g",
            baseline=True, s_probe=True, s_full=True,
        ),
        oracle_outcomes={
            ("instance_demo__repo-007", "cand-g"): {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "fail",
            }
        },
    )

    row = report["per_row_results"][0]
    assert row["fail_to_pass_status"] == "pass"
    assert row["pass_to_pass_status"] == "fail"
    assert row["pass_to_pass_regression"] is True
    # Oracle accept requires BOTH FAIL_TO_PASS pass AND PASS_TO_PASS pass.
    assert row["oracle_accept"] is False
    findings = report["no_regression_findings"]
    assert findings
    assert findings[0]["reason"] == "pass_to_pass_regression"
    assert findings[0]["protected_scope"] == "swe_bench_pro_pass_to_pass_suite"
    assert "pass_to_pass_regression_detected" in report["gaming_flags"]
    assert len(report["no_regression_sha256"]) == 64


# ---------------------------------------------------------------------------
# Slice 3: Oracle ceiling is coupled and cannot drive improvement claim
# ---------------------------------------------------------------------------


def test_oracle_ceiling_is_coupled_and_never_supervisor_improvement():
    instance = _instance("instance_demo__repo-008")
    candidate = _candidate("cand-h")
    before = AgenticLeadCfg().model_dump()
    report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts={("instance_demo__repo-008", "cand-h"): candidate},
        s_probe_substrate=_substrate(),
        arm_decisions=_arm_decisions(
            "instance_demo__repo-008", "cand-h",
            baseline=False, s_probe=True, s_full=True,
        ),
        oracle_outcomes={
            ("instance_demo__repo-008", "cand-h"): {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            }
        },
    )
    after = AgenticLeadCfg().model_dump()

    assert after == before
    oracle_arm = report["arms"][ARM_ORACLE_CEILING]
    assert oracle_arm["oracle_coupled"] is True
    assert report["improvement_claim_allowed"] is False
    assert report["metric_applyable"] is False
    # Policy derivation must refuse this report-only report.
    proposals = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=Path("."),
        affected_gates=("agentic_lead",),
    )
    assert proposals == []


# ---------------------------------------------------------------------------
# Slice 3: FAR / TAR / FRR denominators use post-decision oracle labels
# ---------------------------------------------------------------------------


def test_far_tar_frr_denominators_use_post_decision_oracle_labels():
    # 1 oracle-positive accepted by s_probe (true accept),
    # 1 oracle-negative accepted by s_probe (false accept),
    # 1 oracle-positive rejected by s_probe (false reject).
    instances = []
    candidate_artifacts: dict = {}
    arm_decisions: dict = {ARM_BASELINE: {}, ARM_S_PROBE: {}, ARM_S_FULL: {}}
    oracle_outcomes: dict = {}
    scenarios = [
        # (instance_suffix, s_probe_accept, fail_to_pass, pass_to_pass)
        ("ok", True, "pass", "pass"),
        ("bad", True, "fail", "pass"),
        ("reject", False, "pass", "pass"),
    ]
    for suffix, s_probe_accept, ftp, ptp in scenarios:
        instance_id = f"instance_demo__repo-{suffix}"
        candidate_id = f"cand-{suffix}"
        instances.append(_instance(instance_id))
        candidate_artifacts[(instance_id, candidate_id)] = _candidate(candidate_id)
        arm_decisions[ARM_BASELINE][(instance_id, candidate_id)] = {
            "accept": True, "unavailable": False,
        }
        arm_decisions[ARM_S_PROBE][(instance_id, candidate_id)] = {
            "accept": s_probe_accept, "unavailable": False,
        }
        arm_decisions[ARM_S_FULL][(instance_id, candidate_id)] = {
            "accept": s_probe_accept, "unavailable": False,
        }
        oracle_outcomes[(instance_id, candidate_id)] = {
            "fail_to_pass_status": ftp,
            "pass_to_pass_status": ptp,
        }

    report = swebench_pro_mergeability_bridge_report(
        instances=instances,
        candidate_artifacts=candidate_artifacts,
        s_probe_substrate=_substrate(),
        arm_decisions=arm_decisions,
        oracle_outcomes=oracle_outcomes,
    )

    s_probe_arm = report["arms"][ARM_S_PROBE]
    assert s_probe_arm["n_bad"] == 1
    assert s_probe_arm["n_good"] == 2
    assert s_probe_arm["false_accept_count"] == 1
    assert s_probe_arm["true_accept_count"] == 1
    assert s_probe_arm["false_reject_count"] == 1
    assert s_probe_arm["false_accept_rate"] == pytest.approx(1.0)
    assert s_probe_arm["true_accept_rate"] == pytest.approx(0.5)
    assert s_probe_arm["false_reject_rate"] == pytest.approx(0.5)
    far_tar_frr = report["far_tar_frr"][ARM_S_PROBE]
    assert far_tar_frr["n_bad"] == 1
    assert far_tar_frr["n_good"] == 2
    assert far_tar_frr["false_accept_confidence_interval"]["denominator"] == 1
    assert far_tar_frr["true_accept_confidence_interval"]["denominator"] == 2
    # Decision rows are unchanged even after oracle labels attached.
    decision_row = report["decision_phase_rows"][0]
    assert "oracle_accept" not in decision_row
    assert "fail_to_pass_status" not in decision_row


# ---------------------------------------------------------------------------
# Slice 4: Existing instruments remain green
# ---------------------------------------------------------------------------


def test_existing_swe_bench_pass_at_k_behavior_remains_green():
    report = build_swe_bench_report(
        sample=load_swe_bench_pilot_sample(SAMPLE),
        results=load_swe_bench_results(RESULTS),
    )
    assert report["arms"]["baseline"]["resolved_count"] == 96
    assert report["arms"]["baseline"]["trial_count"] == 150
    assert report["arms"]["baseline"]["pass_at_1_mean"] == pytest.approx(0.64)
    assert report["arms"]["harness"]["pass_at_1_mean"] == pytest.approx(0.7)
    assert report["delta"]["harness_minus_baseline_pass_at_1"] == pytest.approx(0.06)
    assert report["default_change_allowed"] is False
    assert report["report_only"]["policy_mutated"] is False


def test_existing_mergeability_behavior_remains_green():
    # Bridge module reuses mergeability helpers without rewriting them.
    from supervisor.mergeability_bench import (
        ORACLE_REVIEW_FORBIDDEN_KEYS,
        _public_input_oracle_refs,
        _summarize_acceptance_arm,
        _wilson_interval,
    )

    # Forbidden key set still contains the local mergeability oracle fields.
    for key in ("expected_outcome", "final_score", "oracle_accept", "hidden_test_commands"):
        assert key in ORACLE_REVIEW_FORBIDDEN_KEYS

    # Public-input oracle scanner still detects forbidden keys.
    refs = _public_input_oracle_refs({"oracle_accept": True, "ok_field": 1})
    assert "oracle_accept" in refs

    # Summarizer still computes the documented denominators on a tiny fixture.
    rows = [
        {"baseline_accept": True, "oracle_accept": True, "baseline_unavailable": False},
        {"baseline_accept": False, "oracle_accept": False, "baseline_unavailable": False},
    ]
    summary = _summarize_acceptance_arm(
        rows,
        arm="baseline",
        arm_role="baseline_self_report",
        decision_source="candidate_self_report",
        oracle_coupled=False,
    )
    assert summary["n_bad"] == 1
    assert summary["n_good"] == 1
    assert summary["accepted_count"] == 1

    ci = _wilson_interval(0, 0)
    assert ci["lower"] is None
    assert ci["upper"] is None
