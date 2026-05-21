from __future__ import annotations

import importlib
import json
import sys
import types

import pytest

from supervisor.config import Config
from supervisor.state import State
from supervisor.target.types import ScopeContract


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


@pytest.mark.asyncio
async def test_codex_mcp_get_run_metadata_reads_completed_runs(monkeypatch, tmp_path):
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

    import mcp_tools.codex_tools as codex_tools
    codex_tools = importlib.reload(codex_tools)

    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-done",
        session_id="session-done",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Review completed changes",
        scope=ScopeContract(allowed_paths=("src",)),
        target_kind="codex",
    )
    state.end_run("run-done", "completed")
    server = codex_tools.build_codex_mcp_server(_cfg(tmp_path), state)

    result = await server["get_run_metadata"]({"run_id": "run-done"})
    text = result["content"][0]["text"]
    payload = json.loads(text)

    assert payload["run_id"] == "run-done"
    assert payload["status"] == "completed"
    assert payload["task"] == "Review completed changes"
