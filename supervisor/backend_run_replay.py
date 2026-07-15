"""Durable replay protection for attested execution-backend runs."""
from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from pathlib import Path


BACKEND_RUN_REPLAY_SCHEMA_VERSION = (
    "supervisor-swe-bench-backend-run-replay/v1"
)
_REPLAY_TABLE_NAME = "swe_bench_backend_run_consumptions"
_CANONICAL_REPLAY_TABLE_SQL = """
CREATE TABLE swe_bench_backend_run_consumptions(
  schema_version TEXT NOT NULL CHECK(
    schema_version =
    'supervisor-swe-bench-backend-run-replay/v1'
  ),
  backend_id TEXT NOT NULL CHECK(
    length(trim(backend_id)) > 0
  ),
  backend_run_id TEXT NOT NULL CHECK(
    length(trim(backend_run_id)) > 0
  ),
  authority_hash TEXT NOT NULL CHECK(
    length(authority_hash) = 64
    AND authority_hash = lower(authority_hash)
    AND authority_hash NOT GLOB '*[^0-9a-f]*'
  ),
  consumed_at_ms INTEGER NOT NULL CHECK(
    consumed_at_ms >= 0
  ),
  PRIMARY KEY(backend_id, backend_run_id)
)
""".strip()
_CANONICAL_REPLAY_TRIGGER_SQL = (
    """
    CREATE TRIGGER swe_bench_backend_run_consumptions_no_replace
    BEFORE INSERT ON swe_bench_backend_run_consumptions
    WHEN EXISTS(
      SELECT 1
        FROM swe_bench_backend_run_consumptions
       WHERE backend_id = NEW.backend_id
         AND backend_run_id = NEW.backend_run_id
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay consumption is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_backend_run_consumptions_no_update
    BEFORE UPDATE ON swe_bench_backend_run_consumptions
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay consumption is immutable'
      );
    END
    """.strip(),
    """
    CREATE TRIGGER swe_bench_backend_run_consumptions_no_delete
    BEFORE DELETE ON swe_bench_backend_run_consumptions
    BEGIN
      SELECT RAISE(
        ABORT,
        'backend run replay consumption is immutable'
      );
    END
    """.strip(),
)


class BackendRunReplayGuardError(RuntimeError):
    """Base error for durable backend-run replay protection."""


class BackendRunReplayConflictError(BackendRunReplayGuardError):
    """A consumed backend run was presented with different authority."""


