from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sqlite3
import threading
import time

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


@pytest.mark.asyncio
async def test_decision_verdict_commit_rejects_expired_lease_without_writes(
    tmp_path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id="run-expired"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=5,
        now=100,
    )
    assert claimed is not None

    verdict_id = state.commit_decision_verdict(
        claimed,
        model="provider/model",
        output={"decision": "accept"},
        latency_ms=7,
        now=105,
    )

    assert verdict_id is None
    assert state.list_decision_outbox()[0]["status"] == "leased"
    assert state._conn.execute(
        "SELECT COUNT(*) FROM verdicts"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_decision_verdict_commit_rejects_identity_mismatches(
    tmp_path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    await state.enqueue_decision(
        Decision(kind="review_updates", run_id="run-authoritative"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=30,
        now=100,
    )
    assert claimed is not None

    mismatches = (
        replace(claimed, decision_id="different-decision"),
        replace(claimed, lease_token="different-token"),
        replace(claimed, kind="evaluate_run"),
        replace(claimed, run_id="caller-substituted-run"),
    )
    for mismatched in mismatches:
        assert state.commit_decision_verdict(
            mismatched,
            model="provider/model",
            output={"decision": "accept"},
            latency_ms=7,
            now=101,
        ) is None

    assert state.list_decision_outbox()[0]["status"] == "leased"
    assert state._conn.execute(
        "SELECT COUNT(*) FROM verdicts"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_decision_verdict_commit_is_atomic_and_exactly_once(
    tmp_path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    await state.enqueue_decision(
        Decision(kind="adjudicate_drift", run_id="run-authoritative"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=30,
        now=100,
    )
    assert claimed is not None
    kwargs = {
        "model": "provider/model",
        "output": {"decision": "accept"},
        "latency_ms": 7,
        "now": 101,
    }

    verdict_id = state.commit_decision_verdict(claimed, **kwargs)
    replayed_verdict_id = state.commit_decision_verdict(claimed, **kwargs)

    assert isinstance(verdict_id, int)
    assert verdict_id > 0
    assert replayed_verdict_id is None
    row = state.list_decision_outbox()[0]
    assert row["status"] == "acked"
    assert row["lease_token"] is None
    verdicts = state._conn.execute(
        """SELECT verdict_id, decision_id, run_id, phase, layer, model,
                  output_json, latency_ms
             FROM verdicts"""
    ).fetchall()
    assert len(verdicts) == 1
    assert verdicts[0]["verdict_id"] == verdict_id
    assert verdicts[0]["decision_id"] == claimed.decision_id
    assert verdicts[0]["run_id"] == "run-authoritative"
    assert verdicts[0]["phase"] == "adjudicate_drift"
    assert verdicts[0]["layer"] == "L4"
    assert verdicts[0]["model"] == "provider/model"
    assert json.loads(verdicts[0]["output_json"]) == {
        "decision": "accept"
    }
    assert verdicts[0]["latency_ms"] == 7


@pytest.mark.asyncio
async def test_decision_verdict_commit_rolls_back_ack_on_insert_failure(
    tmp_path,
) -> None:
    state = State(str(tmp_path / "state.db"))
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id="run-insert-failure"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=30,
        now=100,
    )
    assert claimed is not None
    state._conn.execute(
        """CREATE TRIGGER reject_decision_verdict
           BEFORE INSERT ON verdicts
           WHEN NEW.decision_id IS NOT NULL
           BEGIN
             SELECT RAISE(ABORT, 'injected verdict failure');
           END"""
    )
    state._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected verdict failure"):
        state.commit_decision_verdict(
            claimed,
            model="provider/model",
            output={"decision": "accept"},
            latency_ms=7,
            now=101,
        )

    assert state.list_decision_outbox()[0]["status"] == "leased"
    assert state._conn.execute(
        "SELECT COUNT(*) FROM verdicts"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settle",
    ("ack", "retry", "dead_letter"),
)
async def test_decision_settlement_rejects_expired_lease(
    tmp_path,
    settle,
) -> None:
    state = State(str(tmp_path / f"{settle}.db"))
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id=f"run-{settle}"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=5,
        now=100,
    )
    assert claimed is not None

    if settle == "ack":
        changed = state.ack_decision(claimed, now=105)
    elif settle == "retry":
        changed = state.retry_decision(
            claimed,
            error="retry",
            delay_s=1,
            now=105,
        )
    else:
        changed = state.dead_letter_decision(
            claimed,
            error="dead",
            now=105,
        )

    assert changed is False
    assert state.list_decision_outbox()[0]["status"] == "leased"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settle",
    ("commit", "ack", "retry", "dead_letter"),
)
async def test_decision_settlement_samples_time_after_write_lock(
    tmp_path,
    monkeypatch,
    settle,
) -> None:
    state = State(str(tmp_path / f"{settle}.db"))
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id=f"run-{settle}"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=5,
        now=100,
    )
    assert claimed is not None
    clock = [104.0]

    class AdvanceClockOnEnter:
        def __enter__(self):
            clock[0] = 106.0
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    state._write_lock = AdvanceClockOnEnter()
    monkeypatch.setattr(
        "supervisor.state.time.time",
        lambda: clock[0],
    )

    if settle == "commit":
        changed = state.commit_decision_verdict(
            claimed,
            model="provider/model",
            output={"decision": "accept"},
            latency_ms=7,
        )
        assert changed is None
    elif settle == "ack":
        assert state.ack_decision(claimed) is False
    elif settle == "retry":
        assert state.retry_decision(
            claimed,
            error="retry",
            delay_s=1,
        ) is False
    else:
        assert state.dead_letter_decision(
            claimed,
            error="dead",
        ) is False

    assert state.list_decision_outbox()[0]["status"] == "leased"
    assert state._conn.execute(
        "SELECT COUNT(*) FROM verdicts"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settle",
    ("commit", "ack", "retry", "dead_letter"),
)
async def test_decision_settlement_samples_time_after_database_write_lock(
    tmp_path,
    monkeypatch,
    settle,
) -> None:
    db_path = tmp_path / f"database-lock-{settle}.db"
    state = State(str(db_path))
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id=f"run-{settle}"),
        available_at=100,
    )
    claimed = state.claim_decision(
        worker_id="worker-a",
        lease_s=1,
        now=100,
    )
    assert claimed is not None

    blocker = sqlite3.connect(db_path, timeout=30)
    blocker.execute("BEGIN IMMEDIATE")
    entered_local_lock = threading.Event()
    database_released = threading.Event()
    result: list[object] = []
    errors: list[BaseException] = []
    real_lock = state._write_lock

    class SignalingLock:
        def __enter__(self):
            real_lock.acquire()
            entered_local_lock.set()
            return self

        def __exit__(self, exc_type, exc, traceback):
            real_lock.release()
            return False

    state._write_lock = SignalingLock()
    monkeypatch.setattr(
        "supervisor.state.time.time",
        lambda: 102.0 if database_released.is_set() else 100.0,
    )

    def settle_decision() -> None:
        try:
            if settle == "commit":
                result.append(
                    state.commit_decision_verdict(
                        claimed,
                        model="provider/model",
                        output={"decision": "accept"},
                        latency_ms=7,
                    )
                )
            elif settle == "ack":
                result.append(state.ack_decision(claimed))
            elif settle == "retry":
                result.append(
                    state.retry_decision(
                        claimed,
                        error="retry",
                        delay_s=1,
                    )
                )
            else:
                result.append(
                    state.dead_letter_decision(
                        claimed,
                        error="dead",
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=settle_decision)
    worker.start()
    assert entered_local_lock.wait(timeout=1)
    time.sleep(0.05)
    assert worker.is_alive()

    database_released.set()
    blocker.commit()
    blocker.close()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert errors == []
    assert result == [None if settle == "commit" else False]
    assert state.list_decision_outbox()[0]["status"] == "leased"
    assert state._conn.execute(
        "SELECT COUNT(*) FROM verdicts"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_decision_claim_starts_lease_after_database_write_lock(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "database-lock-claim.db"
    state = State(str(db_path))
    await state.enqueue_decision(
        Decision(kind="evaluate_run", run_id="run-claim"),
        available_at=100,
    )

    blocker = sqlite3.connect(db_path, timeout=30)
    blocker.execute("BEGIN IMMEDIATE")
    entered_local_lock = threading.Event()
    database_released = threading.Event()
    claimed: list[Decision | None] = []
    errors: list[BaseException] = []
    real_lock = state._write_lock

    class SignalingLock:
        def __enter__(self):
            real_lock.acquire()
            entered_local_lock.set()
            return self

        def __exit__(self, exc_type, exc, traceback):
            real_lock.release()
            return False

    state._write_lock = SignalingLock()
    monkeypatch.setattr(
        "supervisor.state.time.time",
        lambda: 102.0 if database_released.is_set() else 100.0,
    )

    def claim() -> None:
        try:
            claimed.append(
                state.claim_decision(
                    worker_id="worker-a",
                    lease_s=1,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=claim)
    worker.start()
    assert entered_local_lock.wait(timeout=1)
    time.sleep(0.05)
    assert worker.is_alive()

    database_released.set()
    blocker.commit()
    blocker.close()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert errors == []
    assert len(claimed) == 1
    assert claimed[0] is not None
    row = state.list_decision_outbox()[0]
    assert row["updated_at"] == 102.0
    assert row["lease_expires_at"] == 103.0
