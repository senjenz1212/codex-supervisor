from __future__ import annotations

import asyncio

import pytest

from supervisor.state import Decision, State


@pytest.mark.asyncio
async def test_decision_outbox_survives_restart_and_ack_is_durable(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "state.db")
    first = State(db_path)
    decision_id = await first.enqueue_decision(
        Decision(
            kind="evaluate_run",
            run_id="run-1",
            payload={"final_status": "completed"},
        )
    )

    restarted = State(db_path)
    claimed = await asyncio.wait_for(
        restarted.next_decision(lease_s=30),
        timeout=1,
    )

    assert claimed.decision_id == decision_id
    assert claimed.attempt_count == 1
    assert restarted.ack_decision(claimed)
    assert restarted.list_decision_outbox()[0]["status"] == "acked"
    assert State(db_path).available_decision_count() == 0


@pytest.mark.asyncio
async def test_decision_outbox_enqueue_is_idempotent(tmp_path) -> None:
    state = State(str(tmp_path / "state.db"))
    decision = Decision(
        kind="evaluate_run",
        run_id="run-1",
        payload={"final_status": "completed"},
    )

    first_id = await state.enqueue_decision(decision)
    second_id = await state.enqueue_decision(decision)

    assert second_id == first_id
    assert len(state.list_decision_outbox()) == 1


@pytest.mark.asyncio
async def test_decision_outbox_retry_dead_letter_and_lease_expiry(
    tmp_path,
) -> None:
    db_path = str(tmp_path / "state.db")
    state = State(db_path)
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id="retry-run"),
        available_at=100,
    )
    first = state.claim_decision(
        worker_id="worker-a",
        lease_s=5,
        now=100,
    )
    assert first is not None
    assert first.attempt_count == 1

    restarted = State(db_path)
    assert restarted.claim_decision(
        worker_id="worker-b",
        lease_s=5,
        now=104,
    ) is None
    expired = restarted.claim_decision(
        worker_id="worker-b",
        lease_s=5,
        now=106,
    )
    assert expired is not None
    assert expired.decision_id == first.decision_id
    assert expired.attempt_count == 2

    assert restarted.retry_decision(
        expired,
        error="temporary provider failure",
        delay_s=10,
        now=106,
    )
    assert restarted.claim_decision(
        worker_id="worker-c",
        now=115,
    ) is None
    final = restarted.claim_decision(
        worker_id="worker-c",
        now=116,
    )
    assert final is not None
    assert final.attempt_count == 3
    assert restarted.dead_letter_decision(
        final,
        error="retry budget exhausted",
        now=116,
    )

    row = restarted.list_decision_outbox()[0]
    assert row["status"] == "dead_letter"
    assert row["attempts"] == 3
    assert row["last_error"] == "retry budget exhausted"
