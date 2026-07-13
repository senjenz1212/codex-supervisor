from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from supervisor.evidence_committer import (
    EvidenceCommitIntegrityError,
    _export_sqlite_database,
)


def test_sqlite_snapshot_aba_substitution_cannot_change_exported_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    replacement = tmp_path / "replacement.db"
    _write_marker_database(source, "trusted")
    _write_marker_database(replacement, "substituted")
    expected = _export_sqlite_database(source, logical_name="test")
    parked = tmp_path / "parked-source.db"
    original_connect = sqlite3.connect
    substitutions = 0

    def racing_connect(
        database: object,
        *args: object,
        **kwargs: object,
    ) -> sqlite3.Connection:
        nonlocal substitutions
        if substitutions == 0:
            source.rename(parked)
            replacement.rename(source)
            try:
                connection = original_connect(database, *args, **kwargs)
            finally:
                source.rename(replacement)
                parked.rename(source)
            substitutions += 1
            return connection
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", racing_connect)

    observed = _export_sqlite_database(source, logical_name="test")

    assert substitutions == 1
    assert observed == expected
    assert not list(tmp_path.glob(".evidence-snapshot-*"))


def test_sqlite_snapshot_fails_closed_when_source_path_is_substituted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    replacement = tmp_path / "replacement.db"
    _write_marker_database(source, "trusted")
    _write_marker_database(replacement, "substituted")
    parked = tmp_path / "parked-source.db"
    original_connect = sqlite3.connect
    substituted = False

    def racing_connect(
        database: object,
        *args: object,
        **kwargs: object,
    ) -> sqlite3.Connection:
        nonlocal substituted
        if not substituted:
            source.rename(parked)
            replacement.rename(source)
            substituted = True
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", racing_connect)

    with pytest.raises(
        EvidenceCommitIntegrityError,
        match="changed during snapshot acquisition",
    ):
        _export_sqlite_database(source, logical_name="test")

    assert substituted
    assert not list(tmp_path.glob(".evidence-snapshot-*"))


def test_sqlite_snapshot_rejects_symlink_source(tmp_path: Path) -> None:
    target = tmp_path / "target.db"
    source = tmp_path / "source.db"
    _write_marker_database(target, "outside")
    source.symlink_to(target)

    with pytest.raises(EvidenceCommitIntegrityError, match="symlink"):
        _export_sqlite_database(source, logical_name="test")


def test_sqlite_snapshot_preserves_committed_wal_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "wal.db"
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        writer.commit()
        writer.execute("INSERT INTO marker VALUES ('committed-in-wal')")
        writer.commit()
        assert source.with_name(f"{source.name}-wal").is_file()

        snapshot = json.loads(
            _export_sqlite_database(source, logical_name="test")
        )
    finally:
        writer.close()

    marker = next(
        table for table in snapshot["tables"] if table["name"] == "marker"
    )
    assert marker["rows"] == [["committed-in-wal"]]
    assert not list(tmp_path.glob(".evidence-snapshot-*"))


def _write_marker_database(path: Path, marker: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker VALUES (?)", (marker,))
