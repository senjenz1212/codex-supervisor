from __future__ import annotations

import base64
import hashlib
import hmac
import threading
from pathlib import Path
from typing import Any, Mapping

import pytest

from supervisor.evidence_ledger import canonical_json_bytes
from supervisor.ledger_checkpoints import (
    MAX_CHECKPOINT_EVENT_INTERVAL,
    CheckpointPersistenceError,
    LedgerCheckpointCoordinator,
    LedgerCheckpointPolicy,
    LedgerCheckpointStore,
    normalize_checkpoint_identity,
)
from supervisor.state import State


class _ExternallyManagedTestKey:
    key_id = "external-test-checkpoint-key"
    algorithm = "hmac-sha256"

    def __init__(self) -> None:
        self._key = b"external-test-key-material-never-persist"
        self.sign_calls = 0

    def sign(self, payload: bytes) -> bytes:
        self.sign_calls += 1
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def verify(
        self,
        payload: bytes,
        signature: Mapping[str, Any],
    ) -> bool:
        expected = base64.b64encode(
            hmac.new(self._key, payload, hashlib.sha256).digest()
        ).decode("ascii")
        return (
            signature.get("key_id") == self.key_id
            and signature.get("algorithm") == self.algorithm
            and hmac.compare_digest(
                str(signature.get("signature") or ""),
                expected,
            )
        )


