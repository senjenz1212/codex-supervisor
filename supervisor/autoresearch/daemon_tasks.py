"""Daemon cadences for report-only AutoResearch maintenance."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

from .generator import run_runnable_autoresearch_experiments
from ..quality_trends import run_weekly_p11_audit_if_due

log = logging.getLogger(__name__)


class AutoResearchRunnerTask:
    """Run operator-activated AutoResearch experiments from the daemon only."""

    def __init__(
        self,
        cfg: Any,
        state: Any,
        *,
        repo_root: str | Path,
        output_root: str | Path | None = None,
        runner: Callable[..., list[dict[str, Any]]] = run_runnable_autoresearch_experiments,
    ):
        self.cfg = cfg
        self.state = state
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.output_root = Path(output_root or (self.repo_root / ".scratch" / "autoresearch-auto-runs"))
        self.runner = runner

    async def run(self) -> None:
        interval = max(1, int(getattr(self.cfg.autoresearch, "runner_interval_s", 3600)))
        log.info("AutoResearchRunnerTask: starting interval=%ss", interval)
        while True:
            await self.tick_once()
            await asyncio.sleep(interval)

    async def tick_once(self, *, now: int | None = None) -> dict[str, Any]:
        timestamp = int(time.time()) if now is None else int(now)
        cap = int(getattr(self.cfg.autoresearch, "max_runnable_experiments_per_week", 2))
        results = self.runner(
            state=self.state,
            repo_root=self.repo_root,
            output_root=self.output_root,
            run_id_prefix=f"autoresearch-daemon-{timestamp}",
            max_runnable_per_week=cap,
            now=timestamp,
        )
        return {
            "status": "ok",
            "executed_count": len(results),
            "results": results,
            "default_change_allowed": False,
            "gate_authority": "unchanged",
        }


class WeeklyP11AuditTask:
    """Run due sampled P11 audits from the daemon cadence."""

    def __init__(
        self,
        cfg: Any,
        state: Any,
        *,
        run_id_provider: Callable[[], list[str]] | None = None,
        auditor: Callable[..., dict[str, Any]] = run_weekly_p11_audit_if_due,
    ):
        self.cfg = cfg
        self.state = state
        self.run_id_provider = run_id_provider or self._candidate_run_ids
        self.auditor = auditor

    async def run(self) -> None:
        interval = max(1, int(getattr(self.cfg.autoresearch, "p11_audit_cadence_s", 604800)))
        log.info("WeeklyP11AuditTask: starting interval=%ss", interval)
        while True:
            await self.tick_once()
            await asyncio.sleep(interval)

    async def tick_once(self, *, now: int | None = None) -> dict[str, Any]:
        timestamp = int(time.time()) if now is None else int(now)
        cadence = int(getattr(self.cfg.autoresearch, "p11_audit_cadence_s", 604800))
        results: list[dict[str, Any]] = []
        for run_id in self.run_id_provider():
            results.append(self.auditor(
                self.state,
                run_id=run_id,
                now=timestamp,
                cadence_s=cadence,
            ))
        return {
            "status": "ok",
            "audited_run_count": len(results),
            "results": results,
            "observational_only": True,
            "gate_authority": "unchanged",
        }

    def _candidate_run_ids(self) -> list[str]:
        if hasattr(self.state, "list_p11_audit_candidate_run_ids"):
            return list(self.state.list_p11_audit_candidate_run_ids(limit=50))
        return []
