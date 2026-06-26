"""Public-boundary tests for the SWE-bench Pro mergeability bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.config import AgenticLeadCfg
from supervisor.mergeability_bench import _copy_public_fixture_tree
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
    PATCH_APPLY_FAILURE_REASON,
    PUBLIC_STATIC_PATCH_PROBE,
    REVIEWER_PANEL_UNAVAILABLE_REASON,
    SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION,
    SWEBENCH_PRO_FORBIDDEN_KEYS,
    SwebenchMergeabilityFixtureRunnerError,
    SwebenchProBridgeError,
    build_swe_bench_pro_public_packet,
    swebench_mergeability_fixture_runner,
    swebench_mergeability_official_all_arms_diagnostic_runner,
    swebench_mergeability_official_live_runner,
    swebench_mergeability_live_runner,
    swebench_mergeability_official_replay_runner,
    swebench_mergeability_replay_runner,
    swebench_pro_mergeability_bridge_report,
)
from supervisor.swe_bench_official_oracle import run_official_harness_oracle
from supervisor.autoresearch.policy_evolution import (
    derive_policy_evolution_proposals_from_report,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "swe_bench_pro"
SAMPLE = FIXTURE_ROOT / "pilot_sample.yaml"
RESULTS = FIXTURE_ROOT / "pilot_results.json"
DEFAULT_CANDIDATE_PATCH = "diff --git a/parser.py..."


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


def _candidate(candidate_id: str, *, patch: str = DEFAULT_CANDIDATE_PATCH) -> dict:
    return {"candidate_id": candidate_id, "patch": patch}


def _produced_baseline_decision(
    candidate_id: str,
    *,
    patch: str = DEFAULT_CANDIDATE_PATCH,
    accept: bool = True,
    decision_source: str = "replayed_single_agent_baseline",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "accept": accept,
        "decision_source": decision_source,
        "candidate_artifact_hash": sha256(patch.encode("utf-8")).hexdigest(),
        "producer": {
            "model": "fixture-baseline-llm",
            "provider": "fixture",
            "runner_label": "swebench-single-agent-baseline-replay",
        },
        "prompt_sha256": sha256(f"prompt:{candidate_id}".encode("utf-8")).hexdigest(),
    }


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
            (instance_id, candidate_id): _produced_baseline_decision(
                candidate_id,
                accept=baseline,
            ),
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
        arm_decisions[ARM_BASELINE][(instance_id, candidate_id)] = (
            _produced_baseline_decision(candidate_id, accept=True)
        )
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


def test_bridge_legacy_bool_baseline_row_is_unavailable():
    instance = _instance("instance_demo__repo-legacy-baseline")
    candidate = _candidate("cand-legacy-bool")
    report = swebench_pro_mergeability_bridge_report(
        instances=[instance],
        candidate_artifacts={
            ("instance_demo__repo-legacy-baseline", "cand-legacy-bool"): candidate
        },
        s_probe_substrate=_substrate(),
        arm_decisions={
            ARM_BASELINE: {
                ("instance_demo__repo-legacy-baseline", "cand-legacy-bool"): True,
            },
            ARM_S_PROBE: {
                ("instance_demo__repo-legacy-baseline", "cand-legacy-bool"): {
                    "accept": True,
                    "unavailable": False,
                },
            },
            ARM_S_FULL: {
                ("instance_demo__repo-legacy-baseline", "cand-legacy-bool"): {
                    "accept": True,
                    "unavailable": False,
                },
            },
        },
        oracle_outcomes={
            ("instance_demo__repo-legacy-baseline", "cand-legacy-bool"): {
                "fail_to_pass_status": "fail",
                "pass_to_pass_status": "pass",
            }
        },
    )

    row = report["per_row_results"][0]
    assert row["baseline_accept"] is False
    assert row["baseline_unavailable"] is True
    assert row["baseline_evidence_kind"] == "legacy_bool"
    assert row["baseline_unavailable_reason"] == "legacy_bool_baseline_row_not_replayable"
    assert report["arms"][ARM_BASELINE]["availability_status"] == "unavailable"


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
        arm_decisions[ARM_BASELINE][(instance_id, candidate_id)] = (
            _produced_baseline_decision(candidate_id, accept=True)
        )
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
    assert far_tar_frr["false_reject_confidence_interval"]["denominator"] == 2
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


# ---------------------------------------------------------------------------
# Fixture-first executable runner
# ---------------------------------------------------------------------------


def _build_runner_fixture(tmp_path: Path) -> Path:
    """Build a minimal local fixture with public + protected oracle files."""
    root = tmp_path / "fixture"
    root.mkdir()
    (root / "parser.py").write_text(
        "def parse(value):\n    return value\n",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "lib.py").write_text(
        "def lib():\n    return 1\n",
        encoding="utf-8",
    )
    # Protected oracle paths that must never appear in the public worktree.
    (root / "hidden").mkdir()
    (root / "hidden" / "test_behavior.py").write_text(
        "def test_hidden():\n    assert True\n",
        encoding="utf-8",
    )
    (root / ".mergeability").mkdir()
    (root / ".mergeability" / "oracle.json").write_text(
        "{\"oracle_accept\": true}",
        encoding="utf-8",
    )
    return root


def _runner_substrate() -> dict:
    return {
        "kind": PUBLIC_STATIC_PATCH_PROBE,
        "requires_patch_applies": True,
        "public_lint_commands": [],
        "public_build_commands": [],
    }


def _runner_instance(instance_id: str = "fixture_demo__repo-001") -> dict:
    return {
        "instance_id": instance_id,
        "repo": "octocat/example",
        "base_commit": "deadbeef" * 5,
        "problem_statement": "Add type hints to parser",
        "public_checkout_ref": "refs/heads/main",
        "public_checkout_sha256": "b" * 64,
    }


def _runner_candidate(
    candidate_id: str,
    *,
    patch_mode: str = "modify",
    patch_target: str = "parser.py",
    patch_content: str = (
        "def parse(value: str) -> str:\n    return value\n"
    ),
    oracle_fail_to_pass_passes: bool = True,
    oracle_pass_to_pass_passes: bool = True,
    baseline_self_report: bool = True,
    include_baseline_decision: bool = True,
) -> dict:
    def _cmd_for(passes: bool) -> list[str]:
        if passes:
            return ["python", "-c", "print('ok')"]
        return ["python", "-c", "import sys; sys.exit(1)"]

    patch_text = (
        f"diff --git a/{patch_target} b/{patch_target}\n"
        f"--- a/{patch_target}\n"
        f"+++ b/{patch_target}\n"
    )
    candidate = {
        "candidate_id": candidate_id,
        "patch": patch_text,
        "baseline_self_report": baseline_self_report,
        "patch_operations": [
            {"path": patch_target, "mode": patch_mode, "content": patch_content},
        ],
        "oracle_commands": {
            "fail_to_pass": [_cmd_for(oracle_fail_to_pass_passes)],
            "pass_to_pass": [_cmd_for(oracle_pass_to_pass_passes)],
        },
    }
    if include_baseline_decision:
        candidate["single_agent_baseline_decision"] = _produced_baseline_decision(
            candidate_id,
            patch=patch_text,
            accept=baseline_self_report,
        )
    return candidate


def test_fixture_runner_executes_public_probe_and_excludes_hidden_oracle(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance()
    candidate = _runner_candidate("cand-1")
    public_commands = [
        ["python", "-c", "import pathlib; print(sorted(pathlib.Path('.').iterdir()))"],
    ]

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate],
        s_probe_substrate=_runner_substrate(),
        public_commands=public_commands,
        output_dir=output_dir,
    )

    # Public command actually executed and produced receipts.
    per_candidate = report["per_candidate_artifacts"]
    assert len(per_candidate) == 1
    receipts = per_candidate[0]["public_command_receipts"]
    assert len(receipts) == 1
    assert receipts[0]["status"] == "passed"
    assert receipts[0]["returncode"] == 0
    assert receipts[0]["stdout_sha256"]
    assert receipts[0]["stderr_sha256"]
    assert receipts[0]["argv"][-1].startswith("import pathlib")

    # Public worktree must not contain any protected oracle paths.
    public_worktree = Path(per_candidate[0]["public_worktree"])
    assert (public_worktree / "parser.py").exists()
    assert not (public_worktree / "hidden").exists()
    assert not (public_worktree / ".mergeability").exists()
    assert not (public_worktree / "hidden" / "test_behavior.py").exists()

    # Reviewer packet exists, has no forbidden oracle material.
    reviewer_packet_path = Path(report["reviewer_packets"][0]["reviewer_packet_path"])
    packet_text = reviewer_packet_path.read_text(encoding="utf-8")
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "oracle_accept"):
        assert forbidden not in packet_text
    reviewer_packet = json.loads(packet_text)
    evidence = reviewer_packet["public_execution_evidence"]
    assert evidence["schema_version"] == "supervisor-swebench-public-execution-evidence/v1"
    assert evidence["patch_apply_check"]["status"] == "passed"
    assert evidence["public_probe_commands"]["status"] == "passed"
    assert evidence["hidden_oracle_exclusion"]["hidden_oracle_material_included"] is False
    assert evidence["protected_path_exclusion"]["protected_path_content_included"] is False
    # Schema version reflects the fixture runner.
    assert report["schema_version"] == SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION


def test_fixture_runner_freezes_decisions_before_oracle_execution(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-002")
    candidate = _runner_candidate("cand-freeze")

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate],
        s_probe_substrate=_runner_substrate(),
        public_commands=[],
        output_dir=output_dir,
    )

    frozen_path = Path(report["frozen_decisions_path"])
    oracle_path = Path(report["oracle_outputs_path"])
    assert frozen_path.exists()
    assert oracle_path.exists()
    # Decision artifact must be on disk before oracle outputs.
    assert frozen_path.stat().st_mtime_ns <= oracle_path.stat().st_mtime_ns
    frozen_payload = report["frozen_decisions"]
    assert isinstance(frozen_payload["frozen_at_epoch_s"], float)
    assert len(frozen_payload["frozen_decisions_sha256"]) == 64
    # Frozen rows must not include oracle outcomes.
    for row in frozen_payload["rows"]:
        assert "fail_to_pass_status" not in row
        assert "pass_to_pass_status" not in row
        assert "oracle_accept" not in row
        assert len(row["public_packet_sha256"]) == 64
    # Bridge report decision-phase rows do not include oracle outcomes either.
    for decision_row in report["bridge_report"]["decision_phase_rows"]:
        assert "oracle_accept" not in decision_row
        assert "fail_to_pass_status" not in decision_row


def test_fixture_runner_patch_apply_failure_is_recorded_not_crashed(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-003")
    # Modify a path that does not exist in the public worktree.
    candidate = _runner_candidate(
        "cand-bad-patch",
        patch_mode="modify",
        patch_target="missing/never_existed.py",
        patch_content="raise SystemExit(0)\n",
    )

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate],
        s_probe_substrate=_runner_substrate(),
        public_commands=[["python", "-c", "print('would not run')"]],
        output_dir=output_dir,
    )

    # Runner produced a report instead of crashing.
    assert report["bridge_report"]["candidate_count"] == 1
    per_candidate = report["per_candidate_artifacts"][0]
    assert per_candidate["patch_apply_status"] == "failed"
    receipts = per_candidate["patch_apply_receipts"]
    assert receipts
    assert receipts[0]["status"] == "failed"
    assert receipts[0]["reason"] == "modify_missing_target"
    # Public commands must not be executed when patch apply failed.
    assert per_candidate["public_command_status"] == "not_executed"
    assert per_candidate["public_command_receipts"] == []
    # S_probe decision is a hard reject with patch_apply_failure reason.
    assert per_candidate["s_probe_decision"]["accept"] is False
    assert per_candidate["s_probe_decision"]["reason"] == PATCH_APPLY_FAILURE_REASON
    # Bridge report rows reflect the rejection.
    row = report["bridge_report"]["per_row_results"][0]
    assert row["s_probe_accept"] is False
    assert row["baseline_accept"] is True
    assert row["baseline_evidence_kind"] == "produced_single_agent_baseline"
    # Frozen decisions also record the rejection.
    frozen = report["frozen_decisions"]["rows"][0]
    assert frozen["s_probe_accept"] is False
    assert frozen["s_probe_reason"] == PATCH_APPLY_FAILURE_REASON
    # Report-only invariants remain false.
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["bridge_report"]["improvement_claim_allowed"] is False


def test_fixture_runner_missing_produced_baseline_receipt_is_unavailable(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-missing-baseline")
    candidate = _runner_candidate(
        "cand-missing-baseline",
        baseline_self_report=True,
        include_baseline_decision=False,
    )

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate],
        s_probe_substrate=_runner_substrate(),
        public_commands=[],
        output_dir=output_dir,
    )

    row = report["bridge_report"]["per_row_results"][0]
    assert row["baseline_accept"] is False
    assert row["baseline_unavailable"] is True
    assert row["baseline_evidence_kind"] == "missing"
    assert row["baseline_unavailable_reason"] == "baseline_decisions_not_supplied"
    assert row["legacy_baseline_self_report"] is True
    assert row["legacy_baseline_self_report_calibration_only"] is True
    baseline_arm = report["bridge_report"]["arms"][ARM_BASELINE]
    assert baseline_arm["availability_status"] == "unavailable"
    assert baseline_arm["accepted_count"] == 0
    frozen = report["frozen_decisions"]["rows"][0]
    assert frozen["baseline_accept"] is False
    assert frozen["baseline_unavailable"] is True
    assert frozen["baseline_unavailable_reason"] == "baseline_decisions_not_supplied"


def test_fixture_runner_produced_baseline_receipt_populates_baseline_arm(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-produced-baseline")
    candidate = _runner_candidate("cand-produced-baseline")

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate],
        s_probe_substrate=_runner_substrate(),
        public_commands=[],
        output_dir=output_dir,
    )

    row = report["bridge_report"]["per_row_results"][0]
    decision = candidate["single_agent_baseline_decision"]
    assert row["baseline_accept"] is True
    assert row["baseline_unavailable"] is False
    assert row["baseline_decision_source"] == "replayed_single_agent_baseline"
    assert row["baseline_evidence_kind"] == "produced_single_agent_baseline"
    assert row["baseline_candidate_id"] == "cand-produced-baseline"
    assert row["baseline_candidate_artifact_hash"] == decision["candidate_artifact_hash"]
    assert row["baseline_prompt_sha256"] == decision["prompt_sha256"]
    assert row["baseline_producer"]["runner_label"] == (
        "swebench-single-agent-baseline-replay"
    )
    baseline_arm = report["bridge_report"]["arms"][ARM_BASELINE]
    assert baseline_arm["availability_status"] == "available"
    assert baseline_arm["decision_source"] == "replayed_single_agent_baseline"
    assert baseline_arm["evidence_kind"] == "produced_single_agent_baseline"


def test_fixture_runner_marks_full_gate_unavailable_without_panel(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-004")
    candidate = _runner_candidate("cand-no-panel")

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate],
        s_probe_substrate=_runner_substrate(),
        public_commands=[],
        output_dir=output_dir,
        reviewer_panel=None,
    )

    per_candidate = report["per_candidate_artifacts"][0]
    assert per_candidate["s_full_decision"]["unavailable"] is True
    assert per_candidate["s_full_decision"]["accept"] is False
    assert per_candidate["s_full_decision"]["reason"] == REVIEWER_PANEL_UNAVAILABLE_REASON

    reviewer_results = report["independent_reviewer_results"]
    assert len(reviewer_results) == 1
    assert reviewer_results[0]["unavailable"] is True
    assert reviewer_results[0]["accept"] is False
    assert reviewer_results[0]["reason"] == REVIEWER_PANEL_UNAVAILABLE_REASON

    row = report["bridge_report"]["per_row_results"][0]
    assert row["s_full_unavailable"] is True
    assert row["s_full_accept"] is False
    # S_probe was accepted but is NOT copied into S_full.
    assert row["s_probe_accept"] is True
    full_arm = report["bridge_report"]["arms"][ARM_S_FULL]
    assert full_arm["unavailable_count"] == 1
    assert full_arm["accepted_count"] == 0
    assert full_arm["availability_status"] == "unavailable"


def test_fixture_runner_preserves_panel_disagreement_with_probe(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-005")
    # Two candidates: panel disagrees with first, agrees with second.
    candidate_a = _runner_candidate("cand-disagree")
    candidate_b = _runner_candidate(
        "cand-agree",
        patch_target="src/lib.py",
        patch_content="def lib() -> int:\n    return 1\n",
    )
    call_log: list[str] = []

    def panel(packet):
        call_log.append(packet["candidate_id"])
        if packet["candidate_id"] == "cand-disagree":
            return {
                "accept": False,
                "unavailable": False,
                "reason": "independent_reviewer_rejected",
                "reviewer_id": "reviewer-1",
                "reviewer_notes": "missing test coverage",
            }
        return {
            "accept": True,
            "unavailable": False,
            "reason": "independent_reviewer_accepted",
            "reviewer_id": "reviewer-1",
        }

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate_a, candidate_b],
        s_probe_substrate=_runner_substrate(),
        public_commands=[],
        output_dir=output_dir,
        reviewer_panel=panel,
    )

    assert call_log == ["cand-disagree", "cand-agree"]
    rows = report["bridge_report"]["per_row_results"]
    disagree_rows = [r for r in rows if r["candidate_id"] == "cand-disagree"]
    agree_rows = [r for r in rows if r["candidate_id"] == "cand-agree"]
    assert disagree_rows and agree_rows
    # Disagreement is preserved in the per-row data.
    assert disagree_rows[0]["s_probe_accept"] is True
    assert disagree_rows[0]["s_full_accept"] is False
    assert disagree_rows[0]["s_full_unavailable"] is False
    assert disagree_rows[0]["s_full_disagrees_with_s_probe"] is True
    assert agree_rows[0]["s_full_disagrees_with_s_probe"] is False
    # Independent reviewer results record both outcomes with packet refs.
    reviewer_results = {
        r["candidate_id"]: r for r in report["independent_reviewer_results"]
    }
    assert reviewer_results["cand-disagree"]["accept"] is False
    assert reviewer_results["cand-disagree"]["reviewer_id"] == "reviewer-1"
    assert reviewer_results["cand-agree"]["accept"] is True
    reviewer_packets = {
        p["candidate_id"]: p for p in report["reviewer_packets"]
    }
    assert reviewer_packets["cand-disagree"]["reviewer_packet_sha256"]
    assert reviewer_packets["cand-agree"]["reviewer_packet_sha256"]
    # The panel marginal delta has a reportable status (computed / unavailable
    # / not_matched / insufficient_candidate_pool).
    panel_delta = report["bridge_report"]["panel_marginal_delta_at_matched_true_accept"]
    assert panel_delta["status"] in {
        "computed",
        "not_matched",
        "insufficient_candidate_pool",
        "unavailable",
    }


def test_fixture_runner_report_only_invariants_and_no_policy_outputs(tmp_path):
    fixture_root = _build_runner_fixture(tmp_path)
    output_dir = tmp_path / "out"
    instance = _runner_instance("fixture_demo__repo-006")
    # Two candidates: one oracle-positive (n_good >= 1) and one oracle-negative
    # (n_bad >= 1) so FAR/TAR denominators are both non-empty.
    candidate_pos = _runner_candidate(
        "cand-pos",
        oracle_fail_to_pass_passes=True,
        oracle_pass_to_pass_passes=True,
    )
    candidate_neg = _runner_candidate(
        "cand-neg",
        patch_target="src/lib.py",
        patch_content="def lib() -> int:\n    return 1\n",
        oracle_fail_to_pass_passes=False,
        oracle_pass_to_pass_passes=True,
    )

    report = swebench_mergeability_fixture_runner(
        fixture_root=fixture_root,
        instance=instance,
        candidates=[candidate_pos, candidate_neg],
        s_probe_substrate=_runner_substrate(),
        public_commands=[],
        output_dir=output_dir,
    )

    bridge_report = report["bridge_report"]
    s_probe_arm = bridge_report["arms"][ARM_S_PROBE]
    # Non-empty denominators: one oracle-positive, one oracle-negative.
    assert s_probe_arm["n_good"] >= 1
    assert s_probe_arm["n_bad"] >= 1
    # Report-only invariants are all false.
    assert bridge_report["metric_applyable"] is False
    assert bridge_report["improvement_claim_allowed"] is False
    assert bridge_report["default_change_allowed"] is False
    assert bridge_report["policy_mutated"] is False
    assert bridge_report["gate_advanced"] is False
    # Policy evolution must refuse to derive applyable proposals.
    proposals = derive_policy_evolution_proposals_from_report(
        bridge_report,
        repo_root=Path("."),
        affected_gates=("agentic_lead",),
    )
    assert proposals == []
    # No policy proposal artifacts created under output_dir.
    output_files = {p.name for p in Path(output_dir).rglob("*") if p.is_file()}
    for forbidden_name in (
        "policy_proposal.json",
        "policy_mutation.json",
        "applyable_proposal.json",
    ):
        assert forbidden_name not in output_files
    # Schema version explicit on the runner report.
    assert report["schema_version"] == SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# SWE-bench real-instance replay runner
# ---------------------------------------------------------------------------


def _valid_model_patch() -> str:
    return (
        "diff --git a/parser.py b/parser.py\n"
        "--- a/parser.py\n"
        "+++ b/parser.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def parse(value):\n"
        "+def parse(value: str) -> str:\n"
        "     return value\n"
    )


def _invalid_model_patch() -> str:
    return (
        "diff --git a/parser.py b/parser.py\n"
        "--- a/parser.py\n"
        "+++ b/parser.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def parse(missing):\n"
        "+def parse(value: str) -> str:\n"
        "     return value\n"
    )


def _write_replay_manifest(
    tmp_path: Path,
    *,
    candidates: list[dict] | None = None,
    public_commands: list[list[str]] | None = None,
) -> Path:
    root = tmp_path / "replay"
    bundle = root / "bundles" / "demo"
    bundle.mkdir(parents=True)
    (bundle / "parser.py").write_text(
        "def parse(value):\n    return value\n",
        encoding="utf-8",
    )
    (bundle / "hidden").mkdir()
    (bundle / "hidden" / "test_behavior.py").write_text(
        "def test_hidden():\n    assert parse('x') == 'x'\n",
        encoding="utf-8",
    )
    (bundle / ".mergeability").mkdir()
    (bundle / ".mergeability" / "oracle.json").write_text(
        json.dumps({"oracle_accept": True}),
        encoding="utf-8",
    )
    (bundle / ".git" / "objects" / "pack").mkdir(parents=True)
    (bundle / ".git" / "objects" / "pack" / "pack-demo.idx").write_text(
        "git internals should not be copied\n",
        encoding="utf-8",
    )
    patches = root / "patches"
    patches.mkdir()
    default_candidates = [
        {
            "candidate_id": "real-good",
            "model_patch_path": "patches/real-good.patch",
            "baseline_self_report": True,
            "oracle_commands": {
                "fail_to_pass": [["python", "-c", "print('ftp ok')"]],
                "pass_to_pass": [["python", "-c", "print('ptp ok')"]],
            },
        },
        {
            "candidate_id": "real-bad",
            "model_patch_path": "patches/real-bad.patch",
            "baseline_self_report": True,
            "oracle_commands": {
                "fail_to_pass": [["python", "-c", "import sys; sys.exit(1)"]],
                "pass_to_pass": [["python", "-c", "print('ptp ok')"]],
            },
        },
    ]
    for candidate_id in ("real-good", "real-bad", "bad-patch"):
        patch_text = _invalid_model_patch() if candidate_id == "bad-patch" else _valid_model_patch()
        (patches / f"{candidate_id}.patch").write_text(patch_text, encoding="utf-8")
    for candidate in default_candidates:
        candidate["single_agent_baseline_decision"] = _produced_baseline_decision(
            str(candidate["candidate_id"]),
            patch=_valid_model_patch(),
            accept=True,
        )

    manifest = {
        "schema_version": "supervisor-swebench-mergeability-replay-manifest/v1",
        "instances": [
            {
                "instance_id": "real_demo__repo-001",
                "repo": "octocat/example",
                "base_commit": "deadbeef" * 5,
                "problem_statement": "Type the parser without changing behavior",
                "public_checkout_ref": "refs/heads/main",
                "public_checkout_sha256": "c" * 64,
                "public_bundle": "bundles/demo",
                "protected_paths": ["hidden/", ".mergeability/"],
                "FAIL_TO_PASS": ["hidden/test_behavior.py::test_hidden"],
                "PASS_TO_PASS": ["hidden/test_behavior.py::test_existing"],
                "test_patch": "diff --git a/hidden/test_behavior.py...",
                "public_commands": public_commands if public_commands is not None else [],
                "candidates": candidates if candidates is not None else default_candidates,
            }
        ],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


def test_replay_runner_loads_manifest_applies_model_patch_and_keeps_oracle_hidden(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)
    output_dir = tmp_path / "out"

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=output_dir,
    )

    assert report["schema_version"] == "supervisor-swebench-mergeability-replay-report/v1"
    assert report["instance_count"] == 1
    assert report["candidate_count"] == 2
    assert report["s_probe_execution_substrate"]["execution_label"] == "static_lint_only"
    bridge_report = report["bridge_report"]
    assert bridge_report["arms"][ARM_S_PROBE]["n_good"] == 1
    assert bridge_report["arms"][ARM_S_PROBE]["n_bad"] == 1
    assert bridge_report["metric_applyable"] is False
    assert bridge_report["improvement_claim_allowed"] is False

    per_candidate = report["instance_reports"][0]["per_candidate_artifacts"][0]
    assert per_candidate["patch_apply_status"] == "passed"
    assert per_candidate["patch_apply_receipts"][0]["mode"] == "model_patch"
    public_worktree = Path(per_candidate["public_worktree"])
    assert "def parse(value: str) -> str" in (public_worktree / "parser.py").read_text(encoding="utf-8")
    assert not (public_worktree / "hidden").exists()
    assert not (public_worktree / ".mergeability").exists()
    assert not (public_worktree / ".git").exists()

    public_packet_text = json.dumps(bridge_report["public_packets"], sort_keys=True)
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "oracle_accept"):
        assert forbidden not in public_packet_text
    reviewer_packet_path = Path(report["instance_reports"][0]["reviewer_packets"][0]["reviewer_packet_path"])
    reviewer_packet_text = reviewer_packet_path.read_text(encoding="utf-8")
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "oracle_accept"):
        assert forbidden not in reviewer_packet_text


def test_public_fixture_copy_replaces_stale_target_and_excludes_git(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / ".git" / "objects").mkdir(parents=True)
    (source / ".git" / "objects" / "pack.idx").write_text("git internals\n", encoding="utf-8")
    (source / "parser.py").write_text("def parse(value):\n    return value\n", encoding="utf-8")
    (target / ".git").mkdir(parents=True)
    (target / ".git" / "stale").write_text("stale rerun state\n", encoding="utf-8")

    _copy_public_fixture_tree(
        source,
        target,
        protected_paths=("hidden/", ".mergeability/", ".git/"),
    )

    assert (target / "parser.py").exists()
    assert not (target / ".git").exists()


def test_replay_runner_records_deterministic_patch_apply_failure(tmp_path):
    manifest_path = _write_replay_manifest(
        tmp_path,
        candidates=[
            {
                "candidate_id": "bad-patch",
                "model_patch_path": "patches/bad-patch.patch",
                "baseline_self_report": True,
                "oracle_commands": {
                    "fail_to_pass": [["python", "-c", "import sys; sys.exit(1)"]],
                    "pass_to_pass": [["python", "-c", "print('ptp ok')"]],
                },
            }
        ],
    )

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
    )

    per_candidate = report["instance_reports"][0]["per_candidate_artifacts"][0]
    assert per_candidate["patch_apply_status"] == "failed"
    assert per_candidate["patch_apply_receipts"][0]["reason"] == "git_apply_check_failed"
    assert per_candidate["s_probe_decision"]["accept"] is False
    assert per_candidate["s_probe_decision"]["reason"] == PATCH_APPLY_FAILURE_REASON
    frozen_path = Path(report["instance_reports"][0]["frozen_decisions_path"])
    oracle_path = Path(report["instance_reports"][0]["oracle_outputs_path"])
    assert frozen_path.stat().st_mtime_ns <= oracle_path.stat().st_mtime_ns
    assert report["bridge_report"]["per_row_results"][0]["s_probe_accept"] is False


def test_replay_runner_accepts_configured_style_panel_result(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)
    calls: list[str] = []

    def configured_style_panel(packet):
        calls.append(packet["candidate_id"])
        return {
            "decision": "accept",
            "available": True,
            "reason": "all_available_reviewers_accept",
            "reviewer_ids": ["independent-reviewer-0"],
            "available_reviewers": ["independent-reviewer-0"],
            "reviewer_results": [
                {
                    "reviewer_id": "independent-reviewer-0",
                    "verdict_present": True,
                    "decision": "accept",
                }
            ],
        }

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        reviewer_panel=configured_style_panel,
        reviewer_panel_mode="configured",
    )

    assert calls == ["real-good", "real-bad"]
    rows = report["bridge_report"]["per_row_results"]
    assert all(row["s_full_unavailable"] is False for row in rows)
    assert all(row["s_full_accept"] is True for row in rows)
    preflight = report["configured_reviewer_panel_preflight"]
    assert preflight["full_roster_available"] is True
    assert preflight["failure_classification"] == "available"
    assert preflight["available_reviewers"] == ["independent-reviewer-0"]
    assert report["instance_reports"][0]["independent_reviewer_results"][0]["reviewer_id"] == (
        "independent-reviewer-0"
    )


def test_replay_runner_unconfigured_panel_preflight_reports_uninvoked(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
    )

    preflight = report["configured_reviewer_panel_preflight"]
    assert preflight["mode"] == "custom"
    assert preflight["configured_panel_invoked"] is False
    assert preflight["full_roster_available"] is False
    assert preflight["failure_classification"] == "uninvoked"
    assert all(
        row["s_full_unavailable"]
        for row in report["bridge_report"]["per_row_results"]
    )


def test_replay_runner_configured_missing_reviewer_keeps_s_full_unavailable(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)

    def missing_reviewer_panel(_packet):
        return {
            "decision": "revise",
            "available": True,
            "reason": "missing_reviewer_verdict",
            "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"],
            "available_reviewers": ["independent-reviewer-0"],
            "missing_reviewers": ["independent-reviewer-1"],
            "reviewer_results": [
                {
                    "reviewer_id": "independent-reviewer-0",
                    "runtime": "cursor_sdk",
                    "model": "cursor-default",
                    "verdict_present": True,
                    "decision": "accept",
                    "severity": "low",
                    "transcript_sha256": "a" * 64,
                    "output_sha256": "b" * 64,
                },
                {
                    "reviewer_id": "independent-reviewer-1",
                    "runtime": "codex_cli",
                    "model": "gpt-5.5",
                    "verdict_present": False,
                    "failure_classification": "reviewer_invocation_failed",
                    "transcript_sha256": "c" * 64,
                    "output_sha256": "d" * 64,
                },
            ],
        }

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        reviewer_panel=missing_reviewer_panel,
        reviewer_panel_mode="configured",
    )

    rows = report["bridge_report"]["per_row_results"]
    assert all(row["s_full_unavailable"] is True for row in rows)
    assert all(row["s_full_accept"] is False for row in rows)
    preflight = report["configured_reviewer_panel_preflight"]
    assert preflight["configured_panel_invoked"] is True
    assert preflight["full_roster_available"] is False
    assert preflight["failure_classification"] == "missing_reviewer_verdict"
    assert preflight["available_reviewers"] == ["independent-reviewer-0"]
    assert preflight["missing_reviewers"] == ["independent-reviewer-1"]
    assert preflight["transcript_sha256s"] == ["a" * 64, "c" * 64]
    reviewer_row = report["instance_reports"][0]["independent_reviewer_results"][0]
    assert reviewer_row["failure_classification"] == "missing_reviewer_verdict"
    assert reviewer_row["reviewer_results"][1]["failure_classification"] == (
        "reviewer_invocation_failed"
    )


def test_replay_runner_configured_missing_roster_evidence_keeps_s_full_unavailable(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)

    def missing_roster_panel(_packet):
        return {
            "decision": "accept",
            "available": True,
            "reason": "all_available_reviewers_accept",
            "available_reviewers": ["independent-reviewer-0"],
            "missing_reviewers": [],
            "reviewer_results": [
                {
                    "reviewer_id": "independent-reviewer-0",
                    "runtime": "cursor_sdk",
                    "model": "cursor-default",
                    "verdict_present": True,
                    "decision": "accept",
                    "severity": "low",
                    "transcript_sha256": "3" * 64,
                    "output_sha256": "4" * 64,
                },
            ],
        }

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        reviewer_panel=missing_roster_panel,
        reviewer_panel_mode="configured",
    )

    rows = report["bridge_report"]["per_row_results"]
    assert all(row["s_full_unavailable"] is True for row in rows)
    assert all(row["s_full_accept"] is False for row in rows)
    preflight = report["configured_reviewer_panel_preflight"]
    assert preflight["full_roster_available"] is False
    assert preflight["failure_classification"] == "missing_reviewer_roster"
    assert preflight["available_reviewers"] == ["independent-reviewer-0"]
    assert preflight["missing_reviewers"] == []
    reviewer_row = report["instance_reports"][0]["independent_reviewer_results"][0]
    assert reviewer_row["failure_classification"] == "missing_reviewer_roster"
    assert reviewer_row["reason"] == "missing_reviewer_roster"


def test_replay_runner_configured_quality_reject_is_not_infrastructure_unavailable(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)

    def rejecting_full_roster_panel(_packet):
        return {
            "decision": "revise",
            "available": True,
            "reason": "reviewer_non_accept",
            "reviewer_ids": ["independent-reviewer-0", "independent-reviewer-1"],
            "available_reviewers": ["independent-reviewer-0", "independent-reviewer-1"],
            "missing_reviewers": [],
            "reviewer_results": [
                {
                    "reviewer_id": "independent-reviewer-0",
                    "runtime": "cursor_sdk",
                    "model": "cursor-default",
                    "verdict_present": True,
                    "decision": "revise",
                    "severity": "important",
                    "transcript_sha256": "e" * 64,
                    "output_sha256": "f" * 64,
                },
                {
                    "reviewer_id": "independent-reviewer-1",
                    "runtime": "codex_cli",
                    "model": "gpt-5.5",
                    "verdict_present": True,
                    "decision": "accept",
                    "severity": "low",
                    "transcript_sha256": "1" * 64,
                    "output_sha256": "2" * 64,
                },
            ],
        }

    report = swebench_mergeability_replay_runner(
        manifest_path=manifest_path,
        output_dir=tmp_path / "out",
        reviewer_panel=rejecting_full_roster_panel,
        reviewer_panel_mode="configured",
    )

    rows = report["bridge_report"]["per_row_results"]
    assert all(row["s_full_unavailable"] is False for row in rows)
    assert all(row["s_full_accept"] is False for row in rows)
    preflight = report["configured_reviewer_panel_preflight"]
    assert preflight["full_roster_available"] is True
    assert preflight["failure_classification"] == "quality_reject"
    assert "reviewer_infrastructure_unavailable" not in preflight["failure_classifications"]


def test_replay_cli_runs_manifest_without_live_fetch_or_panel(tmp_path):
    manifest_path = _write_replay_manifest(tmp_path)
    output_dir = tmp_path / "cli-out"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_swe_bench_mergeability_replay.py",
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    summary = json.loads(result.stdout)
    assert summary["status"] == "reported"
    assert summary["allow_live"] is False
    report_path = Path(summary["report"])
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["bridge_report"]["arms"][ARM_S_FULL]["availability_status"] == "unavailable"
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["bridge_report"]["improvement_claim_allowed"] is False


# ---------------------------------------------------------------------------
# Official SWE-bench replay adapter
# ---------------------------------------------------------------------------


def _write_official_predictions(
    tmp_path: Path,
    *,
    instance_id: str = "official_demo__repo-001",
    candidate_id: str = "official-cand",
) -> Path:
    prediction = {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "model_patch": _valid_model_patch(),
        "single_agent_baseline_decision": _produced_baseline_decision(
            candidate_id,
            patch=_valid_model_patch(),
            accept=True,
        ),
    }
    predictions_path = tmp_path / "predictions.jsonl"
    predictions_path.write_text(json.dumps(prediction) + "\n", encoding="utf-8")
    return predictions_path


def _official_record(instance_id: str = "official_demo__repo-001") -> dict:
    return {
        "instance_id": instance_id,
        "repo": "octocat/example",
        "base_commit": "cafebabe" * 5,
        "problem_statement": "Fix the public parser behavior",
        "patch": "SECRET GOLD PATCH",
        "test_patch": "SECRET TEST PATCH",
        "FAIL_TO_PASS": ["tests/test_parser.py::test_hidden"],
        "PASS_TO_PASS": ["tests/test_parser.py::test_existing"],
        "final_score": 1,
    }


def _official_materializer(*, record, output_dir):
    assert "patch" not in record
    assert "test_patch" not in record
    assert "FAIL_TO_PASS" not in record
    assert "PASS_TO_PASS" not in record
    bundle = Path(output_dir) / "public-bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "parser.py").write_text(
        "def parse(value):\n    return value\n",
        encoding="utf-8",
    )
    (bundle / "hidden").mkdir()
    (bundle / "hidden" / "should_not_copy.py").write_text(
        "oracle_accept = True\n",
        encoding="utf-8",
    )
    return bundle


def test_official_replay_gold_smoke_without_produced_baseline_receipt_is_unavailable(tmp_path):
    prediction = {
        "instance_id": "official_demo__repo-001",
        "candidate_id": "gold-smoke",
        "model_patch": _valid_model_patch(),
    }
    predictions_path = tmp_path / "predictions.jsonl"
    predictions_path.write_text(json.dumps(prediction) + "\n", encoding="utf-8")

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    row = report["bridge_report"]["per_row_results"][0]
    assert row["candidate_id"] == "gold-smoke"
    assert row["baseline_accept"] is False
    assert row["baseline_unavailable"] is True
    assert row["baseline_evidence_kind"] == "missing"
    assert row["baseline_unavailable_reason"] == "baseline_decisions_not_supplied"
    baseline_arm = report["bridge_report"]["arms"][ARM_BASELINE]
    assert baseline_arm["availability_status"] == "unavailable"
    matched = report["bridge_report"]["false_accept_at_matched_true_accept"][ARM_S_PROBE]
    assert matched == {
        "status": "unavailable",
        "reason": "baseline_arm_unavailable",
        "unavailable_count": 1,
    }
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_official_replay_produced_baseline_receipt_populates_baseline_arm(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    row = report["bridge_report"]["per_row_results"][0]
    assert row["baseline_accept"] is True
    assert row["baseline_unavailable"] is False
    assert row["baseline_decision_source"] == "replayed_single_agent_baseline"
    assert row["baseline_evidence_kind"] == "produced_single_agent_baseline"
    assert row["baseline_candidate_id"] == "official-cand"
    assert row["baseline_candidate_artifact_hash"] == sha256(
        _valid_model_patch().encode("utf-8")
    ).hexdigest()
    baseline_arm = report["bridge_report"]["arms"][ARM_BASELINE]
    assert baseline_arm["availability_status"] == "available"
    assert baseline_arm["evidence_kind"] == "produced_single_agent_baseline"
    assert baseline_arm["decision_source"] == "replayed_single_agent_baseline"


def test_official_replay_hash_mismatched_baseline_receipt_is_unavailable(tmp_path):
    decision = _produced_baseline_decision(
        "official-cand",
        patch=_valid_model_patch(),
        accept=True,
    )
    bogus_hash = sha256(b"not-the-candidate-patch").hexdigest()
    decision["candidate_artifact_hash"] = bogus_hash
    prediction = {
        "instance_id": "official_demo__repo-001",
        "candidate_id": "official-cand",
        "model_patch": _valid_model_patch(),
        "single_agent_baseline_decision": decision,
    }
    predictions_path = tmp_path / "predictions.jsonl"
    predictions_path.write_text(json.dumps(prediction) + "\n", encoding="utf-8")

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    row = report["bridge_report"]["per_row_results"][0]
    assert row["baseline_accept"] is False
    assert row["baseline_unavailable"] is True
    assert row["baseline_evidence_kind"] == "hash_mismatch"
    assert row["baseline_candidate_artifact_hash"] == bogus_hash
    assert row["baseline_unavailable_reason"] == "candidate_artifact_hash_mismatch"
    baseline_arm = report["bridge_report"]["arms"][ARM_BASELINE]
    assert baseline_arm["availability_status"] == "unavailable"
    matched = report["bridge_report"]["false_accept_at_matched_true_accept"][ARM_S_PROBE]
    assert matched["status"] == "unavailable"
    assert matched["reason"] == "baseline_arm_unavailable"


def test_official_replay_refuses_dataset_fetch_without_opt_in(tmp_path):
    calls: list[str] = []

    def materializer(**_kwargs):
        calls.append("materializer")
        raise AssertionError("materializer must not run")

    def oracle(**_kwargs):
        calls.append("oracle")
        raise AssertionError("oracle must not run")

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="allow_dataset_fetch"):
        swebench_mergeability_official_replay_runner(
            dataset="SWE-bench/SWE-bench_Verified",
            dataset_split="test",
            predictions_path=tmp_path / "missing.jsonl",
            output_dir=tmp_path / "out",
            allow_dataset_fetch=False,
            repo_materializer=materializer,
            oracle_runner=oracle,
        )

    assert calls == []


def test_official_replay_materializes_public_bundle_and_excludes_hidden_oracle(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": {
                "saw_fail_to_pass": context["FAIL_TO_PASS"],
                "frozen_exists": Path(context["frozen_decisions_path"]).exists(),
                **_official_adapter_receipt(context),
            },
        },
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["schema_version"] == "supervisor-swebench-official-replay-report/v1"
    assert report["official_replay_used"] is True
    assert report["live_fetch_used"] is False
    assert report["live_generation_used"] is False
    assert report["bridge_report"]["oracle_isolation"]["ok"] is True
    public_packet_text = json.dumps(report["bridge_report"]["public_packets"], sort_keys=True)
    for forbidden in (
        "SECRET GOLD PATCH",
        "SECRET TEST PATCH",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "final_score",
        "oracle_accept",
    ):
        assert forbidden not in public_packet_text
    reviewer_packet_path = Path(
        report["replay_report"]["instance_reports"][0]["reviewer_packets"][0][
            "reviewer_packet_path"
        ]
    )
    reviewer_packet_text = reviewer_packet_path.read_text(encoding="utf-8")
    for forbidden in (
        "SECRET GOLD PATCH",
        "SECRET TEST PATCH",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "final_score",
        "oracle_accept",
    ):
        assert forbidden not in reviewer_packet_text
    frozen_text = Path(
        report["replay_report"]["instance_reports"][0]["frozen_decisions_path"]
    ).read_text(encoding="utf-8")
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "SECRET TEST PATCH"):
        assert forbidden not in frozen_text


def test_official_replay_freezes_decisions_before_oracle_adapter(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)
    oracle_calls: list[dict] = []

    def oracle(context):
        oracle_calls.append({
            "frozen_exists": Path(context["frozen_decisions_path"]).exists(),
            "fail_to_pass": list(context["FAIL_TO_PASS"]),
            "pass_to_pass": list(context["PASS_TO_PASS"]),
        })
        return {
            "fail_to_pass_status": "fail",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(
                context,
                fail_to_pass_status="fail",
                pass_to_pass_status="pass",
            ),
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=oracle,
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert oracle_calls == [{
        "frozen_exists": True,
        "fail_to_pass": ["tests/test_parser.py::test_hidden"],
        "pass_to_pass": ["tests/test_parser.py::test_existing"],
    }]
    instance_report = report["replay_report"]["instance_reports"][0]
    frozen_path = Path(instance_report["frozen_decisions_path"])
    oracle_path = Path(instance_report["oracle_outputs_path"])
    assert frozen_path.stat().st_mtime_ns <= oracle_path.stat().st_mtime_ns
    oracle_payload = json.loads(oracle_path.read_text(encoding="utf-8"))
    assert oracle_payload["rows"][0]["oracle_adapter_kind"] == "official_docker_or_equivalent"
    assert report["bridge_report"]["per_row_results"][0]["oracle_accept"] is False


def test_official_replay_report_labels_oracle_adapter_and_stays_report_only(tmp_path):
    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=_write_official_predictions(tmp_path),
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(context),
        },
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["oracle_adapter_kind"] == "official_docker_or_equivalent"
    assert report["replay_report"]["oracle_adapter_kind"] == "official_docker_or_equivalent"
    assert report["status"] == "completed"
    assert report["oracle_receipt_validation"]["validated"] is True
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["bridge_report"]["improvement_claim_allowed"] is False
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False


def test_official_replay_cli_refuses_without_opt_in(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_swe_bench_mergeability_replay.py",
            "--official-replay",
            "--dataset",
            "SWE-bench/SWE-bench_Verified",
            "--dataset-split",
            "test",
            "--predictions",
            str(tmp_path / "missing.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "without --allow-dataset-fetch" in result.stderr


# ---------------------------------------------------------------------------
# SWE-bench budget-gated live mergeability runner
# ---------------------------------------------------------------------------


def _write_live_manifest(tmp_path: Path) -> Path:
    root = tmp_path / "live"
    bundle = root / "bundles" / "demo"
    bundle.mkdir(parents=True)
    (bundle / "parser.py").write_text(
        "def parse(value):\n    return value\n",
        encoding="utf-8",
    )
    (bundle / "hidden").mkdir()
    (bundle / "hidden" / "test_behavior.py").write_text(
        "def test_hidden():\n    assert False\n",
        encoding="utf-8",
    )
    (bundle / ".mergeability").mkdir()
    (bundle / ".mergeability" / "oracle.json").write_text(
        json.dumps({"oracle_accept": False}),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "supervisor-swebench-mergeability-live-source/v1",
        "instances": [
            {
                "instance_id": "live_demo__repo-001",
                "repo": "octocat/example",
                "base_commit": "deadbeef" * 5,
                "problem_statement": "Generate a patch that types the parser",
                "public_checkout_ref": "refs/heads/main",
                "public_checkout_sha256": "d" * 64,
                "public_bundle": "bundles/demo",
                "protected_paths": ["hidden/", ".mergeability/"],
                "FAIL_TO_PASS": ["hidden/test_behavior.py::test_hidden"],
                "PASS_TO_PASS": ["hidden/test_behavior.py::test_existing"],
                "test_patch": "diff --git a/hidden/test_behavior.py...",
                "public_commands": [],
                "oracle_commands": {
                    "fail_to_pass": [["python", "-c", "print('ftp ok')"]],
                    "pass_to_pass": [["python", "-c", "print('ptp ok')"]],
                },
            }
        ],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


class _FailingLiveGenerator:
    calls: list[dict]

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, generator_input):
        self.calls.append(dict(generator_input))
        raise AssertionError("live generator should not be called")


class _FakeLiveGenerator:
    def __init__(self, *, cost_usd: float = 0.25) -> None:
        self.cost_usd = cost_usd
        self.calls: list[dict] = []

    def __call__(self, generator_input):
        self.calls.append(dict(generator_input))
        return {
            "candidate_id": f"{generator_input['arm']}-generated",
            "model_patch": _valid_model_patch(),
            "cost_usd": self.cost_usd,
            "wall_clock_s": 0.5,
            "token_usage": {"input_tokens": 101, "output_tokens": 23},
        }


class _AcceptingFakeLiveGenerator(_FakeLiveGenerator):
    def __call__(self, generator_input):
        result = super().__call__(generator_input)
        result["accept"] = True
        return result


class _BaselineReceiptSmugglingLiveGenerator(_FakeLiveGenerator):
    def __call__(self, generator_input):
        result = super().__call__(generator_input)
        result["single_agent_baseline_decision"] = {
            "candidate_id": result["candidate_id"],
            "accept": True,
            "decision_source": "single_agent_candidate_generation",
            "candidate_artifact_hash": sha256(
                result["model_patch"].encode("utf-8")
            ).hexdigest(),
            "producer": {
                "model": "supervisor-model",
                "provider": "fixture",
                "runner_label": "supervisor-generator-not-baseline",
            },
            "prompt_sha256": sha256(b"supervisor-prompt").hexdigest(),
        }
        return result


def test_official_live_refuses_without_allow_live_before_generators_run(tmp_path):
    baseline = _FailingLiveGenerator()
    supervisor = _FailingLiveGenerator()
    loader_calls: list[str] = []

    def loader(**_kwargs):
        loader_calls.append("loader")
        raise AssertionError("loader should not run")

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="allow_live"):
        swebench_mergeability_official_live_runner(
            dataset="fixture-official",
            dataset_split="test",
            output_dir=tmp_path / "out",
            baseline_generator=baseline,
            supervisor_generator=supervisor,
            allow_live=False,
            max_budget_usd=1.0,
            model="claude-opus-4-8",
            provider="anthropic",
            dataset_loader=loader,
            repo_materializer=_official_materializer,
            oracle_runner=lambda _context: {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
        )

    assert loader_calls == []
    assert baseline.calls == []
    assert supervisor.calls == []


def test_official_live_refuses_without_budget_before_generators_run(tmp_path):
    baseline = _FailingLiveGenerator()
    supervisor = _FailingLiveGenerator()

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="max_budget_usd"):
        swebench_mergeability_official_live_runner(
            dataset="fixture-official",
            dataset_split="test",
            output_dir=tmp_path / "out",
            baseline_generator=baseline,
            supervisor_generator=supervisor,
            allow_live=True,
            max_budget_usd=0.0,
            model="claude-opus-4-8",
            provider="anthropic",
            dataset_loader=lambda **_kwargs: [_official_record()],
            repo_materializer=_official_materializer,
            oracle_runner=lambda _context: {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
        )

    assert baseline.calls == []
    assert supervisor.calls == []


def test_official_live_generates_matched_arms_and_reuses_official_replay(tmp_path):
    baseline = _AcceptingFakeLiveGenerator()
    supervisor = _FakeLiveGenerator()

    report = swebench_mergeability_official_live_runner(
        dataset="fixture-official",
        dataset_split="test",
        output_dir=tmp_path / "out",
        baseline_generator=baseline,
        supervisor_generator=supervisor,
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(context),
        },
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["schema_version"] == "supervisor-swebench-official-live-report/v1"
    assert report["status"] == "completed"
    assert report["official_replay_used"] is True
    assert report["live_generation_used"] is True
    assert report["live_fetch_used"] is False
    assert Path(report["generated_predictions_path"]).exists()
    assert report["official_replay_report"]["schema_version"] == (
        "supervisor-swebench-official-replay-report/v1"
    )
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["metric_applyable"] is False
    assert report["policy_mutated"] is False

    arms = report["live_generation_arms"]
    assert set(arms) == {"baseline", "supervisor"}
    assert arms["baseline"]["model"] == arms["supervisor"]["model"] == "claude-opus-4-8"
    assert arms["baseline"]["provider"] == arms["supervisor"]["provider"] == "anthropic"
    assert arms["baseline"]["budget_usd"] == arms["supervisor"]["budget_usd"] == 1.0
    assert arms["baseline"]["timeout_s"] == arms["supervisor"]["timeout_s"] == 30.0
    assert arms["baseline"]["prompt_hash"] == arms["supervisor"]["prompt_hash"]
    assert arms["baseline"]["candidate_artifact_hash"]
    assert arms["supervisor"]["candidate_artifact_hash"]
    assert report["evaluator_hash"] == report["official_replay_report"]["report_sha256"]

    rows = {
        row["candidate_id"]: row
        for row in report["bridge_report"]["per_row_results"]
    }
    baseline_row = rows["baseline-generated"]
    assert baseline_row["baseline_unavailable"] is False
    assert baseline_row["baseline_accept"] is True
    assert baseline_row["baseline_decision_source"] == "single_agent_candidate_generation"
    assert baseline_row["baseline_evidence_kind"] == "produced_single_agent_baseline"
    assert baseline_row["baseline_producer"]["runner_label"] == (
        "swebench-official-live-baseline-generator"
    )


def test_official_live_ignores_supervisor_supplied_baseline_receipts(tmp_path):
    report = swebench_mergeability_official_live_runner(
        dataset="fixture-official",
        dataset_split="test",
        output_dir=tmp_path / "out",
        baseline_generator=_AcceptingFakeLiveGenerator(),
        supervisor_generator=_BaselineReceiptSmugglingLiveGenerator(),
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(context),
        },
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    rows = {
        row["candidate_id"]: row
        for row in report["bridge_report"]["per_row_results"]
    }
    baseline_row = rows["baseline-generated"]
    assert baseline_row["baseline_unavailable"] is False
    assert baseline_row["baseline_decision_source"] == "single_agent_candidate_generation"
    supervisor_row = rows["supervisor-generated"]
    assert supervisor_row["baseline_accept"] is False
    assert supervisor_row["baseline_unavailable"] is True
    assert supervisor_row["baseline_evidence_kind"] == "missing"
    assert supervisor_row["baseline_unavailable_reason"] == (
        "baseline_decisions_not_supplied"
    )
    assert supervisor_row["baseline_producer"] == {}


def test_official_live_generator_inputs_exclude_hidden_oracle(tmp_path):
    baseline = _FakeLiveGenerator()
    supervisor = _FakeLiveGenerator()

    swebench_mergeability_official_live_runner(
        dataset="fixture-official",
        dataset_split="test",
        output_dir=tmp_path / "out",
        baseline_generator=baseline,
        supervisor_generator=supervisor,
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda _context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
        },
    )

    for generator in (baseline, supervisor):
        assert len(generator.calls) == 1
        encoded = json.dumps(generator.calls[0], sort_keys=True)
        for forbidden in (
            "SECRET GOLD PATCH",
            "SECRET TEST PATCH",
            "FAIL_TO_PASS",
            "PASS_TO_PASS",
            "final_score",
            "oracle_accept",
        ):
            assert forbidden not in encoded
        public_worktree = Path(generator.calls[0]["public_worktree_ref"])
        assert (public_worktree / "parser.py").exists()
        assert not (public_worktree / "hidden").exists()


def test_official_live_budget_overrun_is_unavailable_not_accepted(tmp_path):
    report = swebench_mergeability_official_live_runner(
        dataset="fixture-official",
        dataset_split="test",
        output_dir=tmp_path / "out",
        baseline_generator=_FakeLiveGenerator(cost_usd=1.5),
        supervisor_generator=_FakeLiveGenerator(),
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda _context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
        },
    )

    assert report["status"] == "unavailable"
    assert report["unavailable_reason"] == "budget_exceeded"
    assert "budget_exceeded" in report["gaming_flags"]
    assert report["live_generation_arms"]["baseline"]["status"] == "unavailable"
    assert report["live_generation_arms"]["baseline"]["accepted"] is False
    assert "official_replay_report" not in report
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["policy_mutated"] is False


def test_live_runner_refuses_without_allow_live_before_generators_run(tmp_path):
    baseline = _FailingLiveGenerator()
    supervisor = _FailingLiveGenerator()

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="allow_live"):
        swebench_mergeability_live_runner(
            manifest_path=_write_live_manifest(tmp_path),
            output_dir=tmp_path / "out",
            baseline_generator=baseline,
            supervisor_generator=supervisor,
            allow_live=False,
            max_budget_usd=1.0,
            model="claude-opus-4-8",
            provider="anthropic",
        )

    assert baseline.calls == []
    assert supervisor.calls == []


def test_live_runner_refuses_without_budget_before_generators_run(tmp_path):
    baseline = _FailingLiveGenerator()
    supervisor = _FailingLiveGenerator()

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="max_budget_usd"):
        swebench_mergeability_live_runner(
            manifest_path=_write_live_manifest(tmp_path),
            output_dir=tmp_path / "out",
            baseline_generator=baseline,
            supervisor_generator=supervisor,
            allow_live=True,
            max_budget_usd=0.0,
            model="claude-opus-4-8",
            provider="anthropic",
        )

    assert baseline.calls == []
    assert supervisor.calls == []


def test_live_runner_generates_matched_arms_and_reuses_replay_report(tmp_path):
    baseline = _FakeLiveGenerator()
    supervisor = _FakeLiveGenerator()

    report = swebench_mergeability_live_runner(
        manifest_path=_write_live_manifest(tmp_path),
        output_dir=tmp_path / "out",
        baseline_generator=baseline,
        supervisor_generator=supervisor,
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
        timeout_s=30.0,
    )

    assert report["schema_version"] == "supervisor-swebench-mergeability-live-report/v1"
    assert report["status"] == "completed"
    assert report["allow_live"] is True
    assert report["max_budget_usd"] == 1.0
    assert report["live_generation_used"] is True
    assert report["live_fetch_used"] is False
    assert Path(report["generated_replay_manifest_path"]).exists()
    assert Path(report["replay_report"]["report_path"]).exists()
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["bridge_report"]["improvement_claim_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["improvement_claim_allowed"] is False

    arms = report["live_generation_arms"]
    assert set(arms) == {"baseline", "supervisor"}
    assert arms["baseline"]["model"] == arms["supervisor"]["model"] == "claude-opus-4-8"
    assert arms["baseline"]["provider"] == arms["supervisor"]["provider"] == "anthropic"
    assert arms["baseline"]["budget_usd"] == arms["supervisor"]["budget_usd"] == 1.0
    assert arms["baseline"]["accepted"] is False
    assert arms["baseline"]["evaluated_by_replay"] is True
    assert arms["baseline"]["prompt_hash"] == arms["supervisor"]["prompt_hash"]
    assert arms["baseline"]["token_usage"] == {"input_tokens": 101, "output_tokens": 23}

    for generator in (baseline, supervisor):
        assert len(generator.calls) == 1
        encoded = json.dumps(generator.calls[0], sort_keys=True)
        for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "oracle_accept"):
            assert forbidden not in encoded
        public_worktree = Path(generator.calls[0]["public_worktree_ref"])
        assert (public_worktree / "parser.py").exists()
        assert not (public_worktree / "hidden").exists()
        assert not (public_worktree / ".mergeability").exists()

    frozen_path = Path(report["replay_report"]["instance_reports"][0]["frozen_decisions_path"])
    oracle_path = Path(report["replay_report"]["instance_reports"][0]["oracle_outputs_path"])
    assert frozen_path.stat().st_mtime_ns <= oracle_path.stat().st_mtime_ns


def test_live_runner_without_baseline_decision_receipts_does_not_synthesize_accept(tmp_path):
    report = swebench_mergeability_live_runner(
        manifest_path=_write_live_manifest(tmp_path),
        output_dir=tmp_path / "out",
        baseline_generator=_FakeLiveGenerator(),
        supervisor_generator=_FakeLiveGenerator(),
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
    )

    rows = report["bridge_report"]["per_row_results"]
    assert rows
    assert all(row["baseline_accept"] is False for row in rows)
    assert all(row["baseline_unavailable"] is True for row in rows)
    assert {row["baseline_evidence_kind"] for row in rows} == {"missing"}
    baseline_arm = report["bridge_report"]["arms"][ARM_BASELINE]
    assert baseline_arm["availability_status"] == "unavailable"
    assert baseline_arm["accepted_count"] == 0
    assert baseline_arm["unavailable_count"] == len(rows)
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["bridge_report"]["improvement_claim_allowed"] is False


def test_live_runner_budget_overrun_is_unavailable_not_accepted(tmp_path):
    report = swebench_mergeability_live_runner(
        manifest_path=_write_live_manifest(tmp_path),
        output_dir=tmp_path / "out",
        baseline_generator=_FakeLiveGenerator(cost_usd=1.5),
        supervisor_generator=_FakeLiveGenerator(),
        allow_live=True,
        max_budget_usd=1.0,
        model="claude-opus-4-8",
        provider="anthropic",
    )

    assert report["status"] == "unavailable"
    assert report["unavailable_reason"] == "budget_exceeded"
    assert "budget_exceeded" in report["gaming_flags"]
    assert report["live_generation_arms"]["baseline"]["status"] == "unavailable"
    assert report["live_generation_arms"]["baseline"]["accepted"] is False
    assert "replay_report" not in report
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["policy_mutated"] is False


def test_live_cli_requires_allow_live_and_budget(tmp_path):
    manifest_path = _write_live_manifest(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_swe_bench_mergeability_replay.py",
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "cli-out"),
            "--run-live",
            "--max-budget-usd",
            "1.0",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    assert "without --allow-live" in result.stderr

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_swe_bench_mergeability_replay.py",
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "cli-out"),
            "--run-live",
            "--allow-live",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    assert "without --max-budget-usd > 0" in result.stderr


def test_live_cli_approved_path_runs_command_generators(tmp_path):
    manifest_path = _write_live_manifest(tmp_path)
    generator = tmp_path / "fake_generator.py"
    generator.write_text(
        "import json, os\n"
        "input_path = os.environ['SWEBENCH_MERGEABILITY_GENERATOR_INPUT']\n"
        "output_path = os.environ['SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT']\n"
        "arm = os.environ['SWEBENCH_MERGEABILITY_GENERATOR_ARM']\n"
        "payload = json.loads(open(input_path, encoding='utf-8').read())\n"
        "assert 'FAIL_TO_PASS' not in json.dumps(payload)\n"
        "patch = '''diff --git a/parser.py b/parser.py\\n"
        "--- a/parser.py\\n"
        "+++ b/parser.py\\n"
        "@@ -1,2 +1,2 @@\\n"
        "-def parse(value):\\n"
        "+def parse(value: str) -> str:\\n"
        "     return value\\n'''\n"
        "json.dump({\n"
        "  'candidate_id': arm + '-cli-generated',\n"
        "  'model_patch': patch,\n"
        "  'cost_usd': 0.2,\n"
        "  'token_usage': {'input_tokens': 3, 'output_tokens': 2},\n"
        "}, open(output_path, 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_swe_bench_mergeability_replay.py",
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "cli-live-out"),
            "--run-live",
            "--allow-live",
            "--max-budget-usd",
            "1.0",
            "--baseline-generator-command",
            f"{sys.executable} {generator}",
            "--supervisor-generator-command",
            f"{sys.executable} {generator}",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "reported"
    assert summary["allow_live"] is True
    assert summary["live_generation_used"] is True
    report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["bridge_report"]["metric_applyable"] is False
    assert report["live_generation_arms"]["baseline"]["cost_usd"] == 0.2


# ---------------------------------------------------------------------------
# Filtered official replay smoke (CLI + filtering + label validation)
# ---------------------------------------------------------------------------


_OFFICIAL_REPLAY_FAKE_DATASET_ENV = "SWEBENCH_OFFICIAL_REPLAY_FAKE_DATASET_PATH"


def _official_adapter_receipt(
    context,
    *,
    command: list[str] | None = None,
    fail_to_pass_status: str = "pass",
    pass_to_pass_status: str = "pass",
    official_equivalent_labels: dict[str, str] | None = None,
) -> dict:
    receipt = {
        "command": command or ["fake-official-harness", str(context["instance_id"])],
        "return_code": 0,
        "stdout_sha256": sha256(b"stdout").hexdigest(),
        "stderr_sha256": sha256(b"stderr").hexdigest(),
        "evaluator_version": "swebench-harness-test",
        "harness": {
            "name": "swebench.harness.run_evaluation",
            "mode": "official-equivalent-test",
        },
        "artifact_paths": {
            "report": str(
                Path(context["frozen_decisions_path"]).parent
                / "fake-official-report.json"
            ),
        },
        "fail_to_pass_status": fail_to_pass_status,
        "pass_to_pass_status": pass_to_pass_status,
        "frozen_decisions_exists": Path(
            context["frozen_decisions_path"]
        ).exists(),
    }
    if official_equivalent_labels is not None:
        receipt["official_equivalent_labels"] = dict(official_equivalent_labels)
    return receipt


def _cli_fake_official_oracle_runner(context):
    """Importable oracle adapter used by the official-replay CLI smoke tests."""
    return {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_adapter_receipt": _official_adapter_receipt(context),
    }


def _cli_label_only_oracle_runner(_context):
    """Importable adapter with insufficient official-harness proof."""
    return {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_adapter_receipt": {
            "command": ["not-actually-official"],
            "return_code": 0,
        },
    }


def _cli_fake_official_dataset_loader(*, dataset, split):
    """Importable dataset loader: reads JSONL of records from an env var."""
    import os as _os
    path = _os.environ.get(_OFFICIAL_REPLAY_FAKE_DATASET_ENV)
    if not path:
        raise RuntimeError(
            f"{_OFFICIAL_REPLAY_FAKE_DATASET_ENV} not set for fake dataset loader"
        )
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _cli_fake_official_repo_materializer(*, record, output_dir):
    """Importable repo materializer that creates a small public bundle."""
    return _official_materializer(record=record, output_dir=output_dir)


def _write_official_dataset_jsonl(
    tmp_path: Path,
    records: list[dict],
    *,
    name: str = "official_records.jsonl",
) -> Path:
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def _write_multi_official_predictions(
    tmp_path: Path,
    instance_ids: list[str],
    *,
    name: str = "predictions.jsonl",
) -> Path:
    lines = []
    for instance_id in instance_ids:
        prediction = {
            "instance_id": instance_id,
            "candidate_id": f"{instance_id}-cand",
            "model_patch": _valid_model_patch(),
            "single_agent_baseline_decision": _produced_baseline_decision(
                f"{instance_id}-cand",
                patch=_valid_model_patch(),
                accept=True,
            ),
        }
        lines.append(json.dumps(prediction))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_official_replay_cli(
    args: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    import os as _os
    repo_root = Path(__file__).resolve().parents[1]
    env = _os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}{_os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(repo_root)
    )
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "scripts/run_swe_bench_mergeability_replay.py", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
    )


def test_official_replay_cli_requires_oracle_adapter_before_metrics(tmp_path):
    result = _run_official_replay_cli([
        "--official-replay",
        "--allow-dataset-fetch",
        "--dataset",
        "SWE-bench/SWE-bench_Verified",
        "--dataset-split",
        "test",
        "--predictions",
        str(tmp_path / "missing.jsonl"),
        "--output-dir",
        str(tmp_path / "out"),
    ])

    assert result.returncode == 2, result.stderr
    assert "without --oracle-adapter" in result.stderr
    out_dir = tmp_path / "out"
    if out_dir.exists():
        assert not (out_dir / "official_replay_report.json").exists()


def test_aeb0_missing_cli_prerequisites_write_blocked_artifact(tmp_path):
    output_dir = tmp_path / "out"
    result = _run_official_replay_cli([
        "--official-all-arms-diagnostic",
        "--dataset",
        "SWE-bench/SWE-bench_Pro",
        "--dataset-split",
        "test",
        "--predictions",
        str(tmp_path / "predictions.jsonl"),
        "--output-dir",
        str(output_dir),
        "--oracle-adapter",
        "tests.test_swe_bench_pro_mergeability_bridge:_cli_fake_official_oracle_runner",
    ])

    assert result.returncode == 2
    assert "without --allow-dataset-fetch" in result.stderr
    report_path = output_dir / "official_all_arms_diagnostic_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "unavailable"
    assert report["aeb0_artifact_gate"]["status"] == "blocked"
    assert report["aeb0_artifact_gate"]["blocked_reasons"] == [
        "missing_cli_prerequisite:allow_dataset_fetch"
    ]
    assert report["metrics_unavailable_reasons"] == [
        "missing_cli_prerequisite:allow_dataset_fetch"
    ]
    _assert_diagnostic_report_only(report)


def test_official_replay_cli_rejects_unknown_adapter_kind_before_metrics(tmp_path):
    output_dir = tmp_path / "out"
    fake_module = "tests.test_swe_bench_pro_mergeability_bridge"

    result = _run_official_replay_cli([
        "--official-replay",
        "--allow-dataset-fetch",
        "--dataset",
        "fixture-official",
        "--dataset-split",
        "test",
        "--predictions",
        str(tmp_path / "predictions.jsonl"),
        "--output-dir",
        str(output_dir),
        "--oracle-adapter",
        f"{fake_module}:_cli_fake_official_oracle_runner",
        "--oracle-adapter-kind",
        "deterministic_test_adapter",
    ])

    assert result.returncode == 2, result.stderr
    assert "unsupported official SWE-bench oracle adapter kind" in result.stderr
    assert "official_docker_or_equivalent" in result.stderr
    assert "official_equivalent" in result.stderr
    assert not (output_dir / "official_replay_report.json").exists()


def test_official_replay_runner_rejects_unknown_adapter_kind_before_metrics(tmp_path):
    with pytest.raises(
        SwebenchMergeabilityFixtureRunnerError,
        match="unsupported official SWE-bench oracle adapter kind",
    ):
        swebench_mergeability_official_replay_runner(
            dataset="fixture-official",
            dataset_split="test",
            predictions_path=tmp_path / "missing.jsonl",
            output_dir=tmp_path / "out",
            dataset_loader=lambda **_kwargs: [_official_record()],
            repo_materializer=_official_materializer,
            oracle_runner=lambda _context: {
                "fail_to_pass_status": "pass",
                "pass_to_pass_status": "pass",
            },
            oracle_adapter_kind="deterministic_test_adapter",
        )

    assert not (tmp_path / "out" / "official_replay_report.json").exists()


def test_official_replay_cli_passes_fake_runner_and_writes_report(tmp_path):
    records = [_official_record("official_demo__repo-A01")]
    dataset_path = _write_official_dataset_jsonl(tmp_path, records)
    predictions_path = _write_multi_official_predictions(
        tmp_path, ["official_demo__repo-A01"]
    )
    output_dir = tmp_path / "out"

    fake_module = "tests.test_swe_bench_pro_mergeability_bridge"
    result = _run_official_replay_cli(
        [
            "--official-replay",
            "--allow-dataset-fetch",
            "--dataset",
            "fixture-official",
            "--dataset-split",
            "test",
            "--predictions",
            str(predictions_path),
            "--output-dir",
            str(output_dir),
            "--oracle-adapter",
            f"{fake_module}:_cli_fake_official_oracle_runner",
            "--oracle-adapter-kind",
            "official_docker_or_equivalent",
            "--dataset-loader",
            f"{fake_module}:_cli_fake_official_dataset_loader",
            "--repo-materializer",
            f"{fake_module}:_cli_fake_official_repo_materializer",
        ],
        env_overrides={_OFFICIAL_REPLAY_FAKE_DATASET_ENV: str(dataset_path)},
    )

    assert result.returncode == 0, result.stderr
    report_path = output_dir / "official_replay_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["official_replay_used"] is True
    assert report["oracle_adapter_kind"] == "official_docker_or_equivalent"
    instance_report = report["replay_report"]["instance_reports"][0]
    oracle_payload = json.loads(
        Path(instance_report["oracle_outputs_path"]).read_text(encoding="utf-8")
    )
    receipt = next(
        r for r in oracle_payload["rows"][0]["oracle_command_receipts"]
        if r["name"] == "official_oracle_adapter"
    )
    assert receipt["receipt"]["return_code"] == 0
    assert receipt["receipt"]["frozen_decisions_exists"] is True
    assert report["oracle_receipt_validation"]["validated"] is True
    assert report["bridge_report"]["metric_applyable"] is False


def test_official_all_arms_diagnostic_cli_writes_unavailable_report_without_panel(tmp_path):
    records = [_official_record("official_demo__repo-A01")]
    dataset_path = _write_official_dataset_jsonl(tmp_path, records)
    predictions_path = _write_multi_official_predictions(
        tmp_path, ["official_demo__repo-A01"]
    )
    output_dir = tmp_path / "out"

    fake_module = "tests.test_swe_bench_pro_mergeability_bridge"
    result = _run_official_replay_cli(
        [
            "--official-all-arms-diagnostic",
            "--allow-dataset-fetch",
            "--dataset",
            "fixture-official",
            "--dataset-split",
            "test",
            "--predictions",
            str(predictions_path),
            "--output-dir",
            str(output_dir),
            "--oracle-adapter",
            f"{fake_module}:_cli_fake_official_oracle_runner",
            "--oracle-adapter-kind",
            "official_docker_or_equivalent",
            "--dataset-loader",
            f"{fake_module}:_cli_fake_official_dataset_loader",
            "--repo-materializer",
            f"{fake_module}:_cli_fake_official_repo_materializer",
        ],
        env_overrides={_OFFICIAL_REPLAY_FAKE_DATASET_ENV: str(dataset_path)},
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "unavailable"
    assert summary["metric_applyable"] is False
    assert summary["diagnostic_ready_for_scale"] is False
    assert summary["all_arms_populated"] is False
    assert summary["s_full_available"] is False
    assert summary["matched_true_accept_status"][ARM_S_FULL] == "unavailable"
    report_path = output_dir / "official_all_arms_diagnostic_report.json"
    assert Path(summary["report"]) == report_path
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "unavailable"
    assert report["s_full_available"] is False
    assert "s_full_unavailable" in report["metrics_unavailable_reasons"]
    _assert_diagnostic_report_only(report)


def test_official_replay_label_only_adapter_receipt_is_unavailable(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    def label_only_adapter(_context):
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": {
                "command": ["not-actually-official"],
                "return_code": 0,
            },
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=label_only_adapter,
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["status"] == "unavailable"
    validation = report["oracle_receipt_validation"]
    assert validation["required"] is True
    assert validation["validated"] is False
    reasons = {mismatch["reason"] for mismatch in validation["mismatches"]}
    assert "missing_official_harness_metadata" in reasons
    assert "missing_required_receipt_field" in reasons
    assert "receipt_status_mismatch" in reasons
    assert report["metrics_unavailable_reasons"] == [
        "oracle_receipt_validation_failed"
    ]
    assert report["bridge_report"]["status"] == "unavailable"
    assert report["bridge_report"]["metrics_suppressed"] is True
    assert "far_tar_frr" not in report["bridge_report"]
    assert report["replay_report"]["status"] == "unavailable"
    assert report["replay_report"]["metrics_suppressed"] is True
    assert "far_tar_frr" not in report["replay_report"]["bridge_report"]
    instance_report = report["replay_report"]["instance_reports"][0]
    assert instance_report["metrics_suppressed"] is True
    assert "far_tar_frr" not in instance_report["bridge_report"]
    replay_report_path = tmp_path / "out" / "replay" / "report.json"
    instance_report_path = (
        tmp_path
        / "out"
        / "replay"
        / "instances"
        / "official_demo__repo-001"
        / "report.json"
    )
    replay_report_on_disk = json.loads(
        replay_report_path.read_text(encoding="utf-8")
    )
    instance_report_on_disk = json.loads(
        instance_report_path.read_text(encoding="utf-8")
    )
    assert replay_report_on_disk["status"] == "unavailable"
    assert instance_report_on_disk["status"] == "unavailable"
    assert "far_tar_frr" not in json.dumps(replay_report_on_disk)
    assert "far_tar_frr" not in json.dumps(instance_report_on_disk)
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_official_replay_cli_suppresses_metrics_when_oracle_proof_invalid(tmp_path):
    records = [_official_record("official_demo__repo-A01")]
    dataset_path = _write_official_dataset_jsonl(tmp_path, records)
    predictions_path = _write_multi_official_predictions(
        tmp_path, ["official_demo__repo-A01"]
    )
    output_dir = tmp_path / "out"

    fake_module = "tests.test_swe_bench_pro_mergeability_bridge"
    result = _run_official_replay_cli(
        [
            "--official-replay",
            "--allow-dataset-fetch",
            "--dataset",
            "fixture-official",
            "--dataset-split",
            "test",
            "--predictions",
            str(predictions_path),
            "--output-dir",
            str(output_dir),
            "--oracle-adapter",
            f"{fake_module}:_cli_label_only_oracle_runner",
            "--oracle-adapter-kind",
            "official_docker_or_equivalent",
            "--dataset-loader",
            f"{fake_module}:_cli_fake_official_dataset_loader",
            "--repo-materializer",
            f"{fake_module}:_cli_fake_official_repo_materializer",
        ],
        env_overrides={_OFFICIAL_REPLAY_FAKE_DATASET_ENV: str(dataset_path)},
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "unavailable"
    assert summary["far_tar_frr"] is None
    assert summary["metrics_suppressed"] is True
    assert summary["metrics_unavailable_reasons"] == [
        "oracle_receipt_validation_failed"
    ]

    report = json.loads(
        (output_dir / "official_replay_report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "unavailable"
    assert report["bridge_report"]["metrics_suppressed"] is True
    assert "far_tar_frr" not in report["bridge_report"]
    replay_report_on_disk = json.loads(
        (output_dir / "replay" / "report.json").read_text(encoding="utf-8")
    )
    instance_report_on_disk = json.loads(
        (
            output_dir
            / "replay"
            / "instances"
            / "official_demo__repo-A01"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert "far_tar_frr" not in json.dumps(replay_report_on_disk)
    assert "far_tar_frr" not in json.dumps(instance_report_on_disk)


def test_instance_id_filtering_happens_before_prediction_coverage(tmp_path):
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
    ]
    dataset_path = _write_official_dataset_jsonl(tmp_path, records)
    predictions_path = _write_multi_official_predictions(
        tmp_path, ["official_demo__repo-A01"]
    )
    output_dir = tmp_path / "out"

    fake_module = "tests.test_swe_bench_pro_mergeability_bridge"
    result = _run_official_replay_cli(
        [
            "--official-replay",
            "--allow-dataset-fetch",
            "--dataset",
            "fixture-official",
            "--dataset-split",
            "test",
            "--predictions",
            str(predictions_path),
            "--output-dir",
            str(output_dir),
            "--oracle-adapter",
            f"{fake_module}:_cli_fake_official_oracle_runner",
            "--dataset-loader",
            f"{fake_module}:_cli_fake_official_dataset_loader",
            "--repo-materializer",
            f"{fake_module}:_cli_fake_official_repo_materializer",
            "--instance-id",
            "official_demo__repo-A01",
        ],
        env_overrides={_OFFICIAL_REPLAY_FAKE_DATASET_ENV: str(dataset_path)},
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(
        (output_dir / "official_replay_report.json").read_text(encoding="utf-8")
    )
    filt = report["selection_filter"]
    assert filt["instance_ids_requested"] == ["official_demo__repo-A01"]
    assert filt["selected_instance_ids"] == ["official_demo__repo-A01"]
    assert report["instance_count"] == 1
    assert report["status"] == "completed"


def test_limit_filtering_is_deterministic_and_reported(tmp_path):
    records = [
        _official_record("official_demo__repo-C03"),
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
    ]
    dataset_path = _write_official_dataset_jsonl(tmp_path, records)
    predictions_path = _write_multi_official_predictions(
        tmp_path,
        [
            "official_demo__repo-A01",
            "official_demo__repo-B02",
            "official_demo__repo-C03",
        ],
    )

    fake_module = "tests.test_swe_bench_pro_mergeability_bridge"

    def _run(output_dir: Path) -> dict:
        result = _run_official_replay_cli(
            [
                "--official-replay",
                "--allow-dataset-fetch",
                "--dataset",
                "fixture-official",
                "--dataset-split",
                "test",
                "--predictions",
                str(predictions_path),
                "--output-dir",
                str(output_dir),
                "--oracle-adapter",
                f"{fake_module}:_cli_fake_official_oracle_runner",
                "--dataset-loader",
                f"{fake_module}:_cli_fake_official_dataset_loader",
                "--repo-materializer",
                f"{fake_module}:_cli_fake_official_repo_materializer",
                "--limit",
                "2",
            ],
            env_overrides={_OFFICIAL_REPLAY_FAKE_DATASET_ENV: str(dataset_path)},
        )
        assert result.returncode == 0, result.stderr
        return json.loads(
            (output_dir / "official_replay_report.json").read_text(encoding="utf-8")
        )

    report_a = _run(tmp_path / "out-a")
    report_b = _run(tmp_path / "out-b")

    assert (
        report_a["selection_filter"]["selected_instance_ids"]
        == report_b["selection_filter"]["selected_instance_ids"]
        == ["official_demo__repo-A01", "official_demo__repo-B02"]
    )
    assert report_a["selection_filter"]["limit_requested"] == 2
    assert report_a["instance_count"] == 2


def test_oracle_receipts_are_after_frozen_decisions_and_hide_oracle_fields(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)
    oracle_calls: list[dict] = []

    def oracle(context):
        oracle_calls.append({
            "frozen_exists": Path(context["frozen_decisions_path"]).exists(),
        })
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(
                context,
                command=["fake-oracle"],
            ),
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=oracle,
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert oracle_calls == [{"frozen_exists": True}]
    instance_report = report["replay_report"]["instance_reports"][0]
    frozen_path = Path(instance_report["frozen_decisions_path"])
    oracle_path = Path(instance_report["oracle_outputs_path"])
    assert frozen_path.exists()
    assert oracle_path.exists()
    assert frozen_path.stat().st_mtime_ns <= oracle_path.stat().st_mtime_ns

    leak_check = report["hidden_field_leak_check"]
    assert leak_check["ok"] is True
    assert leak_check["refs"] == []

    frozen_text = frozen_path.read_text(encoding="utf-8")
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "SECRET"):
        assert forbidden not in frozen_text
    reviewer_packet_path = Path(
        instance_report["reviewer_packets"][0]["reviewer_packet_path"]
    )
    reviewer_text = reviewer_packet_path.read_text(encoding="utf-8")
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "SECRET"):
        assert forbidden not in reviewer_text


def test_official_equivalent_label_validation_failure_is_unavailable(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    def mismatched_adapter(_context):
        # adapter reports "pass" but its official-equivalent labels say "fail"
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(
                _context,
                command=["fake-official-equivalent"],
                official_equivalent_labels={
                    "fail_to_pass_status": "fail",
                    "pass_to_pass_status": "pass",
                },
            ),
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=mismatched_adapter,
        oracle_adapter_kind="official_equivalent",
    )

    assert report["status"] == "unavailable"
    validation = report["label_validation"]
    assert validation["required"] is True
    assert validation["validated"] is False
    assert validation["mismatches"]
    first = validation["mismatches"][0]
    assert first["reason"] == "label_mismatch"
    assert first["field"] == "fail_to_pass_status"
    assert report["metrics_unavailable_reasons"] == ["label_validation_failed"]
    assert report["bridge_report"]["status"] == "unavailable"
    assert report["bridge_report"]["metrics_suppressed"] is True
    assert "far_tar_frr" not in report["bridge_report"]
    assert report["replay_report"]["status"] == "unavailable"
    assert report["replay_report"]["metrics_suppressed"] is True
    assert "far_tar_frr" not in report["replay_report"]["bridge_report"]
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False


def test_official_replay_oracle_unavailable_suppresses_metrics(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    def unavailable_adapter(context):
        return {
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
            "oracle_unavailable": True,
            "oracle_unavailable_reason": "official_harness_failed",
            "oracle_adapter_receipt": {
                **_official_adapter_receipt(
                    context,
                    fail_to_pass_status="unavailable",
                    pass_to_pass_status="unavailable",
                ),
                "return_code": 1,
                "oracle_unavailable": True,
                "unavailable_reason": "official_harness_failed",
            },
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=unavailable_adapter,
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["status"] == "unavailable"
    assert report["oracle_receipt_validation"]["validated"] is True
    assert report["metrics_unavailable_reasons"] == ["oracle_unavailable"]
    assert report["bridge_report"]["metrics_suppressed"] is True
    assert "far_tar_frr" not in report["bridge_report"]
    oracle_path = (
        tmp_path
        / "out"
        / "replay"
        / "instances"
        / "official_demo__repo-001"
        / "oracle_outputs.json"
    )
    oracle_row = json.loads(oracle_path.read_text(encoding="utf-8"))["rows"][0]
    assert oracle_row["fail_to_pass_status"] == "unavailable"
    assert oracle_row["pass_to_pass_status"] == "unavailable"
    assert oracle_row["oracle_unavailable"] is True
    assert oracle_row["oracle_unavailable_reason"] == "official_harness_failed"


def test_official_oracle_receipt_unavailable_requires_reason(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    def unavailable_adapter_without_receipt_reason(context):
        return {
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
            "oracle_unavailable": True,
            "oracle_unavailable_reason": "official_harness_failed",
            "oracle_adapter_receipt": _official_adapter_receipt(
                context,
                fail_to_pass_status="unavailable",
                pass_to_pass_status="unavailable",
            ),
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=unavailable_adapter_without_receipt_reason,
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["status"] == "unavailable"
    assert report["metrics_unavailable_reasons"] == [
        "oracle_unavailable",
        "oracle_receipt_validation_failed",
    ]
    validation = report["oracle_receipt_validation"]
    assert validation["validated"] is False
    assert {
        (mismatch.get("field"), mismatch.get("reason"))
        for mismatch in validation["mismatches"]
    } >= {("unavailable_reason", "missing_required_receipt_field")}


def _smoke_oracle_runner(context):
    return {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_adapter_receipt": _official_adapter_receipt(context),
    }


def _official_oracle_for_good_ids(good_ids: set[str]):
    def oracle(context):
        fail_to_pass = "pass" if str(context["instance_id"]) in good_ids else "fail"
        return {
            "fail_to_pass_status": fail_to_pass,
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(
                context,
                fail_to_pass_status=fail_to_pass,
                pass_to_pass_status="pass",
            ),
        }

    return oracle


def _full_roster_panel_for_bad_ids(bad_candidate_fragments: set[str]):
    def panel(packet):
        candidate_id = str(packet["candidate_id"])
        decision = (
            "revise"
            if any(fragment in candidate_id for fragment in bad_candidate_fragments)
            else "accept"
        )
        return {
            "decision": decision,
            "available": True,
            "reason": "reviewer_non_accept" if decision == "revise" else "all_available_reviewers_accept",
            "reviewer_ids": ["cursor-rigorous", "codex-reviewer"],
            "available_reviewers": ["cursor-rigorous", "codex-reviewer"],
            "missing_reviewers": [],
            "reviewer_results": [
                {
                    "reviewer_id": "cursor-rigorous",
                    "runtime": "cursor_sdk",
                    "model": "rigorous",
                    "verdict_present": True,
                    "decision": decision,
                    "severity": "important" if decision == "revise" else "low",
                    "transcript_sha256": "a" * 64,
                    "output_sha256": "b" * 64,
                },
                {
                    "reviewer_id": "codex-reviewer",
                    "runtime": "codex_cli",
                    "model": "gpt-5.5",
                    "verdict_present": True,
                    "decision": "accept",
                    "severity": "low",
                    "transcript_sha256": "c" * 64,
                    "output_sha256": "d" * 64,
                },
            ],
        }

    return panel


def _assert_diagnostic_report_only(report: dict) -> None:
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["powered_improvement_claim_allowed"] is False
    assert report["human_mergeability_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False


def _write_official_predictions_without_baseline(
    tmp_path: Path,
    instance_ids: list[str],
    *,
    name: str = "predictions-no-baseline.jsonl",
) -> Path:
    lines = [
        json.dumps({
            "instance_id": instance_id,
            "candidate_id": f"{instance_id}-cand",
            "model_patch": _valid_model_patch(),
        })
        for instance_id in instance_ids
    ]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_official_predictions_with_hidden_candidate_id(
    tmp_path: Path,
    *,
    instance_id: str = "official_demo__repo-001",
    candidate_id: str = "official-cand-FAIL_TO_PASS",
) -> Path:
    prediction = {
        "instance_id": instance_id,
        "candidate_id": candidate_id,
        "model_patch": _valid_model_patch(),
        "single_agent_baseline_decision": _produced_baseline_decision(
            candidate_id,
            patch=_valid_model_patch(),
            accept=True,
        ),
    }
    path = tmp_path / "predictions-hidden-candidate.jsonl"
    path.write_text(json.dumps(prediction) + "\n", encoding="utf-8")
    return path


def test_official_replay_smoke_writes_report_with_selected_instances(tmp_path):
    """P1+P2: tiny replay writes manifest+report for the selected rows only."""
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
        _official_record("official_demo__repo-C03"),
    ]
    predictions_path = _write_multi_official_predictions(
        tmp_path,
        ["official_demo__repo-A01", "official_demo__repo-B02"],
    )
    out_dir = tmp_path / "out"

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=out_dir,
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-A01", "official_demo__repo-B02"],
    )

    report_path = out_dir / "official_replay_report.json"
    manifest_path = out_dir / "official_replay_manifest.json"
    assert report_path.exists()
    assert manifest_path.exists()
    assert report["instance_count"] == 2
    assert report["candidate_count"] >= 2
    selection = report["selection_filter"]
    assert selection["instance_ids_requested"] == [
        "official_demo__repo-A01",
        "official_demo__repo-B02",
    ]
    assert selection["selected_instance_ids"] == [
        "official_demo__repo-A01",
        "official_demo__repo-B02",
    ]
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["instance_count"] == 2
    assert persisted["selection_filter"]["selected_instance_ids"] == [
        "official_demo__repo-A01",
        "official_demo__repo-B02",
    ]


def test_official_replay_bounds_long_pro_patch_artifact_filenames(tmp_path):
    """P1: Pro-shaped ids must not produce path components that exceed FS limits."""
    instance_id = "instance_" + ("prorepo-" * 18)
    candidate_id = "candidate_" + ("gold-patch-" * 18)
    records = [_official_record(instance_id)]
    predictions_path = _write_official_predictions(
        tmp_path,
        instance_id=instance_id,
        candidate_id=candidate_id,
    )

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=[instance_id],
    )

    manifest = json.loads(
        Path(report["generated_replay_manifest_path"]).read_text(encoding="utf-8")
    )
    patch_path = Path(manifest["instances"][0]["candidates"][0]["model_patch_path"])
    assert patch_path.exists()
    assert len(patch_path.name.encode("utf-8")) <= 180
    assert patch_path.name.endswith(".patch")


def test_official_replay_normalizes_json_encoded_test_lists(tmp_path):
    """P2: real dataset JSON-string test lists are not persisted as char arrays."""
    record = _official_record()
    record["FAIL_TO_PASS"] = json.dumps(["tests/test_parser.py::test_hidden"])
    record["PASS_TO_PASS"] = json.dumps(["tests/test_parser.py::test_existing"])
    predictions_path = _write_official_predictions(tmp_path)

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [record],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    manifest = json.loads(
        Path(report["generated_replay_manifest_path"]).read_text(encoding="utf-8")
    )
    instance = manifest["instances"][0]
    assert instance["FAIL_TO_PASS"] == ["tests/test_parser.py::test_hidden"]
    assert instance["PASS_TO_PASS"] == ["tests/test_parser.py::test_existing"]
    candidate = instance["candidates"][0]
    assert candidate["FAIL_TO_PASS"] == ["tests/test_parser.py::test_hidden"]
    assert candidate["PASS_TO_PASS"] == ["tests/test_parser.py::test_existing"]
    assert "[" not in candidate["FAIL_TO_PASS"]


def test_official_replay_smoke_records_frozen_before_oracle_receipts(tmp_path):
    """P3: frozen decisions exist before any oracle call and receipts carry hashes."""
    predictions_path = _write_official_predictions(tmp_path)
    observed: list[dict] = []

    def oracle(context):
        observed.append({
            "frozen_exists": Path(context["frozen_decisions_path"]).exists(),
            "frozen_mtime_ns": Path(context["frozen_decisions_path"]).stat().st_mtime_ns,
        })
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(context),
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=oracle,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    assert observed and observed[0]["frozen_exists"] is True
    instance_report = report["replay_report"]["instance_reports"][0]
    frozen_path = Path(instance_report["frozen_decisions_path"])
    oracle_path = Path(instance_report["oracle_outputs_path"])
    assert frozen_path.stat().st_mtime_ns <= oracle_path.stat().st_mtime_ns
    oracle_payload = json.loads(oracle_path.read_text(encoding="utf-8"))
    row = oracle_payload["rows"][0]
    receipts = row.get("oracle_command_receipts") or []
    assert receipts, "oracle_command_receipts must be non-empty"
    adapter_receipt = next(
        (r["receipt"] for r in receipts if r.get("name") == "official_oracle_adapter"),
        None,
    )
    assert isinstance(adapter_receipt, dict)
    assert adapter_receipt["stdout_sha256"]
    assert adapter_receipt["stderr_sha256"]
    assert adapter_receipt["command"]
    assert report["oracle_receipt_validation"]["validated"] is True


def test_official_replay_oracle_context_includes_model_patch_after_freeze(tmp_path):
    """P3: official oracle adapters receive candidate patch text only after freeze."""
    predictions_path = _write_official_predictions(tmp_path)
    observed: dict[str, str] = {}

    def oracle(context):
        observed.update({
            "model_patch": context["model_patch"],
            "model_patch_sha256": context["model_patch_sha256"],
            "model_patch_ref": context["model_patch_ref"],
            "frozen_decisions_path": context["frozen_decisions_path"],
        })
        return {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
            "oracle_adapter_receipt": _official_adapter_receipt(context),
        }

    swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=oracle,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    assert observed["model_patch"] == _valid_model_patch()
    assert observed["model_patch_sha256"] == sha256(
        _valid_model_patch().encode("utf-8")
    ).hexdigest()
    assert observed["model_patch_ref"]
    assert Path(observed["frozen_decisions_path"]).exists()


def test_official_harness_oracle_adapter_invokes_swebench_and_parses_report(
    tmp_path, monkeypatch
):
    """P3: production adapter invokes the SWE-bench harness and parses statuses."""
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_DATASET", "SWE-bench/SWE-bench_Verified")
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_SPLIT", "test")
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        calls.append(list(command))
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        report_dir = (
            Path(cwd)
            / "logs"
            / "run_evaluation"
            / run_id
            / "supervisor-replay"
            / instance_id
        )
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(
            json.dumps({
                instance_id: {
                    "resolved": True,
                    "tests_status": {
                        "FAIL_TO_PASS": {"success": ["test_fixed"], "failure": []},
                        "PASS_TO_PASS": {"success": ["test_existing"], "failure": []},
                    },
                }
            }),
            encoding="utf-8",
        )
        (Path(cwd) / f"supervisor-replay.{run_id}.json").write_text(
            json.dumps({"resolved_ids": [instance_id]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "official stdout", "")

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "gold-smoke",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert calls
    command = calls[0]
    assert command[:3] == [sys.executable, "-m", "swebench.harness.run_evaluation"]
    assert result["fail_to_pass_status"] == "pass"
    assert result["pass_to_pass_status"] == "pass"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["command"] == command
    assert receipt["return_code"] == 0
    assert receipt["harness"]["name"] == "swebench.harness.run_evaluation"
    assert Path(receipt["artifact_paths"]["instance_report"]).exists()


def test_official_harness_oracle_bounds_long_run_id(tmp_path, monkeypatch):
    """P3: Pro-shaped ids must not create overlong SWE-bench run ids."""
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")
    observed: dict[str, str] = {}

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        observed["run_id"] = run_id
        report_dir = (
            Path(cwd)
            / "logs"
            / "run_evaluation"
            / run_id
            / "supervisor-replay"
            / instance_id
        )
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(
            json.dumps({
                instance_id: {
                    "resolved": True,
                    "tests_status": {
                        "FAIL_TO_PASS": {"success": ["test_fixed"], "failure": []},
                        "PASS_TO_PASS": {"success": ["test_existing"], "failure": []},
                    },
                }
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "official stdout", "")

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "instance_" + ("prorepo-" * 18),
        "candidate_id": "candidate_" + ("gold-patch-" * 18),
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert result["fail_to_pass_status"] == "pass"
    assert len(observed["run_id"].encode("utf-8")) <= 180


def test_official_oracle_empty_present_buckets_can_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        report_dir = (
            Path(cwd)
            / "logs"
            / "run_evaluation"
            / run_id
            / "supervisor-replay"
            / instance_id
        )
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(
            json.dumps({
                instance_id: {
                    "resolved": True,
                    "tests_status": {
                        "FAIL_TO_PASS": {},
                        "PASS_TO_PASS": {"success": [], "failure": []},
                    },
                }
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "official stdout", "")

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "candidate-under-test",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert result["fail_to_pass_status"] == "pass"
    assert result["pass_to_pass_status"] == "pass"
    assert "oracle_unavailable" not in result


def test_official_oracle_missing_or_malformed_status_bucket_is_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        report_dir = (
            Path(cwd)
            / "logs"
            / "run_evaluation"
            / run_id
            / "supervisor-replay"
            / instance_id
        )
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(
            json.dumps({
                instance_id: {
                    "resolved": True,
                    "tests_status": {
                        "FAIL_TO_PASS": {"success": [], "failure": []},
                        "PASS_TO_PASS": ["malformed"],
                    },
                }
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "official stdout", "")

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "candidate-under-test",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == (
        "official_report_status_bucket_unavailable:PASS_TO_PASS_bucket_malformed"
    )
    receipt = result["oracle_adapter_receipt"]
    assert receipt["fail_to_pass_status"] == "unavailable"
    assert receipt["pass_to_pass_status"] == "unavailable"
    assert receipt["unavailable_reason"] == result["oracle_unavailable_reason"]


def test_official_harness_oracle_nonzero_return_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(
            command,
            1,
            "official stdout",
            "docker daemon unavailable",
        )

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "candidate-under-test",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "official_harness_failed"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["return_code"] == 1
    assert receipt["oracle_unavailable"] is True
    assert receipt["unavailable_reason"] == "official_harness_failed"
    assert receipt["fail_to_pass_status"] == "unavailable"
    assert receipt["pass_to_pass_status"] == "unavailable"


def test_official_harness_oracle_timeout_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=timeout,
            output="partial official stdout",
            stderr="timed out waiting for official harness",
        )

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "candidate-under-test",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
        "timeout_s": 1,
    })

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "official_oracle_timeout"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["return_code"] == 124
    assert receipt["unavailable_reason"] == "official_oracle_timeout"
    assert receipt["artifact_paths"]["predictions"].endswith("predictions.json")


def test_official_harness_oracle_missing_report_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(command, 0, "official stdout", "")

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "candidate-under-test",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "official_instance_report_missing"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["return_code"] == 0
    assert receipt["unavailable_reason"] == "official_instance_report_missing"
    assert receipt["artifact_paths"]["predictions"].endswith("predictions.json")


def test_official_harness_oracle_valid_failure_report_stays_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    monkeypatch.setenv("SWEBENCH_OFFICIAL_ORACLE_RUN_ID_PREFIX", "test-oracle")

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        report_dir = (
            Path(cwd)
            / "logs"
            / "run_evaluation"
            / run_id
            / "supervisor-replay"
            / instance_id
        )
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(
            json.dumps({
                instance_id: {
                    "resolved": False,
                    "tests_status": {
                        "FAIL_TO_PASS": {"success": [], "failure": ["test_fixed"]},
                        "PASS_TO_PASS": {"success": ["test_existing"], "failure": []},
                    },
                }
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "official stdout", "")

    monkeypatch.setattr(
        "supervisor.swe_bench_official_oracle.subprocess.run",
        fake_run,
    )

    result = run_official_harness_oracle({
        "instance_id": "sympy__sympy-14711",
        "candidate_id": "candidate-under-test",
        "model_patch": _valid_model_patch(),
        "model_patch_sha256": sha256(_valid_model_patch().encode("utf-8")).hexdigest(),
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "abc123",
    })

    assert result["fail_to_pass_status"] == "fail"
    assert result["pass_to_pass_status"] == "pass"
    assert "oracle_unavailable" not in result


def test_verified_smoke_is_labeled_plumbing_only(tmp_path):
    """P4: report carries plumbing_smoke_only and refuses powered/human claims."""
    predictions_path = _write_official_predictions(tmp_path)
    report = swebench_mergeability_official_replay_runner(
        dataset="SWE-bench/SWE-bench_Verified",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    assert report["plumbing_smoke_only"] is True
    assert report["powered_improvement_claim_allowed"] is False
    assert report["human_mergeability_claim_allowed"] is False
    caveats = report["smoke_caveats"]
    assert any("test_pass_proxy" in c for c in caveats)
    assert any("contamination" in c for c in caveats)
    persisted = json.loads(
        (tmp_path / "out" / "official_replay_report.json").read_text(encoding="utf-8")
    )
    assert persisted["plumbing_smoke_only"] is True
    assert persisted["powered_improvement_claim_allowed"] is False
    assert persisted["human_mergeability_claim_allowed"] is False


def test_full_panel_metric_unavailable_without_full_roster(tmp_path):
    """P5: with no reviewer panel, S_full panel metric is unavailable, not imputed."""
    predictions_path = _write_official_predictions(tmp_path)
    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
        reviewer_panel=None,
        reviewer_panel_mode="custom",
    )

    instance_report = report["replay_report"]["instance_reports"][0]
    frozen_path = Path(instance_report["frozen_decisions_path"])
    frozen_payload = json.loads(frozen_path.read_text(encoding="utf-8"))
    rows = frozen_payload["rows"]
    assert rows
    for row in rows:
        assert row["s_full_unavailable"] is True
        assert row["s_full_accept"] is False
        assert row["s_full_reason"] == REVIEWER_PANEL_UNAVAILABLE_REASON
    bridge = report["bridge_report"]
    per_row = bridge.get("per_row_results") or []
    assert per_row
    for entry in per_row:
        assert entry.get("s_full_unavailable") is True
        assert entry.get("s_full_accept") is False
    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False


def test_official_replay_smoke_emits_no_policy_proposal(tmp_path):
    """P6: policy-evolution derivation produces zero proposals from the smoke."""
    predictions_path = _write_official_predictions(tmp_path)
    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        instance_ids=["official_demo__repo-001"],
    )

    assert report["metric_applyable"] is False
    assert report["improvement_claim_allowed"] is False
    assert report["default_change_allowed"] is False
    assert report["policy_mutated"] is False
    assert report["gate_advanced"] is False

    proposals = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("mergeability",),
    )
    assert proposals == []


def test_official_all_arms_diagnostic_completes_with_matched_tar_and_no_claim(tmp_path):
    """Slice 4: official diagnostic only completes when every arm is populated."""
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
        _official_record("official_demo__repo-C03"),
    ]
    predictions_path = _write_multi_official_predictions(
        tmp_path,
        ["official_demo__repo-A01", "official_demo__repo-B02", "official_demo__repo-C03"],
    )

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_official_oracle_for_good_ids({
            "official_demo__repo-A01",
            "official_demo__repo-B02",
        }),
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=_full_roster_panel_for_bad_ids({"repo-C03"}),
        reviewer_panel_mode="configured",
        min_good=2,
        min_bad=1,
    )

    assert report["status"] == "completed"
    assert report["aeb0_artifact_gate"]["status"] == "blocked"
    assert report["aeb0_artifact_gate"]["blocked_reasons"] == [
        "dataset_not_pinned_to_pro_or_held_out_equivalent"
    ]
    assert report["diagnostic_ready_for_scale"] is True
    assert report["all_arms_populated"] is True
    assert report["n_good"] == 2
    assert report["n_bad"] == 1
    assert report["baseline_available"] is True
    assert report["s_probe_available"] is True
    assert report["s_full_available"] is True
    assert report["oracle_ceiling_available"] is True
    assert report["matched_true_accept_status"][ARM_S_FULL] == "computed"
    assert report["supervisor_full_gate_matched_true_accept"]["false_accept_delta"] == -1.0
    assert report["far_tar_frr"][ARM_S_FULL]["false_accept_rate"] == 0.0
    assert report["far_tar_frr"][ARM_BASELINE]["false_accept_rate"] == 1.0
    assert report["benchmark_oracle"]["kind"] == "swe_bench_held_out_test_pass_proxy"
    assert report["benchmark_oracle"]["maintainer_mergeability_claim_allowed"] is False
    assert report["no_maintainer_mergeability_claim"] is True
    assert report["decision_freeze"]["oracle_after_reviewer_decisions"] is True
    assert report["decision_freeze"]["frozen_decision_row_count"] == 3
    assert report["false_accept_reduction_at_matched_true_accept"] == (
        report["supervisor_full_gate_matched_true_accept"]
    )
    assert report["s_probe_vs_s_full"]["s_probe_false_accept_rate"] == 1.0
    assert report["s_probe_vs_s_full"]["s_full_false_accept_rate"] == 0.0
    assert report["reviewer_marginal_delta_at_matched_true_accept"]["status"] == "computed"
    assert report["reviewer_marginal_delta_at_matched_true_accept"]["false_accept_rate_delta"] == -1.0
    assert report["candidate_generation"]["baseline"]["producer_family_count"] >= 1
    assert report["candidate_generation"]["matched_model_budget"]["status"] == "not_applicable_replay"
    assert report["configured_reviewer_panel_preflight"]["full_roster_available"] is True
    assert report["configured_reviewer_panel_preflight"]["failure_classification"] == (
        "quality_reject"
    )
    assert report["hidden_field_leak_check"]["ok"] is True
    assert report["metrics_unavailable_reasons"] == []
    _assert_diagnostic_report_only(report)
    persisted = json.loads(
        (tmp_path / "out" / "official_all_arms_diagnostic_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["report_sha256"] == report["report_sha256"]


def test_official_all_arms_reports_rubric_abstention_and_self_preference(tmp_path):
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
    ]
    predictions_path = tmp_path / "predictions.jsonl"
    lines = []
    for instance_id in ["official_demo__repo-A01", "official_demo__repo-B02"]:
        candidate_id = f"{instance_id}-cand"
        baseline = _produced_baseline_decision(
            candidate_id,
            patch=_valid_model_patch(),
            accept=True,
        )
        baseline["producer"] = {
            **baseline["producer"],
            "provider": "openai",
            "provider_family": "openai",
            "model": "gpt-5.5",
        }
        lines.append(json.dumps({
            "instance_id": instance_id,
            "candidate_id": candidate_id,
            "model_patch": _valid_model_patch(),
            "single_agent_baseline_decision": baseline,
        }))
    predictions_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def codex_panel(packet):
        candidate_id = str(packet["candidate_id"])
        bad = "B02" in candidate_id
        decision = "revise" if bad else "accept"
        label = "needs_human_review" if bad else "mergeable"
        return {
            "decision": decision,
            "available": True,
            "reason": "rubric_abstention" if bad else "all_available_reviewers_accept",
            "reviewer_ids": ["codex-reviewer"],
            "available_reviewers": ["codex-reviewer"],
            "missing_reviewers": [],
            "reviewer_results": [
                {
                    "reviewer_id": "codex-reviewer",
                    "runtime": "codex_cli",
                    "model": "gpt-5.5",
                    "verdict_present": True,
                    "decision": decision,
                    "severity": "important" if bad else "low",
                    "mergeability_label": label,
                    "mergeability_rubric": {
                        "unverifiable_from_public_evidence": bad,
                        "scope_locality": "public evidence only",
                    },
                    "transcript_sha256": "a" * 64,
                    "output_sha256": "b" * 64,
                }
            ],
        }

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_official_oracle_for_good_ids({"official_demo__repo-A01"}),
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=codex_panel,
        reviewer_panel_mode="configured",
        min_good=1,
        min_bad=1,
    )

    coverage = report["abstention_coverage"]
    assert coverage["labels"]["mergeable"] == 1
    assert coverage["labels"]["needs_human_review"] == 1
    assert coverage["abstention_count"] == 1
    assert coverage["scoring_authority"] == "deterministic_oracle"
    assert report["reviewer_provenance"]["reviewers"][0]["provider_family"] == "openai"
    assert report["generator_disjointness"]["same_family_decisive_vote_count"] == 2
    assert report["self_preference_warnings"]
    _assert_diagnostic_report_only(report)


def test_official_all_arms_diagnostic_is_unavailable_when_oracle_is_unavailable(tmp_path):
    predictions_path = _write_official_predictions(tmp_path)

    def unavailable_adapter(context):
        return {
            "fail_to_pass_status": "unavailable",
            "pass_to_pass_status": "unavailable",
            "oracle_unavailable": True,
            "oracle_unavailable_reason": "official_harness_failed",
            "oracle_adapter_receipt": {
                **_official_adapter_receipt(
                    context,
                    fail_to_pass_status="unavailable",
                    pass_to_pass_status="unavailable",
                ),
                "return_code": 1,
                "oracle_unavailable": True,
                "unavailable_reason": "official_harness_failed",
            },
        }

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=unavailable_adapter,
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=_full_roster_panel_for_bad_ids(set()),
        reviewer_panel_mode="configured",
    )

    assert report["status"] == "unavailable"
    assert report["attempt_stage"] == "harness"
    assert report["decision_freeze"]["frozen_decision_row_count"] == 1
    assert len(report["decision_freeze"]["decision_phase_sha256"]) == 64
    assert report["aeb0_artifact_gate"]["status"] == "blocked"
    assert "oracle_unavailable" in report["aeb0_artifact_gate"]["blocked_reasons"]
    assert report["diagnostic_ready_for_scale"] is False
    reasons = set(report["metrics_unavailable_reasons"])
    assert "official_replay_unavailable" in reasons
    assert "oracle_unavailable" in reasons
    assert report["far_tar_frr"] is None
    _assert_diagnostic_report_only(report)


def test_official_all_arms_diagnostic_refuses_claim_when_full_gate_unavailable(tmp_path):
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
        _official_record("official_demo__repo-C03"),
    ]
    predictions_path = _write_multi_official_predictions(
        tmp_path,
        ["official_demo__repo-A01", "official_demo__repo-B02", "official_demo__repo-C03"],
    )

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_official_oracle_for_good_ids({
            "official_demo__repo-A01",
            "official_demo__repo-B02",
        }),
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=None,
        reviewer_panel_mode="configured",
        min_good=2,
        min_bad=1,
    )

    assert report["status"] == "unavailable"
    assert report["s_full_available"] is False
    reasons = set(report["metrics_unavailable_reasons"])
    assert "s_full_unavailable" in reasons
    assert "reviewer_panel_full_roster_unavailable" in reasons
    assert any(
        reason.startswith("matched_true_accept_not_computed:")
        for reason in reasons
    )
    assert report["all_arms_populated"] is False
    _assert_diagnostic_report_only(report)


def test_official_all_arms_diagnostic_refuses_claim_when_baseline_unavailable(tmp_path):
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
        _official_record("official_demo__repo-C03"),
    ]
    predictions_path = _write_official_predictions_without_baseline(
        tmp_path,
        ["official_demo__repo-A01", "official_demo__repo-B02", "official_demo__repo-C03"],
    )

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_official_oracle_for_good_ids({
            "official_demo__repo-A01",
            "official_demo__repo-B02",
        }),
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=_full_roster_panel_for_bad_ids({"repo-C03"}),
        reviewer_panel_mode="configured",
        min_good=2,
        min_bad=1,
    )

    assert report["status"] == "unavailable"
    assert report["aeb0_artifact_gate"]["status"] == "blocked"
    assert report["baseline_available"] is False
    assert report["s_full_available"] is True
    reasons = set(report["metrics_unavailable_reasons"])
    assert "baseline_unavailable" in reasons
    assert "baseline_unavailable" in report["aeb0_artifact_gate"]["blocked_reasons"]
    assert "matched_true_accept_not_computed:unavailable" in reasons
    assert report["bridge_report"]["arms"][ARM_BASELINE]["availability_status"] == (
        "unavailable"
    )
    _assert_diagnostic_report_only(report)


def test_aeb0_verified_dataset_is_smoke_only(tmp_path):
    records = [
        _official_record("official_demo__repo-A01"),
        _official_record("official_demo__repo-B02"),
        _official_record("official_demo__repo-C03"),
    ]
    predictions_path = _write_multi_official_predictions(
        tmp_path,
        ["official_demo__repo-A01", "official_demo__repo-B02", "official_demo__repo-C03"],
    )

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="SWE-bench/SWE-bench_Verified",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: records,
        repo_materializer=_official_materializer,
        oracle_runner=_official_oracle_for_good_ids({
            "official_demo__repo-A01",
            "official_demo__repo-B02",
        }),
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=_full_roster_panel_for_bad_ids({"repo-C03"}),
        reviewer_panel_mode="configured",
        min_good=2,
        min_bad=1,
    )

    assert report["status"] == "completed"
    assert report["dataset_readiness"]["dataset"] == "SWE-bench/SWE-bench_Verified"
    assert report["dataset_readiness"]["claim_scope"] == "smoke_only"
    assert report["dataset_readiness"]["verified_smoke_only"] is True
    assert report["dataset_readiness"]["serious_benchmark_claim_allowed"] is False
    assert report["aeb0_artifact_gate"]["status"] == "blocked"
    assert report["aeb0_artifact_gate"]["blocked_reasons"] == [
        "swe_bench_verified_is_plumbing_smoke_only"
    ]
    assert report["aeb0_artifact_gate"]["real_benchmark_claim_allowed"] is False
    _assert_diagnostic_report_only(report)


def test_official_all_arms_diagnostic_blocks_hidden_field_leak(tmp_path):
    predictions_path = _write_official_predictions_with_hidden_candidate_id(tmp_path)

    report = swebench_mergeability_official_all_arms_diagnostic_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=_smoke_oracle_runner,
        oracle_adapter_kind="official_docker_or_equivalent",
        reviewer_panel=_full_roster_panel_for_bad_ids(set()),
        reviewer_panel_mode="configured",
    )

    assert report["status"] == "unavailable"
    assert report["hidden_field_leak_check"]["ok"] is False
    assert report["hidden_field_leak_check"]["refs"]
    assert "hidden_field_leak_detected" in report["metrics_unavailable_reasons"]
    assert report["bridge_report"]["oracle_isolation"]["ok"] is False
    _assert_diagnostic_report_only(report)
