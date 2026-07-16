from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import supervisor.backend_run_replay as backend_run_replay_module
from supervisor.backend_run_replay import (
    BACKEND_RUN_REPLAY_SCHEMA_VERSION,
    BackendRunReplayCommittedDetachedError,
    BackendRunReplayGuardError,
    SQLiteBackendRunReplayGuard as _SQLiteBackendRunReplayGuard,
    VerificationAttemptConflictError,
    VerificationAttemptSpec,
)
import pytest


_JOURNAL_AUTHORITY_IDS: dict[str, str] = {}


def SQLiteBackendRunReplayGuard(
    path: str | Path,
) -> _SQLiteBackendRunReplayGuard:
    """Provision once per test path, then reopen with its external pin."""
    key = str(path)
    authority_id = _JOURNAL_AUTHORITY_IDS.get(key)
    if authority_id is not None:
        return _SQLiteBackendRunReplayGuard.open(
            path,
            expected_authority_id=authority_id,
        )
    if Path(path).is_file():
        return _SQLiteBackendRunReplayGuard.open(
            path,
            expected_authority_id="0" * 64,
        )
    journal = _SQLiteBackendRunReplayGuard.provision(path)
    _JOURNAL_AUTHORITY_IDS[key] = journal.authority_id
    return journal


def _attempt_spec() -> VerificationAttemptSpec:
    return VerificationAttemptSpec(
        execution_spec_hash="1" * 64,
        frozen_result_hash="2" * 64,
        model_patch_sha256="3" * 64,
        producer_run_result_hash="4" * 64,
        verifier_id="official-swebench",
        verifier_version="4.1.0",
        verifier_hash="5" * 64,
        verification_policy_hash="6" * 64,
        slot_key="7" * 64,
        context={
            "experiment_id": "exp-1",
            "task_id": "task-1",
            "block_attempt": 0,
            "arm": "B",
        },
    )


def _json_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _grade(*, passed: bool = True) -> dict[str, object]:
    return {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "evidence": {"resolved": passed},
        "failure_classification": "",
        "flake_classification": "",
    }


def test_verification_attempt_reuses_nonce_after_reconstruction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()

    with SQLiteBackendRunReplayGuard(database) as journal:
        first = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    with SQLiteBackendRunReplayGuard(database) as journal:
        recovered = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    assert first.state == "PREPARED"
    assert recovered == first
    assert len(recovered.request_nonce) == 64


def test_verification_attempt_records_are_deeply_immutable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    base = _attempt_spec()
    spec = replace(
        base,
        context={
            **dict(base.context),
            "nested": {"items": [{"value": "original"}]},
        },
    )
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {"nested": {"value": "original"}},
        "failure_classification": "",
        "flake_classification": "",
    }

    with pytest.raises(TypeError):
        spec.context["nested"]["items"][0]["value"] = "mutated"

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
        completed = journal.complete_verification_attempt(
            attempt_key=prepared.attempt_key,
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
            grade=grade,
        )

        with pytest.raises(TypeError):
            completed.grade["evidence"]["nested"]["value"] = "mutated"


