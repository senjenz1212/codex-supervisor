from __future__ import annotations

import base64
import copy
import errno
import hashlib
import hmac
import multiprocessing
import os
import stat

import pytest

import supervisor.evidence_ledger as evidence_ledger_module
import supervisor.ledger_checkpoints as ledger_checkpoints_module
from supervisor.evidence_ledger import (
    ArtifactIntegrityError,
    ContentAddressedArtifactStore,
    NATIVE_GENESIS,
    artifact_manifest_hash,
    build_ledger_fields,
    canonical_json_bytes,
    create_ledger_checkpoint,
    rebuild_projection,
    sha256_hex,
    verify_event_chain_structure,
)
from supervisor.ledger_checkpoints import (
    CheckpointIntegrityError,
    FilesystemTrustedCheckpointPinStore,
    LedgerCheckpointStore,
    checkpoint_identity,
    verify_authoritative_event_chain,
)
from supervisor.quality_projection import (
    QUALITY_TREND_PROJECTION_EVENT,
    quality_trend_projection_event_payload,
)
from supervisor.state import State


class HmacCheckpointKey:
    algorithm = "hmac-sha256"

    def __init__(
        self,
        key: bytes = b"ledger-checkpoint-test-key",
        *,
        key_id: str = "ledger-test-key",
        provider_id: str = "ledger-test-provider",
    ) -> None:
        self._key = key
        self.key_id = key_id
        self.provider_id = provider_id

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def verify(self, payload: bytes, signature: dict[str, object]) -> bool:
        expected = base64.b64encode(self.sign(payload)).decode("ascii")
        return (
            signature.get("key_id") == self.key_id
            and signature.get("algorithm") == self.algorithm
            and hmac.compare_digest(str(signature.get("signature") or ""), expected)
        )


class HmacCheckpointKeyring:
    def __init__(self, *keys: HmacCheckpointKey) -> None:
        self._keys = {key.key_id: key for key in keys}

    def verify(self, payload: bytes, signature: dict[str, object]) -> bool:
        key = self._keys.get(str(signature.get("key_id") or ""))
        return key is not None and key.verify(payload, signature)


def _authoritative_projection_arguments(
    state: State,
    tmp_path,
    *run_ids: str,
):
    key = HmacCheckpointKey()
    checkpoints = LedgerCheckpointStore(tmp_path / "projection-checkpoints")
    pin_store = FilesystemTrustedCheckpointPinStore(
        tmp_path / "projection-trusted-pins"
    )
    trusted: dict[str, dict[str, object]] = {}
    for run_id in run_ids:
        persisted = state.checkpoint_event_ledger(
            run_id,
            checkpoint_store=checkpoints,
            signer=key,
            verifier=key,
            created_at=1234,
        )
        identity = checkpoint_identity(persisted.checkpoint)
        pin_store.pin(identity)
        trusted[run_id] = identity
    return {
        "checkpoint_store": checkpoints,
        "verifier": key,
        "trusted_checkpoint_pins": trusted,
    }


def _events(count: int, *, run_id: str = "ledger-run") -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    previous_hash: str | None = None
    for index in range(count):
        payload = {"index": index}
        fields = build_ledger_fields(
            run_id=run_id,
            event_sequence=index + 1,
            ts=100 + index,
            source="test",
            kind="event_msg",
            payload=payload,
            previous_event_hash=previous_hash,
            ledger_genesis_kind=NATIVE_GENESIS if index == 0 else None,
        )
        events.append(
            {
                "event_id": index + 1,
                "event_sequence": index + 1,
                "run_id": run_id,
                "ts": 100 + index,
                "source": "test",
                "kind": "event_msg",
                "payload": payload,
                "previous_event_hash": fields.previous_event_hash,
                "event_hash": fields.event_hash,
                "canonical_payload_hash": fields.canonical_payload_hash,
                "artifact_manifest_hash": fields.artifact_manifest_hash,
                "ledger_genesis_kind": fields.ledger_genesis_kind,
            }
        )
        previous_hash = fields.event_hash
    return events


