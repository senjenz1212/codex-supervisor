from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from supervisor.agent_runtime import AgentTask, ClaudeCodeRuntime
from supervisor.claude_sdk_runtime import ClaudeAgentSdkTransport


class FakeOptions:
    seen = None

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        FakeOptions.seen = self


class FakeClient:
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
            text = "provider-neutral output"

        class Message:
            content = [Block()]
            model = "claude-served-v2"
            usage = {
                "input_tokens": 13,
                "output_tokens": 5,
            }

        class Result:
            content = []
            total_cost_usd = 0.375
            usage = {
                "input_tokens": 13,
                "cache_read_input_tokens": 2,
                "output_tokens": 5,
            }
            model_usage = {
                "claude-served-v2": {
                    "inputTokens": 13,
                    "cacheReadInputTokens": 2,
                    "outputTokens": 5,
                    "costUSD": 0.375,
                }
            }

        yield Message()
        yield Result()


class HangingClient(FakeClient):
    query_started: asyncio.Event | None = None

    async def query(self, message):
        self.message = message
        assert self.query_started is not None
        self.query_started.set()
        await asyncio.Future()


class CleanupBlockingClient(HangingClient):
    cleanup_started: asyncio.Event | None = None
    cleanup_release: asyncio.Event | None = None
    cleanup_finished: asyncio.Event | None = None

    async def __aexit__(self, exc_type, exc, tb):
        assert self.cleanup_started is not None
        assert self.cleanup_release is not None
        assert self.cleanup_finished is not None
        self.cleanup_started.set()
        await self.cleanup_release.wait()
        self.cleanup_finished.set()
        return False


@pytest.mark.asyncio
async def test_claude_sdk_is_confined_to_a_runtime_transport(
    tmp_path: Path,
) -> None:
    runtime = ClaudeCodeRuntime(
        transport=ClaudeAgentSdkTransport(
            sdk_loader=lambda: (FakeClient, FakeOptions),
        )
    )
    handle = await runtime.start(
        AgentTask(
            task_id="decision-1",
            instruction="review",
            cwd=tmp_path,
            model="claude-test",
            env={"ANTHROPIC_API_KEY": "direct"},
            inherit_env=False,
            metadata={
                "system_prompt": "system",
                "max_turns": 4,
                "allowed_tools": ["read"],
                "effort": "high",
                "max_budget_usd": 0.5,
                "mcp_servers": {"codex": object()},
            },
        )
    )
    events = [event async for event in runtime.stream(handle)]
    result = await runtime.collect(handle)

    assert result.output == "provider-neutral output"
    assert result.status == "completed"
    assert result.resolved_model == "claude-served-v2"
    assert result.cost_usd == 0.375
    assert result.token_usage["tokens_in"] == 15
    assert result.token_usage["tokens_out"] == 5
    assert result.model_provenance == "claude_agent_sdk.message.model"
    assert result.cost_provenance == "claude_agent_sdk.result.total_cost_usd"
    assert result.token_provenance == "claude_agent_sdk.result.usage"
    assert [event.kind for event in events] == [
        "run.started",
        "agent.message",
        "run.completed",
    ]
    assert handle.capabilities["filesystem_isolation"] is False
    assert FakeOptions.seen.model == "claude-test"
    assert FakeOptions.seen.system_prompt == "system"
    assert FakeOptions.seen.cwd == tmp_path.resolve()
    assert FakeOptions.seen.env == {"ANTHROPIC_API_KEY": "direct"}
    assert FakeOptions.seen.max_budget_usd == 0.5


