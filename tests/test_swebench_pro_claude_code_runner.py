from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import swebench_pro_claude_code_runner as runner


def _write_fake_claude(path: Path, *, marker: str = "accept") -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('2.1.114 (Claude Code)')\n"
        "    raise SystemExit(0)\n"
        "assert os.environ['ANTHROPIC_BASE_URL'] == 'https://uai-litellm.internal.unity.com'\n"
        "assert os.environ['ANTHROPIC_API_KEY'] == 'route-secret'\n"
        "assert os.environ['ANTHROPIC_AUTH_TOKEN'] == 'route-secret'\n"
        "Path = __import__('pathlib').Path\n"
        "Path('solver_file.txt').write_text('changed by fake claude\\n')\n"
        "print(json.dumps({\n"
        "  'result': 'patched\\nSWEBENCH_SOLVER_DECISION: " + marker + "',\n"
        "  'model': 'claude-3-5-haiku-20241022',\n"
        "  'total_cost_usd': 0.0123,\n"
        "  'usage': {'input_tokens': 11, 'output_tokens': 7},\n"
        "  'modelUsage': {'claude-3-5-haiku-20241022': {'contextWindow': 200000, 'maxOutputTokens': 64000}},\n"
        "}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _packet(tmp_path: Path) -> Path:
    packet = {
        "schema_version": "supervisor-swebench-single-agent-public-packet/v1",
        "instance_id": "instance_repo__proj-abc",
        "repo": "repo/proj",
        "base_commit": "abc123",
        "problem_statement": "Fix the thing.",
        "attempt_index": 2,
        "attempt_count": 5,
    }
    path = tmp_path / "public_packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


def test_claude_code_runner_writes_attempt_output_with_pinned_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_claude = tmp_path / "fake-claude"
    _write_fake_claude(fake_claude)
    template = tmp_path / "prompt.md"
    template.write_text(
        "Instance {{instance_id}}\n{{problem_statement}}\n{{public_packet_json}}\n",
        encoding="utf-8",
    )
    output = tmp_path / "attempt_output.json"
    monkeypatch.setenv(runner.PUBLIC_PACKET_ENV, str(_packet(tmp_path)))
    monkeypatch.setenv(runner.ATTEMPT_OUTPUT_ENV, str(output))
    monkeypatch.setenv("SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN", "route-secret")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    rc = runner.main([
        "--prompt-template",
        str(template),
        "--claude-bin",
        str(fake_claude),
        "--model",
        "claude-3-5-haiku-20241022",
        "--provider",
        "anthropic_via_unity_litellm",
        "--runner-label",
        "claude-code-litellm-haiku-real-swebench-pro-pilot",
    ])

    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["accept"] is True
    assert payload["candidate_id"] == "instance_repo__proj-abc-attempt-2"
    assert payload["provider"] == "anthropic_via_unity_litellm"
    assert payload["runner_label"] == "claude-code-litellm-haiku-real-swebench-pro-pilot"
    assert payload["route"]["base_url"] == "https://uai-litellm.internal.unity.com"
    assert payload["route"]["secret_env"] == "SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN"
    assert payload["claude_code"]["version"] == "2.1.114 (Claude Code)"
    assert payload["token_usage"]["context_window"] == 200000
    assert "route-secret" not in output.read_text(encoding="utf-8")


def test_claude_env_overrides_preexisting_anthropic_token_when_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN", "route-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "route-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "route-secret")

    env = runner._claude_env(
        "https://uai-litellm.internal.unity.com",
        "SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN",
    )

    assert env["ANTHROPIC_AUTH_TOKEN"] == "route-secret"
    assert env["ANTHROPIC_API_KEY"] == "route-secret"
    assert env["ANTHROPIC_BASE_URL"] == "https://uai-litellm.internal.unity.com"


def test_claude_env_fails_closed_when_preexisting_api_key_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN", "route-secret")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "operators-personal-key")

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        runner._claude_env(
            "https://uai-litellm.internal.unity.com",
            "SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN",
        )


def test_claude_env_fails_closed_when_preexisting_auth_token_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN", "route-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "operators-anthropic-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ANTHROPIC_AUTH_TOKEN"):
        runner._claude_env(
            "https://uai-litellm.internal.unity.com",
            "SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN",
        )


def test_claude_env_overrides_preexisting_when_token_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "operators-personal-key")

    env = runner._claude_env(
        "https://uai-litellm.internal.unity.com",
        "SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN",
    )

    assert env["ANTHROPIC_AUTH_TOKEN"] == "operators-personal-key"


def test_decision_from_result_uses_last_marker() -> None:
    text = (
        "I considered rejecting first.\n"
        "SWEBENCH_SOLVER_DECISION: reject\n"
        "After reviewing the tests, here is my final answer.\n"
        "SWEBENCH_SOLVER_DECISION: accept\n"
    )
    assert runner._decision_from_result(text) is True

    text_reverse = (
        "Initial thought:\n"
        "SWEBENCH_SOLVER_DECISION: accept\n"
        "But on second thought:\n"
        "SWEBENCH_SOLVER_DECISION: reject\n"
    )
    assert runner._decision_from_result(text_reverse) is False


def test_claude_code_runner_requires_explicit_decision_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_claude = tmp_path / "fake-claude"
    _write_fake_claude(fake_claude, marker="missing")
    template = tmp_path / "prompt.md"
    template.write_text("{{problem_statement}}\n", encoding="utf-8")
    output = tmp_path / "attempt_output.json"
    monkeypatch.setenv(runner.PUBLIC_PACKET_ENV, str(_packet(tmp_path)))
    monkeypatch.setenv(runner.ATTEMPT_OUTPUT_ENV, str(output))
    monkeypatch.setenv("SWEBENCH_SOLVER_LITELLM_AUTH_TOKEN", "route-secret")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="SWEBENCH_SOLVER_DECISION"):
        runner.main([
            "--prompt-template",
            str(template),
            "--claude-bin",
            str(fake_claude),
            "--model",
            "claude-3-5-haiku-20241022",
        ])

    assert not output.exists()