def _append_checkpoint(
    store: LedgerCheckpointStore,
    events: list[dict[str, object]],
    key: HmacCheckpointKey,
    *,
    created_at: int = 1234,
    verifier=None,
):
    head = events[-1]
    verification = verify_event_chain_structure(
        events,
        expected_run_id=str(head["run_id"]),
    )
    assert verification.valid is True
    assert verification.head_event_identity_hash is not None
    return store.append_signed_head(
        run_id=str(head["run_id"]),
        head_event_id=head["event_id"],
        head_event_hash=str(head["event_hash"]),
        event_count=len(events),
        event_identity_hash=verification.head_event_identity_hash,
        signer=key,
        signer_provider_id=key.provider_id,
        verifier=verifier or key,
        created_at=created_at,
    )


def _checkpoint_for_head(
    store: LedgerCheckpointStore,
    *,
    run_id: str,
    head_event_id: int,
    head_event_hash: str,
    event_count: int,
    created_at: int,
) -> dict[str, object]:
    key = HmacCheckpointKey()
    return create_ledger_checkpoint(
        run_id=run_id,
        head_event_id=head_event_id,
        head_event_hash=head_event_hash,
        event_count=event_count,
        external_anchor_ref=store.external_anchor_ref(
            run_id=run_id,
            event_count=event_count,
            head_event_hash=head_event_hash,
        ),
        signer=key,
        created_at=created_at,
    )


def _publish_pin_in_process(root, identity, barrier, results) -> None:
    import time

    original_append = ledger_checkpoints_module._append_only_file_at

    def delayed_append(*args, **kwargs):
        time.sleep(0.25)
        return original_append(*args, **kwargs)

    ledger_checkpoints_module._append_only_file_at = delayed_append
    store = FilesystemTrustedCheckpointPinStore(root)
    barrier.wait(timeout=10)
    try:
        store.pin(identity)
    except BaseException as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(("ok", "", ""))


def _publish_checkpoint_in_process(root, checkpoint, barrier, results) -> None:
    import time

    original_append = ledger_checkpoints_module._append_only_file_at

    def delayed_append(*args, **kwargs):
        time.sleep(0.25)
        return original_append(*args, **kwargs)

    ledger_checkpoints_module._append_only_file_at = delayed_append
    store = LedgerCheckpointStore(root)
    barrier.wait(timeout=10)
    try:
        store.append(checkpoint, verifier=HmacCheckpointKey())
    except BaseException as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    else:
        results.put(("ok", "", ""))


def _die_after_checkpoint_final_link(root, checkpoint) -> None:
    store = LedgerCheckpointStore(root)
    original_link = evidence_ledger_module.os.link

    def link_then_die(*args, **kwargs):
        original_link(*args, **kwargs)
        os._exit(86)

    evidence_ledger_module.os.link = link_then_die
    store.append(checkpoint, verifier=HmacCheckpointKey())


def _run_two_processes(target, root, first, second):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=target,
            args=(str(root), value, barrier, results),
        )
        for value in (first, second)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        return [results.get(timeout=5), results.get(timeout=5)]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()


def test_artifact_store_rejects_symlinked_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "artifacts"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactIntegrityError, match="symlink"):
        ContentAddressedArtifactStore(root)


def test_artifact_store_rejects_symlinked_sha256_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "sha256").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactIntegrityError, match="symlink"):
        ContentAddressedArtifactStore(root)


def test_artifact_store_rejects_symlinked_digest_parent_on_write(tmp_path):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    value = b"must remain contained"
    digest = sha256_hex(value)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "sha256" / digest[:2]).symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ArtifactIntegrityError, match="symlink"):
        store.put_bytes(value)

    assert not (outside / digest).exists()


def test_artifact_store_rejects_symlinked_artifact_on_read(tmp_path):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    descriptor = store.put_bytes(b"correct bytes")
    digest = str(descriptor["digest"]["sha256"])
    artifact_path = store.root / "sha256" / digest[:2] / digest
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"correct bytes")
    artifact_path.unlink()
    artifact_path.symlink_to(outside)

    with pytest.raises(ArtifactIntegrityError, match="symlink"):
        store.read_bytes(digest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("size", 999, "size"),
        ("uri", "cas:sha256:" + ("0" * 64), "URI"),
        ("media_type", "Text/Plain", "media type"),
        ("name", "../evidence.txt", "name"),
    ),
)
def test_artifact_manifest_rejects_noncanonical_or_false_descriptors(
    tmp_path,
    field,
    value,
    message,
):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    descriptor = store.put_bytes(
        b"immutable evidence",
        name="evidence.txt",
        media_type="text/plain",
    )
    manifest = store.create_manifest([descriptor])
    invalid = copy.deepcopy(manifest)
    invalid["artifacts"][0][field] = value
    invalid["manifest_hash"] = artifact_manifest_hash(invalid)

    with pytest.raises(ArtifactIntegrityError, match=message):
        store.verify_manifest(invalid)


