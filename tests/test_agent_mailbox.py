from __future__ import annotations

from supervisor.agent_mailbox import AgentMailboxMessage, ConfidenceReport


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
