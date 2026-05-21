"""Watches ~/.codex/sessions/.../rollout-*.jsonl and emits normalized events.

Each rollout file is JSONL: one event per line. We track per-file byte offset so
we never re-read what we've already parsed. New files are picked up automatically
by `watchfiles`.

The orchestrator-side registry (~/.codex-supervisor/runs/{session_id}.json) is
checked when we see a new rollout file — if a registry entry exists we use its
task description; otherwise we infer from turn 1.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Awaitable
from watchfiles import awatch, Change

from .state import State
from .target.types import ScopeContract

log = logging.getLogger(__name__)

ROLLOUT_RE = re.compile(
    r"rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-([0-9a-f-]+)\.jsonl$"
)


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
                        await self._drain_file(p)
                    elif change == Change.deleted:
                        self.offsets.pop(p, None)
        finally:
            sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)

    async def _periodic_sweep(self) -> None:
        while True:
            await asyncio.sleep(max(1, self.sweep_interval_s))
            await self.sweep_once()

    async def sweep_once(self) -> None:
        """Poll known/recent rollout files for growth missed by filesystem watches."""
        cutoff = self.started_at - self.startup_backfill_s
        for p in self.sessions_root.rglob("rollout-*.jsonl"):
            try:
                stat = p.stat()
            except FileNotFoundError:
                continue
            known = self.state.get_tail_offset(str(p)) > 0
            if not known and stat.st_mtime < cutoff:
                self.offsets[p] = stat.st_size
                self.state.set_tail_offset(str(p), stat.st_size)
                continue
            await self._drain_file(p)

    async def _initial_backfill(self) -> None:
        """Drain recent/known files, but skip old unseen historical rollouts.

        On first install, ~/.codex/sessions may contain months of completed
        rollouts. Importing all of them marks old sessions as active and can
        flood recovery planning. We advance unseen old files to EOF instead;
        future appends are still observed because the durable tail offset is set.
        """
        cutoff = self.started_at - self.startup_backfill_s
        for p in self.sessions_root.rglob("rollout-*.jsonl"):
            try:
                stat = p.stat()
            except FileNotFoundError:
                continue
            known = self.state.get_tail_offset(str(p)) > 0
            if not known and stat.st_mtime < cutoff:
                self.offsets[p] = stat.st_size
                self.state.set_tail_offset(str(p), stat.st_size)
                continue
            await self._drain_file(p)

    async def _drain_file(self, path: Path) -> None:
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
                        self.offsets[path] = pos
                        self.state.set_tail_offset(str(path), pos)
                        break
                    pos += len(raw)
                    lines.append(raw)
        except OSError as e:
            log.warning("read failed for %s: %s", path, e)
            return

        session_id = self._session_id_from_path(path)
        run_row = self.state.get_run_by_session(session_id)
        run_id = run_row["run_id"] if run_row else self._register_run(session_id, path)

        # Parse line-by-line. JSONL means one event per line; partial last
        # lines are left for the next drain and the persisted offset is not
        # advanced past them.
        parsed_offset = start
        for raw_line in lines:
            parsed_offset += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                self.offsets[path] = parsed_offset
                self.state.set_tail_offset(str(path), parsed_offset)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.offsets[path] = parsed_offset
                self.state.set_tail_offset(str(path), parsed_offset)
                continue
            kind = self._extract_kind(event)
            event_id = self.state.write_event(run_id=run_id, source="rollout",
                                              kind=kind, payload=event)
            self.offsets[path] = parsed_offset
            self.state.set_tail_offset(str(path), parsed_offset)
            if self.on_event:
                await self.on_event(run_id, {"id": event_id, "kind": kind, **event})
            # Terminal events end the run and trigger post-run evaluation.
            if kind in ("turn.failed", "thread.completed", "session.ended"):
                status = "failed" if kind == "turn.failed" else "completed"
                self.state.end_run(run_id, status)
                from .state import Decision
                await self.state.enqueue_decision(Decision(
                    kind="evaluate_run",
                    run_id=run_id,
                    payload={"final_status": status, "final_event_kind": kind},
                ))

    def _session_id_from_path(self, path: Path) -> str:
        m = ROLLOUT_RE.search(path.name)
        return m.group(2) if m else path.stem

    def _register_run(self, session_id: str, rollout_path: Path) -> str:
        """Look up the orchestrator-side registry; otherwise create a bare run."""
        reg = self.registry_dir / f"{session_id}.json"
        task = None
        scope = ScopeContract()
        config_snapshot: dict[str, Any] = {"source": "rollout_watcher"}
        if reg.exists():
            try:
                meta = json.loads(reg.read_text())
                task = meta.get("task")
                if isinstance(meta.get("scope_contract"), dict):
                    scope = ScopeContract.from_dict(meta["scope_contract"])
                elif isinstance(meta.get("scope_hints"), list):
                    scope = ScopeContract(
                        allowed_paths=tuple(str(p) for p in meta["scope_hints"]),
                    )
                if isinstance(meta.get("config_snapshot"), dict):
                    config_snapshot = meta["config_snapshot"]
            except Exception as e:
                log.warning("bad registry file %s: %s", reg, e)
        run_id = f"run_{session_id}"
        self.state.register_run(
            run_id=run_id, session_id=session_id,
            rollout_path=str(rollout_path), task=task, scope=scope,
            target_kind="codex", config_snapshot=config_snapshot,
        )
        return run_id

    @staticmethod
    def _extract_kind(event: dict[str, Any]) -> str:
        # Codex events tend to have a top-level "type" or "kind"; be defensive.
        return (event.get("type") or event.get("kind")
                or event.get("event") or "unknown")
