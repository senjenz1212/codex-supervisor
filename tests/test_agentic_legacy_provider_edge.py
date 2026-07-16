from __future__ import annotations

import subprocess
from pathlib import Path

from supervisor.agent_runtime import AgentTask
from supervisor.agentic_legacy_provider_edge import execute_legacy_agent_task


_SECRET = "sk-ant-leaked-secret-value-123456"


def _task(tmp_path: Path) -> AgentTask:
    return AgentTask(
        task_id="task-1",
        instruction="do work",
        cwd=tmp_path,
        model="claude-test",
        timeout_s=5,
        inherit_env=False,
    )


def test_execute_legacy_agent_task_redacts_stderr_and_error(
    tmp_path: Path,
) -> None:
    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr=f"auth failed for {_SECRET}\n",
        )

    execution = execute_legacy_agent_task(_task(tmp_path), runner=fake_runner)

    metadata = execution.result.metadata
    assert _SECRET not in metadata["stderr"]
    assert _SECRET not in metadata["error"]
    assert "[REDACTED_API_KEY]" in metadata["stderr"]
    assert "[REDACTED_API_KEY]" in metadata["error"]
    for event in execution.events:
        assert _SECRET not in str(event.payload)


def test_execute_legacy_agent_task_redacts_timeout_stderr(
    tmp_path: Path,
) -> None:
    def fake_runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            argv,
            timeout=5,
            output=b"",
            stderr=f"env dump ANTHROPIC_API_KEY={_SECRET}".encode(),
        )

    execution = execute_legacy_agent_task(_task(tmp_path), runner=fake_runner)

    metadata = execution.result.metadata
    assert _SECRET not in metadata["stderr"]
    assert "ANTHROPIC_API_KEY=[REDACTED" in metadata["stderr"]
    assert metadata["failure_reason"] == "timeout"