def test_artifact_manifest_rejects_duplicate_descriptors(tmp_path):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    descriptor = store.put_bytes(
        b"immutable evidence",
        name="evidence.txt",
        media_type="text/plain",
    )
    manifest = store.create_manifest([descriptor])
    duplicate = copy.deepcopy(manifest)
    duplicate["artifacts"].append(copy.deepcopy(descriptor))
    duplicate["manifest_hash"] = artifact_manifest_hash(duplicate)

    with pytest.raises(ArtifactIntegrityError, match="duplicate"):
        store.verify_manifest(duplicate)


@pytest.mark.parametrize(
    ("field", "value", "failure_code"),
    (
        ("event_id", "1", "invalid_event_id"),
        ("event_id", 1.0, "invalid_event_id"),
        ("event_id", True, "invalid_event_id"),
        ("event_sequence", "1", "invalid_event_sequence"),
        ("event_sequence", 1.0, "invalid_event_sequence"),
        ("event_sequence", True, "invalid_event_sequence"),
        ("ts", "100", "invalid_event_timestamp"),
        ("ts", 100.0, "invalid_event_timestamp"),
        ("ts", True, "invalid_event_timestamp"),
    ),
)
def test_event_verification_rejects_nonexact_integer_types(
    field,
    value,
    failure_code,
):
    [event] = _events(1)
    event[field] = value

    result = verify_event_chain_structure(
        [event],
        expected_run_id="ledger-run",
    )

    assert result.valid is False
    assert result.failure_code == failure_code


@pytest.mark.parametrize(
    ("event_index", "field", "mutate", "failure_code"),
    (
        (1, "previous_event_hash", str.upper, "invalid_previous_event_hash"),
        (0, "event_hash", str.upper, "invalid_event_hash"),
        (
            0,
            "canonical_payload_hash",
            str.upper,
            "invalid_canonical_payload_hash",
        ),
        (
            0,
            "artifact_manifest_hash",
            str.upper,
            "invalid_artifact_manifest_hash",
        ),
    ),
)
def test_event_verification_rejects_noncanonical_hash_text(
    event_index,
    field,
    mutate,
    failure_code,
):
    events = _events(2)
    events[event_index][field] = mutate(str(events[event_index][field]))

    result = verify_event_chain_structure(
        events,
        expected_run_id="ledger-run",
    )

    assert result.valid is False
    assert result.failure_code == failure_code


@pytest.mark.parametrize(
    ("event_index", "value", "failure_code"),
    (
        (0, " native ", "invalid_genesis_kind"),
        (1, "", "invalid_genesis_kind"),
        (1, "legacy-import", "unexpected_genesis_marker"),
    ),
)
def test_event_verification_rejects_nonexact_genesis_values(
    event_index,
    value,
    failure_code,
):
    events = _events(2)
    events[event_index]["ledger_genesis_kind"] = value

    result = verify_event_chain_structure(
        events,
        expected_run_id="ledger-run",
    )

    assert result.valid is False
    assert result.failure_code == failure_code


def test_event_verification_requires_canonical_payload_json():
    [event] = _events(1)
    event.pop("payload")
    event["payload_json"] = '{ "index": 0 }'

    result = verify_event_chain_structure(
        [event],
        expected_run_id="ledger-run",
    )

    assert result.valid is False
    assert result.failure_code == "noncanonical_payload_json"

    event["payload_json"] = '{"index":0}'
    assert verify_event_chain_structure(
        [event],
        expected_run_id="ledger-run",
    ).valid is True


def test_noncanonical_event_row_is_rejected_before_event_hashing(monkeypatch):
    [event] = _events(1)
    event["event_sequence"] = "1"
    calls = 0

    def fail_if_hashed(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("noncanonical rows must fail before hashing")

    monkeypatch.setattr(
        evidence_ledger_module,
        "compute_event_hash",
        fail_if_hashed,
    )

    result = verify_event_chain_structure(
        [event],
        expected_run_id="ledger-run",
    )

    assert result.failure_code == "invalid_event_sequence"
    assert calls == 0


def test_authoritative_verification_detects_order_preserving_event_id_mutation(
    tmp_path,
):
    events = _events(3)
    for event, event_id in zip(events, (10, 20, 30)):
        event["event_id"] = event_id
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)
    mutated = copy.deepcopy(events)
    mutated[1]["event_id"] = 21

    assert verify_event_chain_structure(
        mutated,
        expected_run_id="ledger-run",
    ).valid is True
    result = verify_authoritative_event_chain(
        mutated,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=checkpoint_identity(
            persisted.checkpoint
        ),
    )

    assert result.valid is False
    assert result.failure_code == "expected_event_identity_hash_mismatch"


