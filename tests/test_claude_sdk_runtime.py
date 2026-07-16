from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from supervisor import claude_sdk_runtime as claude_sdk_runtime_module
from supervisor.agent_runtime import AgentTask, ClaudeCodeRuntime
from supervisor.claude_sdk_runtime import (
    _SDK_LAUNCH_ROOT_PREFIX,
    _scrub_stale_launch_roots,
    ClaudeAgentSdkContainmentError,
    ClaudeAgentSdkPreflightError,
    ClaudeAgentSdkTransport,
    MissingClaudeAgentSdk,
)


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


def test_claude_sdk_dependency_is_loaded_during_preflight() -> None:
    def missing_loader():
        raise ModuleNotFoundError(
            "No module named 'claude_agent_sdk'",
            name="claude_agent_sdk",
        )

    with pytest.raises(MissingClaudeAgentSdk):
        ClaudeAgentSdkTransport(sdk_loader=missing_loader)


def test_claude_sdk_preflight_rejects_missing_required_capabilities() -> None:
    class IncompleteOptions:
        def __init__(self, model):
            self.model = model

    with pytest.raises(
        ClaudeAgentSdkPreflightError,
        match="lacks required runtime capabilities",
    ):
        ClaudeAgentSdkTransport(
            sdk_loader=lambda: (FakeClient, IncompleteOptions),
            allow_uncontained_test_transport=True,
        )


def test_uncontained_fake_sdk_transport_never_claims_safe_cancellation() -> None:
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (FakeClient, FakeOptions),
        allow_uncontained_test_transport=True,
    )

    capabilities = transport.preflight()

    assert capabilities.production_ready is False
    assert capabilities.environment_isolation is False
    assert capabilities.process_containment is False
    assert capabilities.safe_cancellation is False


@pytest.mark.asyncio
async def test_contained_sdk_cancellation_fails_if_launcher_never_attests(
    tmp_path: Path,
) -> None:
    class IgnoringClient(FakeClient):
        entered: asyncio.Event | None = None

        async def __aenter__(self):
            assert self.entered is not None
            self.entered.set()
            return self

        async def query(self, message):
            await asyncio.Future()

    fake_cli = tmp_path / "claude"
    fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_cli.chmod(0o700)
    IgnoringClient.entered = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (IgnoringClient, FakeOptions),
        claude_cli_path=fake_cli,
    )
    token = await transport.start(
        run_id="sdk-unattested-cancel",
        argv=("claude", "-p", "review", "--model", "claude-test"),
        cwd=tmp_path,
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await asyncio.wait_for(IgnoringClient.entered.wait(), timeout=1)

    with pytest.raises(
        ClaudeAgentSdkContainmentError,
        match="without launcher attestation",
    ):
        await transport.cancel(token)


@pytest.mark.asyncio
async def test_contained_sdk_launcher_scrubs_sdk_inherited_host_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class SpawningClient(FakeClient):
        async def __aenter__(self):
            self.process = await asyncio.create_subprocess_exec(
                str(self.options.cli_path),
                cwd=str(self.options.cwd),
                env={**os.environ, **self.options.env},
            )
            await self.process.wait()
            return self

        async def receive_response(self):
            class Block:
                text = "done"

            class Message:
                content = [Block()]
                model = "claude-served"

            yield Message()

    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    fake_cli = tmp_path / "claude"
    fake_cli.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['CLAUDE_CONFIG_DIR'], 'env.json').write_text("
        "json.dumps(dict(os.environ), sort_keys=True), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o700)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    runtime = ClaudeCodeRuntime(
        transport=ClaudeAgentSdkTransport(
            sdk_loader=lambda: (SpawningClient, FakeOptions),
            claude_cli_path=fake_cli,
        )
    )

    handle = await runtime.start(
        AgentTask(
            task_id="sdk-env-isolation",
            instruction="review",
            cwd=tmp_path,
            model="claude-test",
            inherit_env=True,
            env={
                "ANTHROPIC_API_KEY": "direct-key",
                "CLAUDE_CONFIG_DIR": str(capture_dir),
            },
        )
    )
    result = await runtime.collect(handle)

    assert result.status == "completed"
    assert handle.capabilities["safe_process_cancellation"] is True
    observed = json.loads(
        (capture_dir / "env.json").read_text(encoding="utf-8")
    )
    assert observed["ANTHROPIC_API_KEY"] == "direct-key"
    assert observed["CLAUDE_CONFIG_DIR"] == str(capture_dir)
    for forbidden in (
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_SUPERVISOR_SDK_LAUNCH_CONFIG",
    ):
        assert forbidden not in observed


