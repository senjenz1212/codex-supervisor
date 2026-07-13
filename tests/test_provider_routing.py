from __future__ import annotations

import os

from supervisor.provider_routing import (
    DIRECT_ANTHROPIC_API_KEY_FD_ENV,
    configure_direct_anthropic_process_env,
    direct_anthropic_env,
    is_anthropic_model,
    read_direct_anthropic_api_key_fd,
)


def test_direct_anthropic_key_can_be_read_from_anonymous_fd(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"direct-key\n")
    finally:
        os.close(write_fd)
    monkeypatch.setenv(DIRECT_ANTHROPIC_API_KEY_FD_ENV, str(read_fd))

    assert read_direct_anthropic_api_key_fd() == "direct-key"
    assert DIRECT_ANTHROPIC_API_KEY_FD_ENV not in os.environ


def test_direct_anthropic_key_fd_rejects_invalid_descriptor(monkeypatch) -> None:
    monkeypatch.setenv(DIRECT_ANTHROPIC_API_KEY_FD_ENV, "not-an-fd")

    try:
        read_direct_anthropic_api_key_fd()
    except RuntimeError as exc:
        assert DIRECT_ANTHROPIC_API_KEY_FD_ENV in str(exc)
    else:
        raise AssertionError("invalid key descriptors must fail closed")


def test_direct_anthropic_env_scrubs_proxy_and_oauth_routes() -> None:
    env = direct_anthropic_env(
        {
            "HOME": "/tmp/home",
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "direct-key",
            "ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com",
            "ANTHROPIC_AUTH_TOKEN": "proxy-token",
            "ANTHROPIC_TOKEN": "proxy-token",
            "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.example",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "OPENAI_BASE_URL": "https://uai-litellm.internal.unity.com/v1",
            "OPENAI_API_KEY": "other-provider-secret",
            "GITHUB_TOKEN": "github-secret",
            "ARBITRARY_SECRET": "do-not-forward",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6",
            "CLAUDE_CODE_EXTRA_BODY": '{"thinking":{"type":"adaptive"}}',
        }
    )

    assert env["ANTHROPIC_API_KEY"] == "direct-key"
    assert env["HOME"] == "/tmp/home"
    assert env["PATH"] == "/usr/bin"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_TOKEN" not in env
    assert "ANTHROPIC_VERTEX_BASE_URL" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "OPENAI_BASE_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "ARBITRARY_SECRET" not in env
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-6"
    assert env["CLAUDE_CODE_EXTRA_BODY"] == '{"thinking":{"type":"adaptive"}}'


def test_configure_direct_anthropic_process_env_preserves_other_providers(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-direct-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "proxy-token")
    monkeypatch.setenv("OPENAI_API_KEY", "litellm-key")

    try:
        configure_direct_anthropic_process_env(api_key="direct-key")
        child_env = direct_anthropic_env()

        assert "ANTHROPIC_API_KEY" not in os.environ
        assert "ANTHROPIC_BASE_URL" not in os.environ
        assert "ANTHROPIC_AUTH_TOKEN" not in os.environ
        assert os.environ["OPENAI_API_KEY"] == "litellm-key"
        assert child_env["ANTHROPIC_API_KEY"] == "direct-key"
        assert "OPENAI_API_KEY" not in child_env
    finally:
        configure_direct_anthropic_process_env()


def test_direct_anthropic_env_prefers_explicit_source_key(monkeypatch) -> None:
    try:
        configure_direct_anthropic_process_env(api_key="daemon-key")

        explicit = direct_anthropic_env({"ANTHROPIC_API_KEY": "request-key"})
        ambient = direct_anthropic_env({})

        assert explicit["ANTHROPIC_API_KEY"] == "request-key"
        assert ambient["ANTHROPIC_API_KEY"] == "daemon-key"
    finally:
        configure_direct_anthropic_process_env()


def test_anthropic_model_detection_is_narrow() -> None:
    assert is_anthropic_model("claude-fable-5")
    assert is_anthropic_model("anthropic/claude-fable-5")
    assert is_anthropic_model("fable")
    assert is_anthropic_model("opus")
    assert is_anthropic_model("sonnet")
    assert is_anthropic_model("haiku")
    assert is_anthropic_model("anthropic.claude-opus-4-8-v1:0")
    assert is_anthropic_model("us.anthropic.claude-opus-4-8-v1:0")
    assert is_anthropic_model("bedrock/anthropic.claude-opus-4-8-v1:0")
    assert is_anthropic_model("bedrock/us.anthropic.claude-opus-4-8-v1:0")
    assert is_anthropic_model("vertex_ai/claude-fable-5")
    assert not is_anthropic_model("gpt-5.5")
    assert not is_anthropic_model("gemini-3.1-pro-preview")
    assert not is_anthropic_model("my-claude-compatible-model")
    assert not is_anthropic_model("notclaude")
    assert not is_anthropic_model("claudette")
    assert not is_anthropic_model("")
    assert not is_anthropic_model(None)
