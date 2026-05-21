from __future__ import annotations

import importlib
import json
import sys
import types

import pytest

from supervisor.config import Config
from supervisor.state import State


def _cfg(tmp_path, *, telegram_fyis: str = "off") -> Config:
    return Config(**{
        "target": {"kind": "codex", "codex": {"sessions_root": str(tmp_path)}},
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "modes": {"telegram_fyis": telegram_fyis},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-opus-4-7",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
    })


def _install_fake_sdk(monkeypatch):
    fake_sdk = types.ModuleType("claude_agent_sdk")

    def tool(name, description, schema):
        def decorate(fn):
            fn._tool_name = name
            return fn
        return decorate

    def create_sdk_mcp_server(*, name, version, tools):
        return {fn._tool_name: fn for fn in tools}

    fake_sdk.tool = tool
    fake_sdk.create_sdk_mcp_server = create_sdk_mcp_server
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)


@pytest.mark.asyncio
async def test_quiet_telegram_fyis_suppresses_non_alert_mcp_messages(monkeypatch, tmp_path):
    """CS22 RED: Claude review/advice messages are FYIs. Quiet mode should
    report suppression to the model instead of pinging Sam."""
    _install_fake_sdk(monkeypatch)
    import mcp_tools.telegram_tools as telegram_tools
    telegram_tools = importlib.reload(telegram_tools)

    sent: list[str] = []

    class _FakeNotifier:
        def __init__(self, cfg):
            self.cfg = cfg

        async def send_message(self, text: str, **kwargs):
            sent.append(text)
            return {"ok": True}

    monkeypatch.setattr(telegram_tools, "TelegramNotifier", _FakeNotifier)

    server = telegram_tools.build_telegram_mcp_server(
        _cfg(tmp_path, telegram_fyis="off"),
        State(str(tmp_path / "state.db")),
    )
    result = await server["send_message"]({
        "text": "18c.5 shipped. Suggested next move: archive the watch.",
        "urgency": "fyi",
    })
    payload = json.loads(result["content"][0]["text"])

    assert sent == []
    assert payload == {
        "sent": False,
        "suppressed": True,
        "reason": "telegram_fyis_off",
        "urgency": "fyi",
    }


@pytest.mark.asyncio
async def test_quiet_telegram_fyis_allows_alert_mcp_messages(monkeypatch, tmp_path):
    _install_fake_sdk(monkeypatch)
    import mcp_tools.telegram_tools as telegram_tools
    telegram_tools = importlib.reload(telegram_tools)

    sent: list[str] = []

    class _FakeNotifier:
        def __init__(self, cfg):
            self.cfg = cfg

        async def send_message(self, text: str, **kwargs):
            sent.append(text)
            return {"ok": True}

    monkeypatch.setattr(telegram_tools, "TelegramNotifier", _FakeNotifier)

    server = telegram_tools.build_telegram_mcp_server(
        _cfg(tmp_path, telegram_fyis="off"),
        State(str(tmp_path / "state.db")),
    )
    result = await server["send_message"]({
        "text": "Hooks are broken; approval is needed.",
        "urgency": "alert",
    })
    payload = json.loads(result["content"][0]["text"])

    assert sent == ["[alert] Hooks are broken; approval is needed."]
    assert payload == {"sent": True, "suppressed": False, "urgency": "alert"}