def test_verification_attempt_rejects_backend_drift(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()

    with SQLiteBackendRunReplayGuard(database) as journal:
        journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
        with pytest.raises(
            VerificationAttemptConflictError,
            match="different immutable authority",
        ):
            journal.prepare_verification_attempt(
                spec,
                backend_id="tests.backend/v2",
                backend_manifest_hash="9" * 64,
            )


def test_verification_attempt_nonce_cannot_replace_an_existing_row(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    original_spec = _attempt_spec()
    replacement_spec = replace(
        original_spec,
        execution_spec_hash="9" * 64,
        slot_key="a" * 64,
    )

    with SQLiteBackendRunReplayGuard(database) as journal:
        original = journal.prepare_verification_attempt(
            original_spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "PRAGMA recursive_triggers"
        ).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT OR REPLACE INTO swe_bench_verification_attempts(
                  schema_version, attempt_key, slot_key,
                  execution_spec_hash, frozen_result_hash,
                  model_patch_sha256, producer_run_result_hash,
                  verifier_id, verifier_version, verifier_hash,
                  verification_policy_hash, context_json, context_hash,
                  requested_backend_id,
                  requested_backend_manifest_hash,
                  request_nonce, state, prepared_at_ms
                ) VALUES(
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  'PREPARED', ?
                )
                """,
                (
                    backend_run_replay_module.VERIFICATION_ATTEMPT_SCHEMA_VERSION,
                    replacement_spec.attempt_key,
                    replacement_spec.effective_slot_key,
                    replacement_spec.execution_spec_hash,
                    replacement_spec.frozen_result_hash,
                    replacement_spec.model_patch_sha256,
                    replacement_spec.producer_run_result_hash,
                    replacement_spec.verifier_id,
                    replacement_spec.verifier_version,
                    replacement_spec.verifier_hash,
                    replacement_spec.verification_policy_hash,
                    json.dumps(
                        dict(replacement_spec.context),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    replacement_spec.context_hash,
                    original.requested_backend_id,
                    original.requested_backend_manifest_hash,
                    original.request_nonce,
                    original.prepared_at_ms + 1,
                ),
            )

    with SQLiteBackendRunReplayGuard(database) as journal:
        assert journal.get_verification_attempt(
            attempt_key=original.attempt_key
        ) == original
        assert journal.get_verification_attempt(
            attempt_key=replacement_spec.attempt_key
        ) is None


def test_verification_journal_does_not_recreate_missing_schema_objects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER swe_bench_verification_attempts_no_delete"
        )
        connection.execute(
            "DELETE FROM swe_bench_verification_attempts WHERE attempt_key=?",
            (prepared.attempt_key,),
        )
        connection.commit()

    with pytest.raises(
        BackendRunReplayGuardError,
        match="schema definition mismatch",
    ):
        SQLiteBackendRunReplayGuard(database)


def test_verification_journal_rejects_foreign_key_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    completion_trigger = next(
        trigger
        for trigger in (
            backend_run_replay_module
            ._CANONICAL_VERIFICATION_ATTEMPT_TRIGGER_SQL
        )
        if "verification_attempts_complete_once" in trigger
    )
    grade = _grade()
    grade_json = json.dumps(
        grade,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER swe_bench_verification_attempts_complete_once"
        )
        connection.execute(
            """
            UPDATE swe_bench_verification_attempts
               SET state='COMPLETED',
                   completed_at_ms=?,
                   backend_run_id=?,
                   authority_hash=?,
                   grade_json=?,
                   grade_hash=?,
                   completion_hash=?
             WHERE attempt_key=?
            """,
            (
                prepared.prepared_at_ms + 1,
                "missing-backend-run",
                "a" * 64,
                grade_json,
                _json_hash(grade),
                "b" * 64,
                prepared.attempt_key,
            ),
        )
        connection.execute(completion_trigger)
        connection.commit()

    with pytest.raises(
        BackendRunReplayGuardError,
        match="foreign key integrity",
    ):
        SQLiteBackendRunReplayGuard(database)


def test_verification_journal_requires_its_authority_anchor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    with SQLiteBackendRunReplayGuard(database) as journal:
        anchor = Path(journal.authority_anchor_path)

    anchor.unlink()

    with pytest.raises(
        BackendRunReplayGuardError,
        match="authority anchor is missing",
    ):
        SQLiteBackendRunReplayGuard(database)


def test_existing_journal_requires_an_external_authority_pin(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as provisioned:
        authority_id = provisioned.authority_id

    with pytest.raises(ValueError, match="expected external authority id"):
        _SQLiteBackendRunReplayGuard(database)
    with pytest.raises(
        BackendRunReplayGuardError,
        match="externally pinned authority id",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id="f" * 64,
        )
    with _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    ) as reopened:
        assert reopened.authority_id == authority_id


def test_normal_open_never_reprovisions_a_displaced_store(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "journal-store"
    database = store_root / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as original:
        original_authority_id = original.authority_id

    displaced_root = tmp_path / "displaced-journal-store"
    store_root.replace(displaced_root)

    with pytest.raises(BackendRunReplayGuardError, match="missing"):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=original_authority_id,
        )
    assert not store_root.exists()

    with _SQLiteBackendRunReplayGuard.provision(database) as replacement:
        replacement_authority_id = replacement.authority_id
    assert replacement_authority_id != original_authority_id

    with pytest.raises(
        BackendRunReplayGuardError,
        match="externally pinned authority id",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=original_authority_id,
        )


def test_same_authority_stale_database_snapshot_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    stale_snapshot = tmp_path / "stale-snapshot.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        authority_id = journal.authority_id
        prepared = journal.prepare_verification_attempt(
            _attempt_spec(),
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
    shutil.copy2(database, stale_snapshot)

    with _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    ) as journal:
        journal.complete_verification_attempt(
            attempt_key=prepared.attempt_key,
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
            grade=_grade(),
        )

    shutil.copyfile(stale_snapshot, database)

    with pytest.raises(
        BackendRunReplayGuardError,
        match="state.*rollback|rollback.*state",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=authority_id,
        )


def test_prepared_attempt_nonce_is_integrity_bound(tmp_path: Path) -> None:
    database = tmp_path / "verification-journal.db"
    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            _attempt_spec(),
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    completion_trigger = next(
        trigger
        for trigger in (
            backend_run_replay_module
            ._CANONICAL_VERIFICATION_ATTEMPT_TRIGGER_SQL
        )
        if "verification_attempts_complete_once" in trigger
    )
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "DROP TRIGGER swe_bench_verification_attempts_complete_once"
        )
        connection.execute(
            """
            UPDATE swe_bench_verification_attempts
               SET request_nonce=?
             WHERE attempt_key=?
            """,
            ("f" * 64, prepared.attempt_key),
        )
        connection.execute(completion_trigger)
        connection.commit()
        tampered_row = connection.execute(
            """
            SELECT *
              FROM swe_bench_verification_attempts
             WHERE attempt_key=?
            """,
            (prepared.attempt_key,),
        ).fetchone()

    assert tampered_row is not None
    with pytest.raises(
        BackendRunReplayGuardError,
        match="preparation hash",
    ):
        backend_run_replay_module._verification_attempt_from_row(
            tampered_row
        )

    with pytest.raises(
        BackendRunReplayGuardError,
        match="state hash|preparation hash|nonce.*integrity",
    ):
        SQLiteBackendRunReplayGuard(database)


def test_provisioning_anchor_failure_cleans_partial_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"

    def fail_anchor(*_args, **_kwargs):
        raise KeyboardInterrupt("anchor persistence interrupted")

    monkeypatch.setattr(
        backend_run_replay_module,
        "_create_authority_anchor",
        fail_anchor,
    )
    with pytest.raises(KeyboardInterrupt, match="anchor persistence"):
        _SQLiteBackendRunReplayGuard.provision(database)

    assert not database.exists()
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    assert not backend_run_replay_module._authority_anchor_path(
        database
    ).exists()


def test_provisioning_connection_failure_cleans_partial_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"

    def fail_connection(*_args, **_kwargs):
        raise sqlite3.OperationalError("connection setup failed")

    monkeypatch.setattr(
        backend_run_replay_module.sqlite3,
        "connect",
        fail_connection,
    )
    with pytest.raises(
        sqlite3.OperationalError,
        match="connection setup failed",
    ):
        _SQLiteBackendRunReplayGuard.provision(database)

    assert not database.exists()
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    assert not backend_run_replay_module._authority_anchor_path(
        database
    ).exists()


def test_journal_database_and_sidecars_require_private_permissions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        authority_id = journal.authority_id
    assert stat.S_IMODE(database.stat().st_mode) == 0o600

    database.chmod(0o666)
    with pytest.raises(
        BackendRunReplayGuardError,
        match="database.*permissions|private permissions",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=authority_id,
        )
    database.chmod(0o600)

    wal_path = Path(str(database) + "-wal")
    wal_path.write_bytes(b"untrusted")
    wal_path.chmod(0o666)
    with pytest.raises(
        BackendRunReplayGuardError,
        match="sidecar.*permissions|private permissions",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=authority_id,
        )


def test_authority_anchor_requires_private_permissions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        authority_id = journal.authority_id
        anchor = Path(journal.authority_anchor_path)

    anchor.chmod(0o666)
    with pytest.raises(
        BackendRunReplayGuardError,
        match="anchor requires private permissions",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=authority_id,
        )


def test_malformed_anchor_digest_uses_guard_error_contract(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        authority_id = journal.authority_id
        anchor = Path(journal.authority_anchor_path)

    payload = json.loads(anchor.read_text(encoding="utf-8"))
    payload["state_hash"] = "not-a-digest"
    anchor.write_text(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    anchor.chmod(0o600)
    with pytest.raises(
        BackendRunReplayGuardError,
        match="authority anchor is invalid",
    ):
        _SQLiteBackendRunReplayGuard.open(
            database,
            expected_authority_id=authority_id,
        )


def test_authority_anchor_is_read_from_checked_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        authority_id = journal.authority_id
        anchor = Path(journal.authority_anchor_path)
    replacement = tmp_path / "replacement.anchor"
    replacement.write_text("{}\n", encoding="utf-8")
    replacement.chmod(0o600)
    real_open = backend_run_replay_module.os.open
    swapped = False

    def open_then_replace(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) == anchor and not swapped:
            swapped = True
            os.replace(replacement, anchor)
        return descriptor

    monkeypatch.setattr(
        backend_run_replay_module.os,
        "open",
        open_then_replace,
    )

    with pytest.raises(
        BackendRunReplayGuardError,
        match="single-link regular file|changed while reading|anchor is invalid",
    ):
        backend_run_replay_module._read_authority_anchor(
            anchor,
            database_path=database,
        )

    assert swapped is True
    assert authority_id


def test_authority_anchor_uses_bounded_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        anchor = Path(journal.authority_anchor_path)
    anchor.write_bytes(b"x")
    anchor.chmod(0o600)
    real_fstat = backend_run_replay_module.os.fstat
    real_read = backend_run_replay_module.os.read
    requested_sizes: list[int] = []
    grew = False

    def grow_after_metadata_check(descriptor: int):
        nonlocal grew
        metadata = real_fstat(descriptor)
        if not grew:
            grew = True
            anchor.write_bytes(b"x" * (1024 * 1024))
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_nlink=metadata.st_nlink,
            st_uid=metadata.st_uid,
            st_size=metadata.st_size,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
        )

    def bounded_read(descriptor: int, size: int) -> bytes:
        requested_sizes.append(size)
        return real_read(descriptor, size)

    monkeypatch.setattr(
        backend_run_replay_module.os,
        "fstat",
        grow_after_metadata_check,
    )
    monkeypatch.setattr(
        backend_run_replay_module.os,
        "read",
        bounded_read,
    )
    with pytest.raises(
        BackendRunReplayGuardError,
        match="anchor is oversized",
    ):
        backend_run_replay_module._read_authority_anchor(
            anchor,
            database_path=database,
        )

    assert grew is True
    assert requested_sizes
    assert max(requested_sizes) <= 4097


def test_verification_attempt_completion_is_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {"resolved": True},
        "failure_classification": "",
        "flake_classification": "",
    }

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
        first = journal.complete_verification_attempt(
            attempt_key=prepared.attempt_key,
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
            grade=grade,
        )
        replay = journal.complete_verification_attempt(
            attempt_key=prepared.attempt_key,
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
            grade=grade,
        )

        assert first.state == "COMPLETED"
        assert replay == first
        assert dict(first.grade or {}) == grade
        assert journal.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
        ) is False


def test_verification_attempt_rejects_conflicting_completion(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {},
        "failure_classification": "",
        "flake_classification": "",
    }

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
        journal.complete_verification_attempt(
            attempt_key=prepared.attempt_key,
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
            grade=grade,
        )
        with pytest.raises(
            VerificationAttemptConflictError,
            match="different authority",
        ):
            journal.complete_verification_attempt(
                attempt_key=prepared.attempt_key,
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-2",
                authority_hash="b" * 64,
                grade={**grade, "passed": False, "score": 0.0},
            )


def test_verification_attempt_rejects_mismatched_consumption_authority(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {},
        "failure_classification": "",
        "flake_classification": "",
    }
    grade_json = json.dumps(
        grade,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )

    backend_run_id = "backend-run-1"
    consumed_authority_hash = "a" * 64
    attempted_authority_hash = "b" * 64
    grade_hash = _json_hash(grade)
    completion_hash = _json_hash({
        "schema_version": (
            "supervisor-swe-bench-verification-completion/v1"
        ),
        "attempt_key": prepared.attempt_key,
        "backend_id": "tests.backend/v1",
        "backend_run_id": backend_run_id,
        "authority_hash": attempted_authority_hash,
        "grade_hash": grade_hash,
    })
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute(
            """
            INSERT INTO swe_bench_backend_run_consumptions(
              schema_version, backend_id, backend_run_id,
              authority_hash, consumed_at_ms
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                BACKEND_RUN_REPLAY_SCHEMA_VERSION,
                "tests.backend/v1",
                backend_run_id,
                consumed_authority_hash,
                prepared.prepared_at_ms,
            ),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="verification attempt update is immutable",
        ):
            connection.execute(
                """
                UPDATE swe_bench_verification_attempts
                   SET state='COMPLETED',
                       completed_at_ms=?,
                       backend_run_id=?,
                       authority_hash=?,
                       grade_json=?,
                       grade_hash=?,
                       completion_hash=?
                 WHERE attempt_key=?
                """,
                (
                    prepared.prepared_at_ms + 1,
                    backend_run_id,
                    attempted_authority_hash,
                    grade_json,
                    grade_hash,
                    completion_hash,
                    prepared.attempt_key,
                ),
            )
    finally:
        connection.close()


def test_verification_completion_rolls_back_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {},
        "failure_classification": "",
        "flake_classification": "",
    }

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
        original_parser = (
            backend_run_replay_module._verification_attempt_from_row
        )
        parser_calls = 0

        def interrupt_after_completion_write(row):
            nonlocal parser_calls
            parser_calls += 1
            if parser_calls == 2:
                raise KeyboardInterrupt(
                    "simulated interrupt before transaction commit"
                )
            return original_parser(row)

        monkeypatch.setattr(
            backend_run_replay_module,
            "_verification_attempt_from_row",
            interrupt_after_completion_write,
        )
        with pytest.raises(
            KeyboardInterrupt,
            match="before transaction commit",
        ):
            journal.complete_verification_attempt(
                attempt_key=prepared.attempt_key,
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
                grade=grade,
            )

        assert journal._conn.in_transaction is False
        monkeypatch.setattr(
            backend_run_replay_module,
            "_verification_attempt_from_row",
            original_parser,
        )
        recovered = journal.get_verification_attempt(
            attempt_key=prepared.attempt_key
        )
        assert recovered is not None
        assert recovered.state == "PREPARED"
        assert journal.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
        ) is True


