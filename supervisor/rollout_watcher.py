"""Watches ~/.codex/sessions/.../rollout-*.jsonl and emits normalized events.

Each rollout file is JSONL: one event per line. We track per-file byte offset so
we never re-read what we've already parsed. New files are picked up automatically
by `watchfiles`.

The orchestrator-side registry (~/.codex-supervisor/runs/{session_id}.json)
joins target sessions to workflow runs. Complete lines that arrive before that
join are durably quarantined and replayed after registration.
"""
from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Awaitable
from watchfiles import awatch, Change

from .run_registry import (
    REUSABLE_SESSION_COMPLETION_POLICY,
    SINGLE_TURN_COMPLETION_POLICY,
    WORKFLOW_AGGREGATE_COMPLETION_POLICY,
    load_session_registration,
)
from .state import Decision, State
from .runtime_health import record_subsystem_health
from .target.types import ScopeContract

log = logging.getLogger(__name__)


@dataclass
class _DrainLockEntry:
    lock: asyncio.Lock
    users: int = 0


ROLLOUT_RE = re.compile(
    r"rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-([0-9a-f-]+)\.jsonl$"
)

_NORMALIZED_KINDS = frozenset({
    "run.started",
    "turn.started",
    "tool.started",
    "tool.completed",
    "agent.message",
    "turn.completed",
    "turn.failed",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "turn.aborted",
})

_KIND_ALIASES: dict[str, str] = {
    # Run lifecycle.
    "run_started": "run.started",
    "session_start": "run.started",
    "session_started": "run.started",
    "session_created": "run.started",
    "session_meta": "run.started",
    "thread_started": "run.started",
    "run_completed": "run.completed",
    "session_complete": "run.completed",
    "session_completed": "run.completed",
    "session_end": "run.completed",
    "session_ended": "run.completed",
    "session_idle": "turn.completed",
    "thread_completed": "run.completed",
    "run_failed": "run.failed",
    "session_error": "run.failed",
    "session_failed": "run.failed",
    "run_canceled": "run.cancelled",
    "run_cancelled": "run.cancelled",
    "session_canceled": "run.cancelled",
    "session_cancelled": "run.cancelled",
    # Turn lifecycle.
    "turn_aborted": "turn.aborted",
    "task_started": "turn.started",
    "turn_started": "turn.started",
    "task_complete": "turn.completed",
    "task_completed": "turn.completed",
    "turn_completed": "turn.completed",
    "task_failed": "turn.failed",
    "turn_failed": "turn.failed",
    # Messages and tools.
    "agent_message": "agent.message",
    "assistant_message": "agent.message",
    "function_call": "tool.started",
    "tool_call": "tool.started",
    "tool_use": "tool.started",
    "tool_started": "tool.started",
    "function_call_output": "tool.completed",
    "tool_call_output": "tool.completed",
    "tool_result": "tool.completed",
    "tool_completed": "tool.completed",
    "tool_search_call": "tool.started",
    "tool_search_output": "tool.completed",
    "web_search_call": "tool.started",
    "web_search_end": "tool.completed",
    "mcp_tool_call_begin": "tool.started",
    "mcp_tool_call_end": "tool.completed",
    "tool_execute_before": "tool.started",
    "tool_execute_after": "tool.completed",
}

_TERMINAL_STATUSES: dict[str, str] = {
    "turn.failed": "failed",
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
}

_QUARANTINE_SCHEMA = "supervisor-rollout-quarantine/v1"
_QUARANTINE_CHUNKED_SCHEMA = "supervisor-rollout-quarantine/v2"
_QUARANTINE_DIRNAME = ".rollout-quarantine"
DEFAULT_QUARANTINE_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_QUARANTINE_MAX_AGE_S = 7 * 24 * 3600


