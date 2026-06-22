"""Public-boundary tests for the SWE-bench Pro mergeability bridge."""
from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
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
    PATCH_APPLY_FAILURE_REASON,
    PUBLIC_STATIC_PATCH_PROBE,
    REVIEWER_PANEL_UNAVAILABLE_REASON,
    SWEBENCH_MERGEABILITY_FIXTURE_REPORT_SCHEMA_VERSION,
    SWEBENCH_PRO_FORBIDDEN_KEYS,
    SwebenchMergeabilityFixtureRunnerError,
    SwebenchProBridgeError,
    build_swe_bench_pro_public_packet,
    swebench_mergeability_fixture_runner,
    swebench_mergeability_official_live_runner,
    swebench_mergeability_live_runner,
    swebench_mergeability_official_replay_runner,
    swebench_mergeability_replay_runner,
    swebench_pro_mergeability_bridge_report,
)
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

    public_packet_text = json.dumps(bridge_report["public_packets"], sort_keys=True)
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "oracle_accept"):
        assert forbidden not in public_packet_text
    reviewer_packet_path = Path(report["instance_reports"][0]["reviewer_packets"][0]["reviewer_packet_path"])
    reviewer_packet_text = reviewer_packet_path.read_text(encoding="utf-8")
    for forbidden in ("FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "oracle_accept"):
        assert forbidden not in reviewer_packet_text


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
    )

    assert calls == ["real-good", "real-bad"]
    rows = report["bridge_report"]["per_row_results"]
    assert all(row["s_full_unavailable"] is False for row in rows)
    assert all(row["s_full_accept"] is True for row in rows)
    assert report["instance_reports"][0]["independent_reviewer_results"][0]["reviewer_id"] == (
        "independent-reviewer-0"
    )


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
            },
        },
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
        }

    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=predictions_path,
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=oracle,
        oracle_adapter_kind="deterministic_test_adapter",
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
    assert oracle_payload["rows"][0]["oracle_adapter_kind"] == "deterministic_test_adapter"
    assert report["bridge_report"]["per_row_results"][0]["oracle_accept"] is False


def test_official_replay_report_labels_oracle_adapter_and_stays_report_only(tmp_path):
    report = swebench_mergeability_official_replay_runner(
        dataset="fixture-official",
        dataset_split="test",
        predictions_path=_write_official_predictions(tmp_path),
        output_dir=tmp_path / "out",
        dataset_loader=lambda **_kwargs: [_official_record()],
        repo_materializer=_official_materializer,
        oracle_runner=lambda _context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
        },
        oracle_adapter_kind="official_docker_or_equivalent",
    )

    assert report["oracle_adapter_kind"] == "official_docker_or_equivalent"
    assert report["replay_report"]["oracle_adapter_kind"] == "official_docker_or_equivalent"
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
    baseline = _FakeLiveGenerator()
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
        oracle_runner=lambda _context: {
            "fail_to_pass_status": "pass",
            "pass_to_pass_status": "pass",
        },
        oracle_adapter_kind="deterministic_test_adapter",
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