def test_completed_attempt_rejects_tampered_consumption_authority(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {},
        "failure_classification": "",
        "flake_classification": "",
    }

    with SQLiteBackendRunReplayGuard(database) as journal:
        prepared = journal.prepare_verification_attempt(
            spec,
            backend_id="tests.backend/v1",
            backend_manifest_hash="8" * 64,
        )
        journal.complete_verification_attempt(
            attempt_key=prepared.attempt_key,
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-1",
            authority_hash="a" * 64,
            grade=grade,
        )

    no_update_trigger = next(
        trigger
        for trigger in backend_run_replay_module._CANONICAL_REPLAY_TRIGGER_SQL
        if "consumptions_no_update" in trigger
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "DROP TRIGGER swe_bench_backend_run_consumptions_no_update"
        )
        connection.execute(
            """
            UPDATE swe_bench_backend_run_consumptions
               SET authority_hash=?
             WHERE backend_id=? AND backend_run_id=?
            """,
            ("b" * 64, "tests.backend/v1", "backend-run-1"),
        )
        connection.execute(no_update_trigger)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        BackendRunReplayGuardError,
        match="state hash|consumption authority mismatch",
    ):
        SQLiteBackendRunReplayGuard(database)


def test_verification_attempt_is_exactly_once_across_connections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    spec = _attempt_spec()
    grade = {
        "schema_version": "supervisor-verification-grade/v1",
        "verifier_id": "official-swebench",
        "verifier_version": "4.1.0",
        "verifier_hash": "5" * 64,
        "frozen_result_hash": "2" * 64,
        "passed": True,
        "score": 1.0,
        "evidence": {"resolved": True},
        "failure_classification": "",
        "flake_classification": "",
    }

    with (
        SQLiteBackendRunReplayGuard(database) as first,
        SQLiteBackendRunReplayGuard(database) as second,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        prepare_futures = [
            pool.submit(
                journal.prepare_verification_attempt,
                spec,
                backend_id="tests.backend/v1",
                backend_manifest_hash="8" * 64,
            )
            for journal in (first, second)
        ]
        prepared = tuple(
            future.result() for future in prepare_futures
        )
        assert prepared[0] == prepared[1]

        complete_futures = [
            pool.submit(
                journal.complete_verification_attempt,
                attempt_key=prepared[0].attempt_key,
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
                grade=grade,
            )
            for journal in (first, second)
        ]
        completed = tuple(
            future.result() for future in complete_futures
        )
        assert completed[0] == completed[1]
        assert completed[0].state == "COMPLETED"

    connection = sqlite3.connect(database)
    try:
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM swe_bench_verification_attempts"
        ).fetchone()[0]
        consumption_count = connection.execute(
            "SELECT COUNT(*) FROM swe_bench_backend_run_consumptions"
        ).fetchone()[0]
    finally:
        connection.close()
    assert attempt_count == 1
    assert consumption_count == 1


