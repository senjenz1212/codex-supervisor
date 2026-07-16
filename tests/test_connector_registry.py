from __future__ import annotations

import asyncio
import json

import pytest

from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from supervisor.config import Config
from supervisor.connectors import (
    BASE_SUPERVISOR_ALLOWED_TOOLS,
    connector_allowed_tools,
    external_mcp_servers,
    load_claude_desktop_mcp_servers,
)
from supervisor.state import State
from supervisor.telegram_supervisor import ClaudeAgentSupervisorRuntime
from supervisor.supervisor_tools import SupervisorToolAPI


def _cfg(tmp_path, *, connectors: dict | None = None) -> Config:
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
            "realtime_critique_model": "claude-opus-4-7",
            "drift_l3_model": "claude-opus-4-7",
            "drift_l4_model": "claude-opus-4-7",
            "post_run_eval_model": "claude-opus-4-7",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
        "connectors": connectors or {},
    })


def test_load_claude_desktop_mcp_servers_reads_only_mcp_servers(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({
        "theme": "dark",
        "mcpServers": {
            "slack": {
                "type": "http",
                "url": "https://mcp.slack.com/mcp",
                "headers": {"Authorization": "Bearer ${SLACK_MCP_TOKEN}"},
            }
        },
    }))

    servers = load_claude_desktop_mcp_servers(path)

    assert sorted(servers) == ["slack"]
    assert servers["slack"]["type"] == "http"


