"""Ticket 07: Codex adapter version-probed resume command (PRD promise P2).

Public boundary: target_adapter_conformance (CodexAdapter.build_resume_command)

Requirements:
  - Current CLI form (>=0.130): codex exec resume <session_id> <prompt>
  - Old form (<0.130): codex resume <session_id> <prompt>
  - Unknown/None version: default to current form (safe forward)
  - build_resume_command must NEVER call subprocess — it only returns the argv list
  - CodexAdapter is NOT the default target; the supervisor picks target by config

Forbidden outcomes guarded against:
  - "hardcoded `codex exec` calls outside the adapter"
  - "Codex-specific commands run when target.kind is claude_code"
  - subprocess called from build_resume_command
"""
from __future__ import annotations
import subprocess
import shutil
from unittest.mock import patch

import pytest

from supervisor.target.codex import CodexAdapter
from supervisor.target.types import TargetAction


# ---------- current form (>=0.130) ----------

def test_build_resume_command_current_version():
    """codex exec resume <session_id> <prompt> for version >=0.130."""
    adapter = CodexAdapter()
    cmd = adapter.build_resume_command("sess-abc", "Re-anchor to auth", "0.131.0")
    assert cmd == ["codex", "exec", "resume", "sess-abc", "Re-anchor to auth"]


def test_build_resume_command_boundary_version_0_130():
    """0.130.0 is the first version with the exec resume subcommand."""
    adapter = CodexAdapter()
    cmd = adapter.build_resume_command("sess-abc", "Re-anchor", "0.130.0")
    assert cmd == ["codex", "exec", "resume", "sess-abc", "Re-anchor"]


# ---------- old form (<0.130) ----------

def test_build_resume_command_old_version_fallback():
    """Versions before 0.130 use: codex resume <session_id> <prompt>."""
    adapter = CodexAdapter()
    cmd = adapter.build_resume_command("sess-abc", "Re-anchor to auth", "0.129.5")
    assert cmd == ["codex", "resume", "sess-abc", "Re-anchor to auth"]


def test_build_resume_command_very_old_version():
    adapter = CodexAdapter()
    cmd = adapter.build_resume_command("sess-x", "refocus", "0.100.0")
    assert cmd == ["codex", "resume", "sess-x", "refocus"]


# ---------- unknown/None version → current form ----------

def test_build_resume_command_none_version_uses_current_form():
    """None version should default to current (newest) form, not error."""
    adapter = CodexAdapter()
    cmd = adapter.build_resume_command("sess-x", "Re-anchor", None)
    assert cmd == ["codex", "exec", "resume", "sess-x", "Re-anchor"]


def test_build_resume_command_unparseable_version_uses_current_form():
    adapter = CodexAdapter()
    cmd = adapter.build_resume_command("sess-x", "Re-anchor", "unknown")
    assert cmd == ["codex", "exec", "resume", "sess-x", "Re-anchor"]


# ---------- pure function: no subprocess ----------

def test_build_resume_command_never_calls_subprocess():
    """build_resume_command must return a list without executing anything."""
    adapter = CodexAdapter()
    with patch.object(subprocess, "run", side_effect=AssertionError("must not run")):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("must not Popen")):
            cmd = adapter.build_resume_command("sess-x", "prompt", "0.131.0")
    assert isinstance(cmd, list) and len(cmd) > 0


@pytest.mark.asyncio
async def test_execute_action_inject_steering_runs_version_gated_resume(monkeypatch):
    """Approved steering is delivered through the Codex adapter, not a core
    subprocess call. Version probe selects the current CLI resume form.
    """
    calls: list[tuple[str, ...]] = []

    class _Proc:
        def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self):
            return self._stdout, self._stderr

    async def fake_exec(*argv, stdout=None, stderr=None):
        calls.append(tuple(argv))
        if argv[-1] == "--version":
            return _Proc(b"codex 0.130.0\n")
        return _Proc(b"registered\n")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({"cli_command": "codex-test"})
    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-abc",
        payload={"message": "Re-anchor to the current issue."},
    ))

    assert result["delivered"] is True
    assert calls == [
        ("codex-test", "--version"),
        ("codex-test", "exec", "resume", "sess-abc", "Re-anchor to the current issue."),
    ]
    assert result["argv"][-1] == "[STEERING_PROMPT_REDACTED]"