def test_read_recovery_cannot_overwrite_a_newer_anchor_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"
    base = SQLiteBackendRunReplayGuard(database)
    authority_id = base.authority_id
    base.prepare_verification_attempt(
        _attempt_spec(),
        backend_id="tests.backend/v1",
        backend_manifest_hash="8" * 64,
    )
    reader = _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    )
    advancer = _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    )
    real_replace = backend_run_replay_module._replace_authority_anchor

    def detach_committed_anchor(*_args, **_kwargs):
        raise OSError("simulated post-commit anchor failure")

    monkeypatch.setattr(
        backend_run_replay_module,
        "_replace_authority_anchor",
        detach_committed_anchor,
    )
    with pytest.raises(
        BackendRunReplayCommittedDetachedError,
        match="committed.*authority anchor detached",
    ):
        base.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-detached",
            authority_hash="a" * 64,
        )
    base.close()

    reader_paused = threading.Event()
    allow_reader = threading.Event()
    advancer_done = threading.Event()
    thread_errors: list[BaseException] = []

    def pause_reader_repair(path, **kwargs):
        if (
            threading.current_thread().name == "journal-reader"
            and kwargs.get("generation") == 2
        ):
            reader_paused.set()
            if not allow_reader.wait(timeout=5):
                raise TimeoutError("reader repair was not released")
        return real_replace(path, **kwargs)

    monkeypatch.setattr(
        backend_run_replay_module,
        "_replace_authority_anchor",
        pause_reader_repair,
    )

    def run_reader() -> None:
        try:
            reader.list_verification_attempts()
        except BaseException as exc:
            thread_errors.append(exc)

    def run_advancer() -> None:
        try:
            advancer.consume(
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-advance",
                authority_hash="b" * 64,
            )
        except BaseException as exc:
            thread_errors.append(exc)
        finally:
            advancer_done.set()

    reader_thread = threading.Thread(
        target=run_reader,
        name="journal-reader",
    )
    advancer_thread = threading.Thread(
        target=run_advancer,
        name="journal-advancer",
    )
    try:
        reader_thread.start()
        assert reader_paused.wait(timeout=5)
        advancer_thread.start()
        assert not advancer_done.wait(timeout=0.25)
    finally:
        allow_reader.set()
        reader_thread.join(timeout=5)
        advancer_thread.join(timeout=5)
        reader.close()
        advancer.close()

    assert not reader_thread.is_alive()
    assert not advancer_thread.is_alive()
    assert thread_errors == []
    with _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    ):
        pass


