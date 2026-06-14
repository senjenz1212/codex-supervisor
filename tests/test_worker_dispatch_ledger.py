from __future__ import annotations

import inspect

from supervisor.worker_dispatch_ledger import (
    CROSS_VENDOR_REVIEW_SELECTED,
    DEGRADED_REVIEW_UNAVAILABLE,
    EVIDENCE_ATTEMPT_RECORDED,
    WORKER_BLOCKED,
    WORKER_CANCELLED,
    WORKER_COMPLETED,
    WORKER_DISPATCHED,
    WORKER_SESSION_CREATED,
    EvidenceAttempt,
    RosterEntry,
    RosterPreflight,
    WorkerDispatch,
    build_cross_vendor_payload,
    build_dispatch_payload,
    build_evidence_attempt_payload,
    build_roster_payload,
    compute_attempt_id,
    event_kind_for_status,
    link_evidence_attempt,
    select_cross_vendor_reviewer,
    validate_lifecycle_payload,
)


def _dispatch(status: str = "completed") -> WorkerDispatch:
    return WorkerDispatch(
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
        worker_id="independent-reviewer-1",
        reviewer_id="independent-reviewer-1",
        purpose="review",
        provider_family="openai",
        runtime="codex_cli",
        model="gpt-5.5",
        status=status,
        started_at="2026-06-14T10:00:00Z",
        completed_at="2026-06-14T10:00:02Z",
        wall_clock_s=2.0,
        cost_usd=0.04,
        receipt_ids=["receipt-1"],
        output_ref="reviews/output.json",
        transcript_ref="reviews/transcript.jsonl",
        output_hash="a" * 64,
        transcript_hash="b" * 64,
        worktree_ref=".scratch/read-only-review",
        branch="codex/test",
        session_id="session-1",
        conversation_id="conversation-1",
    )


def test_worker_dispatch_events_persist_typed_lifecycle():
    payload = build_dispatch_payload(_dispatch(status="completed"))

    assert validate_lifecycle_payload(payload) == []
    assert payload["purpose"] == "review"
    assert payload["provider_family"] == "openai"
    assert payload["runtime"] == "codex_cli"
    assert payload["model"] == "gpt-5.5"
    assert payload["receipt_ids"] == ["receipt-1"]
    assert event_kind_for_status("session_created") == WORKER_SESSION_CREATED
    assert event_kind_for_status("dispatched") == WORKER_DISPATCHED
    assert event_kind_for_status("completed") == WORKER_COMPLETED


def test_worker_blocked_event_persists_required_fields():
    record = _dispatch(status="blocked")
    record.reason = "blocked_by_policy"
    payload = build_dispatch_payload(record)

    assert event_kind_for_status("blocked") == WORKER_BLOCKED
    assert validate_lifecycle_payload(payload) == []
    assert payload["status"] == "blocked"
    assert payload["reason"] == "blocked_by_policy"
    assert payload["worker_id"] == "independent-reviewer-1"


def test_worker_cancelled_event_persists_required_fields():
    record = _dispatch(status="cancelled")
    record.reason = "cancelled_by_policy"
    payload = build_dispatch_payload(record)

    assert event_kind_for_status("cancelled") == WORKER_CANCELLED
    assert validate_lifecycle_payload(payload) == []
    assert payload["status"] == "cancelled"
    assert payload["reason"] == "cancelled_by_policy"
    assert payload["worker_id"] == "independent-reviewer-1"


def test_read_only_worker_finding_links_evidence_attempt():
    dispatch = _dispatch(status="completed")
    attempt = EvidenceAttempt(
        attempt_id=compute_attempt_id(
            task_id=dispatch.task_id,
            run_id=dispatch.run_id,
            gate=dispatch.gate,
            worker_id=dispatch.worker_id,
            purpose=dispatch.purpose,
            finding_kind="read_only_review",
            receipt_ids=["receipt-2", "receipt-1"],
        ),
        task_id=dispatch.task_id,
        run_id=dispatch.run_id,
        gate=dispatch.gate,
        worker_id=dispatch.worker_id,
        purpose=dispatch.purpose,
        finding_kind="read_only_review",
        receipt_ids=["receipt-2", "receipt-1"],
        output_hash="c" * 64,
        transcript_hash="d" * 64,
        finding_summary="No blocker found.",
    )

    linked = link_evidence_attempt(dispatch, attempt)
    attempt_payload = build_evidence_attempt_payload(attempt)

    assert linked.evidence_attempt_id == attempt.attempt_id
    assert linked.receipt_ids == ["receipt-1", "receipt-2"]
    assert attempt_payload["attempt_id"].startswith("attempt-")
    assert attempt_payload["receipt_ids"] == ["receipt-2", "receipt-1"]
    assert EVIDENCE_ATTEMPT_RECORDED == "supervisor_evidence_attempt_recorded"


