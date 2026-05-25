from __future__ import annotations

import json
from pathlib import Path

from supervisor.dual_agent import evaluate_outcome_fidelity
from supervisor.dual_agent_lead import LeadInvocationRequest, invoke_claude_lead
from supervisor.dual_agent_runner import make_replay_runner
from supervisor.redaction import redact


FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "dual_agent"
    / "live_lead_no_budget_probe_20260524_01"
)
CURSOR_FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "dual_agent"
    / "live_cursor_sdk_probe_20260525_01"
)
FAILURE_FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "dual_agent"
    / "live_failure_mode_probe_20260525_01"
)


def test_live_lead_no_budget_fixtures_replay_to_typed_outcomes(tmp_path):
    stdout_fixtures = sorted(FIXTURE_DIR.glob("lead-*.stdout.json"))

    assert len(stdout_fixtures) == 6
    for stdout_fixture in stdout_fixtures:
        result = invoke_claude_lead(
            LeadInvocationRequest(
                task_id="live-lead-no-budget-probe-20260524-01",
                gate="tdd_review",
                instruction="Replay captured live /lead fixture.",
                cwd=tmp_path,
            ),
            runner=make_replay_runner(stdout_fixture),
        )

        assert result.probe.ok, stdout_fixture.name
        assert result.outcome is not None
        assert result.outcome.task_id == "live-lead-no-budget-probe-20260524-01"
        assert result.stdout_bytes > 0
        assert result.cost_usd is None or result.cost_usd >= 0


def test_live_cursor_sdk_probe_fixture_is_parseable_and_redacted():
    summary = json.loads((CURSOR_FIXTURE_DIR / "summary.json").read_text(encoding="utf-8"))
    transcript = (CURSOR_FIXTURE_DIR / "transcript.txt").read_text(encoding="utf-8")

    assert redact(summary) == summary
    assert "crsr_" not in json.dumps(summary)
    assert "crsr_" not in transcript
    assert summary["status"] == "completed"
    assert summary["accepted"] is True
    assert summary["probe"]["status"] == "green"

    probe, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=("Cursor Reviewer",),
        expected_decisions=("accept",),
        expected_objections=(),
    )

    assert probe.ok
    assert outcome is not None
    assert outcome.task_id == "live-cursor-sdk-probe"
    assert outcome.changed_files == []
    assert outcome.confidence >= 0.9


def test_live_failure_mode_probe_replays_claude_fixture_and_preserves_p11_block(tmp_path):
    summary = json.loads((FAILURE_FIXTURE_DIR / "summary.json").read_text(encoding="utf-8"))

    assert redact(summary) == summary
    assert "crsr_" not in json.dumps(summary)
    assert summary["status"] == "blocked_as_expected"
    assert summary["final_status"] == "blocked"
    assert summary["claude"]["gate_status"] == "accepted"
    assert summary["claim_without_receipts"]["status"] == "red"
    assert summary["claim_without_receipts"]["reason"] == "workflow_claim_verification_failed"
    assert summary["failure_taxonomy"]["category"] == "task_verification"
    assert summary["failure_taxonomy"]["subcategory"] == "missing_or_stale_receipt"
    assert {
        "tests_passed_without_test_receipt",
        "implemented_without_diff_receipt",
    } <= set(summary["claim_without_receipts"]["details"]["failures"])

    result = invoke_claude_lead(
        LeadInvocationRequest(
            task_id="live-failure-mode-probe-20260525-01",
            gate="outcome_review",
            instruction="Replay captured live failure-mode /lead fixture.",
            cwd=tmp_path,
            expected_specialists=("Failure Probe Lead",),
            expected_decisions=("accept",),
        ),
        runner=make_replay_runner(FAILURE_FIXTURE_DIR / "lead-01.stdout.json"),
    )

    assert result.probe.ok
    assert result.outcome is not None
    assert result.outcome.task_id == "live-failure-mode-probe-20260525-01"
    assert "tests passed" in result.outcome.claims
    assert "implemented" in result.outcome.claims
    assert result.outcome.changed_files == ["phantom_result.txt"]


def test_live_failure_mode_cursor_fixture_is_parseable_when_present():
    transcript_path = FAILURE_FIXTURE_DIR / "cursor-transcript.txt"
    assert transcript_path.exists()
    transcript = transcript_path.read_text(encoding="utf-8")

    assert "crsr_" not in transcript
    probe, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=("Cursor Reviewer",),
        expected_decisions=("accept",),
        expected_objections=(),
    )

    assert probe.ok
    assert outcome is not None
    assert outcome.task_id == "live-failure-mode-probe-20260525-01"
    joined_claims = "\n".join(outcome.claims).lower()
    assert (
        "unsubstantiated" in joined_claims
        or "receipt absence is intentional" in joined_claims
        or "fixture contract satisfied" in joined_claims
        or "phantom" in joined_claims
    )