def test_external_connectors_are_inert_until_enabled_and_tool_allowlisted(tmp_path):
    cfg = _cfg(tmp_path, connectors={
        "enabled": False,
        "mcp_servers": {
            "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        },
        "allowed_tools": ["mcp__slack__search"],
    })

    assert external_mcp_servers(cfg) == {}
    assert connector_allowed_tools(cfg) == list(BASE_SUPERVISOR_ALLOWED_TOOLS)


def test_connector_registry_merges_claude_desktop_and_local_servers(tmp_path):
    desktop = tmp_path / "claude_desktop_config.json"
    desktop.write_text(json.dumps({
        "mcpServers": {
            "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        },
    }))
    cfg = _cfg(tmp_path, connectors={
        "enabled": True,
        "import_from_claude_desktop": True,
        "claude_desktop_config_path": str(desktop),
        "mcp_servers": {
            "drive": {"type": "http", "url": "https://example.test/mcp"},
        },
        "allowed_tools": ["mcp__slack__search", "mcp__drive__search"],
    })

    assert sorted(external_mcp_servers(cfg)) == ["drive", "slack"]
    assert "mcp__slack__search" in connector_allowed_tools(cfg)
    assert "mcp__drive__search" in connector_allowed_tools(cfg)


def test_telegram_runtime_builds_metadata_with_connector_mcp_servers(tmp_path):
    from supervisor.provider_routing import configure_direct_anthropic_process_env

    cfg = _cfg(tmp_path, connectors={
        "enabled": True,
        "mcp_servers": {
            "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        },
        "allowed_tools": ["mcp__slack__search"],
        "disallowed_tools": ["mcp__slack__send_message"],
    })
    state = State(str(tmp_path / "state.db"))
    runtime = ClaudeAgentSupervisorRuntime(
        cfg,
        state,
        agent_runtime=object(),  # type: ignore[arg-type]
        summary_client=object(),  # type: ignore[arg-type]
        supervisor_mcp_factory=lambda _cfg, _state, _api: {
            "name": "supervisor-test-mcp",
        },
    )

    configure_direct_anthropic_process_env(api_key="direct-key")
    try:
        options = runtime._build_runtime_metadata(SupervisorToolAPI(state))
    finally:
        configure_direct_anthropic_process_env()

    assert sorted(options["mcp_servers"]) == ["slack", "supervisor"]
    assert "mcp__supervisor__list_runs" in options["allowed_tools"]
    assert "mcp__supervisor__watch_run" in options["allowed_tools"]
    assert "mcp__supervisor__request_steering" in options["allowed_tools"]
    assert "mcp__supervisor__list_run_watches" in options["allowed_tools"]
    assert "mcp__supervisor__read_workspace_snapshot" in options["allowed_tools"]
    assert "mcp__supervisor__read_workspace_file" in options["allowed_tools"]
    assert "mcp__supervisor__request_mode_change" not in options["allowed_tools"]
    assert "mcp__slack__search" in options["allowed_tools"]
    assert "mcp__slack__send_message" in options["disallowed_tools"]
    assert options["permission_mode"] == "dontAsk"


def test_telegram_runtime_resumes_only_valid_claude_session_ids(tmp_path):
    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    runtime = ClaudeAgentSupervisorRuntime(
        cfg,
        state,
        agent_runtime=object(),  # type: ignore[arg-type]
        summary_client=object(),  # type: ignore[arg-type]
        supervisor_mcp_factory=lambda _cfg, _state, _api: {
            "name": "supervisor-test-mcp",
        },
    )
    api = SupervisorToolAPI(state)

    valid = "019e4f33-1a30-7337-9d9b-0f34c5da0001"
    options = runtime._build_runtime_metadata(
        api,
        conversation_context={"claude_session_id": valid},
    )
    invalid_options = runtime._build_runtime_metadata(
        api,
        conversation_context={"claude_session_id": "not-a-sdk-session-id"},
    )

    assert options["resume_session_id"] == valid
    assert invalid_options["resume_session_id"] is None


@pytest.mark.asyncio
async def test_telegram_answer_runs_through_agent_runtime_seam(tmp_path):
    class RecordingRuntime:
        kind = "recording"

        def __init__(self):
            self.task: AgentTask | None = None

        async def start(self, task: AgentTask) -> AgentRunHandle:
            self.task = task
            return AgentRunHandle(
                run_id="runtime-run",
                task_id=task.task_id,
                runtime=self.kind,
                session_id="runtime-run",
                capabilities={"stream": True},
            )

        async def resume(self, handle, instruction):
            raise AssertionError("resume is represented in start metadata")

        async def cancel(self, handle):
            return None

        async def stream(self, handle):
            yield RuntimeEvent(
                kind="agent.message",
                payload={"message": "Observed through the runtime seam."},
                ts_ms=1,
            )

        async def collect(self, handle):
            return AgentRunResult(
                run_id=handle.run_id,
                task_id=handle.task_id,
                runtime=self.kind,
                session_id="019e4f33-1a30-7337-9d9b-0f34c5da0001",
                status="completed",
                output="Observed through the runtime seam.",
                events=(),
                started_at_ms=1,
                ended_at_ms=2,
                cost_usd=0.0,
                resolved_model="served-model",
                result_hash="a" * 64,
            )

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    agent_runtime = RecordingRuntime()
    runtime = ClaudeAgentSupervisorRuntime(
        cfg,
        state,
        agent_runtime=agent_runtime,
        summary_client=object(),  # type: ignore[arg-type]
        inherit_agent_environment=False,
        supervisor_mcp_factory=lambda _cfg, _state, _api: {
            "name": "supervisor-test-mcp",
        },
    )

    result = await runtime.answer(
        message="What is running?",
        tool_api=SupervisorToolAPI(state),
        conversation_context={
            "active_run_id": "workflow-run",
            "claude_session_id": "019e4f33-1a30-7337-9d9b-0f34c5da0000",
        },
    )

    assert result["text"] == "Observed through the runtime seam."
    assert result["claude_session_id"].endswith("0001")
    assert agent_runtime.task is not None
    assert agent_runtime.task.inherit_env is False
    assert agent_runtime.task.metadata["resume_session_id"].endswith("0000")
    assert "supervisor" in agent_runtime.task.metadata["mcp_servers"]


@pytest.mark.asyncio
async def test_telegram_runtime_cancels_agent_when_answer_is_cancelled(
    tmp_path,
) -> None:
    class BlockingRuntime:
        kind = "blocking"

        def __init__(self):
            self.streaming = asyncio.Event()
            self.cancelled: list[str] = []

        async def start(self, task):
            return AgentRunHandle(
                run_id="telegram-runtime-run",
                task_id=task.task_id,
                runtime=self.kind,
                session_id="telegram-session",
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

    state = State(str(tmp_path / "telegram-cancel.db"))
    agent_runtime = BlockingRuntime()
    runtime = ClaudeAgentSupervisorRuntime(
        _cfg(tmp_path),
        state,
        agent_runtime=agent_runtime,
        summary_client=object(),  # type: ignore[arg-type]
        inherit_agent_environment=False,
        supervisor_mcp_factory=lambda _cfg, _state, _api: {
            "name": "supervisor-test-mcp",
        },
    )
    task = asyncio.create_task(
        runtime.answer(
            message="What is running?",
            tool_api=SupervisorToolAPI(state),
            conversation_context={"active_run_id": "run-cancel"},
        )
    )
    await asyncio.wait_for(agent_runtime.streaming.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent_runtime.cancelled == ["telegram-runtime-run"]