def test_roster_preflight_records_available_and_unavailable_workers():
    roster = RosterPreflight(
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
        entries=[
            RosterEntry(
                worker_id="independent-reviewer-0",
                purpose="reviewer",
                provider_family="cursor",
                runtime="cursor_sdk",
                model="default",
                available=True,
            ),
            RosterEntry(
                worker_id="independent-reviewer-1",
                purpose="reviewer",
                provider_family="openai",
                runtime="codex_cli",
                model="gpt-5.5",
                available=False,
                boot_status="command_not_found",
                failure_reason="codex CLI unavailable",
            ),
        ],
    )

    payload = build_roster_payload(roster)

    assert payload["available_worker_ids"] == ["independent-reviewer-0"]
    assert payload["unavailable_worker_ids"] == ["independent-reviewer-1"]
    assert payload["boot_failed_worker_ids"] == ["independent-reviewer-1"]
    assert payload["unavailable"][0]["failure_reason"] == "codex CLI unavailable"


def test_cross_vendor_review_selects_different_provider_family():
    roster = RosterPreflight(
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
        entries=[
            RosterEntry(
                worker_id="same-vendor",
                purpose="reviewer",
                provider_family="anthropic",
                runtime="claude_code",
                model="claude-opus-4-6",
                available=True,
            ),
            RosterEntry(
                worker_id="other-vendor",
                purpose="reviewer",
                provider_family="openai",
                runtime="codex_cli",
                model="gpt-5.5",
                available=True,
            ),
        ],
    )

    selection = select_cross_vendor_reviewer(
        implementation_provider_family="anthropic",
        roster=roster,
        policy="block",
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
    )

    assert selection.selected_reviewer_id == "other-vendor"
    assert selection.selected_provider_family == "openai"
    assert selection.selection_status == "selected"
    assert build_cross_vendor_payload(selection)["selected_reviewer_id"] == "other-vendor"
    assert CROSS_VENDOR_REVIEW_SELECTED == "supervisor_cross_vendor_review_selected"


def test_cross_vendor_unavailable_blocks_or_degrades_explicitly():
    roster = RosterPreflight(
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
        entries=[
            RosterEntry(
                worker_id="same-vendor",
                purpose="reviewer",
                provider_family="anthropic",
                runtime="claude_code",
                model="claude-opus-4-6",
                available=True,
            )
        ],
    )

    blocked = select_cross_vendor_reviewer(
        implementation_provider_family="anthropic",
        roster=roster,
        policy="block",
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
    )
    degraded = select_cross_vendor_reviewer(
        implementation_provider_family="anthropic",
        roster=roster,
        policy="degraded",
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
    )

    assert blocked.selection_status == "block"
    assert blocked.reason == "cross_vendor_review_unavailable"
    assert degraded.selection_status == "degraded"
    assert degraded.reason == "degraded_review_unavailable"
    assert DEGRADED_REVIEW_UNAVAILABLE == "supervisor_degraded_review_unavailable"


def test_roster_preflight_happens_before_cross_vendor_selection():
    sig = inspect.signature(select_cross_vendor_reviewer)
    assert "roster" in sig.parameters
    roster_param = sig.parameters["roster"]
    assert roster_param.kind == inspect.Parameter.KEYWORD_ONLY
    assert roster_param.annotation in (RosterPreflight, "RosterPreflight")

    # Calling without a roster must fail loudly — no implicit empty roster.
    try:
        select_cross_vendor_reviewer(
            implementation_provider_family="anthropic",
            policy="block",
            task_id="worker-task",
            run_id="run-1",
            gate="outcome_review",
        )
    except TypeError:
        pass
    else:  # pragma: no cover - guard against silent default
        raise AssertionError(
            "select_cross_vendor_reviewer must require a RosterPreflight argument"
        )


def test_roster_boot_failure_marks_backend_unavailable_without_retry_loop():
    roster = RosterPreflight(
        task_id="worker-task",
        run_id="run-1",
        gate="outcome_review",
        entries=[
            RosterEntry(
                worker_id="independent-reviewer-1",
                purpose="reviewer",
                provider_family="openai",
                runtime="codex_cli",
                model="gpt-5.5",
                available=False,
                boot_status="boot_failed",
                failure_reason="codex CLI unavailable",
            )
        ],
    )

    assert roster.available_worker_ids() == []
    assert roster.unavailable_worker_ids() == ["independent-reviewer-1"]
    assert roster.unavailable_worker_ids().count("independent-reviewer-1") == 1
    assert roster.boot_failed_worker_ids() == ["independent-reviewer-1"]
    assert roster.boot_failed_worker_ids().count("independent-reviewer-1") == 1

    payload = build_roster_payload(roster)
    assert payload["available_worker_ids"] == []
    assert payload["unavailable_worker_ids"] == ["independent-reviewer-1"]
    assert payload["boot_failed_worker_ids"] == ["independent-reviewer-1"]
    assert payload["unavailable"][0]["boot_status"] == "boot_failed"
    assert payload["unavailable"][0]["failure_reason"] == "codex CLI unavailable"