@pytest.mark.asyncio
async def test_contained_sdk_launch_config_omits_secret_env_values(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class SpawningClient(FakeClient):
        async def __aenter__(self):
            captured["config_text"] = Path(
                self.options.env["CODEX_SUPERVISOR_SDK_LAUNCH_CONFIG"]
            ).read_text(encoding="utf-8")
            captured["options_env"] = dict(self.options.env)
            self.process = await asyncio.create_subprocess_exec(
                str(self.options.cli_path),
                cwd=str(self.options.cwd),
                env={**os.environ, **self.options.env},
            )
            await self.process.wait()
            return self

        async def receive_response(self):
            class Block:
                text = "done"

            class Message:
                content = [Block()]
                model = "claude-served"

            yield Message()

    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    fake_cli = tmp_path / "claude"
    fake_cli.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['CLAUDE_CONFIG_DIR'], 'env.json').write_text("
        "json.dumps(dict(os.environ), sort_keys=True), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o700)
    runtime = ClaudeCodeRuntime(
        transport=ClaudeAgentSdkTransport(
            sdk_loader=lambda: (SpawningClient, FakeOptions),
            claude_cli_path=fake_cli,
        )
    )

    handle = await runtime.start(
        AgentTask(
            task_id="sdk-launch-config-secrets",
            instruction="review",
            cwd=tmp_path,
            model="claude-test",
            inherit_env=False,
            env={
                "ANTHROPIC_API_KEY": "direct-key",
                "CLAUDE_CONFIG_DIR": str(capture_dir),
            },
        )
    )
    result = await runtime.collect(handle)

    assert result.status == "completed"
    config_text = str(captured["config_text"])
    assert "direct-key" not in config_text
    config = json.loads(config_text)
    assert "ANTHROPIC_API_KEY" not in config["environment"]
    assert "ANTHROPIC_API_KEY" in config["sensitive_env_keys"]
    options_env = captured["options_env"]
    assert options_env["ANTHROPIC_API_KEY"] == "direct-key"
    observed = json.loads(
        (capture_dir / "env.json").read_text(encoding="utf-8")
    )
    assert observed["ANTHROPIC_API_KEY"] == "direct-key"
    assert observed["CLAUDE_CONFIG_DIR"] == str(capture_dir)


def test_scrub_stale_launch_roots_removes_only_old_sdk_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    old = 1.0
    stale = tmp_path / f"{_SDK_LAUNCH_ROOT_PREFIX}stale"
    stale.mkdir()
    (stale / "launch.json").write_text("{}", encoding="utf-8")
    os.utime(stale, (old, old))
    fresh = tmp_path / f"{_SDK_LAUNCH_ROOT_PREFIX}fresh"
    fresh.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    os.utime(unrelated, (old, old))

    _scrub_stale_launch_roots()

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_claude_sdk_is_confined_to_a_runtime_transport(
    tmp_path: Path,
) -> None:
    runtime = ClaudeCodeRuntime(
        transport=ClaudeAgentSdkTransport(
            sdk_loader=lambda: (FakeClient, FakeOptions),
            allow_uncontained_test_transport=True,
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
    assert handle.capabilities["cancel"] is False
    assert handle.capabilities["safe_process_cancellation"] is False
    assert FakeOptions.seen.model == "claude-test"
    assert FakeOptions.seen.system_prompt == "system"
    assert FakeOptions.seen.cwd == tmp_path.resolve()
    assert FakeOptions.seen.env["ANTHROPIC_API_KEY"] == "direct"
    assert "OPENAI_API_KEY" not in FakeOptions.seen.env
    assert "GITHUB_TOKEN" not in FakeOptions.seen.env
    assert FakeOptions.seen.max_budget_usd == 0.5


@pytest.mark.asyncio
async def test_claude_sdk_timeout_budget_starts_when_runtime_starts(
    tmp_path: Path,
) -> None:
    FakeOptions.seen = None
    HangingClient.query_started = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (HangingClient, FakeOptions),
        allow_uncontained_test_transport=True,
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
async def test_claude_sdk_retains_terminal_execution_until_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        claude_sdk_runtime_module,
        "_MAX_RETAINED_TERMINAL_EXECUTIONS",
        1,
    )
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (FakeClient, FakeOptions),
        allow_uncontained_test_transport=True,
    )

    async def start_terminal(run_id: str) -> str:
        token = await transport.start(
            run_id=run_id,
            argv=("claude", "-p", "review", "--model", "claude-test"),
            cwd=tmp_path.resolve(),
            env={"ANTHROPIC_API_KEY": "direct"},
            timeout_s=30,
            metadata={},
        )
        while transport.is_active(token):
            await asyncio.sleep(0)
        return token

    retained = await start_terminal("sdk-unharvested-terminal")
    await start_terminal("sdk-retention-pressure-0")
    await start_terminal("sdk-retention-pressure-1")

    result = await transport.collect(retained)

    assert result.returncode == 0


@pytest.mark.asyncio
async def test_claude_sdk_evicts_collected_execution_at_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        claude_sdk_runtime_module,
        "_MAX_RETAINED_TERMINAL_EXECUTIONS",
        1,
    )
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (FakeClient, FakeOptions),
        allow_uncontained_test_transport=True,
    )
    collected = await transport.start(
        run_id="sdk-collected-terminal",
        argv=("claude", "-p", "first", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await transport.collect(collected)

    replacement = await transport.start(
        run_id="sdk-replacement-terminal",
        argv=("claude", "-p", "replacement", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await transport.collect(replacement)

    with pytest.raises(KeyError, match="unknown Claude SDK runtime token"):
        await transport.collect(collected)


@pytest.mark.asyncio
async def test_claude_sdk_same_run_id_requires_terminal_collect(
    tmp_path: Path,
) -> None:
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (FakeClient, FakeOptions),
        allow_uncontained_test_transport=True,
    )
    token = await transport.start(
        run_id="sdk-same-run-id",
        argv=("claude", "-p", "first", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    while transport.is_active(token):
        await asyncio.sleep(0)

    with pytest.raises(
        RuntimeError,
        match="terminal execution before collection",
    ):
        await transport.start(
            run_id="sdk-same-run-id",
            argv=("claude", "-p", "replacement", "--model", "claude-test"),
            cwd=tmp_path.resolve(),
            env={"ANTHROPIC_API_KEY": "direct"},
            timeout_s=30,
            metadata={},
        )

    await transport.collect(token)
    replacement = await transport.start(
        run_id="sdk-same-run-id",
        argv=("claude", "-p", "replacement", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    assert (await transport.collect(replacement)).returncode == 0


@pytest.mark.asyncio
async def test_claude_sdk_resume_requires_new_collect_before_eviction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        claude_sdk_runtime_module,
        "_MAX_RETAINED_TERMINAL_EXECUTIONS",
        1,
    )
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (FakeClient, FakeOptions),
        allow_uncontained_test_transport=True,
    )
    resumed = await transport.start(
        run_id="sdk-resumed-retention",
        argv=("claude", "-p", "first", "--model", "claude-test"),
        cwd=tmp_path.resolve(),
        env={"ANTHROPIC_API_KEY": "direct"},
        timeout_s=30,
        metadata={},
    )
    await transport.collect(resumed)
    await transport.resume(
        resumed,
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
    while transport.is_active(resumed):
        await asyncio.sleep(0)
    for index in range(2):
        pressure = await transport.start(
            run_id=f"sdk-resume-pressure-{index}",
            argv=("claude", "-p", "pressure", "--model", "claude-test"),
            cwd=tmp_path.resolve(),
            env={"ANTHROPIC_API_KEY": "direct"},
            timeout_s=30,
            metadata={},
        )
        while transport.is_active(pressure):
            await asyncio.sleep(0)

    result = await transport.collect(resumed)

    assert result.returncode == 0


@pytest.mark.asyncio
async def test_claude_sdk_collect_propagates_caller_cancellation(
    tmp_path: Path,
) -> None:
    HangingClient.query_started = asyncio.Event()
    transport = ClaudeAgentSdkTransport(
        sdk_loader=lambda: (HangingClient, FakeOptions),
        allow_uncontained_test_transport=True,
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
        allow_uncontained_test_transport=True,
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
        allow_uncontained_test_transport=True,
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
        allow_uncontained_test_transport=True,
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