class SQLiteBackendRunReplayGuard:
    """Atomically consume each backend execution identity exactly once."""

    def __init__(self, path: str | Path) -> None:
        raw_path = str(path).strip()
        if not raw_path or raw_path == ":memory:":
            raise ValueError(
                "durable backend run replay guard requires a file path"
            )
        expanded = Path(raw_path).expanduser()
        if not expanded.is_absolute():
            raise ValueError(
                "durable backend run replay guard requires an absolute "
                "file path"
            )
        normalized = Path(os.path.normpath(str(expanded)))
        _reject_symlink_components(normalized)
        normalized.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(normalized)
        initial_identity = _database_identity(
            normalized,
            missing_ok=True,
        )
        self.path = str(normalized)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            opened_identity = _database_identity(
                normalized,
                missing_ok=False,
            )
            if (
                initial_identity is not None
                and initial_identity != opened_identity
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay database identity changed while "
                    "opening"
                )
            self._database_identity = opened_identity
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA recursive_triggers = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._initialise_schema()
            if (
                _database_identity(normalized, missing_ok=False)
                != self._database_identity
            ):
                raise BackendRunReplayGuardError(
                    "backend run replay database identity changed during "
                    "setup"
                )
        except Exception:
            self._conn.close()
            raise

    def __enter__(self) -> "SQLiteBackendRunReplayGuard":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _assert_bound_database_identity(self) -> None:
        current_identity = _database_identity(
            Path(self.path),
            missing_ok=False,
        )
        if current_identity != self._database_identity:
            raise BackendRunReplayGuardError(
                "backend run replay database identity changed"
            )

    def consume(
        self,
        *,
        backend_id: str,
        backend_run_id: str,
        authority_hash: str,
    ) -> bool:
        """Return True once, False for an identical replay, and raise on drift."""
        normalized_backend_id = _required_text(
            backend_id,
            field="backend_id",
        )
        normalized_backend_run_id = _required_text(
            backend_run_id,
            field="backend_run_id",
        )
        normalized_authority_hash = _canonical_sha256(authority_hash)
        with self._lock:
            self._assert_bound_database_identity()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existing = self._conn.execute(
                    """
                    SELECT authority_hash
                      FROM swe_bench_backend_run_consumptions
                     WHERE backend_id=? AND backend_run_id=?
                    """,
                    (
                        normalized_backend_id,
                        normalized_backend_run_id,
                    ),
                ).fetchone()
                if existing is not None:
                    observed_hash = str(existing["authority_hash"])
                    self._conn.execute("COMMIT")
                    self._assert_bound_database_identity()
                    if observed_hash != normalized_authority_hash:
                        raise BackendRunReplayConflictError(
                            "backend run was already consumed with a "
                            "different authority hash"
                        )
                    return False
                self._conn.execute(
                    """
                    INSERT INTO swe_bench_backend_run_consumptions(
                      schema_version, backend_id, backend_run_id,
                      authority_hash, consumed_at_ms
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        BACKEND_RUN_REPLAY_SCHEMA_VERSION,
                        normalized_backend_id,
                        normalized_backend_run_id,
                        normalized_authority_hash,
                        time.time_ns() // 1_000_000,
                    ),
                )
                self._conn.execute("COMMIT")
                self._assert_bound_database_identity()
                return True
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _initialise_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                _CANONICAL_REPLAY_TABLE_SQL.replace(
                    "CREATE TABLE ",
                    "CREATE TABLE IF NOT EXISTS ",
                    1,
                )
            )
            for trigger_sql in _CANONICAL_REPLAY_TRIGGER_SQL:
                self._conn.execute(
                    trigger_sql.replace(
                        "CREATE TRIGGER ",
                        "CREATE TRIGGER IF NOT EXISTS ",
                        1,
                    )
                )
            self._validate_schema()

    def _validate_schema(self) -> None:
        rows = self._conn.execute(
            """
            SELECT type, name, tbl_name, sql
              FROM sqlite_master
             WHERE (name=? OR tbl_name=?)
               AND sql IS NOT NULL
             ORDER BY type, name
            """,
            (_REPLAY_TABLE_NAME, _REPLAY_TABLE_NAME),
        ).fetchall()
        expected = {
            ("table", _REPLAY_TABLE_NAME): (
                _REPLAY_TABLE_NAME,
                _normalise_sqlite_ddl(_CANONICAL_REPLAY_TABLE_SQL),
            ),
            **{
                ("trigger", _sqlite_schema_object_name(trigger_sql)): (
                    _REPLAY_TABLE_NAME,
                    _normalise_sqlite_ddl(trigger_sql),
                )
                for trigger_sql in _CANONICAL_REPLAY_TRIGGER_SQL
            },
        }
        observed = {
            (str(row["type"]), str(row["name"])): (
                str(row["tbl_name"]),
                _normalise_sqlite_ddl(row["sql"]),
            )
            for row in rows
        }
        if observed != expected:
            raise BackendRunReplayGuardError(
                "backend run replay schema definition mismatch"
            )


def _required_text(value: object, *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} must be non-empty")
    return normalized


def _canonical_sha256(value: object) -> str:
    normalized = str(value).strip()
    if (
        len(normalized) != 64
        or normalized != normalized.casefold()
        or any(character not in "0123456789abcdef" for character in normalized)
    ):
        raise ValueError("authority_hash must be a canonical sha256 digest")
    return normalized


def _normalise_sqlite_ddl(value: object) -> str:
    return " ".join(str(value or "").strip().rstrip(";").split())


def _sqlite_schema_object_name(sql: str) -> str:
    return sql.split()[2]


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(
                "durable backend run replay guard path contains a symlink "
                f"component: {current}"
            )
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                "durable backend run replay guard path contains a "
                f"non-directory component: {current}"
            )


def _database_identity(
    path: Path,
    *,
    missing_ok: bool,
) -> tuple[int, int] | None:
    _reject_symlink_components(path)
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise BackendRunReplayGuardError(
            "backend run replay database is missing"
        ) from None
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            "durable backend run replay guard database must be a regular file"
        )
    if metadata.st_nlink != 1:
        raise ValueError(
            "durable backend run replay guard database must have one hard link"
        )
    return metadata.st_dev, metadata.st_ino


__all__ = [
    "BACKEND_RUN_REPLAY_SCHEMA_VERSION",
    "BackendRunReplayConflictError",
    "BackendRunReplayGuardError",
    "SQLiteBackendRunReplayGuard",
]