def test_verification_completion_rolls_back_if_path_changes_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_root = tmp_path / "journal-store"
    database = store_root / "verification-journal.db"
    guard = SQLiteBackendRunReplayGuard(database)
    prepared = guard.prepare_verification_attempt(
        _attempt_spec(),
        backend_id="tests.backend/v1",
        backend_manifest_hash="8" * 64,
    )
    original_database_identity = (
        backend_run_replay_module._database_identity
    )
    displaced_root = tmp_path / "displaced-journal-store"
    identity_checks = 0

    def swap_after_preflight(path: Path, *, missing_ok: bool):
        nonlocal identity_checks
        identity_checks += 1
        identity = original_database_identity(path, missing_ok=missing_ok)
        if identity_checks == 2:
            store_root.replace(displaced_root)
        return identity

    monkeypatch.setattr(
        backend_run_replay_module,
        "_database_identity",
        swap_after_preflight,
    )
    try:
        with pytest.raises(
            BackendRunReplayGuardError,
            match="database identity changed",
        ):
            guard.complete_verification_attempt(
                attempt_key=prepared.attempt_key,
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
                grade=_grade(),
            )
    finally:
        guard.close()

    displaced_database = (
        displaced_root / "verification-journal.db"
    )
    with sqlite3.connect(displaced_database) as connection:
        state = connection.execute(
            """
            SELECT state
              FROM swe_bench_verification_attempts
             WHERE attempt_key=?
            """,
            (prepared.attempt_key,),
        ).fetchone()[0]
        consumption_count = connection.execute(
            "SELECT COUNT(*) FROM swe_bench_backend_run_consumptions"
        ).fetchone()[0]
    assert state == "PREPARED"
    assert consumption_count == 0

    with pytest.raises(BackendRunReplayGuardError):
        SQLiteBackendRunReplayGuard(database)
    assert not store_root.exists()