@pytest.mark.asyncio
async def test_claude_sdk_timeout_budget_starts_when_runtime_starts(
    tmp_path: Path,
) -> None:
    FakeOptions.seen = None
    HangingClient.query_started = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (HangingClient, FakeOptions),
    )
    token = await transport.start(
        run_id="sdk-timeout-from-start",
        argv=("claude", "-p", "review", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=1,
        metadata={},
    )
    await asyncio.wait_for(HangingClient.query_started.wait(), timeout=0.2)

    await asyncio.sleep(1.1)
    result = await asyncio.wait_for(transport.collect(token), timeout=0.2)

    assert result.returncode == 124
    assert [event["type"] for event in result.raw_events] == [
        "run.started",
        "run.failed",
    ]
    assert FakeOptions.seen.cwd == tmp_path.resolve()


@pytest.mark.asyncio
async def test_claude_sdk_collect_propagates_caller_cancellation(
    tmp_path: Path,
) -> None:
    HangingClient.query_started = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (HangingClient, FakeOptions),
    )
    token = await transport.start(
        run_id="sdk-collect-caller-cancelled",
        argv=("claude", "-p", "review", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await asyncio.wait_for(HangingClient.query_started.wait(), timeout=0.2)

    collection = asyncio.create_task(transport.collect(token))
    await asyncio.sleep(0)
    collection.cancel()

    with pytest.raises(asyncio.CancelledError):
        await collection

    cancelled = await transport.collect(token)
    assert cancelled.returncode == 130
    assert [event["type"] for event in cancelled.raw_events] == [
        "run.started",
        "run.cancelled",
    ]


@pytest.mark.asyncio
async def test_claude_sdk_collect_waits_for_cleanup_before_propagating(
    tmp_path: Path,
) -> None:
    CleanupBlockingClient.query_started = asyncio.Event()
    CleanupBlockingClient.cleanup_started = asyncio.Event()
    CleanupBlockingClient.cleanup_release = asyncio.Event()
    CleanupBlockingClient.cleanup_finished = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (CleanupBlockingClient, FakeOptions),
    )
    token = await transport.start(
        run_id="sdk-collect-cancellation-cleanup",
        argv=("claude", "-p", "review", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await asyncio.wait_for(
        CleanupBlockingClient.query_started.wait(),
        timeout=0.2,
    )

    collection = asyncio.create_task(transport.collect(token))
    await asyncio.sleep(0)
    collection.cancel()
    await asyncio.wait_for(
        CleanupBlockingClient.cleanup_started.wait(),
        timeout=0.2,
    )

    assert not collection.done()
    assert not CleanupBlockingClient.cleanup_finished.is_set()

    CleanupBlockingClient.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await collection

    assert CleanupBlockingClient.cleanup_finished.is_set()
    events = [
        event
        async for event in transport.stream(token)
    ]
    assert [event["type"] for event in events] == [
        "run.started",
        "run.cancelled",
    ]


@pytest.mark.asyncio
async def test_claude_sdk_resume_rejects_overlapping_execution(
    tmp_path: Path,
) -> None:
    HangingClient.query_started = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (HangingClient, FakeOptions),
    )
    token = await transport.start(
        run_id="sdk-overlapping-resume",
        argv=("claude", "-p", "review", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await asyncio.wait_for(HangingClient.query_started.wait(), timeout=0.2)

    try:
        with pytest.raises(
            RuntimeError,
            match="previous runtime generation is active",
        ):
            await transport.resume(
                token,
                argv=(
                    "claude",
                    "-p",
                    "continue",
                    "--model",
                    "claude-test",
                    "--resume",
                    "session-1",
                ),
                cwd=tmp_path.resolve(),
                env={"ANTHROPIC_API_KEY": "direct"},
                timeout_s=30,
                metadata={},
            )
    finally:
        await transport.cancel(token)
        await transport.collect(token)


@pytest.mark.asyncio
async def test_claude_sdk_resume_stream_starts_at_the_resumed_generation(
    tmp_path: Path,
) -> None:
    class SequencedClient(FakeClient):
        generation = 0

        async def __aenter__(self):
            type(self).generation += 1
            self.current_generation = type(self).generation
            return self

        async def receive_response(self):
            generation = self.current_generation

            class Block:
                text = f"generation-{generation}"

            class Message:
                content = [Block()]
                session_id = f"session-{generation}"
                model = "claude-served-v2"

            yield Message()

    SequencedClient.generation = 0
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (SequencedClient, FakeOptions),
    )
    token = await transport.start(
        run_id="sdk-resume-stream-generation",
        argv=("claude", "-p", "first", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    first = await transport.collect(token)
    assert any(
        event.get("message") == "generation-1"
        for event in first.raw_events
    )

    await transport.resume(
        token,
        argv=(
            "claude",
            "-p",
            "second",
            "--model",
            "claude-test",
            "--resume",
            "session-1",
        ),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    resumed_events = [
        event async for event in transport.stream(token)
    ]
    second = await transport.collect(token)

    assert [event["type"] for event in resumed_events] == [
        "run.started",
        "agent_message",
        "run.completed",
    ]
    assert [
        event.get("message")
        for event in resumed_events
        if event["type"] == "agent_message"
    ] == ["generation-2"]
    assert [
        event.get("message")
        for event in second.raw_events
        if event["type"] == "agent_message"
    ] == ["generation-1", "generation-2"]
