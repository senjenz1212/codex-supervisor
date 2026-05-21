"""Monitors the Codex desktop process and overall run health.

State machine:
  healthy        — process up, recent rollout writes
  stalled        — process up, no rollout writes for stall_threshold_s while run active
  crashed        — process not running while a run was active
  hooks_broken   — process up, but hook server hasn't been pinged in 5 min
  idle           — process not running, no active run
"""
from __future__ import annotations
import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

import psutil

from .config import Config
from .state import State, Decision

log = logging.getLogger(__name__)


class ProcessMonitor:
    def __init__(self, cfg: Config, state: State,
                 on_telegram: Callable[[str, dict], Awaitable[None]] | None = None):
        self.cfg = cfg
        self.state = state
        self.on_telegram = on_telegram
        self.last_hook_seen: float = 0.0
        self._last_state: dict[str, str] = {}

    def mark_hook_seen(self) -> None:
        self.last_hook_seen = time.time()

    def is_codex_running(self) -> bool:
        target_codex = (
            self.cfg.target.codex
            if self.cfg.target is not None and self.cfg.target.codex is not None
            else None
        )
        codex_cfg = target_codex or self.cfg.codex
        names = [n.lower() for n in (codex_cfg.desktop_process_names if codex_cfg else ["Codex"])]
        for p in psutil.process_iter(["name"]):
            try:
                nm = (p.info["name"] or "").lower()
            except Exception:
                continue
            if any(n in nm for n in names):
                return True
        return False

    async def run(self) -> None:
        log.info("ProcessMonitor: starting")
        while True:
            try:
                await self._tick()
            except Exception as e:
                log.exception("process tick failed: %s", e)
            await asyncio.sleep(10)

    async def _tick(self) -> None:
        codex_up = self.is_codex_running()
        for run in self.state.active_runs():
            run_id = run["run_id"]
            rollout = Path(run["rollout_path"])
            try:
                mtime = rollout.stat().st_mtime
            except FileNotFoundError:
                mtime = 0
            stale = time.time() - mtime > self.cfg.supervisor.stall_threshold_s

            new_state = self._classify(codex_up, stale)
            old_state = self._last_state.get(run_id)
            if new_state != old_state:
                log.info("run %s state: %s -> %s", run_id, old_state, new_state)
                self._last_state[run_id] = new_state
                self.state.write_event(run_id=run_id, source="monitor",
                                       kind=f"state.{new_state}",
                                       payload={"prev": old_state})
                await self._on_state_change(run_id, old_state, new_state)

    def _classify(self, codex_up: bool, rollout_stale: bool) -> str:
        if not codex_up:
            return "crashed"
        if rollout_stale:
            return "stalled"
        # hooks_broken if we never see a hook event after first 5 minutes of life.
        if self.last_hook_seen and time.time() - self.last_hook_seen > 300:
            return "hooks_broken"
        return "healthy"

    async def _on_state_change(self, run_id: str, old: str | None, new: str) -> None:
        if new == "healthy":
            return
        if self.cfg.modes.recovery_actions == "off":
            return
        # Enqueue a recovery decision; the agent invoker will handle it.
        await self.state.enqueue_decision(Decision(
            kind="plan_recovery",
            run_id=run_id,
            payload={"old_state": old, "new_state": new},
        ))
        if self.on_telegram:
            await self.on_telegram(
                f"Run {run_id} is now {new}.",
                {"run_id": run_id, "state": new},
            )
