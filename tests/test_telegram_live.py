"""Telegram live boundary tests.

These tests keep Telegram usable for Codex Desktop monitoring even when the
optional Claude Agent SDK is not installed. Network calls are faked below the
public poller/notifier boundary.
"""
from __future__ import annotations
import json
import time

import pytest

from supervisor.config import Config
from supervisor.state import State


def _cfg(tmp_path) -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
                "desktop_process_names": ["Codex"],
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
    })


def _config_file(tmp_path, *, steering_injection: str = "advise", telegram_fyis: str = "advise"):
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join([
            "target:",
            "  kind: codex",
            "  codex:",
            f"    sessions_root: {tmp_path / 'sessions'}",
            "orchestrator:",
            f"  run_registry_dir: {tmp_path / 'runs'}",
            "supervisor:",
            f"  state_db: {tmp_path / 'state.db'}",
            "modes:",
            f"  telegram_fyis: {telegram_fyis}",
            f"  steering_injection: {steering_injection}",
            "models:",
            "  realtime_critique_model: claude-haiku-4-5",
            "  drift_l3_model: claude-haiku-4-5",
            "  drift_l4_model: claude-sonnet-4-6",
            "  post_run_eval_model: claude-sonnet-4-6",
            "  embedding_model: text-embedding-3-small",
            "telegram:",
            "  bot_token: 123456:test-token",
            "  chat_id: '42'",
            "",
        ])
    )
    return path


class _FakeClient:
    def __init__(self):
        self.posts: list[dict] = []

    async def post(self, url, data=None, **kwargs):
        self.posts.append({"url": url, "data": data or {}, "kwargs": kwargs})
        return _FakeResponse({"ok": True})


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAdapter:
    def __init__(self):
        self.calls: list[object] = []

    async def execute_action(self, action):
        self.calls.append(action)
        return {"delivered": True}


def test_telegram_module_imports_without_agent_sdk():
    """The poller/notifier must not import claude_agent_sdk at module import."""
    from supervisor.telegram import TelegramNotifier, TelegramPoller, telegram_enabled

    assert TelegramNotifier is not None
    assert TelegramPoller is not None
    assert telegram_enabled is not None


@pytest.mark.asyncio
async def test_telegram_callback_with_nonce_answers_pending_ask(tmp_path):
    """Button callbacks include ask_id + nonce; stale/spoofed callbacks fail closed."""
    from supervisor.telegram import TelegramPoller

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "telegram.db"))
    ask_id = state.create_ask(
        "run-1",
        "Approve steering?",
        ["Approve", "Reject"],
        nonce="nonce-123",
        expires_at=int(time.time()) + 60,
    )
    poller = TelegramPoller(cfg, state)
    client = _FakeClient()

    await poller._handle_callback(
        {"id": "callback-1", "data": f"ask:{ask_id}:nonce-123:0"},
        client=client,
    )

    row = state.get_ask(ask_id)
    assert row["status"] == "answered"
    assert row["answer"] == "Approve"
    assert client.posts, "callback must be acknowledged to Telegram"


@pytest.mark.asyncio
async def test_expired_telegram_callback_closes_pending_action(tmp_path):
    """Regression: an expired tap must not leave the action pending forever.

    The real symptom was a Telegram ask marked expired while the related
    inject_steering action stayed pending_approval, which made it look like the
    tap registered nowhere and also blocked future steering via dedup.
    """
    from supervisor.action_executor import execute_actions
    from supervisor.telegram import TelegramPoller

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "telegram.db"))
    state.upsert_run(
        run_id="run-1",
        session_id="session-1",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="test",
        scope_hints=None,
    )
    adapter = _FakeAdapter()
    execute_actions(
        [{
            "kind": "inject_steering",
            "run_id": "run-1",
            "session_id": "session-1",
            "message": "Re-anchor.",
            "options": ["Approve", "Reject"],
            "timeout_s": 30,
        }],
        state=state,
        adapter=adapter,
        telegram_sender=lambda **kwargs: None,
    )
    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()
    state._conn.execute(
        "UPDATE telegram_asks SET expires_at=0 WHERE ask_id=?",
        (ask["ask_id"],),
    )
    state._conn.commit()
    poller = TelegramPoller(cfg, state, target_adapter=adapter)
    client = _FakeClient()

    await poller._handle_callback(
        {"id": "callback-expired", "data": f"ask:{ask['ask_id']}:{ask['nonce']}:0"},
        client=client,
    )

    refreshed_ask = state.get_ask(ask["ask_id"])
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert refreshed_ask["status"] == "expired"
    assert action["status"] == "approval_expired"
    assert adapter.calls == []
    assert client.posts, "expired callback must still be acknowledged"


