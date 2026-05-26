from __future__ import annotations

from supervisor.agent_mailbox import (
    AgentMailboxMessage,
    ConfidenceReport,
    codex_confidence_report,
    codex_review_packet,
)


def test_agent_mailbox_message_carries_trace_fields():
    message = AgentMailboxMessage(
        task_id="trace-task",
        gate="outcome_review",
        sender="codex",
        recipient="cursor",
        message_type="review_request",
        content="Challenge the evidence.",
        round_index=2,
        persona_id="codex.lifecycle_reviewer",
        addresses=("event:41",),
        confidence=ConfidenceReport(
            value=0.82,
            source="deterministic_policy",
            criteria=("tests_receipt_present",),
            rationale="Codex needs Cursor to challenge receipt coverage.",
            evidence=("receipt:test:passed",),
        ),
        claims=("tests passed",),
        objections=("push receipt missing",),
        questions=("Does the remote receipt map to this commit?",),
        tool_receipts=(
            {
                "receipt_id": "test-1",
                "kind": "test",
                "status": "passed",
                "command": "uv run pytest -q",
            },
        ),
        evidence_refs=(
            {
                "kind": "pytest",
                "ref": "receipt:test-1",
                "status": "passed",
            },
        ),
        raw_transcript_refs=(
            {
                "kind": "claude_stdout",
                "ref": ".handoff/trace-task.stdout",
                "sha256": "abc123",
            },
        ),
        would_change_if="A matching git_remote receipt appears.",
        review_packet={"schema_version": "codex-review-packet/v1", "decision": "revise"},
        artifacts=({"kind": "prd", "path": "docs/prd.md"},),
    )

    payload = message.to_event_payload()

    assert payload["schema_version"] == "dual-agent-interaction/v1"
    assert payload["persona_id"] == "codex.lifecycle_reviewer"
    assert payload["addresses"] == ("event:41",)
    assert payload["claims"] == ("tests passed",)
    assert payload["objections"] == ("push receipt missing",)
    assert payload["questions"] == ("Does the remote receipt map to this commit?",)
    assert payload["tool_receipts"][0]["receipt_id"] == "test-1"
    assert payload["evidence_refs"][0]["ref"] == "receipt:test-1"
    assert payload["raw_transcript_refs"][0]["kind"] == "claude_stdout"
    assert payload["would_change_if"] == "A matching git_remote receipt appears."
    assert payload["review_packet"]["schema_version"] == "codex-review-packet/v1"


def test_codex_confidence_report_and_review_packet_explain_decision():
    report = codex_confidence_report(
        decision="deny",
        gate_status="accepted",
        probe_statuses={"P1": "green", "P2": "green", "P11": "red"},
        claim_verification={"status": "red", "reason": "workflow_claim_verification_failed"},
        cursor_review=None,
    )
    packet = codex_review_packet(
        task_id="trace-task",
        gate="outcome_review",
        decision="deny",
        confidence=report,
        probe_statuses={"P1": "green", "P2": "green", "P11": "red"},
        claim_verification={"status": "red", "reason": "workflow_claim_verification_failed"},
        cursor_review=None,
        objection="claim verification failed",
        evidence_refs=({"kind": "pytest", "ref": "receipt:pytest"},),
    )

    assert report.value == 0.75
    assert "blocked_or_failed_probes=P11" in report.criteria
    assert packet["schema_version"] == "codex-review-packet/v1"
    assert packet["decision"] == "deny"
    assert {"requirement_id": "probe.P11", "status": "fail", "evidence": ["P11:red"]} in packet["requirements"]
    assert packet["round_policy"]["force_next_round"] is True
    assert packet["round_policy"]["blocking_findings"] == ["finding-001"]
    assert packet["findings"][0]["severity"] == "CRITICAL"
    assert packet["findings"][0]["code"] == "P11"
    assert packet["findings"][0]["receipt_replay"]["failures"] == []


def test_codex_review_packet_links_claim_failures_to_receipt_replay():
    report = codex_confidence_report(
        decision="deny",
        gate_status="accepted",
        probe_statuses={"P1": "green", "P2": "green", "P3": "green"},
        claim_verification={
            "status": "red",
            "reason": "workflow_claim_verification_failed",
            "details": {
                "failures": ["implemented_without_diff_receipt"],
                "receipts": [{"receipt_id": "pytest-focused", "kind": "test"}],
            },
        },
        cursor_review=None,
    )

    packet = codex_review_packet(
        task_id="trace-task",
        gate="outcome_review",
        decision="deny",
        confidence=report,
        probe_statuses={"P1": "green", "P2": "green", "P3": "green"},
        claim_verification={
            "status": "red",
            "reason": "workflow_claim_verification_failed",
            "details": {
                "failures": ["implemented_without_diff_receipt"],
                "receipts": [{"receipt_id": "pytest-focused", "kind": "test"}],
            },
        },
        cursor_review=None,
        objection="claim verification failed",
        evidence_refs=({"kind": "git_diff", "ref": "receipt:missing"},),
        tool_receipts=({"receipt_id": "pytest-focused", "kind": "test"},),
    )

    finding = packet["findings"][0]
    assert finding["severity"] == "CRITICAL"
    assert finding["title"] == "claim verification failed"
    assert finding["receipt_replay"]["failures"] == ["implemented_without_diff_receipt"]
    assert finding["receipt_replay"]["observed_receipt_ids"] == ["pytest-focused"]
    assert packet["round_policy"]["force_next_round"] is True
