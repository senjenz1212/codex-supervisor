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
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Awaitable
from watchfiles import awatch, Change

from .run_registry import (
    load_session_registration,
)
from .state import State
from .runtime_health import record_subsystem_health
from .target.types import ScopeContract

log = logging.getLogger(__name__)

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
    "session_idle": "run.completed",
    "thread_completed": "run.completed",
    "run_failed": "run.failed",
    "session_error": "run.failed",
    "session_failed": "run.failed",
    "run_canceled": "run.cancelled",
    "run_cancelled": "run.cancelled",
    "session_canceled": "run.cancelled",
    "session_cancelled": "run.cancelled",
    "turn_aborted": "run.cancelled",
    # Turn lifecycle.
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
    "turn.completed": "completed",
    "turn.failed": "failed",
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
}

_QUARANTINE_SCHEMA = "supervisor-rollout-quarantine/v1"
_QUARANTINE_DIRNAME = ".rollout-quarantine"


class RolloutWatcher:
    def __init__(self, sessions_root: str, registry_dir: str, state: State,
                 on_event: Callable[[str, dict], Awaitable[None]] | None = None,
                 startup_backfill_s: int = 300,
                 sweep_interval_s: int = 10):
        self.sessions_root = Path(sessions_root)
        self.registry_dir = Path(registry_dir).expanduser()
        self.state = state
        self.on_event = on_event
        self.offsets: dict[Path, int] = {}
        self._drain_locks: dict[Path, asyncio.Lock] = {}
        self.started_at = time.time()
        self.startup_backfill_s = startup_backfill_s
        self.sweep_interval_s = sweep_interval_s

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
                        self.offsets.pop(p, None)
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
        lock = self._drain_locks.setdefault(path, asyncio.Lock())
        async with lock:
            await self._drain_file_locked(path)

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
            self._write_quarantine(
                path=path,
                session_id=session_id,
                start_offset=start,
                raw_lines=lines,
                captured_cwd=captured_cwd,
            )
            # Keep both cursors pinned until a real workflow/session join exists.
            # The quarantine sidecar is the durable copy used if the source file
            # disappears before registration.
            self.offsets[path] = start
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
        registration = load_session_registration(self.registry_dir, session_id)
        if (
            registration is not None
            and not _registration_cwd_matches(
                registration,
                captured_cwd=captured_cwd,
            )
        ):
            log.warning(
                "rollout session %s reported cwd inconsistent with its "
                "launch-bound registration",
                session_id,
            )
            return registration, None
        if registration is not None:
            run_row = self.state.get_run(registration["workflow_run_id"])
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
            parsed_offset += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                self.offsets[path] = parsed_offset
                self.state.set_tail_offset(str(path), parsed_offset)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                self.offsets[path] = parsed_offset
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
            kinds = self._extract_kinds(event)
            for kind_index, kind in enumerate(kinds):
                event_payload = dict(event)
                if len(kinds) > 1:
                    event_payload["normalized_from_shared_entry"] = True
                    event_payload["normalized_kind"] = kind
                if registration is not None:
                    event_payload.setdefault("workflow_run_id", run_id)
                    event_payload.setdefault("target_session_id", session_id)
                    task_id = registration.get("task_id")
                    if task_id:
                        event_payload.setdefault("task_id", str(task_id))
                if kind_index == len(kinds) - 1:
                    event_id = self.state.write_event_and_tail_offset(
                        run_id=run_id,
                        source="rollout",
                        kind=kind,
                        payload=event_payload,
                        path=str(path),
                        byte_offset=parsed_offset,
                    )
                    self.offsets[path] = parsed_offset
                else:
                    event_id = self.state.write_event(
                        run_id=run_id,
                        source="rollout",
                        kind=kind,
                        payload=event_payload,
                    )
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
                # Terminal events end the run and trigger post-run evaluation.
                status = _TERMINAL_STATUSES.get(kind)
                if status is not None:
                    run = self.state.get_run(run_id)
                    if run is not None and run["status"] != "running":
                        continue
                    self.state.end_run(run_id, status)
                    from .state import Decision
                    await self.state.enqueue_decision(Decision(
                        kind="evaluate_run",
                        run_id=run_id,
                        payload={"final_status": status, "final_event_kind": kind},
                    ))

    async def _replay_quarantines_once(self) -> None:
        root = self._quarantine_root()
        if not root.is_dir():
            return
        for quarantine_path in sorted(root.glob("*.json")):
            quarantine = self._load_quarantine(quarantine_path)
            if quarantine is None:
                continue
            path = Path(quarantine["rollout_path"])
            lock = self._drain_locks.setdefault(path, asyncio.Lock())
            async with lock:
                await self._replay_quarantine(quarantine_path)

    async def _replay_quarantine(self, quarantine_path: Path) -> bool:
        quarantine = self._load_quarantine(quarantine_path)
        if quarantine is None:
            return False
        path = Path(quarantine["rollout_path"])
        session_id = str(quarantine["session_id"])
        start_offset = int(quarantine["start_offset"])
        end_offset = int(quarantine["end_offset"])
        raw_bytes = quarantine["raw_bytes"]
        captured_cwd = quarantine.get("captured_cwd")
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
            return False

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
    ) -> None:
        raw_bytes = b"".join(raw_lines)
        end_offset = start_offset + len(raw_bytes)
        payload = {
            "schema_version": _QUARANTINE_SCHEMA,
            "rollout_path": str(path),
            "session_id": session_id,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "raw_bytes_b64": base64.b64encode(raw_bytes).decode("ascii"),
            "captured_cwd": captured_cwd,
            "updated_at": int(time.time()),
        }
        quarantine_path = self._quarantine_path(path)
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            self._load_quarantine(quarantine_path)
            if quarantine_path.is_file()
            else None
        )
        if (
            existing is not None
            and int(existing["start_offset"]) <= start_offset
            and int(existing["end_offset"]) >= end_offset
        ):
            return
        temp_path = quarantine_path.with_name(
            f".{quarantine_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, sort_keys=True, separators=(",", ":"))
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

    def _load_quarantine(self, quarantine_path: Path) -> dict[str, Any] | None:
        try:
            resolved_root = self._quarantine_root().resolve()
            resolved_path = quarantine_path.resolve(strict=True)
            resolved_path.relative_to(resolved_root)
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != _QUARANTINE_SCHEMA
            ):
                return None
            rollout_path = str(payload["rollout_path"])
            session_id = str(payload["session_id"])
            start_offset = int(payload["start_offset"])
            end_offset = int(payload["end_offset"])
            raw_bytes = base64.b64decode(
                str(payload["raw_bytes_b64"]),
                validate=True,
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            log.warning("bad rollout quarantine %s: %s", quarantine_path, e)
            return None
        if (
            not rollout_path
            or not session_id
            or start_offset < 0
            or end_offset < start_offset
            or len(raw_bytes) != end_offset - start_offset
            or (raw_bytes and not raw_bytes.endswith(b"\n"))
        ):
            return None
        rollout = Path(rollout_path)
        if (
            self._session_id_from_path(rollout) != session_id
            or self._quarantine_path(rollout).resolve(strict=False)
            != quarantine_path.resolve(strict=False)
        ):
            return None
        return {
            **payload,
            "rollout_path": rollout_path,
            "session_id": session_id,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "raw_bytes": raw_bytes,
        }

    def _delete_quarantine(self, quarantine_path: Path) -> None:
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
        reg = self.registry_dir / f"{session_id}.json"
        task = None
        scope = ScopeContract()
        config_snapshot: dict[str, Any] = {"source": "rollout_watcher"}
        meta = registration
        if meta is None and reg.exists():
            try:
                meta = json.loads(reg.read_text())
            except Exception as e:
                log.warning("bad registry file %s: %s", reg, e)
                meta = None
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
        registered_run_id = meta.get("workflow_run_id") or meta.get("run_id")
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
                return "run.completed"
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

        # Top-level lifecycle names from Codex CLI/OpenCode are real source
        # events, unlike the flattened watcher fixtures retained for v0.2.
        if "." in top_kind:
            mapped = _KIND_ALIASES.get(_canonical_raw_kind(top_kind))
            if mapped:
                return mapped
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
) -> bool:
    """Validate cwd only after a session/receipt join already selected the run."""
    if not captured_cwd:
        return True
    snapshot = registration.get("config_snapshot")
    registered_cwd = str(
        snapshot.get("cwd")
        if isinstance(snapshot, dict)
        else ""
    ).strip()
    if not registered_cwd:
        return True
    try:
        return (
            Path(registered_cwd).expanduser().resolve()
            == Path(captured_cwd).expanduser().resolve()
        )
    except (OSError, RuntimeError):
        return False
