from __future__ import annotations

import copy
import json

import pytest

from supervisor.evidence_ledger import (
    ArtifactIntegrityError,
    ContentAddressedArtifactStore,
    canonical_payload_hash,
    create_ledger_checkpoint,
    verify_event_chain,
    verify_event_chain_structure,
)
from supervisor.postgres_state import (
    POSTGRES_EVENT_IMMUTABILITY_SQL,
    POSTGRES_SCHEMA_SQL,
)
from supervisor.state import State


def test_sqlite_event_chain_hashes_canonical_stamped_redacted_payload(tmp_path):
    state = State(str(tmp_path / "state.db"))

    first_id = state.write_event(
        run_id="ledger-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": "ledger-task",
            "status": "accepted",
            "secret": "sk-super-secret",
        },
    )
    second_id = state.write_event(
        run_id="ledger-run",
        source="rollout",
        kind="event_msg",
        payload={"type": "follow-up"},
    )

    assert isinstance(first_id, int)
    assert isinstance(second_id, int)
    events = state.read_events_since("ledger-run", after_event_id=0, limit=10)
    assert events[0]["payload"]["secret"] == "[REDACTED_API_KEY]"
    assert events[0]["payload"]["trace_envelope"]["run_id"] == "ledger-run"
    assert events[0]["canonical_payload_hash"] == canonical_payload_hash(
        events[0]["payload"]
    )
    assert events[0]["ledger_genesis_kind"] == "native"
    assert events[0]["previous_event_hash"] is None
    assert events[1]["ledger_genesis_kind"] is None
    assert events[1]["previous_event_hash"] == events[0]["event_hash"]
    assert json.loads(state.get_event(run_id="ledger-run", event_id=first_id)["payload_json"])[
        "secret"
    ] == "[REDACTED_API_KEY]"

    structural = state.verify_event_ledger_structure("ledger-run")
    release = state.verify_event_ledger("ledger-run")

    assert structural.valid is True
    assert structural.head_event_hash == events[-1]["event_hash"]
    assert structural.truncation_checked is False
    assert release.valid is False
    assert release.failure_code == "trusted_head_required"
    assert release.truncation_checked is False