def test_authoritative_verification_fails_closed_without_checkpoint(tmp_path):
    events = _events(3)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)
    trusted_latest = checkpoint_identity(persisted.checkpoint)
    persisted.path.unlink()

    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )

    assert result.valid is False
    assert result.failure_code == "checkpoint_rollback_detected"
    assert result.truncation_checked is True


def test_authoritative_verification_requires_trusted_latest_pin(tmp_path):
    events = _events(3)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    _append_checkpoint(store, events, key)

    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
    )

    assert result.valid is False
    assert result.failure_code == "trusted_checkpoint_required"
    assert result.truncation_checked is False


def test_authoritative_verification_accepts_valid_anchored_chain(tmp_path):
    events = _events(3)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)

    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=checkpoint_identity(
            persisted.checkpoint
        ),
    )

    assert persisted.path.is_file()
    assert persisted.external_anchor_ref == persisted.path.as_uri()
    assert result.valid is True
    assert result.event_count == 3
    assert result.head_event_id == 3
    assert result.head_event_hash == events[-1]["event_hash"]
    assert result.truncation_checked is True


def test_authoritative_verification_detects_truncated_tail(tmp_path):
    events = _events(3)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)

    result = verify_authoritative_event_chain(
        events[:-1],
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=checkpoint_identity(
            persisted.checkpoint
        ),
    )

    assert result.valid is False
    assert result.failure_code == "checkpoint_event_count_mismatch"
    assert result.truncation_checked is True


def test_authoritative_verification_detects_joint_ledger_checkpoint_rollback(
    tmp_path,
):
    events = _events(3)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    _append_checkpoint(store, events[:2], key, created_at=100)
    latest = _append_checkpoint(store, events, key, created_at=101)
    trusted_latest = checkpoint_identity(latest.checkpoint)
    latest.path.unlink()

    unpinned = verify_authoritative_event_chain(
        events[:2],
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
    )
    assert unpinned.valid is False
    assert unpinned.failure_code == "trusted_checkpoint_required"

    result = verify_authoritative_event_chain(
        events[:2],
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )

    assert result.valid is False
    assert result.failure_code == "checkpoint_rollback_detected"
    assert result.expected_head_hash == events[-1]["event_hash"]
    assert result.truncation_checked is True


def test_filesystem_trusted_pin_store_rejects_rollback_and_fork(tmp_path):
    events = _events(3)
    key = HmacCheckpointKey()
    checkpoints = LedgerCheckpointStore(tmp_path / "checkpoints")
    first = _append_checkpoint(
        checkpoints,
        events[:2],
        key,
        created_at=100,
    )
    latest = _append_checkpoint(
        checkpoints,
        events,
        key,
        created_at=101,
    )
    pins = FilesystemTrustedCheckpointPinStore(tmp_path / "trusted-pins")
    first_identity = checkpoint_identity(first.checkpoint)
    latest_identity = checkpoint_identity(latest.checkpoint)

    pins.pin(first_identity)
    pins.pin(latest_identity)
    assert pins.get(latest_identity) == latest_identity
    assert pins.latest("ledger-run") == latest_identity

    pins.pin(first_identity)
    assert pins.get(first_identity) == first_identity
    assert pins.latest("ledger-run") == latest_identity

    early_checkpoints = LedgerCheckpointStore(tmp_path / "early-checkpoints")
    early = _append_checkpoint(
        early_checkpoints,
        events[:1],
        key,
        created_at=99,
    )
    with pytest.raises(CheckpointIntegrityError, match="rollback"):
        pins.pin(checkpoint_identity(early.checkpoint))

    forked = dict(latest_identity)
    forked["head_event_id"] = 999
    with pytest.raises(CheckpointIntegrityError, match="fork"):
        pins.pin(forked)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("signer_provider_id", "substituted-provider"),
        ("signer_key_id", "substituted-key"),
        ("signer_algorithm", "substituted-algorithm"),
    ),
)
def test_trusted_checkpoint_identity_binds_signer_and_provider(
    tmp_path,
    field,
    replacement,
):
    events = _events(2)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)
    identity = checkpoint_identity(persisted.checkpoint)

    assert identity["signer_provider_id"] == key.provider_id
    assert identity["signer_key_id"] == key.key_id
    assert identity["signer_algorithm"] == key.algorithm
    substituted = dict(identity)
    substituted[field] = replacement

    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=substituted,
    )

    assert result.valid is False
    assert result.failure_code == "trusted_checkpoint_identity_mismatch"


