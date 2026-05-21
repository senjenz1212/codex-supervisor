"""Tool API exposed to the conversational Claude supervisor.

These methods are deliberately higher level than SQLite. Claude gets bounded,
redacted, normalized JSON and cannot reach raw hook payloads or run SQL.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .mode_policy import propose_actions
from .named_session import NamedSessionResolver
from .redaction import redact
from .run_timeline import build_run_timeline
from .scope_policy import evaluate_scope as evaluate_scope_findings
from .state import State
from .action_executor import execute_actions, execute_actions_async
from .desktop_visibility import classify_desktop_status_visibility
from .workspace_grounding import (
    read_workspace_file as read_grounded_workspace_file,
    resolve_workspace_root,
    workspace_snapshot,
)


class SupervisorToolAPI:
    def __init__(
        self,
        state: State,
        *,
        aliases_path: str | Path | None = None,
        target_adapter: Any | None = None,
        telegram_sender: Any | None = None,
        steering_mode: str = "advise",
    ):
        self.state = state
        self.aliases_path = aliases_path
        self.target_adapter = target_adapter
        self.telegram_sender = telegram_sender
        self.steering_mode = steering_mode

    def list_runs(self, *, limit: int = 10, include_completed: bool = True) -> dict[str, Any]:
        rows = self.state.list_runs(
            limit=max(1, min(int(limit), 50)),
            include_completed=include_completed,
        )
        return {"runs": [self._run_summary(dict(r)) for r in rows]}

    def resolve_session(self, name: str) -> dict[str, Any]:
        resolver = NamedSessionResolver(self.state, aliases_path=self.aliases_path)
        return resolver.resolve(name)

    def read_recent_events(
        self,
        *,
        run_id: str | None = None,
        session_name: str | None = None,
        n: int = 20,
    ) -> dict[str, Any]:
        run_id = self._resolve_run_id(run_id=run_id, session_name=session_name)
        if not run_id:
            return {"status": "not_found", "events": []}
        return {
            "status": "ok",
            "run_id": run_id,
            "events": redact(self.state.recent_events(run_id, n=max(1, min(int(n), 50)))),
        }

    def read_hooks(self, *, run_id: str, n: int = 20) -> dict[str, Any]:
        rows = self.state._conn.execute(
            """SELECT id, run_id, hook_event, tool_name, response_json, latency_ms, mode, created_at
               FROM hook_requests
               WHERE run_id=?
               ORDER BY id DESC LIMIT ?""",
            (run_id, max(1, min(int(n), 50))),
        ).fetchall()
        rows = list(reversed(rows))
        return {"status": "ok", "run_id": run_id, "hooks": [self._hook_row(r) for r in rows]}

    def read_actions(self, *, run_id: str, n: int = 20) -> dict[str, Any]:
        rows = self.state._conn.execute(
            """SELECT id, run_id, action_type, requested_by, status, payload_json, created_at, completed_at
               FROM actions
               WHERE run_id=?
               ORDER BY id DESC LIMIT ?""",
            (run_id, max(1, min(int(n), 50))),
        ).fetchall()
        rows = list(reversed(rows))
        return {"status": "ok", "run_id": run_id, "actions": [self._action_row(r) for r in rows]}

    def read_supervisor_turns(
        self,
        *,
        chat_id: str,
        n: int = 10,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "chat_id": str(chat_id),
            "turns": self.state.recent_supervisor_turns(
                chat_id=str(chat_id),
                n=max(1, min(int(n), 50)),
            ),
        }

    def read_run_timeline(
        self,
        *,
        run_id: str | None = None,
        session_name: str | None = None,
        n: int = 80,
    ) -> dict[str, Any]:
        run_id = self._resolve_run_id(run_id=run_id, session_name=session_name)
        if not run_id:
            return {"status": "not_found", "timeline": None}
        row = self.state._conn.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return {"status": "not_found", "timeline": None}
        events = self.state.recent_events(run_id, n=max(1, min(int(n), 200)))
        return {
            "status": "ok",
            "run_id": run_id,
            "timeline": build_run_timeline(
                run=dict(row),
                events=events,
                max_items=10,
            ),
        }

    def watch_run(
        self,
        *,
        chat_id: str,
        run_id: str | None = None,
        session_name: str | None = None,
    ) -> dict[str, Any]:
        run_id = self._resolve_run_id(run_id=run_id, session_name=session_name)
        if not run_id:
            return {"status": "not_found", "watch": None}
        watch_id = self.state.create_run_watch(chat_id=str(chat_id), run_id=run_id)
        row = self.state._conn.execute(
            "SELECT * FROM run_watches WHERE id=?", (watch_id,)
        ).fetchone()
        return {
            "status": "ok",
            "watch": self._watch_row(row) if row else None,
        }

    def list_run_watches(
        self,
        *,
        chat_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "watches": [
                self._watch_row(row)
                for row in self.state.list_run_watches(
                    chat_id=str(chat_id) if chat_id is not None else None,
                    run_id=run_id,
                )
            ],
        }

    def read_workspace_snapshot(
        self,
        *,
        run_id: str,
        max_diff_chars: int = 12_000,
    ) -> dict[str, Any]:
        return workspace_snapshot(
            self.state,
            run_id=run_id,
            max_diff_chars=max(0, min(int(max_diff_chars), 50_000)),
        )

    def read_workspace_file(
        self,
        *,
        run_id: str,
        path: str,
        max_bytes: int = 20_000,
    ) -> dict[str, Any]:
        return read_grounded_workspace_file(
            self.state,
            run_id=run_id,
            path=path,
            max_bytes=max(1, min(int(max_bytes), 50_000)),
        )

    def evaluate_scope(self, *, run_id: str, n: int = 30) -> dict[str, Any]:
        snapshot = self.state.get_run_snapshot(run_id)
        scope_contract = {}
        if snapshot is not None:
            scope_contract = json.loads(snapshot["scope_contract_json"])
        events = self.state.recent_events(run_id, n=max(1, min(int(n), 100)))
        findings = evaluate_scope_findings(scope_contract, events)
        return {
            "status": "ok",
            "run_id": run_id,
            "findings": redact(findings),
            "event_count": len(events),
        }

    def propose_action(self, *, run_id: str, findings: list[dict] | None = None) -> dict[str, Any]:
        snapshot = self.state.get_run_snapshot(run_id)
        modes: dict[str, Any] = {}
        if snapshot is not None:
            config = json.loads(snapshot["config_json"])
            modes = dict(config.get("modes") or {})
        if not modes:
            modes = {"drift_l1_l3": "shadow"}
        row = self.state._conn.execute(
            "SELECT session_id FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        session_id = row["session_id"] if row else ""
        proposed = propose_actions(
            findings or [],
            modes,
            run_id=run_id,
            session_id=session_id,
        )
        return {
            "status": "ok",
            "run_id": run_id,
            "proposed_actions": redact(proposed),
        }

    def request_steering(
        self,
        *,
        message: str,
        run_id: str | None = None,
        session_name: str | None = None,
        timeout_s: int = 300,
    ) -> dict[str, Any]:
        """Queue a steering request.

        In advise mode this creates a Telegram approval prompt. In enforce
        mode, callers should use `request_steering_async`, which auto-delivers
        this non-destructive action through the target adapter.
        """
        if self.steering_mode == "off":
            return {"status": "disabled", "reason": "steering_injection_off"}
        if self.steering_mode == "enforce":
            return {
                "status": "async_required",
                "reason": "steering_enforce_requires_async_tool_path",
                "action_results": [],
            }
        run_id = self._resolve_run_id(run_id=run_id, session_name=session_name)
        if not run_id:
            return {"status": "not_found", "action_results": []}
        action_or_error = self._build_steering_action(
            run_id=run_id,
            message=message,
            timeout_s=timeout_s,
        )
        if "error" in action_or_error:
            return action_or_error["error"]
        results = execute_actions(
            [action_or_error["action"]],
            state=self.state,
            adapter=self.target_adapter,
            telegram_sender=self.telegram_sender,
        )
        return redact({
            "status": "ok",
            "run_id": run_id,
            "action_results": results,
        })

    async def request_steering_async(
        self,
        *,
        message: str,
        run_id: str | None = None,
        session_name: str | None = None,
        timeout_s: int = 300,
    ) -> dict[str, Any]:
        """Async steering tool used by the MCP runtime.

        In enforce mode, non-destructive steering is auto-delivered. In advise
        mode, it preserves the existing Telegram approval path.
        """
        if self.steering_mode == "off":
            return {"status": "disabled", "reason": "steering_injection_off"}
        run_id = self._resolve_run_id(run_id=run_id, session_name=session_name)
        if not run_id:
            return {"status": "not_found", "action_results": []}
        action_or_error = self._build_steering_action(
            run_id=run_id,
            message=message,
            timeout_s=timeout_s,
        )
        if "error" in action_or_error:
            return action_or_error["error"]

        if self.steering_mode == "enforce":
            results = await execute_actions_async(
                [action_or_error["action"]],
                state=self.state,
                adapter=self.target_adapter,
                telegram_sender=self.telegram_sender,
                auto_execute_actions={"inject_steering"},
            )
        else:
            results = execute_actions(
                [action_or_error["action"]],
                state=self.state,
                adapter=self.target_adapter,
                telegram_sender=self.telegram_sender,
            )
        return redact({
            "status": "ok",
            "run_id": run_id,
            "mode": self.steering_mode,
            "action_results": results,
        })

    def _build_steering_action(
        self,
        *,
        run_id: str,
        message: str,
        timeout_s: int,
    ) -> dict[str, Any]:
        row = self.state.get_run(run_id)
        if row is None:
            return {"error": {"status": "not_found", "action_results": []}}
        text = str(message or "").strip()
        if not text:
            return {"error": {"status": "invalid", "reason": "empty_message", "action_results": []}}
        action = {
            "kind": "inject_steering",
            "status": "would_inject_steering",
            "run_id": run_id,
            "session_id": row["session_id"],
            "message": text,
            "summary": "Approve steering message to the supervised Codex session?",
            "options": ["Approve", "Reject"],
            "timeout_s": max(30, min(int(timeout_s), 3600)),
        }
        cwd = resolve_workspace_root(self.state, run_id=run_id)
        if cwd is not None:
            action["cwd"] = str(cwd)
        return {"action": action}

    def _resolve_run_id(self, *, run_id: str | None, session_name: str | None) -> str | None:
        if run_id:
            return run_id
        if not session_name:
            return None
        resolved = self.resolve_session(session_name)
        if resolved.get("status") != "resolved":
            return None
        return resolved.get("run", {}).get("run_id")

    @staticmethod
    def _run_summary(row: dict[str, Any]) -> dict[str, Any]:
        return redact({
            "run_id": row.get("run_id"),
            "session_id": row.get("session_id"),
            "task": row.get("task"),
            "status": row.get("status"),
            "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "target": "codex" if (
                ".codex/sessions" in str(row.get("rollout_path") or "")
                or "rollout-" in Path(str(row.get("rollout_path") or "")).name
            ) else "unknown",
        })

    @staticmethod
    def _hook_row(row) -> dict[str, Any]:
        response = json.loads(row["response_json"] or "{}")
        return redact({
            "id": row["id"],
            "run_id": row["run_id"],
            "hook_event": row["hook_event"],
            "tool_name": row["tool_name"],
            "response": response,
            "latency_ms": row["latency_ms"],
            "mode": row["mode"],
            "created_at": row["created_at"],
        })

    @staticmethod
    def _action_row(row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"] or "{}")
        if (
            row["action_type"] == "append_status_item"
            and "visibility" not in payload
            and isinstance(payload.get("adapter_result"), dict)
        ):
            payload["visibility"] = classify_desktop_status_visibility(
                payload["adapter_result"]
            )
        return redact({
            "id": row["id"],
            "run_id": row["run_id"],
            "action_type": row["action_type"],
            "requested_by": row["requested_by"],
            "status": row["status"],
            "payload": payload,
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        })

    @staticmethod
    def _watch_row(row) -> dict[str, Any]:
        return redact({
            "id": row["id"],
            "chat_id": row["chat_id"],
            "run_id": row["run_id"],
            "status": row["status"],
            "last_event_id": row["last_event_id"],
            "last_notified_at": row["last_notified_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