class _IndependentTrustedPins:
    """In-memory test double for a separately managed trusted pin service."""

    def __init__(self, *, fail_pin_calls: int = 0) -> None:
        self._history: dict[bytes, dict[str, Any]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.fail_pin_calls = fail_pin_calls

    def pin(self, identity: Mapping[str, Any]) -> None:
        normalized = normalize_checkpoint_identity(identity)
        with self._lock:
            if self.fail_pin_calls > 0:
                self.fail_pin_calls -= 1
                raise RuntimeError("simulated external pin outage")
            run_id = str(normalized["run_id"])
            current = self._latest.get(run_id)
            if current is not None:
                current_count = int(current["event_count"])
                new_count = int(normalized["event_count"])
                if new_count < current_count:
                    raise RuntimeError("trusted pin rollback")
                if (
                    new_count == current_count
                    and canonical_json_bytes(current)
                    != canonical_json_bytes(normalized)
                ):
                    raise RuntimeError("trusted pin fork")
            encoded = canonical_json_bytes(normalized)
            self._history[encoded] = dict(normalized)
            self._latest[run_id] = dict(normalized)

    def get(
        self,
        identity: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        normalized = normalize_checkpoint_identity(identity)
        with self._lock:
            observed = self._history.get(canonical_json_bytes(normalized))
            return None if observed is None else dict(observed)

    def latest(self, run_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            observed = self._latest.get(str(run_id))
            return None if observed is None else dict(observed)


def _authoritative_state(
    tmp_path: Path,
    *,
    interval: int,
    pins: _IndependentTrustedPins | None = None,
) -> tuple[
    State,
    _ExternallyManagedTestKey,
    LedgerCheckpointStore,
    _IndependentTrustedPins,
]:
    key = _ExternallyManagedTestKey()
    store = LedgerCheckpointStore(tmp_path / "external-checkpoints")
    trusted_pins = pins or _IndependentTrustedPins()
    coordinator = LedgerCheckpointCoordinator(
        signer=key,
        verifier=key,
        checkpoint_store=store,
        trusted_pin_store=trusted_pins,
        policy=LedgerCheckpointPolicy(
            max_events_between_checkpoints=interval,
        ),
    )
    state = State(
        str(tmp_path / "state.db"),
        ledger_checkpoint_coordinator=coordinator,
    )
    return state, key, store, trusted_pins


def test_state_write_event_checkpoints_every_bounded_interval(tmp_path):
    state, key, store, pins = _authoritative_state(
        tmp_path,
        interval=2,
    )
    assert state.event_ledger_assurance == "authoritative"

    state.write_event(
        run_id="periodic-run",
        source="test",
        kind="event_msg",
        payload={"index": 1},
        ts=101,
    )
    assert store.load_latest("periodic-run") is None
    assert pins.latest("periodic-run") is None

    state.write_event(
        run_id="periodic-run",
        source="test",
        kind="event_msg",
        payload={"index": 2},
        ts=102,
    )
    first_pin = pins.latest("periodic-run")
    assert first_pin is not None
    assert first_pin["event_count"] == 2

    state.write_event(
        run_id="periodic-run",
        source="test",
        kind="event_msg",
        payload={"index": 3},
        ts=103,
    )
    assert pins.latest("periodic-run") == first_pin
    bounded_suffix = state.verify_event_ledger("periodic-run")
    assert bounded_suffix.valid is False
    assert bounded_suffix.failure_code == "checkpoint_event_count_mismatch"

    state.write_event(
        run_id="periodic-run",
        source="test",
        kind="event_msg",
        payload={"index": 4},
        ts=104,
    )

    latest = pins.latest("periodic-run")
    assert latest is not None
    assert latest["event_count"] == 4
    assert key.sign_calls == 2
    verification = state.verify_event_ledger("periodic-run")
    assert verification.valid is True
    assert verification.authoritative_head_verified is True


def test_state_write_event_forces_terminal_run_checkpoint(tmp_path):
    state, key, _store, pins = _authoritative_state(
        tmp_path,
        interval=100,
    )
    state.write_event(
        run_id="terminal-run",
        source="runtime",
        kind="event_msg",
        payload={"phase": "running"},
        ts=201,
    )

    state.write_event(
        run_id="terminal-run",
        source="runtime",
        kind="run.completed",
        payload={"type": "run.completed"},
        ts=202,
    )

    latest = pins.latest("terminal-run")
    assert latest is not None
    assert latest["event_count"] == 2
    assert key.sign_calls == 1
    payloads = [
        row["payload_json"]
        for row in state._conn.execute(
            "SELECT payload_json FROM events WHERE run_id=?",
            ("terminal-run",),
        )
    ]
    assert all("external-test-key-material-never-persist" not in item for item in payloads)


@pytest.mark.parametrize(
    "terminal_kind",
    (
        "historical_operation.completed",
        "historical_operation.failed",
    ),
)
def test_historical_operation_terminal_event_forces_checkpoint(
    tmp_path,
    terminal_kind,
):
    state, key, _store, pins = _authoritative_state(
        tmp_path,
        interval=100,
    )
    operation_id = "historical-operation"
    request_hash = "a" * 64
    owner_token = "checkpoint-owner"
    claim, reserved = state.reserve_historical_operation(
        operation_id=operation_id,
        request_hash=request_hash,
        operation="replay",
    )
    assert reserved is True
    claimed, requested_event_id, acquired = (
        state.claim_historical_operation_execution(
            operation_id=operation_id,
            request_hash=request_hash,
            operation="replay",
            request={"operation": "replay"},
            owner_token=owner_token,
            expected_claim_updated_at=claim["updated_at"],
            expected_execution_owner_token=claim[
                "execution_owner_token"
            ],
            expected_execution_generation=claim[
                "execution_generation"
            ],
            expected_execution_heartbeat_at=claim[
                "execution_heartbeat_at"
            ],
        )
    )
    assert acquired is True
    assert requested_event_id is not None
    terminal_status = terminal_kind.rsplit(".", 1)[-1]
    event_id, created = state.terminalize_historical_operation_execution(
        operation_id=operation_id,
        request_hash=request_hash,
        operation="replay",
        owner_token=owner_token,
        execution_generation=claimed["execution_generation"],
        status=terminal_status,
        payload={
            "operation_id": operation_id,
            "operation": "replay",
            "request_hash": request_hash,
            "requested_event_id": requested_event_id,
            "execution_owner_token": owner_token,
            "execution_generation": claimed["execution_generation"],
        },
    )
    assert event_id is not None
    assert created is True

    latest = pins.latest(operation_id)
    assert latest is not None
    assert latest["head_event_id"] == event_id
    assert latest["event_count"] == 2
    assert key.sign_calls == 1
    assert state.verify_event_ledger(operation_id).valid is True


def test_terminal_workflow_completion_forces_and_retries_pin_publication(
    tmp_path,
):
    pins = _IndependentTrustedPins(fail_pin_calls=1)
    state, key, store, _pins = _authoritative_state(
        tmp_path,
        interval=100,
        pins=pins,
    )
    state.upsert_dual_agent_workflow_job(
        job_id="terminal-job",
        run_id="workflow-run",
        task_id="workflow-task",
        cwd=str(tmp_path),
        status="running",
        request_path=str(tmp_path / "request.json"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "worker.log"),
    )
    outcome = {
        "job_id": "terminal-job",
        "run_id": "workflow-run",
        "task_id": "workflow-task",
        "status": "accepted",
    }

    with pytest.raises(
        CheckpointPersistenceError,
        match="trusted_pin_persistence",
    ):
        state.complete_dual_agent_workflow_job(
            job_id="terminal-job",
            status="accepted",
            terminal_outcome=outcome,
        )

    assert state.get_dual_agent_workflow_job(
        job_id="terminal-job"
    )["terminal_outcome_json"] is not None
    assert store.load_latest("workflow-run") is not None
    assert pins.latest("workflow-run") is None
    failed_closed = state.verify_event_ledger("workflow-run")
    assert failed_closed.valid is False
    assert failed_closed.authoritative_head_verified is False

    assert state.complete_dual_agent_workflow_job(
        job_id="terminal-job",
        status="accepted",
        terminal_outcome=outcome,
    ) == 0

    latest = pins.latest("workflow-run")
    assert latest is not None
    assert latest["event_count"] == 1
    assert key.sign_calls == 1
    verification = state.verify_event_ledger("workflow-run")
    assert verification.valid is True
    assert verification.authoritative_head_verified is True


def test_write_event_once_retry_publishes_checkpoint_after_pin_failure(
    tmp_path,
):
    pins = _IndependentTrustedPins(fail_pin_calls=1)
    state, key, store, _pins = _authoritative_state(
        tmp_path,
        interval=1,
        pins=pins,
    )
    kwargs = {
        "run_id": "idempotent-checkpoint-retry",
        "source": "test",
        "kind": "event_msg",
        "payload": {"value": "durable-before-pin"},
        "idempotency_key": "event-msg:durable-before-pin",
        "ts": 240,
    }

    with pytest.raises(
        CheckpointPersistenceError,
        match="trusted_pin_persistence",
    ):
        state.write_event_once(**kwargs)

    events_after_failure = state.read_events_since(
        "idempotent-checkpoint-retry"
    )
    assert len(events_after_failure) == 1
    event_id = events_after_failure[0]["event_id"]
    assert store.load_latest("idempotent-checkpoint-retry") is not None
    assert pins.latest("idempotent-checkpoint-retry") is None

    assert state.write_event_once(**kwargs) == event_id

    assert len(
        state.read_events_since("idempotent-checkpoint-retry")
    ) == 1
    latest = pins.latest("idempotent-checkpoint-retry")
    assert latest is not None
    assert latest["head_event_id"] == event_id
    assert latest["event_count"] == 1
    assert key.sign_calls == 1


def test_state_startup_reconciles_commit_before_checkpoint_failure(tmp_path):
    pins = _IndependentTrustedPins(fail_pin_calls=1)
    state, _key, store, _pins = _authoritative_state(
        tmp_path,
        interval=1,
        pins=pins,
    )

    with pytest.raises(
        CheckpointPersistenceError,
        match="trusted_pin_persistence",
    ):
        state.write_event(
            run_id="startup-recovery",
            source="runtime",
            kind="run.completed",
            payload={"status": "completed"},
            ts=250,
        )

    assert state.read_events_since("startup-recovery")[0]["event_id"] == 1
    assert store.load_latest("startup-recovery") is not None
    assert pins.latest("startup-recovery") is None
    state._conn.close()

    recovered_key = _ExternallyManagedTestKey()
    recovered = State(
        str(tmp_path / "state.db"),
        ledger_checkpoint_coordinator=LedgerCheckpointCoordinator(
            signer=recovered_key,
            verifier=recovered_key,
            checkpoint_store=store,
            trusted_pin_store=pins,
            policy=LedgerCheckpointPolicy(
                max_events_between_checkpoints=1,
            ),
        ),
    )

    latest = pins.latest("startup-recovery")
    assert latest is not None
    assert latest["event_count"] == 1
    verification = recovered.verify_event_ledger("startup-recovery")
    assert verification.valid is True
    assert verification.authoritative_head_verified is True


def _recording_coordinator(key, store, pins, *, interval=1):
    coordinator = LedgerCheckpointCoordinator(
        signer=key,
        verifier=key,
        checkpoint_store=store,
        trusted_pin_store=pins,
        policy=LedgerCheckpointPolicy(
            max_events_between_checkpoints=interval,
        ),
    )
    calls: list[tuple[str, int]] = []
    original = coordinator.coordinate_event

    def recording(**kwargs):
        calls.append((kwargs["run_id"], kwargs["event_count"]))
        return original(**kwargs)

    coordinator.coordinate_event = recording
    return coordinator, calls


def test_startup_reconcile_skips_events_covered_by_trusted_head(tmp_path):
    pins = _IndependentTrustedPins()
    state, _key, store, _pins = _authoritative_state(
        tmp_path,
        interval=1,
        pins=pins,
    )
    for index in range(3):
        state.write_event(
            run_id="covered-run",
            source="test",
            kind="event_msg",
            payload={"index": index},
            ts=100 + index,
        )
    assert pins.latest("covered-run")["event_count"] == 3
    state._conn.close()

    coordinator, calls = _recording_coordinator(
        _ExternallyManagedTestKey(),
        store,
        pins,
    )
    recovered = State(
        str(tmp_path / "state.db"),
        ledger_checkpoint_coordinator=coordinator,
    )

    assert calls == []
    assert recovered.reconcile_event_checkpoints() == 0
    verification = recovered.verify_event_ledger("covered-run")
    assert verification.valid is True
    assert verification.authoritative_head_verified is True


def test_startup_reconcile_replays_only_events_beyond_trusted_head(tmp_path):
    pins = _IndependentTrustedPins()
    state, _key, store, _pins = _authoritative_state(
        tmp_path,
        interval=1,
        pins=pins,
    )
    state.write_event(
        run_id="partial-run",
        source="test",
        kind="event_msg",
        payload={"index": 0},
        ts=100,
    )
    pins.fail_pin_calls = 1
    with pytest.raises(
        CheckpointPersistenceError,
        match="trusted_pin_persistence",
    ):
        state.write_event(
            run_id="partial-run",
            source="test",
            kind="event_msg",
            payload={"index": 1},
            ts=101,
        )
    assert pins.latest("partial-run")["event_count"] == 1
    state._conn.close()

    coordinator, calls = _recording_coordinator(
        _ExternallyManagedTestKey(),
        store,
        pins,
    )
    recovered = State(
        str(tmp_path / "state.db"),
        ledger_checkpoint_coordinator=coordinator,
    )

    assert calls == [("partial-run", 2)]
    assert pins.latest("partial-run")["event_count"] == 2
    verification = recovered.verify_event_ledger("partial-run")
    assert verification.valid is True
    assert verification.authoritative_head_verified is True


def test_checkpoint_store_failure_leaves_authoritative_verification_closed(
    monkeypatch,
    tmp_path,
):
    state, _key, store, pins = _authoritative_state(
        tmp_path,
        interval=1,
    )

    def fail_checkpoint(**_kwargs):
        raise OSError("simulated checkpoint service outage")

    monkeypatch.setattr(store, "append_signed_head", fail_checkpoint)
    with pytest.raises(
        CheckpointPersistenceError,
        match="checkpoint_signing_or_persistence",
    ):
        state.write_event(
            run_id="failed-checkpoint-run",
            source="test",
            kind="event_msg",
            payload={"value": "durable-before-anchor"},
            ts=301,
        )

    assert state.latest_event_id("failed-checkpoint-run") > 0
    assert pins.latest("failed-checkpoint-run") is None
    verification = state.verify_event_ledger("failed-checkpoint-run")
    assert verification.valid is False
    assert verification.authoritative_head_verified is False
    diagnostic = state.verify_event_ledger_structure(
        "failed-checkpoint-run"
    )
    assert diagnostic.valid is True
    assert diagnostic.authoritative_head_verified is False


def test_unconfigured_state_is_explicitly_diagnostic_only(tmp_path):
    state = State(str(tmp_path / "state.db"))
    assert state.event_ledger_assurance == "diagnostic-only"
    state.write_event(
        run_id="diagnostic-run",
        source="test",
        kind="event_msg",
        payload={"value": 1},
    )

    release = state.verify_event_ledger("diagnostic-run")
    diagnostic = state.verify_event_ledger_structure("diagnostic-run")

    assert release.valid is False
    assert release.failure_code == "trusted_head_required"
    assert diagnostic.valid is True
    assert diagnostic.authoritative_head_verified is False


@pytest.mark.parametrize(
    "interval",
    (True, 0, -1, MAX_CHECKPOINT_EVENT_INTERVAL + 1),
)
def test_checkpoint_policy_requires_a_bounded_positive_interval(interval):
    with pytest.raises(ValueError, match="max_events_between_checkpoints"):
        LedgerCheckpointPolicy(max_events_between_checkpoints=interval)
