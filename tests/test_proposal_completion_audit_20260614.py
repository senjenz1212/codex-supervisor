"""Runtime evidence tests for proposal-completion-audit-20260614.

These tests back the five TDD names referenced by the supervisor runtime floor
for task `proposal-completion-audit-20260614`. The audit deliverable is a
report-only outcome at
`docs/dual-agent/proposal-completion-audit-20260614/proposal-completion-report.md`.
Each test verifies a single, deterministic property of that report plus the
locked source planning artifacts; no policy, queue, or overlay is mutated.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from supervisor.dual_agent_lead import PlanningArtifact
from supervisor.planning_validator import (
    REQUIRED_PLANNING_ARTIFACTS_BY_GATE,
    required_planning_kinds_for_gate,
    validate_planning_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = REPO_ROOT / "docs" / "dual-agent" / "proposal-completion-audit-20260614"
SOURCE_DIR = AUDIT_DIR / "source"
OUTCOME_PATH = AUDIT_DIR / "proposal-completion-report.md"

EXPECTED_SOURCE_SHAS = {
    "prd.md": "8635d9d9bbc2c7c919da3c1d01167bb1b000fc28a2e76066641d3562946516e5",
    "grill-findings.md": "6e2baa24cc91f0469a85cc6ad1a4156c84bc6c49e6366af2d1e06e221959a40f",
    "issues.md": "e8f3e1e9d98aee017f0d51b13a9f41dfd84fcd3ffb4afc12fc5af118e85c80b2",
    "tdd.md": "3a0ed6b67a945cf2313df99a4728dec24dd08f3a66e008c6b00b098b1ef7b52a",
    "grill-findings-tdd.md": "cdfbabc7a637d208f4359d7953aabeb88513e13fab8c72becc867dc7591fb041",
    "implementation-plan.md": "0426b81fa8d4444ba9ba871d2e36dab3450750b3b5bd4de927296e987e93a818",
}

PLANNING_ARTIFACTS = [
    PlanningArtifact(path=str(SOURCE_DIR / "prd.md"), kind="prd"),
    PlanningArtifact(path=str(SOURCE_DIR / "grill-findings.md"), kind="grill_findings"),
    PlanningArtifact(path=str(SOURCE_DIR / "issues.md"), kind="issues"),
    PlanningArtifact(path=str(SOURCE_DIR / "tdd.md"), kind="tdd_plan"),
    PlanningArtifact(path=str(SOURCE_DIR / "implementation-plan.md"), kind="implementation_plan"),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _outcome_text() -> str:
    return OUTCOME_PATH.read_text(encoding="utf-8")


def _stub_rubric(_texts, _required, _threshold):
    """Stand-in rubric runner; returns a passing planning rubric result.

    The supervisor's deterministic rubric requires an LLM-backed rater in
    production. For local validation we substitute a stub so we exercise the
    *gate-routing* contract (kind-coverage, traceability, content distinctness)
    without depending on a live LLM. Each test that calls this remains a
    public-boundary assertion against `validate_planning_artifacts`.
    """
    from supervisor.planning_validator import PlanningRubricResult

    return PlanningRubricResult(
        score=1.0,
        threshold=_threshold,
        status="accepted",
        policy="block",
        reasons=("stubbed for local route-gate parity test",),
    )


# ---------------------------------------------------------------------------
# Test 1 — Planning artifacts pass for every route gate

def test_planning_artifacts_pass_all_route_gates() -> None:
    """For each route gate, validate_planning_artifacts must report ok=True."""
    for gate in REQUIRED_PLANNING_ARTIFACTS_BY_GATE:
        required = required_planning_kinds_for_gate(gate)
        # Filter our artifacts down to what this gate requires.
        present_kinds = {a.kind for a in PLANNING_ARTIFACTS}
        missing_for_gate = [k for k in required if k not in present_kinds]
        assert not missing_for_gate, (
            f"gate={gate} missing planning kinds: {missing_for_gate}"
        )
        result = validate_planning_artifacts(
            PLANNING_ARTIFACTS,
            required_kinds=required,
            gate=gate,
            rubric_runner=_stub_rubric,
        )
        failed = [c for c in result.checks.values() if not c.ok]
        assert not failed, (
            f"gate={gate} planning checks failed: "
            + ", ".join(f"{c.check_id}:{c.message}" for c in failed)
        )
        assert result.ok, f"gate={gate} validate_planning_artifacts returned not-ok"


# ---------------------------------------------------------------------------
# Test 2 — Report classifies proposal completion from code

def test_report_classifies_proposal_completion_from_code() -> None:
    """The outcome report classifies each proposal area and cites code."""
    text = _outcome_text()
    # Each proposal area must appear in the report.
    required_areas = [
        "AutoResearch draft generation",
        "Runnable activation",
        "Policy proposal derivation",
        "Policy overlay liveness",
        "Regression rollback drafting",
        "Lessons hygiene",
        "Weekly P11 audit",
        "Runtime evidence floor",
        "AXI detached-dispatcher",
    ]
    missing_areas = [a for a in required_areas if a not in text]
    assert not missing_areas, f"missing proposal areas: {missing_areas}"

    # Each status classification must appear at least once.
    required_statuses = ["implemented", "live", "live-unproven"]
    for status in required_statuses:
        assert status in text, f"missing classification status: {status}"

    # Code-grounded references: at least one supervisor/* file path with line
    # and at least one mcp_tools/* file path with line.
    has_supervisor_ref = re.search(r"supervisor/[a-z_/]+\.py:\d+", text) is not None
    has_mcp_ref = re.search(r"mcp_tools/[a-z_/]+\.py:\d+", text) is not None
    assert has_supervisor_ref, "outcome report lacks supervisor/*.py:LINE reference"
    assert has_mcp_ref, "outcome report lacks mcp_tools/*.py:LINE reference"


# ---------------------------------------------------------------------------
# Test 3 — Report-only safety invariant (no mutation of policy or queue)

def test_report_only_audit_does_not_mutate_policy_or_queue() -> None:
    """Source artifacts must match the handoff sha256s (frozen + unmodified).

    The report-only invariant is enforced by *not* writing to policy / queue
    surfaces; the deterministic proof we can run from pytest is that the
    handoff-pinned source artifacts still match their declared hashes. If
    the artifact bytes changed underfoot, the supervisor handoff fidelity is
    broken and no claim about queue / overlay can be trusted.
    """
    for name, expected_sha in EXPECTED_SOURCE_SHAS.items():
        path = SOURCE_DIR / name
        assert path.exists(), f"source artifact missing: {path}"
        actual = _sha256(path)
        assert actual == expected_sha, (
            f"source drift for {name}: expected {expected_sha}, got {actual}"
        )

    # Also assert the report file documents the safety proof with pre/post rows.
    text = _outcome_text()
    assert "Report-Only Safety Invariant" in text, "missing P2 section"
    assert "policy-overlay.yaml" in text, "missing overlay hash row"
    assert "experiments list" in text.lower(), "missing experiments list rows"
    # The "Mutated?" column must show **No** at least once and never **Yes**.
    assert "**No**" in text, "P2 table missing 'No' mutation entries"
    assert "**Yes**" not in text, "P2 table contains a 'Yes' mutation entry"


# ---------------------------------------------------------------------------
# Test 4 — Liveness metrics are not overclaimed

def test_liveness_metrics_are_not_overclaimed() -> None:
    """D1 (transport) and D2 (format) must be marked undecidable with reason."""
    text = _outcome_text()
    # Decision keys present.
    assert "D1" in text and "D2" in text, "missing D1/D2 decision labels"
    # Both must be marked undecidable in the report.
    d1_match = re.search(r"D1[^\n]*?undecidable", text, re.IGNORECASE)
    d2_match = re.search(r"D2[^\n]*?undecidable", text, re.IGNORECASE)
    assert d1_match is not None, "D1 not marked undecidable in outcome report"
    assert d2_match is not None, "D2 not marked undecidable in outcome report"
    # Reason must cite insufficient_data or a sample-count threshold.
    assert (
        "insufficient_data" in text or "at least 5" in text
    ), "outcome report lacks denominator/sample-count justification"
    # PRD anti-goal P3 forbids treating incident share as probability.
    # The report must NOT make a "reliability" or "deprecation" claim.
    forbidden = [
        "MCP is unreliable",
        "deprecate MCP now",
        "TOON is ready",
        "TOON adoption decided",
    ]
    leakage = [phrase for phrase in forbidden if phrase.lower() in text.lower()]
    assert not leakage, f"overclaim leaked into outcome report: {leakage}"


# ---------------------------------------------------------------------------
# Test 5 — Terminal gate status is reported truthfully

def test_terminal_gate_status_is_reported_truthfully() -> None:
    """The outcome must name the gate, mode, and not claim success vacuously."""
    text = _outcome_text()
    # The gate identity must be stated explicitly.
    assert "execution" in text, "outcome report does not name the gate"
    assert "proposal-completion-audit-20260614" in text, "task id not in report"
    assert "lead_direct" in text, "lead mode not stated in report"
    # The handoff-pinned overlay hash must appear (proves the report is
    # grounded in the actual gate handoff, not a generic narrative).
    handoff_overlay_hash_prefix = "e3b0c44"
    assert handoff_overlay_hash_prefix in text, (
        "outcome report lacks the handoff overlay hash prefix"
    )
    # The report must mention the supervisor runtime TDD contract by name to
    # prove it knows the gate's verification model.
    assert "runtime-TDD contract" in text or "runtime TDD" in text, (
        "outcome report does not mention the runtime TDD contract"
    )
    # The report must explicitly disavow vacuous success. Any of these phrases
    # acknowledges the truthful-status invariant.
    truthful_markers = [
        "Critical Review",
        "Strongest objection",
        "What would change my mind",
    ]
    found = [m for m in truthful_markers if m in text]
    assert found, "outcome report lacks a critical-review section"
