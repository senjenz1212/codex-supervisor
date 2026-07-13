from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from supervisor.hook_critic import ModelClientHookCritic
from supervisor.provider_clients import ClaudeAgentSdkModelClient
from supervisor.provider_routing import configure_direct_anthropic_process_env
from supervisor.telegram_supervisor import ClaudeAgentSupervisorRuntime


class _FakeOptions:
    instances: list["_FakeOptions"] = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.instances.append(self)


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
        text = (
            '{"action":"allow","reason":"safe","confidence":0.9}'
            if "supervisor critic" in self.options.system_prompt
            else '{"summary":"Watching the active run.","active_run_id":"run-1"}'
        )
        yield SimpleNamespace(
            content=[SimpleNamespace(text=text)],
            session_id="session-1",
            model="claude-fable-5",
        )


@pytest.fixture
def direct_claude_sdk(monkeypatch):
    _FakeOptions.instances.clear()
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(
            ClaudeAgentOptions=_FakeOptions,
            ClaudeSDKClient=_FakeClient,
        ),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "litellm-key")
    configure_direct_anthropic_process_env(api_key="direct-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    try:
        yield
    finally:
        configure_direct_anthropic_process_env()


def _assert_direct_child_env(options: _FakeOptions) -> None:
    assert options.env["ANTHROPIC_API_KEY"] == "direct-key"
    assert "ANTHROPIC_BASE_URL" not in options.env
    assert "OPENAI_API_KEY" not in options.env


@pytest.mark.asyncio
async def test_hook_critic_injects_direct_anthropic_child_env(
    direct_claude_sdk,
) -> None:
    cfg = SimpleNamespace(
        models=SimpleNamespace(realtime_critique_model="claude-fable-5")
    )
    critic = ModelClientHookCritic(
        cfg,
        model_client=ClaudeAgentSdkModelClient(),
    )
    hook_event = SimpleNamespace(
        source_target="codex",
        hook_kind="PreToolUse",
        session_id="session-1",
        tool_name="Read",
        tool_args={"path": "README.md"},
    )

    result = await critic.critique(hook_event, raw_payload={})

    assert result["action"] == "allow"
    _assert_direct_child_env(_FakeOptions.instances[-1])


@pytest.mark.asyncio
async def test_telegram_summary_injects_direct_anthropic_child_env(
    direct_claude_sdk,
) -> None:
    cfg = SimpleNamespace(
        models=SimpleNamespace(post_run_eval_model="claude-fable-5")
    )
    runtime = ClaudeAgentSupervisorRuntime(
        cfg,
        state=object(),
        agent_runtime=object(),  # type: ignore[arg-type]
        summary_client=ClaudeAgentSdkModelClient(),
    )

    result = await runtime.summarize_conversation(
        conversation_context={"active_run_id": "run-1"},
        recent_turns=[],
    )

    assert result["active_run_id"] == "run-1"
    options = _FakeOptions.instances[-1]
    _assert_direct_child_env(options)
    assert options.permission_mode == "dontAsk"
