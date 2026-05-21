from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent_invoker import AgentInvoker
from supervisor.config import Config
from supervisor.state import Decision, State


def _cfg(tmp_path) -> Config:
    return Config(**{
        "target": {"kind": "codex", "codex": {"sessions_root": str(tmp_path)}},
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-opus-4-7",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
    })


class _FakeOptions:
    seen = None

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        _FakeOptions.seen = self


class _FakeClient:
    def __init__(self, *, options):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def query(self, message):
        self.message = message

    async def receive_response(self):
        class Block:
            text = '{"review_sent": true, "grounding": "workspace"}'

        class Msg:
            content = [Block()]

        yield Msg()


@pytest.mark.asyncio
async def test_review_updates_invoker_uses_read_only_grounding_tools(monkeypatch, tmp_path):
    import supervisor.agent_invoker as agent_invoker

    monkeypatch.setattr(agent_invoker, "ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr(agent_invoker, "ClaudeSDKClient", _FakeClient)

    state = State(str(tmp_path / "state.db"))
    invoker = AgentInvoker(
        _cfg(tmp_path),
        state,
        Path("skills"),
        codex_mcp_server=object(),
        telegram_mcp_server=object(),
    )

    await invoker._handle(Decision(
        kind="review_updates",
        run_id="run-vela",
        payload={"event_id": 21},
    ))

    assert _FakeOptions.seen is not None
    allowed = _FakeOptions.seen.allowed_tools
    assert "mcp__codex__read_workspace_snapshot" in allowed
    assert "mcp__codex__read_workspace_file" in allowed
    assert "mcp__telegram__send_message" in allowed
    assert "mcp__codex__inject_steering" not in allowed
    assert "mcp__telegram__ask_user" not in allowed

    verdict = state._conn.execute(
        "SELECT phase, model, output_json FROM verdicts WHERE run_id='run-vela'"
    ).fetchone()
    assert verdict["phase"] == "review_updates"
    assert verdict["model"] == "claude-opus-4-7"