@pytest.mark.asyncio
async def test_telegram_notifier_formats_approval_buttons_with_nonce(tmp_path):
    """Real approval prompts need callback_data that can be validated later."""
    from supervisor.telegram import TelegramNotifier

    cfg = _cfg(tmp_path)
    notifier = TelegramNotifier(cfg)
    client = _FakeClient()

    await notifier.send_approval_prompt(
        ask_id=7,
        question="Re-anchor run?",
        options=["Re-anchor", "Continue"],
        nonce="n-7",
        client=client,
    )

    payload = client.posts[0]["data"]
    markup = json.loads(payload["reply_markup"])
    buttons = markup["inline_keyboard"]
    assert buttons[0][0]["callback_data"] == "ask:7:n-7:0"
    assert buttons[1][0]["callback_data"] == "ask:7:n-7:1"


@pytest.mark.asyncio
async def test_autosteer_command_requires_approval_before_changing_config(tmp_path):
    """CS23 RED: Telegram can request autosteer, but mode mutation is gated by
    a nonce approval callback."""
    from supervisor.config import Config
    from supervisor.telegram import TelegramPoller

    config_path = _config_file(tmp_path, steering_injection="advise")
    cfg = Config.load(config_path)
    state = State(str(tmp_path / "telegram.db"))
    restarts: list[dict] = []

    async def restart_runner() -> dict:
        restarts.append({"called": True})
        return {"ok": True, "method": "fake_restart"}

    poller = TelegramPoller(
        cfg,
        state,
        config_path=str(config_path),
        restart_runner=restart_runner,
    )
    client = _FakeClient()

    await poller._handle_command(
        {"chat": {"id": 42}, "text": "/autosteer on"},
        client,
    )

    fresh = Config.load(config_path)
    assert fresh.modes.steering_injection == "advise"
    assert restarts == []
    action = state._conn.execute(
        "SELECT action_type, status, payload_json FROM actions"
    ).fetchone()
    assert action["action_type"] == "mode_change"
    assert action["status"] == "pending_approval"
    payload = json.loads(action["payload_json"])
    assert payload["mode_key"] == "steering_injection"
    assert payload["new_value"] == "enforce"
    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()
    assert ask["status"] == "pending"
    assert "steering_injection" in ask["question"]
    message_payload = client.posts[0]["data"]
    markup = json.loads(message_payload["reply_markup"])
    approve_callback = markup["inline_keyboard"][0][0]["callback_data"]

    await poller._handle_callback(
        {"id": "callback-mode", "data": approve_callback},
        client=client,
    )

    changed = Config.load(config_path)
    assert changed.modes.steering_injection == "enforce"
    assert restarts == [{"called": True}]
    ask = state.get_ask(ask["ask_id"])
    assert ask["status"] == "answered"
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["status"] == "applied"
    payload = json.loads(action["payload_json"])
    assert payload["answer"] == "Approve"
    assert payload["restart_result"]["ok"] is True
    assert client.posts[-1]["data"]["text"] == "Recorded: Approve"


@pytest.mark.asyncio
async def test_quiet_command_reject_does_not_change_config_or_restart(tmp_path):
    from supervisor.config import Config
    from supervisor.telegram import TelegramPoller

    config_path = _config_file(tmp_path, telegram_fyis="advise")
    cfg = Config.load(config_path)
    state = State(str(tmp_path / "telegram.db"))
    restarts: list[dict] = []

    async def restart_runner() -> dict:
        restarts.append({"called": True})
        return {"ok": True, "method": "fake_restart"}

    poller = TelegramPoller(
        cfg,
        state,
        config_path=str(config_path),
        restart_runner=restart_runner,
    )
    client = _FakeClient()

    await poller._handle_command(
        {"chat": {"id": 42}, "text": "/quiet on"},
        client,
    )
    message_payload = client.posts[0]["data"]
    markup = json.loads(message_payload["reply_markup"])
    reject_callback = markup["inline_keyboard"][1][0]["callback_data"]

    await poller._handle_callback(
        {"id": "callback-mode-reject", "data": reject_callback},
        client=client,
    )

    unchanged = Config.load(config_path)
    assert unchanged.modes.telegram_fyis == "advise"
    assert restarts == []
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["status"] == "cancelled"