@pytest.mark.asyncio
async def test_execute_action_inject_steering_uses_resume_extra_args(monkeypatch):
    """LaunchAgents often run outside a trusted repo. Approved steering must
    pass the configured Codex resume flags through the real execute path.
    """
    calls: list[tuple[str, ...]] = []

    class _Proc:
        def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self):
            return self._stdout, self._stderr

    async def fake_exec(*argv, stdout=None, stderr=None):
        calls.append(tuple(argv))
        if argv[-1] == "--version":
            return _Proc(b"codex 0.130.0\n")
        return _Proc(b"registered\n")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "codex-test",
        "resume_extra_args": ["--skip-git-repo-check"],
    })
    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-abc",
        payload={"message": "Re-anchor to the current issue."},
    ))

    assert result["delivered"] is True
    assert calls == [
        ("codex-test", "--version"),
        (
            "codex-test", "exec", "resume", "--skip-git-repo-check",
            "sess-abc", "Re-anchor to the current issue.",
        ),
    ]


@pytest.mark.asyncio
async def test_execute_action_inject_steering_uses_payload_cwd(monkeypatch, tmp_path):
    """Approved steering should resume from the supervised workspace, not the
    daemon's own cwd, so Codex keeps the right writable project root.
    """
    worktree = tmp_path / "vela-worktree"
    worktree.mkdir()
    calls: list[tuple[tuple[str, ...], str | None]] = []

    class _Proc:
        def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
            self._stdout = stdout
            self._stderr = stderr
            self.returncode = returncode

        async def communicate(self):
            return self._stdout, self._stderr

    async def fake_exec(*argv, stdout=None, stderr=None, cwd=None):
        calls.append((tuple(argv), cwd))
        if argv[-1] == "--version":
            return _Proc(b"codex 0.130.0\n")
        return _Proc(b"registered\n")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({"cli_command": "codex-test"})
    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-abc",
        payload={
            "message": "Re-anchor to the current issue.",
            "cwd": str(worktree),
        },
    ))

    assert result["delivered"] is True
    assert calls[0] == (("codex-test", "--version"), None)
    assert calls[1] == (
        ("codex-test", "exec", "resume", "sess-abc", "Re-anchor to the current issue."),
        str(worktree.resolve()),
    )
    assert result["cwd"] == str(worktree.resolve())


@pytest.mark.asyncio
async def test_execute_action_uses_configured_resume_timeout(monkeypatch):
    """Huge desktop sessions can take several seconds just to resume. The
    adapter must use the target-configured timeout instead of a tiny constant.
    """
    timeouts: list[float] = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"codex 0.130.0\n", b""

    async def fake_exec(*argv, stdout=None, stderr=None):
        return _Proc()

    async def fake_wait_for(awaitable, timeout):
        timeouts.append(timeout)
        return await awaitable

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr("asyncio.wait_for", fake_wait_for)
    adapter = CodexAdapter({
        "cli_command": "codex-test",
        "resume_timeout_s": 42,
    })

    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-abc",
        payload={"message": "Re-anchor to the current issue."},
    ))

    assert result["delivered"] is True
    assert timeouts == [2, 42]


@pytest.mark.asyncio
async def test_execute_action_finds_cli_from_search_path_when_launchd_path_misses(
    monkeypatch,
    tmp_path,
):
    """Regression for approved steering failing with codex_binary_not_found
    under launchd, where interactive shell PATH entries are absent.
    """
    fake_cli = tmp_path / "codex"
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o755)
    calls: list[tuple[str, ...]] = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"codex-cli 0.130.0\n", b""

    async def fake_exec(*argv, stdout=None, stderr=None):
        calls.append(tuple(argv))
        return _Proc()

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    adapter = CodexAdapter({
        "cli_command": "codex",
        "cli_search_paths": [str(fake_cli)],
    })

    result = await adapter.execute_action(TargetAction(
        kind="inject_steering",
        session_id="sess-abc",
        payload={"message": "Re-anchor to the current issue."},
    ))

    assert result["delivered"] is True
    assert calls[0][0] == str(fake_cli)
    assert calls[1][0] == str(fake_cli)


# ---------- CodexAdapter is NOT default ----------

def test_codex_is_not_default_target_kind():
    """Codex must not be the default target; config.target.kind defaults to claude_code."""
    from supervisor.config import Config
    cfg = Config(**{
        "orchestrator": {"run_registry_dir": "/tmp"},
        "supervisor":   {"state_db": "/tmp/test.db"},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model":          "claude-haiku-4-5",
            "drift_l4_model":          "claude-opus-4-5",
            "post_run_eval_model":     "claude-haiku-4-5",
            "embedding_model":         "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "fake"},
    })
    assert cfg.target is not None
    assert cfg.target.kind == "claude_code", (
        "Default target must be claude_code, not codex — "
        "codex is a future-compat adapter"
    )
