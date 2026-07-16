from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from supervisor.cursor_agent import CursorInvocationRequest
from supervisor.reviewer_legacy_provider_edge import CodexCliReviewer


@dataclass(frozen=True)
class _Spec:
    reviewer_id: str = "independent-reviewer-1"
    runtime: str = "codex_cli"
    model: str | None = "gpt-5.5"


def test_codex_cli_reviewer_passes_allowlisted_env_without_ambient_secrets(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("HOME", "/safe/home")
    monkeypatch.setenv("OPENAI_API_KEY", "codex-openai-key")
    monkeypatch.setenv("CODEX_HOME", "/safe/codex-home")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret-must-not-leak")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "litellm-secret-must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret-must-not-leak")
    runner_kwargs: dict[str, object] = {}

    def fake_runner(argv, **kwargs):
        runner_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    reviewer = CodexCliReviewer(spec=_Spec(), runner=fake_runner)
    reviewer.review(CursorInvocationRequest(
        task_id="workflow-1",
        gate="outcome_review",
        instruction="Review.",
        cwd=tmp_path,
    ))

    child_env = runner_kwargs["env"]
    assert isinstance(child_env, dict)
    assert child_env["PATH"] == "/safe/bin"
    assert child_env["HOME"] == "/safe/home"
    assert child_env["OPENAI_API_KEY"] == "codex-openai-key"
    assert child_env["CODEX_HOME"] == "/safe/codex-home"
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "LITELLM_MASTER_KEY" not in child_env
    assert "GITHUB_TOKEN" not in child_env