def test_checkpoint_signer_rotation_preserves_old_identity_and_chains_forward(
    tmp_path,
):
    events = _events(3)
    old = HmacCheckpointKey(
        b"old-checkpoint-key",
        key_id="old-key",
        provider_id="provider-a",
    )
    new = HmacCheckpointKey(
        b"new-checkpoint-key",
        key_id="new-key",
        provider_id="provider-b",
    )
    keyring = HmacCheckpointKeyring(old, new)
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    first = _append_checkpoint(
        store,
        events[:2],
        old,
        created_at=100,
        verifier=keyring,
    )

    retried = _append_checkpoint(
        store,
        events[:2],
        new,
        created_at=100,
        verifier=keyring,
    )
    assert retried.path == first.path
    assert checkpoint_identity(retried.checkpoint)["signer_key_id"] == "old-key"

    latest = _append_checkpoint(
        store,
        events,
        new,
        created_at=101,
        verifier=keyring,
    )
    latest_identity = checkpoint_identity(latest.checkpoint)

    assert latest_identity["signer_provider_id"] == "provider-b"
    assert latest_identity["signer_key_id"] == "new-key"
    assert (
        latest.checkpoint["predicate"]["previous_checkpoint_hash"]
        == sha256_hex(canonical_json_bytes(first.checkpoint))
    )
    assert verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=keyring,
        trusted_latest_checkpoint=latest_identity,
    ).valid is True
    without_old_key = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=new,
        trusted_latest_checkpoint=latest_identity,
    )
    assert without_old_key.valid is False
    assert without_old_key.failure_code == "checkpoint_signature_invalid"


def test_checkpoint_history_detects_deleted_nonlatest_checkpoint(tmp_path):
    events = _events(3)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    _append_checkpoint(store, events[:1], key, created_at=100)
    middle = _append_checkpoint(store, events[:2], key, created_at=101)
    latest = _append_checkpoint(store, events, key, created_at=102)
    trusted_latest = checkpoint_identity(latest.checkpoint)

    middle.path.unlink()

    with pytest.raises(CheckpointIntegrityError, match="checkpoint history"):
        store.load_all("ledger-run")
    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )
    assert result.valid is False
    assert result.failure_code == "checkpoint_store_invalid"


def test_conflicting_trusted_pins_are_atomic_across_processes(tmp_path):
    checkpoint_store = LedgerCheckpointStore(tmp_path / "checkpoints")
    first_checkpoint = _checkpoint_for_head(
        checkpoint_store,
        run_id="pin-race",
        head_event_id=2,
        head_event_hash="a" * 64,
        event_count=2,
        created_at=100,
    )
    second_checkpoint = _checkpoint_for_head(
        checkpoint_store,
        run_id="pin-race",
        head_event_id=999,
        head_event_hash="b" * 64,
        event_count=2,
        created_at=101,
    )
    root = tmp_path / "trusted-pins"

    results = _run_two_processes(
        _publish_pin_in_process,
        root,
        checkpoint_identity(first_checkpoint),
        checkpoint_identity(second_checkpoint),
    )

    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert "fork" in next(
        result[2] for result in results if result[0] == "error"
    )
    store = FilesystemTrustedCheckpointPinStore(root)
    assert store.latest("pin-race") is not None
    assert len(list(root.rglob("*.json"))) == 1