def test_post_commit_path_replacement_fails_closed_for_future_openers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_root = tmp_path / "journal-store"
    database = store_root / "verification-journal.db"
    guard = SQLiteBackendRunReplayGuard(database)
    prepared = guard.prepare_verification_attempt(
        _attempt_spec(),
        backend_id="tests.backend/v1",
        backend_manifest_hash="8" * 64,
    )
    displaced_root = tmp_path / "displaced-journal-store"
    identity_checks = 0
    original_assertion = guard._assert_bound_database_identity

    def swap_after_precommit_identity_check() -> None:
        nonlocal identity_checks
        identity_checks += 1
        original_assertion()
        if identity_checks == 2:
            store_root.replace(displaced_root)

    monkeypatch.setattr(
        guard,
        "_assert_bound_database_identity",
        swap_after_precommit_identity_check,
    )
    try:
        with pytest.raises(
            BackendRunReplayCommittedDetachedError,
            match="committed.*locator.*detached",
        ):
            guard.complete_verification_attempt(
                attempt_key=prepared.attempt_key,
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
                grade=_grade(),
            )
    finally:
        guard.close()

    displaced_database = (
        displaced_root / "verification-journal.db"
    )
    with sqlite3.connect(displaced_database) as connection:
        state = connection.execute(
            """
            SELECT state
              FROM swe_bench_verification_attempts
             WHERE attempt_key=?
            """,
            (prepared.attempt_key,),
        ).fetchone()[0]
        consumption_count = connection.execute(
            "SELECT COUNT(*) FROM swe_bench_backend_run_consumptions"
        ).fetchone()[0]
    assert state == "COMPLETED"
    assert consumption_count == 1

    with pytest.raises(BackendRunReplayGuardError):
        SQLiteBackendRunReplayGuard(database)
    assert not store_root.exists()