def test_sqlite_event_hash_authenticates_contiguous_per_run_sequence(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.write_event(
        run_id="run-a",
        source="test",
        kind="event_msg",
        payload={"index": 1},
        ts=101,
    )
    state.write_event(
        run_id="run-b",
        source="test",
        kind="event_msg",
        payload={"index": 1},
        ts=102,
    )
    state.write_event(
        run_id="run-a",
        source="test",
        kind="event_msg",
        payload={"index": 2},
        ts=103,
    )

    run_a = state.read_events_since("run-a", after_event_id=0, limit=10)
    run_b = state.read_events_since("run-b", after_event_id=0, limit=10)

    assert [event["event_id"] for event in run_a] == [1, 3]
    assert [event["event_sequence"] for event in run_a] == [1, 2]
    assert [event["event_sequence"] for event in run_b] == [1]
    edited = copy.deepcopy(run_a)
    edited[1]["event_sequence"] = 3
    verification = verify_event_chain_structure(
        edited,
        expected_run_id="run-a",
    )
    assert verification.valid is False
    assert verification.failure_code == "event_sequence_gap"


def test_event_chain_detects_content_edit_deletion_reordering_and_truncation(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        state.write_event(
            run_id="ledger-run",
            source="test",
            kind="event_msg",
            payload={"index": index},
            ts=100 + index,
        )
    events = state.read_events_since("ledger-run", after_event_id=0, limit=10)
    pinned_head = events[-1]["event_hash"]

    edited = copy.deepcopy(events)
    edited[1]["payload"]["index"] = 999
    assert verify_event_chain_structure(
        edited,
        expected_run_id="ledger-run",
    ).failure_code == "canonical_payload_hash_mismatch"

    deleted = [events[0], events[2]]
    assert verify_event_chain_structure(
        deleted,
        expected_run_id="ledger-run",
    ).failure_code == "event_sequence_gap"

    reordered = [events[1], events[0], events[2]]
    assert verify_event_chain_structure(
        reordered,
        expected_run_id="ledger-run",
    ).failure_code == "event_sequence_gap"

    truncated = verify_event_chain(
        events[:-1],
        expected_run_id="ledger-run",
        expected_head_hash=pinned_head,
    )
    matched_raw_head = verify_event_chain(
        events,
        expected_run_id="ledger-run",
        expected_head_hash=pinned_head,
    )
    assert truncated.valid is False
    assert truncated.failure_code == "expected_head_hash_mismatch"
    assert truncated.truncation_checked is True
    assert truncated.authoritative_head_verified is False
    assert matched_raw_head.valid is True
    assert matched_raw_head.truncation_checked is True
    assert matched_raw_head.authoritative_head_verified is False
    assert matched_raw_head.external_anchor_ref is None


def test_release_chain_verification_rejects_unpinned_valid_prefix(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        state.write_event(
            run_id="release-run",
            source="test",
            kind="event_msg",
            payload={"index": index},
            ts=100 + index,
        )
    prefix = state.read_events_since(
        "release-run",
        after_event_id=0,
        limit=2,
    )

    structural = verify_event_chain_structure(
        prefix,
        expected_run_id="release-run",
    )
    release = verify_event_chain(
        prefix,
        expected_run_id="release-run",
    )

    assert structural.valid is True
    assert structural.truncation_checked is False
    assert release.valid is False
    assert release.failure_code == "trusted_head_required"
    assert release.truncation_checked is False


def test_content_addressed_artifacts_verify_bytes_and_reject_tamper(tmp_path):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    descriptor = store.put_bytes(
        b"immutable evidence",
        name="evidence.txt",
        media_type="text/plain",
    )
    manifest = store.create_manifest([descriptor], metadata={"run_id": "ledger-run"})

    assert store.verify_manifest(manifest) is True

    digest = descriptor["digest"]["sha256"]
    artifact_path = (
        tmp_path / "artifacts" / "sha256" / digest[:2] / digest
    )
    artifact_path.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError, match="does not match digest"):
        store.verify_manifest(manifest)


def test_ledger_checkpoint_is_signed_and_externally_anchorable():
    class Signer:
        key_id = "test-key"
        algorithm = "test-sha256"

        def sign(self, payload: bytes) -> bytes:
            return b"signed:" + payload[:8]

    checkpoint = create_ledger_checkpoint(
        run_id="ledger-run",
        head_event_id=7,
        head_event_hash="a" * 64,
        event_count=7,
        external_anchor_ref="file:///immutable/checkpoints/ledger-run.json",
        signer=Signer(),
        created_at=1234,
    )

    assert checkpoint["_type"] == "https://in-toto.io/Statement/v1"
    assert checkpoint["subject"][0]["digest"]["sha256"] == "a" * 64
    assert checkpoint["predicate"]["external_anchor_ref"].startswith("file://")
    assert checkpoint["signatures"] == [{
        "key_id": "test-key",
        "algorithm": "test-sha256",
        "signature": "c2lnbmVkOnsiX3R5cGUi",
    }]
    assert len(checkpoint["signing_payload_sha256"]) == 64


def test_sqlite_and_postgres_event_schemas_share_ledger_fields_and_guards(tmp_path):
    state = State(str(tmp_path / "state.db"))
    sqlite_columns = {
        row["name"] for row in state._conn.execute("PRAGMA table_info(events)")
    }
    required = {
        "event_sequence",
        "previous_event_hash",
        "event_hash",
        "canonical_payload_hash",
        "artifact_manifest_hash",
        "ledger_genesis_kind",
    }

    assert required <= sqlite_columns
    for column in required:
        assert column in POSTGRES_SCHEMA_SQL
    sqlite_triggers = {
        row["name"]
        for row in state._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert {"events_no_update", "events_no_delete"} <= sqlite_triggers
    assert "events_run_sequence_unique" in POSTGRES_EVENT_IMMUTABILITY_SQL
    assert "events_sequence_positive" in POSTGRES_EVENT_IMMUTABILITY_SQL
    assert "CREATE TRIGGER events_no_update" in POSTGRES_EVENT_IMMUTABILITY_SQL
    assert "CREATE TRIGGER events_no_delete" in POSTGRES_EVENT_IMMUTABILITY_SQL
    assert "CREATE TRIGGER events_no_truncate" in POSTGRES_EVENT_IMMUTABILITY_SQL