def test_conflicting_checkpoints_are_atomic_across_processes(tmp_path):
    root = tmp_path / "checkpoints"
    store = LedgerCheckpointStore(root)
    first = _checkpoint_for_head(
        store,
        run_id="checkpoint-race",
        head_event_id=2,
        head_event_hash="a" * 64,
        event_count=2,
        created_at=100,
    )
    second = _checkpoint_for_head(
        store,
        run_id="checkpoint-race",
        head_event_id=999,
        head_event_hash="b" * 64,
        event_count=2,
        created_at=101,
    )

    results = _run_two_processes(
        _publish_checkpoint_in_process,
        root,
        first,
        second,
    )

    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert "fork" in next(
        result[2] for result in results if result[0] == "error"
    )
    assert len(store.load_all("checkpoint-race")) == 1
    assert len(list(root.rglob("*.json"))) == 1


def test_checkpoint_publication_recovers_after_death_after_final_link(tmp_path):
    root = tmp_path / "checkpoints"
    store = LedgerCheckpointStore(root)
    checkpoint = _checkpoint_for_head(
        store,
        run_id="crash-after-link",
        head_event_id=2,
        head_event_hash="c" * 64,
        event_count=2,
        created_at=100,
    )
    final_path = store._checkpoint_path(
        run_id="crash-after-link",
        event_count=2,
        head_event_hash="c" * 64,
    )
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_die_after_checkpoint_final_link,
        args=(str(root), checkpoint),
    )
    process.start()
    process.join(timeout=15)

    assert not process.is_alive()
    assert process.exitcode == 86
    assert final_path.is_file()
    assert list(final_path.parent.glob(".tmp-*"))

    persisted = store.load_all("crash-after-link")

    assert len(persisted) == 1
    assert persisted[0].path == final_path
    assert not list(final_path.parent.glob(".tmp-*"))
    assert final_path.stat().st_nlink == 1


def test_append_only_publication_fsyncs_directory_after_temp_cleanup(
    monkeypatch,
    tmp_path,
):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    operations: list[tuple[str, str]] = []
    original_fsync = evidence_ledger_module.os.fsync
    original_unlink = evidence_ledger_module.os.unlink

    def tracking_fsync(descriptor):
        kind = (
            "directory"
            if stat.S_ISDIR(os.fstat(descriptor).st_mode)
            else "file"
        )
        operations.append(("fsync", kind))
        return original_fsync(descriptor)

    def tracking_unlink(path, *args, **kwargs):
        operations.append(("unlink", os.fspath(path)))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(evidence_ledger_module.os, "fsync", tracking_fsync)
    monkeypatch.setattr(evidence_ledger_module.os, "unlink", tracking_unlink)

    store.put_bytes(b"durable temp cleanup")

    cleanup_index = next(
        index
        for index, operation in enumerate(operations)
        if operation[0] == "unlink" and operation[1].startswith(".tmp-")
    )
    assert ("fsync", "directory") in operations[:cleanup_index]
    assert ("fsync", "directory") in operations[cleanup_index + 1 :]


def test_append_only_publication_propagates_directory_fsync_error(
    monkeypatch,
    tmp_path,
):
    store = ContentAddressedArtifactStore(tmp_path / "artifacts")
    original_fsync = evidence_ledger_module.os.fsync

    def fail_directory_fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "simulated directory fsync failure")
        return original_fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(
            evidence_ledger_module.os,
            "fsync",
            fail_directory_fsync,
        )
        with pytest.raises(ArtifactIntegrityError, match="directory fsync"):
            store.put_bytes(b"directory fsync must fail closed")

    descriptor = store.put_bytes(b"directory fsync must fail closed")
    assert store.read_bytes(descriptor["digest"]["sha256"]) == (
        b"directory fsync must fail closed"
    )


def test_state_checkpoint_api_detects_trusted_latest_rollback(tmp_path):
    state = State(str(tmp_path / "state.db"))
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    run_id = "state-checkpoint-run"
    state.write_event(
        run_id=run_id,
        source="test",
        kind="first",
        payload={"index": 1},
        ts=100,
    )
    state.write_event(
        run_id=run_id,
        source="test",
        kind="second",
        payload={"index": 2},
        ts=101,
    )
    state.checkpoint_event_ledger(
        run_id,
        checkpoint_store=store,
        signer=key,
        verifier=key,
        created_at=200,
    )
    state.write_event(
        run_id=run_id,
        source="test",
        kind="third",
        payload={"index": 3},
        ts=102,
    )
    latest = state.checkpoint_event_ledger(
        run_id,
        checkpoint_store=store,
        signer=key,
        verifier=key,
        created_at=201,
    )
    trusted_latest = checkpoint_identity(latest.checkpoint)

    valid = state.verify_event_ledger_authoritatively(
        run_id,
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )
    assert valid.valid is True
    assert valid.event_count == 3

    latest.path.unlink()
    rolled_back = state.verify_event_ledger_authoritatively(
        run_id,
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )
    assert rolled_back.valid is False
    assert rolled_back.failure_code == "checkpoint_rollback_detected"


