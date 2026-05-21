"""Resolve human-facing session names to tracked target runs.

The resolver is intentionally local-only. It searches explicit aliases first,
then normalized run metadata, stored event content, and a bounded slice of the
rollout file. That lets Telegram users say "Vela chat bot" while the daemon
maps it to a concrete Codex Desktop session id.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .redaction import redact
from .state import State


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").replace("_", " ").split())


def _tokens(text: str) -> set[str]:
    stop = {"the", "a", "an", "session", "codex", "desktop"}
    return {t for t in _norm(text).split() if len(t) > 2 and t not in stop}


@dataclass(frozen=True)
class _Candidate:
    score: int
    source: str
    run: dict[str, Any]


class NamedSessionResolver:
    def __init__(
        self,
        state: State,
        *,
        aliases: dict[str, str] | None = None,
        aliases_path: str | Path | None = None,
        scan_limit: int = 50,
    ) -> None:
        self.state = state
        self.aliases = {_norm(k): v for k, v in (aliases or {}).items()}
        self.aliases_path = Path(aliases_path).expanduser() if aliases_path else None
        self.scan_limit = scan_limit

    def resolve(self, name: str) -> dict[str, Any]:
        query = _norm(name)
        if not query:
            return self._not_found()

        aliases = {**self._load_aliases(), **self.aliases}
        alias_target = aliases.get(query)
        if alias_target:
            run = self._find_run_by_id(alias_target)
            if run:
                return {"status": "resolved", "source": "alias", "run": run}

        candidates = self._score_candidates(query)
        if not candidates:
            return self._not_found()

        candidates.sort(key=lambda c: (c.score, c.run.get("started_at", 0)), reverse=True)
        top = candidates[0]
        tied = [
            c for c in candidates
            if c.score == top.score and c.run["run_id"] != top.run["run_id"]
        ]
        if tied:
            return {
                "status": "ambiguous",
                "query": name,
                "candidates": [self._public_run(c.run) for c in [top, *tied]][:5],
            }
        return {
            "status": "resolved",
            "source": top.source,
            "run": self._public_run(top.run),
        }

    def _load_aliases(self) -> dict[str, str]:
        if not self.aliases_path or not self.aliases_path.exists():
            return {}
        try:
            raw = json.loads(self.aliases_path.read_text())
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {_norm(str(k)): str(v) for k, v in raw.items()}

    def _score_candidates(self, query: str) -> list[_Candidate]:
        q_tokens = _tokens(query)
        out: list[_Candidate] = []
        for row in self.state.list_runs(limit=self.scan_limit, include_completed=True):
            run = dict(row)
            status_bonus = 10 if run.get("status") == "running" else 0
            best: tuple[int, str] = (0, "")
            fields = {
                "run_id": str(run.get("run_id") or ""),
                "session_id": str(run.get("session_id") or ""),
                "task": str(run.get("task") or ""),
                "rollout_path": Path(str(run.get("rollout_path") or "")).name,
            }
            for source, value in fields.items():
                value_norm = _norm(value)
                if not value_norm:
                    continue
                if query == value_norm:
                    best = max(best, (90 + status_bonus, source))
                elif query in value_norm:
                    best = max(best, (55 + status_bonus, source))
                elif q_tokens and q_tokens.issubset(_tokens(value_norm)):
                    best = max(best, (35 + status_bonus, source))

            event_score = self._event_content_score(run["run_id"], query, q_tokens)
            if event_score:
                best = max(best, (event_score + status_bonus, "event_content"))
            rollout_score = self._rollout_content_score(run.get("rollout_path"), query, q_tokens)
            if rollout_score:
                best = max(best, (rollout_score + status_bonus, "rollout_content"))

            if best[0] > 0:
                public = self._public_run(run)
                public["match_score"] = best[0]
                out.append(_Candidate(best[0], best[1], public))
        return out

    def _event_content_score(self, run_id: str, query: str, q_tokens: set[str]) -> int:
        like_terms = [query, *sorted(q_tokens)]
        if not like_terms:
            return 0
        score = 0
        for term in like_terms[:6]:
            row = self.state._conn.execute(
                """SELECT 1 FROM events
                   WHERE run_id=? AND lower(payload_json) LIKE ?
                   LIMIT 1""",
                (run_id, f"%{term}%"),
            ).fetchone()
            if row and term == query:
                score = max(score, 50)
            elif row:
                score += 8
        return min(score, 48 if score and score < 50 else score)

    @staticmethod
    def _rollout_content_score(path_value: Any, query: str, q_tokens: set[str]) -> int:
        if not path_value:
            return 0
        path = Path(str(path_value)).expanduser()
        if not path.exists():
            return 0
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                head = f.read(256_000)
                tail = b""
                if size > 256_000:
                    f.seek(max(0, size - 512_000))
                    tail = f.read(512_000)
            text = (head + b"\n" + tail).decode("utf-8", errors="ignore").casefold()
        except OSError:
            return 0
        if query in _norm(text):
            return 50
        hits = sum(1 for token in q_tokens if token in text)
        if q_tokens and hits == len(q_tokens):
            return 40
        if hits:
            return min(30, hits * 8)
        return 0

    def _find_run_by_id(self, value: str) -> dict[str, Any] | None:
        row = self.state._conn.execute(
            """SELECT * FROM runs
               WHERE run_id=? OR session_id=?
               ORDER BY started_at DESC LIMIT 1""",
            (value, value),
        ).fetchone()
        return self._public_run(dict(row)) if row else None

    def _not_found(self) -> dict[str, Any]:
        return {
            "status": "not_found",
            "candidates": [
                self._public_run(dict(r))
                for r in self.state.list_runs(limit=5, include_completed=True)
            ],
        }

    @staticmethod
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        rollout_path = str(run.get("rollout_path") or "")
        return redact({
            "run_id": run.get("run_id"),
            "session_id": run.get("session_id"),
            "task": run.get("task"),
            "status": run.get("status"),
            "rollout_path": rollout_path,
            "started_at": run.get("started_at"),
            "target": "codex" if (".codex/sessions" in rollout_path or "rollout-" in Path(rollout_path).name) else "unknown",
            "age_s": max(0, int(time.time()) - int(run.get("started_at") or time.time())),
        })