def test_post_commit_anchor_failure_is_reported_as_committed_detached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"
    guard = SQLiteBackendRunReplayGuard(database)
    authority_id = guard.authority_id
    prepared = guard.prepare_verification_attempt(
        _attempt_spec(),
        backend_id="tests.backend/v1",
        backend_manifest_hash="8" * 64,
    )
    original_replace = (
        backend_run_replay_module._replace_authority_anchor
    )

    def interrupt_anchor_write(*_args, **_kwargs):
        raise KeyboardInterrupt("anchor write interrupted after commit")

    monkeypatch.setattr(
        backend_run_replay_module,
        "_replace_authority_anchor",
        interrupt_anchor_write,
    )
    try:
        with pytest.raises(
            BackendRunReplayCommittedDetachedError,
            match="committed.*authority anchor detached",
        ) as observed:
            guard.complete_verification_attempt(
                attempt_key=prepared.attempt_key,
                backend_id="tests.backend/v1",
                backend_run_id="backend-run-1",
                authority_hash="a" * 64,
                grade=_grade(),
            )
        assert isinstance(observed.value.__cause__, KeyboardInterrupt)
        with pytest.raises(
            BackendRunReplayGuardError,
            match="poisoned",
        ):
            guard.get_verification_attempt(
                attempt_key=prepared.attempt_key
            )
    finally:
        guard.close()

    monkeypatch.setattr(
        backend_run_replay_module,
        "_replace_authority_anchor",
        original_replace,
    )
    with _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    ) as recovered:
        completed = recovered.get_verification_attempt(
            attempt_key=prepared.attempt_key
        )

    assert completed is not None
    assert completed.state == "COMPLETED"
    assert completed.backend_run_id == "backend-run-1"