def test_state_release_verification_uses_externally_pinned_checkpoint(tmp_path):
    state = State(str(tmp_path / "state.db"))
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    pins = FilesystemTrustedCheckpointPinStore(tmp_path / "trusted-pins")
    run_id = "state-release-run"
    for index in range(3):
        state.write_event(
            run_id=run_id,
            source="test",
            kind="event_msg",
            payload={"index": index},
            ts=100 + index,
        )
    persisted = state.checkpoint_event_ledger(
        run_id,
        checkpoint_store=store,
        signer=key,
        verifier=key,
        created_at=1234,
    )
    pins.pin(checkpoint_identity(persisted.checkpoint))

    verification = state.verify_event_ledger(
        run_id,
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=pins.latest(run_id),
    )

    assert verification.valid is True
    assert verification.truncation_checked is True
    assert verification.authoritative_head_verified is True
    assert verification.external_anchor_ref == persisted.external_anchor_ref
    assert verification.event_count == 3

    truncated = State(str(tmp_path / "truncated.db"))
    for index in range(2):
        truncated.write_event(
            run_id=run_id,
            source="test",
            kind="event_msg",
            payload={"index": index},
            ts=100 + index,
        )
    truncated_verification = truncated.verify_event_ledger(
        run_id,
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=pins.latest(run_id),
    )

    assert truncated_verification.valid is False
    assert (
        truncated_verification.failure_code
        == "checkpoint_event_count_mismatch"
    )
    assert truncated_verification.authoritative_head_verified is False


def test_checkpoint_store_is_append_only_for_same_anchored_head(tmp_path):
    events = _events(2)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    _append_checkpoint(store, events, key, created_at=100)

    head = events[-1]
    replacement = create_ledger_checkpoint(
        run_id="ledger-run",
        head_event_id=head["event_id"],
        head_event_hash=str(head["event_hash"]),
        event_count=len(events),
        external_anchor_ref=store.external_anchor_ref(
            run_id="ledger-run",
            event_count=len(events),
            head_event_hash=str(head["event_hash"]),
        ),
        signer=key,
        created_at=101,
    )

    with pytest.raises(CheckpointIntegrityError, match="immutable"):
        store.append(replacement, verifier=key)


def test_authoritative_verification_detects_checkpoint_signature_tamper(tmp_path):
    events = _events(2)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)
    trusted_latest = checkpoint_identity(persisted.checkpoint)
    tampered = copy.deepcopy(persisted.checkpoint)
    tampered["signatures"][0]["signature"] = base64.b64encode(b"forged").decode(
        "ascii"
    )
    persisted.path.write_bytes(canonical_json_bytes(tampered))

    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )

    assert result.valid is False
    assert result.failure_code == "checkpoint_signature_invalid"


def test_authoritative_verification_detects_checkpoint_payload_tamper(tmp_path):
    events = _events(2)
    key = HmacCheckpointKey()
    store = LedgerCheckpointStore(tmp_path / "checkpoints")
    persisted = _append_checkpoint(store, events, key)
    trusted_latest = checkpoint_identity(persisted.checkpoint)
    tampered = copy.deepcopy(persisted.checkpoint)
    tampered["predicate"]["head_event_id"] = 999
    persisted.path.write_bytes(canonical_json_bytes(tampered))

    result = verify_authoritative_event_chain(
        events,
        expected_run_id="ledger-run",
        checkpoint_store=store,
        verifier=key,
        trusted_latest_checkpoint=trusted_latest,
    )

    assert result.valid is False
    assert result.failure_code == "checkpoint_signing_payload_hash_mismatch"


