from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from supervisor.config import Config
from supervisor.state import State
from supervisor.target.types import ScopeContract


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, **kwargs):
        self.messages.append(text)
        return {"ok": True}


class _FakeAdapter:
    kind = "codex"

    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "delivered": True,
            "reason": "app_server_injected",
            "desktop_gui_repaint": "unverified",
        }
        self.actions = []

    async def execute_action(self, action):
        self.actions.append(action)
        return dict(self.result)


def _state_with_watch(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Monitor Vela",
        scope=ScopeContract(),
        target_kind="codex",
    )
    state.create_run_watch(chat_id="42", run_id="run-vela", last_event_id=10)
    return state


def _cfg(tmp_path, *, desktop_status_sync: str = "advise") -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "modes": {"desktop_status_sync": desktop_status_sync},
        "models": {
            "realtime_critique_model": "claude-opus-4-7",
            "drift_l3_model": "claude-opus-4-7",
            "drift_l4_model": "claude-opus-4-7",
            "post_run_eval_model": "claude-opus-4-7",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
    })


@pytest.mark.asyncio
async def test_watched_progress_can_append_passive_desktop_status(tmp_path):
    """CS13 RED: progress streaming should optionally mirror high-signal
    watched-run events into Codex Desktop history via append_status_item.
    """
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = _state_with_watch(tmp_path)
    notifier = _FakeNotifier()
    adapter = _FakeAdapter()
    streamer = TelegramProgressStreamer(
        state=state,
        notifier=notifier,
        target_adapter=adapter,
        desktop_status_mode="advise",
    )

    await streamer.handle_event("run-vela", {
        "id": 11,
        "kind": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "turn-1",
            "last_agent_message": "18d grill drafted. HALT for HITL.",
        },
    })

    assert len(notifier.messages) == 1
    assert len(adapter.actions) == 1
    action = adapter.actions[0]
    assert action.kind == "append_status_item"
    assert action.session_id == "session-vela"
    assert "Run complete" in action.payload["message"]
    assert "18d grill drafted" in action.payload["message"]
    assert action.payload["source"] == "watched_run_progress"
    assert action.payload["event_id"] == 11

    rows = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='append_status_item'"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "delivered"
    payload = json.loads(row["payload_json"])
    assert payload["run_id"] == "run-vela"
    assert payload["event_id"] == 11
    assert payload["adapter_result"]["delivered"] is True
    assert payload["adapter_result"]["desktop_gui_repaint"] == "unverified"


@pytest.mark.asyncio
async def test_unverified_desktop_repaint_is_audited_as_history_only(tmp_path):
    """CS14 RED: transport delivery is not a GUI-visible claim.

    A successful app-server `thread/inject_items` write with
    `desktop_gui_repaint=unverified` must persist an operator-facing
    `history_only` visibility state.
    """
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = _state_with_watch(tmp_path)
    notifier = _FakeNotifier()
    adapter = _FakeAdapter({
        "delivered": True,
        "reason": "app_server_injected",
        "method": "thread/inject_items",
        "desktop_gui_repaint": "unverified",
    })
    streamer = TelegramProgressStreamer(
        state=state,
        notifier=notifier,
        target_adapter=adapter,
        desktop_status_mode="advise",
    )

    await streamer.handle_event("run-vela", {
        "id": 12,
        "kind": "event_msg",
        "payload": {
            "type": "task_complete",
            "last_agent_message": "18d grill halted for fresh approval.",
        },
    })

    row = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='append_status_item'"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    visibility = payload["visibility"]
    assert row["status"] == "delivered"
    assert visibility["effective_state"] == "history_only"
    assert visibility["history_appended"] is True
    assert visibility["gui_repaint"] == "unverified"
    assert visibility["gui_live"] is False
    summary = visibility["user_visible_summary"].lower()
    assert "history" in summary
    assert "unverified" in summary
    assert "gui reflected" not in summary
    assert "gui updated" not in summary


def test_verified_desktop_repaint_can_be_classified_as_gui_live():
    """Keep the future verified transport path open."""
    from supervisor.desktop_visibility import classify_desktop_status_visibility

    visibility = classify_desktop_status_visibility({
        "delivered": True,
        "reason": "app_server_injected",
        "method": "thread/inject_items",
        "desktop_gui_repaint": "verified",
    })

    assert visibility["effective_state"] == "gui_live"
    assert visibility["history_appended"] is True
    assert visibility["gui_repaint"] == "verified"
    assert visibility["gui_live"] is True


def test_health_reports_desktop_status_sync_as_history_only_when_unverified(tmp_path):
    """CS14: /health should not imply the Desktop GUI is live-synced."""
    from supervisor.hook_server import build_app
    from supervisor.target.codex import CodexAdapter

    cfg = _cfg(tmp_path, desktop_status_sync="advise")
    state = State(str(tmp_path / "state.db"))
    app = build_app(cfg, state, target_adapter=CodexAdapter())
    body = TestClient(app).get("/health").json()

    assert body["modes"]["desktop_status_sync"] == "advise"
    assert body["desktop_status_sync_effective"] == "history_only"
    assert body["desktop_gui_live_stream"] is False


@pytest.mark.asyncio
async def test_desktop_status_sync_off_does_not_call_target_adapter(tmp_path):
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = _state_with_watch(tmp_path)
    adapter = _FakeAdapter()
    streamer = TelegramProgressStreamer(
        state=state,
        notifier=_FakeNotifier(),
        target_adapter=adapter,
        desktop_status_mode="off",
    )

    await streamer.handle_event("run-vela", {
        "id": 11,
        "kind": "event_msg",
        "payload": {"type": "task_started", "turn_id": "turn-1"},
    })

    assert adapter.actions == []
    rows = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='append_status_item'"
    ).fetchall()
    assert rows == []


@pytest.mark.asyncio
async def test_desktop_status_sync_failure_is_audited_without_blocking_telegram(tmp_path):
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = _state_with_watch(tmp_path)
    notifier = _FakeNotifier()
    adapter = _FakeAdapter({
        "delivered": False,
        "reason": "app_server_error",
        "error": "proxy did not respond",
    })
    streamer = TelegramProgressStreamer(
        state=state,
        notifier=notifier,
        target_adapter=adapter,
        desktop_status_mode="advise",
    )

    await streamer.handle_event("run-vela", {
        "id": 11,
        "kind": "event_msg",
        "payload": {"type": "task_started", "turn_id": "turn-1"},
    })

    assert len(notifier.messages) == 1
    rows = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='append_status_item'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["adapter_result"]["reason"] == "app_server_error"