def test_hard_process_exit_after_commit_recovers_from_stale_anchor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verification-journal.db"
    with _SQLiteBackendRunReplayGuard.provision(database) as journal:
        authority_id = journal.authority_id

    script = """
import os
import sys
from pathlib import Path
import supervisor.backend_run_replay as journal_module

database = Path(sys.argv[1])
authority_id = sys.argv[2]
guard = journal_module.SQLiteBackendRunReplayGuard.open(
    database,
    expected_authority_id=authority_id,
)

def exit_after_commit(*_args, **_kwargs):
    os._exit(23)

journal_module._replace_authority_anchor = exit_after_commit
guard.consume(
    backend_id="tests.backend/v1",
    backend_run_id="backend-run-hard-exit",
    authority_hash="a" * 64,
)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(database),
            authority_id,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 23

    with _SQLiteBackendRunReplayGuard.open(
        database,
        expected_authority_id=authority_id,
    ) as recovered:
        assert recovered.consume(
            backend_id="tests.backend/v1",
            backend_run_id="backend-run-hard-exit",
            authority_hash="a" * 64,
        ) is False


def test_verification_read_fails_if_path_changes_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_root = tmp_path / "journal-store"
    database = store_root / "verification-journal.db"
    guard = SQLiteBackendRunReplayGuard(database)
    prepared = guard.prepare_verification_attempt(
        _attempt_spec(),
        backend_id="tests.backend/v1",
        backend_manifest_hash="8" * 64,
    )
    original_database_identity = (
        backend_run_replay_module._database_identity
    )
    displaced_root = tmp_path / "displaced-journal-store"
    identity_checks = 0

    def swap_after_preflight(path: Path, *, missing_ok: bool):
        nonlocal identity_checks
        identity_checks += 1
        identity = original_database_identity(path, missing_ok=missing_ok)
        if identity_checks == 2:
            store_root.replace(displaced_root)
        return identity

    monkeypatch.setattr(
        backend_run_replay_module,
        "_database_identity",
        swap_after_preflight,
    )
    try:
        with pytest.raises(
            BackendRunReplayGuardError,
            match="database identity changed",
        ):
            guard.get_verification_attempt(
                attempt_key=prepared.attempt_key
            )
    finally:
        guard.close()

    with pytest.raises(BackendRunReplayGuardError):
        SQLiteBackendRunReplayGuard(database)
    assert not store_root.exists()


def test_verification_journal_rejects_fresh_file_open_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "verification-journal.db"
    displaced = tmp_path / "displaced-verification-journal.db"
    real_connect = backend_run_replay_module.sqlite3.connect
    swapped = False

    def connect_then_replace(path, *args, **kwargs):
        nonlocal swapped
        connection = real_connect(path, *args, **kwargs)
        if not swapped:
            swapped = True
            database.replace(displaced)
            real_connect(database).close()
        return connection

    monkeypatch.setattr(
        backend_run_replay_module.sqlite3,
        "connect",
        connect_then_replace,
    )
    with pytest.raises(BackendRunReplayGuardError):
        _SQLiteBackendRunReplayGuard.provision(database)

    monkeypatch.setattr(
        backend_run_replay_module.sqlite3,
        "connect",
        real_connect,
    )
    assert swapped is True
    assert displaced.exists()
    assert not database.exists()
    with _SQLiteBackendRunReplayGuard.provision(database):
        pass
