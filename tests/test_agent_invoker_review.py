from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from supervisor.agent_runtime import AgentRunHandle
from supervisor.config import Config
from supervisor.state import Decision, State


def _cfg(tmp_path) -> Config:
    return Config(**{
        "target": {"kind": "codex", "codex": {"sessions_root": str(tmp_path)}},
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-fable-5",
            "drift_l3_model": "claude-fable-5",
            "drift_l4_model": "claude-fable-5",
            "post_run_eval_model": "claude-fable-5",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
    })


def test_agent_invoker_imports_without_claude_agent_sdk(monkeypatch):
    import builtins
    import importlib
    import sys

    sys.modules.pop("supervisor.agent_invoker", None)
    sys.modules.pop("claude_agent_sdk", None)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ModuleNotFoundError("No module named 'claude_agent_sdk'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    module = importlib.import_module("supervisor.agent_invoker")

    assert module.AgentInvoker is not None


def test_real_claude_agent_options_accepts_effort_and_env():
    sdk = pytest.importorskip("claude_agent_sdk")

    options = sdk.ClaudeAgentOptions(
        system_prompt="probe",
        model="claude-fable-5",
        max_turns=1,
        effort="high",
        env={"ANTHROPIC_API_KEY": "probe-key"},
    )

    assert options.effort == "high"
    assert options.env["ANTHROPIC_API_KEY"] == "probe-key"


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
async def test_agent_invoker_cancels_runtime_when_caller_is_cancelled(
    tmp_path: Path,
) -> None:
    class BlockingRuntime:
        kind = "blocking"

        def __init__(self):
            self.streaming = asyncio.Event()
            self.cancelled: list[str] = []

        async def start(self, task):
            return AgentRunHandle(
                run_id="runtime-run",
                task_id=task.task_id,
                runtime=self.kind,
                session_id="runtime-session",
                capabilities={},
            )

        async def resume(self, handle, instruction):
            raise AssertionError("resume not expected")

        async def cancel(self, handle):
            self.cancelled.append(handle.run_id)

        async def stream(self, handle):
            self.streaming.set()
            await asyncio.Future()
            yield  # pragma: no cover

        async def collect(self, handle):
            raise AssertionError("collect not expected after cancellation")

    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "review-updates.md").write_text(
        "Review updates.",
        encoding="utf-8",
    )
    runtime = BlockingRuntime()
    invoker = __import__(
        "supervisor.agent_invoker",
        fromlist=["AgentInvoker"],
    ).AgentInvoker(
        _cfg(tmp_path),
        State(str(tmp_path / "cancel.db")),
        skills,
        codex_mcp_server=object(),
        telegram_mcp_server=object(),
        agent_runtime=runtime,
    )
    task = asyncio.create_task(
        invoker._handle(
            Decision(
                kind="review_updates",
                run_id="run-cancel",
                payload={},
            )
        )
    )
    await asyncio.wait_for(runtime.streaming.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.cancelled == ["runtime-run"]


@pytest.mark.asyncio
async def test_review_updates_invoker_uses_read_only_grounding_tools(monkeypatch, tmp_path):
    import supervisor.agent_invoker as agent_invoker
    from supervisor.agent_runtime import ClaudeCodeRuntime
    from supervisor.claude_sdk_runtime import ClaudeAgentSdkTransport
    from supervisor.provider_routing import (
        configure_direct_anthropic_process_env,
        direct_anthropic_env,
    )

    monkeypatch.setenv("OPENAI_API_KEY", "litellm-key")
    configure_direct_anthropic_process_env(api_key="direct-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")

    try:
        state = State(str(tmp_path / "state.db"))
        invoker = agent_invoker.AgentInvoker(
            _cfg(tmp_path),
            state,
            Path("skills"),
            codex_mcp_server=object(),
            telegram_mcp_server=object(),
            agent_runtime=ClaudeCodeRuntime(
                transport=ClaudeAgentSdkTransport(
                    sdk_loader=lambda: (_FakeClient, _FakeOptions),
                )
            ),
            agent_environment=direct_anthropic_env(),
            inherit_agent_environment=False,
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
        assert _FakeOptions.seen.effort == "high"
        assert _FakeOptions.seen.env["ANTHROPIC_API_KEY"] == "direct-key"
        assert "ANTHROPIC_BASE_URL" not in _FakeOptions.seen.env
        assert "OPENAI_API_KEY" not in _FakeOptions.seen.env

        verdict = state._conn.execute(
            "SELECT phase, model, output_json FROM verdicts WHERE run_id='run-vela'"
        ).fetchone()
        assert verdict["phase"] == "review_updates"
        assert verdict["model"] == "claude-fable-5"
        verdict_output = json.loads(verdict["output_json"])
        assert verdict_output["requested_model"] == "claude-fable-5"
        assert verdict_output["resolved_model"] is None
        assert verdict_output["model_provenance"] == "requested_model_fallback"
        assert verdict_output["agent_outputs"] == [
            '{"review_sent": true, "grounding": "workspace"}'
        ]
    finally:
        configure_direct_anthropic_process_env()