class RolloutWatcher:
    def __init__(self, sessions_root: str, registry_dir: str, state: State,
                 on_event: Callable[[str, dict], Awaitable[None]] | None = None,
                 startup_backfill_s: int = 300,
                 sweep_interval_s: int = 10,
                 quarantine_max_bytes: int = DEFAULT_QUARANTINE_MAX_BYTES,
                 quarantine_max_age_s: int = DEFAULT_QUARANTINE_MAX_AGE_S):
        self.sessions_root = Path(sessions_root)
        self.registry_dir = Path(registry_dir).expanduser()
        self.state = state
        self.on_event = on_event
        self.offsets: dict[Path, int] = {}
        self._drain_locks: dict[Path, _DrainLockEntry] = {}
        self.started_at = time.time()
        self.startup_backfill_s = startup_backfill_s
        self.sweep_interval_s = sweep_interval_s
        self.quarantine_max_bytes = max(1, int(quarantine_max_bytes))
        self.quarantine_max_age_s = max(1, int(quarantine_max_age_s))
        self._quarantine_spans: dict[Path, tuple[int, int]] = {}

    async def run(self) -> None:
        """Main loop. Watch the sessions root recursively, drain any growth on change."""
        log.info("RolloutWatcher: watching %s", self.sessions_root)
        await self._initial_backfill()

        sweep_task = asyncio.create_task(self._periodic_sweep(), name="rollout_sweep")
        try:
            async for changes in awatch(self.sessions_root, recursive=True):
                for change, path_str in changes:
                    p = Path(path_str)
                    if not ROLLOUT_RE.search(p.name):
                        continue
                    if change in (Change.added, Change.modified):
                        await self._drain_file_guarded(p)
                    elif change == Change.deleted:
                        self._forget_deleted_path(p)
        finally:
            sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)

    async def _periodic_sweep(self) -> None:
        while True:
            await asyncio.sleep(max(1, self.sweep_interval_s))
            await self.guarded_sweep_once()

    async def guarded_sweep_once(self) -> None:
        try:
            await self.sweep_once()
        except Exception as e:
            log.exception("RolloutWatcher sweep failed: %s", e)
            self._record_health(
                subsystem="rollout_watcher.sweep",
                status="degraded",
                reason="sweep_exception",
                details={
                    "exception_type": type(e).__name__,
                    "error": str(e),
                },
            )

    async def sweep_once(self) -> None:
        """Poll known/recent rollout files for growth missed by filesystem watches."""
        await self._replay_quarantines_once()
        cutoff = self.started_at - self.startup_backfill_s
        for p in self.sessions_root.rglob("rollout-*.jsonl"):
            try:
                stat = p.stat()
            except FileNotFoundError:
                continue
            known = (
                self.state.get_tail_offset(str(p)) > 0
                or self._quarantine_path(p).is_file()
            )
            if not known and stat.st_mtime < cutoff:
                self.offsets[p] = stat.st_size
                self.state.set_tail_offset(str(p), stat.st_size)
                continue
            await self._drain_file_guarded(p)

    async def _initial_backfill(self) -> None:
        """Drain recent/known files, but skip old unseen historical rollouts.

        On first install, ~/.codex/sessions may contain months of completed
        rollouts. Importing all of them marks old sessions as active and can
        flood recovery planning. We advance unseen old files to EOF instead;
        future appends are still observed because the durable tail offset is set.
        """
        await self._replay_quarantines_once()
        cutoff = self.started_at - self.startup_backfill_s
        for p in self.sessions_root.rglob("rollout-*.jsonl"):
            try:
                stat = p.stat()
            except FileNotFoundError:
                continue
            known = (
                self.state.get_tail_offset(str(p)) > 0
                or self._quarantine_path(p).is_file()
            )
            if not known and stat.st_mtime < cutoff:
                self.offsets[p] = stat.st_size
                self.state.set_tail_offset(str(p), stat.st_size)
                continue
            await self._drain_file_guarded(p)

    async def _drain_file_guarded(self, path: Path) -> None:
        try:
            await self._drain_file(path)
        except Exception as e:
            log.exception("RolloutWatcher drain failed for %s: %s", path, e)
            self._record_health(
                subsystem="rollout_watcher.drain",
                status="degraded",
                reason="drain_exception",
                details={
                    "path": str(path),
                    "exception_type": type(e).__name__,
                    "error": str(e),
                },
            )

    async def _drain_file(self, path: Path) -> None:
        async with self._drain_path_lock(path):
            await self._drain_file_locked(path)

    @asynccontextmanager
    async def _drain_path_lock(
        self,
        path: Path,
    ) -> AsyncIterator[None]:
        entry = self._drain_locks.get(path)
        if entry is None:
            entry = _DrainLockEntry(lock=asyncio.Lock())
            self._drain_locks[path] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if (
                entry.users == 0
                and self._drain_locks.get(path) is entry
            ):
                self._drain_locks.pop(path, None)

    def _forget_deleted_path(self, path: Path) -> None:
        self.offsets.pop(path, None)

    async def _drain_file_locked(self, path: Path) -> None:
        quarantine_path = self._quarantine_path(path)
        if quarantine_path.is_file():
            await self._replay_quarantine(quarantine_path)

        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        start = self.offsets.get(path)
        if start is None:
            start = self.state.get_tail_offset(str(path))
        if size <= start:
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                pos = start
                lines: list[bytes] = []
                while True:
                    raw = f.readline()
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        break
                    pos += len(raw)
                    lines.append(raw)
        except OSError as e:
            log.warning("read failed for %s: %s", path, e)
            self._record_health(
                subsystem="rollout_watcher.drain",
                status="degraded",
                reason="read_exception",
                details={
                    "path": str(path),
                    "exception_type": type(e).__name__,
                    "error": str(e),
                },
            )
            return

        if not lines:
            return
        session_id = self._session_id_from_path(path)
        captured_cwd = _captured_rollout_cwd(lines)
        registration, run_id = self._resolve_run(
            session_id,
            path,
            captured_cwd=captured_cwd,
        )
        if run_id is None:
            quarantined_end = self._write_quarantine(
                path=path,
                session_id=session_id,
                start_offset=start,
                raw_lines=lines,
                captured_cwd=captured_cwd,
            )
            # The durable cursor stays pinned until a real workflow/session
            # join exists; the quarantine sidecar is the durable copy used if
            # the source file disappears before registration. The in-memory
            # cursor advances past already-quarantined bytes so each drain
            # appends only new growth.
            self.offsets[path] = max(start, quarantined_end)
            return

        await self._ingest_lines(
            path=path,
            lines=lines,
            start_offset=start,
            session_id=session_id,
            registration=registration,
            run_id=run_id,
        )

    def _resolve_run(
        self,
        session_id: str,
        rollout_path: Path,
        *,
        captured_cwd: str | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        registration_path = self.registry_dir / f"{session_id}.json"
        try:
            registration = load_session_registration(
                self.registry_dir,
                session_id,
            )
        except (OSError, ValueError) as e:
            log.warning(
                "rollout session %s has a rejected registration sidecar: %s",
                session_id,
                e,
            )
            registration = None
        if (
            registration is None
            and (
                registration_path.is_file()
                or registration_path.is_symlink()
            )
        ):
            log.warning(
                "rollout session %s registration sidecar failed strict load",
                session_id,
            )
            return None, None
        if (
            registration is not None
            and not _registration_cwd_matches(
                registration,
                captured_cwd=captured_cwd,
                require_runtime_cwd=(
                    self.state.get_tail_offset(str(rollout_path)) == 0
                ),
            )
        ):
            log.warning(
                "rollout session %s reported cwd inconsistent with its "
                "launch-bound registration",
                session_id,
            )
            return registration, None
        if registration is not None:
            registered_run_id = str(
                registration.get("target_run_id")
                or registration.get("run_id")
                or registration["workflow_run_id"]
            )
            run_row = self.state.get_run(registered_run_id)
        else:
            run_row = self.state.get_run_by_session(session_id)
        if run_row is not None:
            if (
                registration is not None
                and str(run_row["session_id"] or "") != session_id
            ):
                log.warning(
                    "run registration %s is not bound to rollout session %s",
                    registration["workflow_run_id"],
                    session_id,
                )
                return registration, None
            return registration, str(run_row["run_id"])
        return registration, self._register_run(
            session_id,
            rollout_path,
            registration=registration,
        )

    async def _ingest_lines(
        self,
        *,
        path: Path,
        lines: list[bytes],
        start_offset: int,
        session_id: str,
        registration: dict[str, Any] | None,
        run_id: str,
    ) -> None:
        # Parse line-by-line. JSONL means one event per line; partial last
        # lines are left for the next drain and the persisted offset is not
        # advanced past them.
        parsed_offset = start_offset
        for raw_line in lines:
            line_start = parsed_offset
            parsed_offset += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                self.state.ingest_source_line(
                    run_id=run_id,
                    source="rollout",
                    path=str(path),
                    start_offset=line_start,
                    end_offset=parsed_offset,
                    raw_line=raw_line,
                )
                self.offsets[path] = parsed_offset
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                ingestion = self.state.ingest_source_line(
                    run_id=run_id,
                    source="rollout",
                    path=str(path),
                    start_offset=line_start,
                    end_offset=parsed_offset,
                    raw_line=raw_line,
                    dead_letter={
                        "reason": "json_decode_exception",
                        "exception_type": type(e).__name__,
                        "error": str(e),
                    },
                )
                self.offsets[path] = parsed_offset
                if ingestion.inserted:
                    self._record_health(
                        run_id=run_id,
                        subsystem="rollout_watcher.parse",
                        status="degraded",
                        reason="json_decode_exception",
                        details={
                            "path": str(path),
                            "byte_offset": parsed_offset,
                            "exception_type": type(e).__name__,
                            "error": str(e),
                        },
                        tail_path=str(path),
                        tail_offset=parsed_offset,
                    )
                continue
            if not isinstance(event, dict):
                ingestion = self.state.ingest_source_line(
                    run_id=run_id,
                    source="rollout",
                    path=str(path),
                    start_offset=line_start,
                    end_offset=parsed_offset,
                    raw_line=raw_line,
                    dead_letter={
                        "reason": "invalid_event_shape",
                        "observed_type": type(event).__name__,
                    },
                )
                self.offsets[path] = parsed_offset
                if ingestion.inserted:
                    self._record_health(
                        run_id=run_id,
                        subsystem="rollout_watcher.parse",
                        status="degraded",
                        reason="invalid_event_shape",
                        details={
                            "path": str(path),
                            "byte_offset": parsed_offset,
                            "observed_type": type(event).__name__,
                        },
                        tail_path=str(path),
                        tail_offset=parsed_offset,
                    )
                continue
            kinds = self._extract_kinds(event)
            normalized_events: list[tuple[str, dict[str, Any]]] = []
            for kind in kinds:
                event_payload = dict(event)
                if len(kinds) > 1:
                    event_payload["normalized_from_shared_entry"] = True
                    event_payload["normalized_kind"] = kind
                if registration is not None:
                    event_payload["workflow_run_id"] = str(
                        registration["workflow_run_id"]
                    )
                    event_payload["run_id"] = run_id
                    if registration.get("target_run_id"):
                        event_payload["target_run_id"] = str(
                            registration["target_run_id"]
                        )
                    else:
                        event_payload.pop("target_run_id", None)
                    event_payload["target_session_id"] = session_id
                    task_id = registration.get("task_id")
                    if task_id:
                        event_payload["task_id"] = str(task_id)
                    else:
                        event_payload.pop("task_id", None)
                normalized_events.append((kind, event_payload))
            terminal_kind = next(
                (
                    kind
                    for kind, _payload in reversed(normalized_events)
                    if self._terminal_status(
                        kind,
                        registration=registration,
                    ) is not None
                ),
                None,
            )
            terminal_status = (
                self._terminal_status(
                    terminal_kind,
                    registration=registration,
                )
                if terminal_kind is not None
                else None
            )
            decision = (
                Decision(
                    kind="evaluate_run",
                    run_id=run_id,
                    payload={
                        "final_status": terminal_status,
                        "final_event_kind": terminal_kind,
                    },
                )
                if terminal_kind is not None
                else None
            )
            ingestion = self.state.ingest_source_line(
                run_id=run_id,
                source="rollout",
                path=str(path),
                start_offset=line_start,
                end_offset=parsed_offset,
                raw_line=raw_line,
                events=normalized_events,
                terminal_status=terminal_status,
                terminal_event_kind=terminal_kind,
                decision=decision,
            )
            self.offsets[path] = parsed_offset
            if not ingestion.inserted:
                continue
            for event_id, (kind, event_payload) in zip(
                ingestion.event_ids,
                normalized_events,
            ):
                if self.on_event:
                    try:
                        # Keep the callback shape compatible with consumers that
                        # still inspect the raw Codex wrapper (event_msg /
                        # response_item), while exposing the canonical kind.
                        callback_kind = (
                            event.get("type")
                            or event.get("kind")
                            or event.get("event")
                            or kind
                        )
                        await self.on_event(run_id, {
                            **event_payload,
                            "id": event_id,
                            "kind": callback_kind,
                            "normalized_kind": kind,
                        })
                    except Exception as e:
                        log.exception(
                            "RolloutWatcher on_event failed for %s: %s",
                            path,
                            e,
                        )
                        self._record_health(
                            run_id=run_id,
                            subsystem="rollout_watcher.on_event",
                            status="degraded",
                            reason="callback_exception",
                            details={
                                "path": str(path),
                                "event_id": event_id,
                                "event_kind": kind,
                                "exception_type": type(e).__name__,
                                "error": str(e),
                            },
                        )

    @staticmethod
    def _terminal_status(
        kind: str,
        *,
        registration: dict[str, Any] | None,
    ) -> str | None:
        if registration is None:
            return _TERMINAL_STATUSES.get(kind)
        completion_policy = registration.get("completion_policy")
        if completion_policy == SINGLE_TURN_COMPLETION_POLICY:
            if kind == "turn.completed":
                return "completed"
            if kind == "turn.aborted":
                return "cancelled"
            return _TERMINAL_STATUSES.get(kind)
        if completion_policy in {
            REUSABLE_SESSION_COMPLETION_POLICY,
            WORKFLOW_AGGREGATE_COMPLETION_POLICY,
        }:
            return None
        # Legacy sidecars without an explicit policy retain their historical
        # run-terminal behavior; new registrations are always policy-bearing.
        return _TERMINAL_STATUSES.get(kind)

    async def _replay_quarantines_once(self) -> None:
        root = self._quarantine_root()
        if not root.is_dir():
            return
        for quarantine_path in sorted(root.glob("*.json")):
            header = self._load_quarantine_header(quarantine_path)
            if header is None:
                self._drop_undecodable_quarantine(quarantine_path)
                continue
            path = Path(header["rollout_path"])
            async with self._drain_path_lock(path):
                await self._replay_quarantine(quarantine_path)

    async def _replay_quarantine(self, quarantine_path: Path) -> bool:
        header = self._load_quarantine_header(quarantine_path)
        if header is None:
            return False
        path = Path(header["rollout_path"])
        session_id = str(header["session_id"])
        captured_cwd = header.get("captured_cwd")
        registration, run_id = self._resolve_run(
            session_id,
            path,
            captured_cwd=(
                str(captured_cwd)
                if isinstance(captured_cwd, str) and captured_cwd
                else None
            ),
        )
        if run_id is None:
            self._enforce_quarantine_age_cap(quarantine_path, header=header)
            return False
        quarantine = self._load_quarantine(quarantine_path)
        if quarantine is None:
            span = self._quarantine_spans.get(quarantine_path)
            if span is not None:
                self._drop_quarantine(
                    quarantine_path,
                    rollout_path=path,
                    session_id=session_id,
                    reason="quarantine_payload_corrupt",
                    start_offset=span[0],
                    end_offset=span[1],
                )
            return False
        start_offset = int(quarantine["start_offset"])
        end_offset = int(quarantine["end_offset"])
        raw_bytes = quarantine["raw_bytes"]

        durable_offset = self.state.get_tail_offset(str(path))
        if durable_offset >= end_offset:
            self.offsets[path] = durable_offset
            self._delete_quarantine(quarantine_path)
            return True
        if durable_offset < start_offset:
            self._record_health(
                run_id=run_id,
                subsystem="rollout_watcher.quarantine",
                status="degraded",
                reason="quarantine_offset_regression",
                details={
                    "path": str(path),
                    "durable_offset": durable_offset,
                    "quarantine_start_offset": start_offset,
                    "quarantine_end_offset": end_offset,
                },
            )
            return False

        replay_lines: list[bytes] = []
        line_start = start_offset
        for raw_line in raw_bytes.splitlines(keepends=True):
            line_end = line_start + len(raw_line)
            if line_end <= durable_offset:
                line_start = line_end
                continue
            if line_start != durable_offset and not replay_lines:
                self._record_health(
                    run_id=run_id,
                    subsystem="rollout_watcher.quarantine",
                    status="degraded",
                    reason="quarantine_offset_not_on_line_boundary",
                    details={
                        "path": str(path),
                        "durable_offset": durable_offset,
                        "line_start": line_start,
                        "line_end": line_end,
                    },
                )
                return False
            replay_lines.append(raw_line)
            line_start = line_end

        if not replay_lines or line_start != end_offset:
            self._record_health(
                run_id=run_id,
                subsystem="rollout_watcher.quarantine",
                status="degraded",
                reason="quarantine_payload_incomplete",
                details={
                    "path": str(path),
                    "durable_offset": durable_offset,
                    "quarantine_start_offset": start_offset,
                    "quarantine_end_offset": end_offset,
                    "decoded_end_offset": line_start,
                },
            )
            return False

        await self._ingest_lines(
            path=path,
            lines=replay_lines,
            start_offset=durable_offset,
            session_id=session_id,
            registration=registration,
            run_id=run_id,
        )
        durable_offset = self.state.get_tail_offset(str(path))
        self.offsets[path] = durable_offset
        if durable_offset >= end_offset:
            self._delete_quarantine(quarantine_path)
            return True
        return False

    def _write_quarantine(
        self,
        *,
        path: Path,
        session_id: str,
        start_offset: int,
        raw_lines: list[bytes],
        captured_cwd: str | None,
    ) -> int:
        """Durably quarantine unjoined bytes; returns the quarantined end offset.

        The sidecar is chunked JSONL: one header line followed by append-only
        base64 chunk lines, so steady growth never rewrites already-persisted
        bytes.
        """
        raw_bytes = b"".join(raw_lines)
        end_offset = start_offset + len(raw_bytes)
        quarantine_path = self._quarantine_path(path)
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            self._read_quarantine_records(quarantine_path)
            if quarantine_path.is_file()
            else None
        )
        existing_start: int | None = None
        existing_end: int | None = None
        if existing is not None:
            existing_header, existing_chunks = existing
            existing_start = int(existing_header["start_offset"])
            existing_end = (
                int(existing_chunks[-1]["end_offset"])
                if existing_chunks
                else existing_start
            )
            if existing_start <= start_offset and existing_end >= end_offset:
                return existing_end
        retained_start = (
            existing_start
            if existing_start is not None and existing_start <= start_offset
            else start_offset
        )
        if end_offset - retained_start > self.quarantine_max_bytes:
            self._drop_quarantine(
                quarantine_path,
                rollout_path=path,
                session_id=session_id,
                reason="quarantine_size_cap_exceeded",
                start_offset=retained_start,
                end_offset=end_offset,
            )
            return end_offset
        if (
            existing is not None
            and existing_header.get("schema_version")
            == _QUARANTINE_CHUNKED_SCHEMA
            and existing_start is not None
            and existing_end is not None
            and existing_start <= start_offset <= existing_end < end_offset
        ):
            chunk_bytes = raw_bytes[existing_end - start_offset:]
            self._append_quarantine_chunk(
                quarantine_path,
                end_offset=end_offset,
                chunk_bytes=chunk_bytes,
            )
            self._quarantine_spans[quarantine_path] = (
                existing_start,
                end_offset,
            )
            return end_offset

        header = {
            "schema_version": _QUARANTINE_CHUNKED_SCHEMA,
            "rollout_path": str(path),
            "session_id": session_id,
            "start_offset": start_offset,
            "captured_cwd": captured_cwd,
            "created_at": int(time.time()),
        }
        chunks: list[dict[str, Any]] = []
        chunk_start = start_offset
        if (
            existing is not None
            and existing_start is not None
            and existing_end is not None
            and existing_start <= start_offset <= existing_end
        ):
            header["start_offset"] = existing_start
            header["captured_cwd"] = (
                existing_header.get("captured_cwd") or captured_cwd
            )
            created_at = existing_header.get("created_at")
            if created_at:
                header["created_at"] = int(created_at)
            chunks.extend(existing_chunks)
            chunk_start = existing_end
        chunks.append({
            "end_offset": end_offset,
            "raw_bytes_b64": base64.b64encode(
                raw_bytes[chunk_start - start_offset:]
            ).decode("ascii"),
        })
        temp_path = quarantine_path.with_name(
            f".{quarantine_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(header, f, sort_keys=True, separators=(",", ":"))
                f.write("\n")
                for chunk in chunks:
                    json.dump(chunk, f, sort_keys=True, separators=(",", ":"))
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, quarantine_path)
            try:
                directory_fd = os.open(quarantine_path.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        self._quarantine_spans[quarantine_path] = (
            int(header["start_offset"]),
            end_offset,
        )
        return end_offset

    def _append_quarantine_chunk(
        self,
        quarantine_path: Path,
        *,
        end_offset: int,
        chunk_bytes: bytes,
    ) -> None:
        record = json.dumps(
            {
                "end_offset": end_offset,
                "raw_bytes_b64": base64.b64encode(chunk_bytes).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with quarantine_path.open("ab") as f:
            f.write(record.encode("ascii") + b"\n")
            f.flush()
            os.fsync(f.fileno())

    def _read_quarantine_records(
        self,
        quarantine_path: Path,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        try:
            resolved_root = self._quarantine_root().resolve()
            resolved_path = quarantine_path.resolve(strict=True)
            resolved_path.relative_to(resolved_root)
            contents = resolved_path.read_bytes()
            complete_end = contents.rfind(b"\n") + 1
            if complete_end <= 0:
                return None
            torn_suffix_bytes = len(contents) - complete_end
            lines = contents[:complete_end].decode("utf-8").splitlines()
            if not lines:
                return None
            first = json.loads(lines[0])
            if not isinstance(first, dict):
                return None
            if first.get("schema_version") == _QUARANTINE_SCHEMA:
                header = {
                    "schema_version": _QUARANTINE_SCHEMA,
                    "rollout_path": str(first["rollout_path"]),
                    "session_id": str(first["session_id"]),
                    "start_offset": int(first["start_offset"]),
                    "captured_cwd": first.get("captured_cwd"),
                    "created_at": int(first.get("updated_at") or 0),
                }
                chunks: list[dict[str, Any]] = [{
                    "end_offset": int(first["end_offset"]),
                    "raw_bytes_b64": str(first["raw_bytes_b64"]),
                }]
            elif first.get("schema_version") == _QUARANTINE_CHUNKED_SCHEMA:
                header = {
                    "schema_version": _QUARANTINE_CHUNKED_SCHEMA,
                    "rollout_path": str(first["rollout_path"]),
                    "session_id": str(first["session_id"]),
                    "start_offset": int(first["start_offset"]),
                    "captured_cwd": first.get("captured_cwd"),
                    "created_at": int(first.get("created_at") or 0),
                }
                chunks = []
                for line in lines[1:]:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        return None
                    chunks.append({
                        "end_offset": int(record["end_offset"]),
                        "raw_bytes_b64": str(record["raw_bytes_b64"]),
                    })
            else:
                return None
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            log.warning("bad rollout quarantine %s: %s", quarantine_path, e)
            return None
        rollout_path = str(header["rollout_path"])
        session_id = str(header["session_id"])
        start_offset = int(header["start_offset"])
        if not rollout_path or not session_id or start_offset < 0:
            return None
        previous_end = start_offset
        for chunk in chunks:
            end_offset = int(chunk["end_offset"])
            if end_offset <= previous_end:
                return None
            previous_end = end_offset
        rollout = Path(rollout_path)
        if (
            self._session_id_from_path(rollout) != session_id
            or self._quarantine_path(rollout).resolve(strict=False)
            != quarantine_path.resolve(strict=False)
        ):
            return None
        if torn_suffix_bytes:
            self._truncate_quarantine_suffix(
                resolved_path,
                complete_end=complete_end,
            )
        self._quarantine_spans[quarantine_path] = (start_offset, previous_end)
        return header, chunks

    @staticmethod
    def _truncate_quarantine_suffix(
        quarantine_path: Path,
        *,
        complete_end: int,
    ) -> None:
        """Remove an append that never reached its newline commit boundary."""
        with quarantine_path.open("r+b") as handle:
            handle.truncate(complete_end)
            handle.flush()
            os.fsync(handle.fileno())

    def _load_quarantine_header(
        self,
        quarantine_path: Path,
    ) -> dict[str, Any] | None:
        records = self._read_quarantine_records(quarantine_path)
        if records is None:
            return None
        return records[0]

    def _load_quarantine(self, quarantine_path: Path) -> dict[str, Any] | None:
        records = self._read_quarantine_records(quarantine_path)
        if records is None:
            return None
        header, chunks = records
        start_offset = int(header["start_offset"])
        previous_end = start_offset
        pieces: list[bytes] = []
        try:
            for chunk in chunks:
                end_offset = int(chunk["end_offset"])
                piece = base64.b64decode(
                    str(chunk["raw_bytes_b64"]),
                    validate=True,
                )
                if len(piece) != end_offset - previous_end:
                    return None
                pieces.append(piece)
                previous_end = end_offset
        except (ValueError, TypeError) as e:
            log.warning("bad rollout quarantine %s: %s", quarantine_path, e)
            return None
        raw_bytes = b"".join(pieces)
        if not raw_bytes or not raw_bytes.endswith(b"\n"):
            return None
        return {
            **header,
            "rollout_path": str(header["rollout_path"]),
            "session_id": str(header["session_id"]),
            "start_offset": start_offset,
            "end_offset": previous_end,
            "raw_bytes": raw_bytes,
        }

    def _enforce_quarantine_age_cap(
        self,
        quarantine_path: Path,
        *,
        header: dict[str, Any],
    ) -> None:
        created_at = int(header.get("created_at") or 0)
        if not created_at:
            try:
                created_at = int(quarantine_path.stat().st_mtime)
            except OSError:
                return
        if not created_at:
            return
        if int(time.time()) - created_at <= self.quarantine_max_age_s:
            return
        span = self._quarantine_spans.get(quarantine_path)
        if span is None:
            return
        self._drop_quarantine(
            quarantine_path,
            rollout_path=Path(str(header["rollout_path"])),
            session_id=str(header["session_id"]),
            reason="quarantine_age_cap_exceeded",
            start_offset=span[0],
            end_offset=span[1],
        )

    def _drop_undecodable_quarantine(self, quarantine_path: Path) -> None:
        try:
            age = time.time() - quarantine_path.stat().st_mtime
        except OSError:
            return
        if age <= self.quarantine_max_age_s:
            return
        self._record_health(
            subsystem="rollout_watcher.quarantine",
            status="degraded",
            reason="quarantine_dropped_undecodable",
            details={"quarantine_path": str(quarantine_path)},
        )
        self._delete_quarantine(quarantine_path)

    def _drop_quarantine(
        self,
        quarantine_path: Path,
        *,
        rollout_path: Path,
        session_id: str,
        reason: str,
        start_offset: int,
        end_offset: int,
    ) -> None:
        self._record_health(
            subsystem="rollout_watcher.quarantine",
            status="degraded",
            reason=reason,
            details={
                "path": str(rollout_path),
                "session_id": session_id,
                "quarantine_path": str(quarantine_path),
                "dropped_start_offset": int(start_offset),
                "dropped_end_offset": int(end_offset),
            },
        )
        durable_offset = self.state.get_tail_offset(str(rollout_path))
        reconciled_offset = max(
            durable_offset,
            self.offsets.get(rollout_path, 0),
            int(end_offset),
        )
        if reconciled_offset > durable_offset:
            self.state.set_tail_offset(str(rollout_path), reconciled_offset)
        self.offsets[rollout_path] = reconciled_offset
        self._delete_quarantine(quarantine_path)

    def _delete_quarantine(self, quarantine_path: Path) -> None:
        self._quarantine_spans.pop(quarantine_path, None)
        try:
            quarantine_path.unlink()
        except FileNotFoundError:
            pass

    def _quarantine_root(self) -> Path:
        return self.registry_dir / _QUARANTINE_DIRNAME

    def _quarantine_path(self, rollout_path: Path) -> Path:
        digest = hashlib.sha256(str(rollout_path).encode("utf-8")).hexdigest()
        return self._quarantine_root() / f"{digest}.json"

    def _session_id_from_path(self, path: Path) -> str:
        m = ROLLOUT_RE.search(path.name)
        return m.group(2) if m else path.stem

    def _register_run(
        self,
        session_id: str,
        rollout_path: Path,
        *,
        registration: dict[str, Any] | None = None,
    ) -> str | None:
        """Register a known workflow/legacy sidecar without inventing a bare run."""
        task = None
        scope = ScopeContract()
        config_snapshot: dict[str, Any] = {"source": "rollout_watcher"}
        meta = registration
        if not isinstance(meta, dict):
            return None
        task = meta.get("task") or meta.get("intent")
        if isinstance(meta.get("scope_contract"), dict):
            scope = ScopeContract.from_dict(meta["scope_contract"])
        elif isinstance(meta.get("scope_hints"), list):
            scope = ScopeContract(
                allowed_paths=tuple(str(p) for p in meta["scope_hints"]),
            )
        if isinstance(meta.get("config_snapshot"), dict):
            config_snapshot = meta["config_snapshot"]
        registered_run_id = (
            meta.get("target_run_id")
            or meta.get("run_id")
            or meta.get("workflow_run_id")
        )
        if not registered_run_id:
            return None
        run_id = str(registered_run_id)
        self.state.register_run(
            run_id=run_id, session_id=session_id,
            rollout_path=str(rollout_path), task=task, scope=scope,
            target_kind=str(meta.get("target_kind") or "codex"),
            config_snapshot=config_snapshot,
        )
        registered = self.state.get_run(run_id)
        if (
            registered is None
            or str(registered["session_id"] or "") != session_id
        ):
            log.warning(
                "registered run %s is not bound to rollout session %s",
                run_id,
                session_id,
            )
            return None
        return run_id

    def _record_health(
        self,
        *,
        subsystem: str,
        status: str,
        reason: str,
        details: dict[str, Any] | None = None,
        run_id: str | None = None,
        tail_path: str | None = None,
        tail_offset: int | None = None,
    ) -> None:
        record_subsystem_health(
            self.state,
            subsystem=subsystem,
            status=status,
            reason=reason,
            details=details,
            run_id=run_id or "rollout_watcher",
            tail_path=tail_path,
            tail_offset=tail_offset,
        )

    @staticmethod
    def _extract_kind(event: dict[str, Any]) -> str:
        """Map raw Claude/Codex/OpenCode events to the shared event taxonomy.

        The raw event remains the durable payload. Only the indexed event kind
        is canonicalized, so replay and diagnostics retain source fidelity.
        """
        top_kind = _text(event.get("type") or event.get("kind") or event.get("event"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        nested_kind = _text(
            payload.get("type")
            or payload.get("kind")
            or payload.get("event")
        )

        # Claude Code transcript entries carry lifecycle state under message.
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if top_kind == "assistant":
            stop_reason = _canonical_raw_kind(message.get("stop_reason"))
            if stop_reason == "end_turn":
                return "turn.completed"
            if stop_reason in {"cancelled", "canceled", "aborted"}:
                return "run.cancelled"
            if event.get("isApiErrorMessage") or event.get("error"):
                return "run.failed"
            if stop_reason == "tool_use":
                return "tool.started"
            return "agent.message"
        if top_kind == "user":
            if _contains_content_type(message.get("content"), "tool_result"):
                return "tool.completed"
            return "turn.started"
        if top_kind == "result":
            subtype = _canonical_raw_kind(event.get("subtype"))
            terminal_reason = _canonical_raw_kind(event.get("terminal_reason"))
            if event.get("is_error") or subtype.startswith("error") or terminal_reason == "failed":
                return "run.failed"
            if subtype in {"cancelled", "canceled"} or terminal_reason in {"cancelled", "canceled"}:
                return "run.cancelled"
            return "run.completed"
        if top_kind == "system" and _canonical_raw_kind(event.get("subtype")) == "init":
            return "run.started"

        # OpenCode emits message metadata and session state as separate events.
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        if top_kind == "message.updated" and _canonical_raw_kind(info.get("role")) == "assistant":
            message_time = info.get("time") if isinstance(info.get("time"), dict) else {}
            if message_time.get("completed"):
                return "turn.completed"
            return "agent.message"
        if top_kind == "session.status":
            status_payload = (
                payload.get("status")
                if isinstance(payload.get("status"), dict)
                else {}
            )
            status = _canonical_raw_kind(
                status_payload.get("type")
                or payload.get("status")
            )
            if status == "idle":
                return "turn.completed"
            if status == "busy":
                return "turn.started"

        # OpenCode message-part events expose tool state under payload.part.
        part = payload.get("part") if isinstance(payload.get("part"), dict) else {}
        if part:
            part_kind = _canonical_raw_kind(part.get("type"))
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = _canonical_raw_kind(state.get("status") or part.get("status"))
            if part_kind == "tool":
                if status in {"completed", "error", "failed"}:
                    return "tool.completed"
                return "tool.started"
            if part_kind in {"text", "reasoning"}:
                return "agent.message"

        # Codex rollouts use top-level wrappers and put the actual event name
        # under payload.type. Synthetic legacy top-level events remain intact.
        candidate = nested_kind if nested_kind else top_kind
        canonical = _canonical_raw_kind(candidate)
        if canonical in _NORMALIZED_KINDS:
            return canonical
        if nested_kind:
            mapped = _KIND_ALIASES.get(canonical)
            if mapped:
                if canonical == "message" and _canonical_raw_kind(payload.get("role")) != "assistant":
                    return f"{top_kind}.{canonical}" if top_kind else canonical
                return mapped
            if canonical == "message" and _canonical_raw_kind(payload.get("role")) == "assistant":
                return "agent.message"

        # Canonicalize both captured provider events and legacy flattened
        # fixtures. The raw source shape remains in payload_json.
        mapped = _KIND_ALIASES.get(_canonical_raw_kind(top_kind))
        if mapped:
            return mapped
        if "." in top_kind:
            if top_kind in _NORMALIZED_KINDS:
                return top_kind
        if top_kind == "session_meta":
            return "run.started"
        return top_kind or nested_kind or "unknown"

    @staticmethod
    def _extract_kinds(event: dict[str, Any]) -> tuple[str, ...]:
        """Preserve Claude text evidence alongside lifecycle normalization."""
        primary = RolloutWatcher._extract_kind(event)
        if _claude_assistant_has_text(event) and primary != "agent.message":
            return ("agent.message", primary)
        return (primary,)


def _canonical_raw_kind(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(".", "_").replace(" ", "_")


def _contains_content_type(content: Any, expected: str) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict)
        and _canonical_raw_kind(item.get("type")) == expected
        for item in content
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _claude_assistant_has_text(event: dict[str, Any]) -> bool:
    if _canonical_raw_kind(event.get("type")) != "assistant":
        return False
    message = event.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict)
        and _canonical_raw_kind(item.get("type")) in {"", "text"}
        and isinstance(item.get("text"), str)
        and bool(item["text"].strip())
        for item in content
    )


def _captured_rollout_cwd(lines: list[bytes]) -> str | None:
    """Extract a runtime-reported cwd without trusting arbitrary free text."""
    for raw_line in lines[:20]:
        try:
            event = json.loads(raw_line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        pending: list[dict[str, Any]] = [event]
        while pending:
            value = pending.pop()
            for key, child in value.items():
                normalized = str(key).casefold().replace("-", "_")
                if normalized in {
                    "cwd",
                    "working_directory",
                    "workspace",
                    "workspace_path",
                } and isinstance(child, str) and child.strip():
                    return child.strip()
                if isinstance(child, dict):
                    pending.append(child)
    return None


def _registration_cwd_matches(
    registration: dict[str, Any],
    *,
    captured_cwd: str | None,
    require_runtime_cwd: bool = False,
) -> bool:
    """Validate cwd only after a session/receipt join already selected the run."""
    snapshot = registration.get("config_snapshot")
    registered_cwd = str(
        snapshot.get("cwd")
        if isinstance(snapshot, dict)
        else ""
    ).strip()
    runtime_owned = bool(
        registration.get("runtime_run_id")
        or registration.get("runtime_result_hash")
        or (
            isinstance(snapshot, dict)
            and snapshot.get("source") == "workflow_runtime_session"
        )
    )
    if runtime_owned and not registered_cwd:
        return False
    if runtime_owned and require_runtime_cwd and not captured_cwd:
        return False
    if not captured_cwd:
        return True
    if not registered_cwd:
        return True
    try:
        return (
            Path(registered_cwd).expanduser().resolve()
            == Path(captured_cwd).expanduser().resolve()
        )
    except (OSError, RuntimeError):
        return False