def test_projection_rebuild_is_byte_deterministic():
    events = _events(3)

    def reduce_event(
        projection: dict[str, object],
        event: dict[str, object],
    ) -> dict[str, object]:
        return {
            "count": int(projection["count"]) + 1,
            "event_hashes": [
                *list(projection["event_hashes"]),
                str(event["event_hash"]),
            ],
        }

    first = rebuild_projection(
        events,
        initial={"event_hashes": [], "count": 0},
        reducer=reduce_event,
        expected_run_id="ledger-run",
        expected_head_hash=str(events[-1]["event_hash"]),
    )
    second = rebuild_projection(
        events,
        initial={"count": 0, "event_hashes": []},
        reducer=reduce_event,
        expected_run_id="ledger-run",
        expected_head_hash=str(events[-1]["event_hash"]),
    )

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_actual_quality_projection_rebuilds_byte_identically_from_ledger(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="quality-run",
        task_id="quality-task",
        task_class="source_change",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=False,
        revision_rounds=2,
        time_to_accepted_outcome_s=12.5,
        policy_overlay_hash="a" * 64,
        policy_proposal_id="proposal-1",
        details={"source": "runtime"},
        computed_at=100,
    )
    state.update_quality_trend_audit(
        run_id="quality-run",
        gate="outcome_review",
        sample_size=3,
        false_accept_count=1,
        false_accept_denominator=3,
        audit_details={"auditor": "hidden"},
    )
    expected = state.quality_trend_projection_snapshot()
    authoritative = _authoritative_projection_arguments(
        state,
        tmp_path,
        "quality-run",
    )
    rebuilt = state.rebuild_quality_trend_projection_from_ledger(
        **authoritative
    )

    assert canonical_json_bytes(rebuilt) == canonical_json_bytes(expected)

    state._conn.execute(
        """UPDATE supervisor_quality_trends
              SET accepted=0,
                  false_accept_count=0,
                  details_json='{}'"""
    )
    state._conn.commit()
    assert state.quality_trend_projection_snapshot() != expected

    restored = state.rebuild_quality_trend_projection_from_ledger(
        replace=True,
        **authoritative,
    )

    assert canonical_json_bytes(restored) == canonical_json_bytes(expected)
    verification = state.verify_event_ledger_structure(run_id="quality-run")
    assert verification.valid is True


def test_quality_projection_rebuild_requires_authoritative_checkpoint(
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="quality-run",
        task_id="quality-task",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
    )

    with pytest.raises(RuntimeError, match="authoritative checkpoint"):
        state.rebuild_quality_trend_projection_from_ledger()


def test_quality_projection_rejects_unauthorized_event_source(tmp_path):
    state = State(str(tmp_path / "state.db"))
    persisted = state.upsert_quality_trend_row(
        run_id="quality-run",
        task_id="quality-task",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
    )
    projection_row = dict(persisted)
    projection_row.pop("id", None)
    with pytest.raises(ValueError, match="reserved projection event"):
        state.write_event(
            run_id="quality-run",
            source="attacker",
            kind=QUALITY_TREND_PROJECTION_EVENT,
            payload=quality_trend_projection_event_payload(projection_row),
        )


def test_quality_projection_rejects_cross_run_projection_row(tmp_path):
    state = State(str(tmp_path / "state.db"))
    persisted = state.upsert_quality_trend_row(
        run_id="victim-run",
        task_id="victim-task",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=1.0,
    )
    spoofed = dict(persisted)
    spoofed.pop("id", None)
    spoofed["accepted"] = False
    with state._write_lock:
        state._conn.execute("BEGIN IMMEDIATE")
        state._insert_event_unlocked(
            run_id="attacker-run",
            source="quality_trends",
            kind=QUALITY_TREND_PROJECTION_EVENT,
            payload=quality_trend_projection_event_payload(spoofed),
        )
        state._conn.commit()
    authoritative = _authoritative_projection_arguments(
        state,
        tmp_path,
        "victim-run",
        "attacker-run",
    )

    with pytest.raises(ValueError, match="run_id"):
        state.rebuild_quality_trend_projection_from_ledger(
            **authoritative,
        )


def test_quality_projection_row_and_event_commit_atomically(
    monkeypatch,
    tmp_path,
):
    state = State(str(tmp_path / "state.db"))

    def fail_event(**_kwargs):
        raise RuntimeError("simulated event append failure")

    monkeypatch.setattr(state, "_insert_event_unlocked", fail_event)
    with pytest.raises(RuntimeError, match="event append failure"):
        state.upsert_quality_trend_row(
            run_id="quality-run",
            task_id="quality-task",
            task_class="source_change",
            gate="execution",
            accepted=True,
            first_pass_accepted=True,
            revision_rounds=0,
            time_to_accepted_outcome_s=1.0,
        )

    assert state.count_quality_trend_rows() == 0
    assert state.latest_event_id("quality-run") == 0
