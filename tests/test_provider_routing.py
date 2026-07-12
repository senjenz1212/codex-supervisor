from __future__ import annotations

import os

from supervisor.provider_routing import (
    configure_direct_anthropic_process_env,
    direct_anthropic_env,
    is_anthropic_model,
)


def test_direct_anthropic_env_scrubs_proxy_and_oauth_routes() -> None:
    env = direct_anthropic_env(
        {
            "ANTHROPIC_API_KEY": "direct-key",
            "ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com",
            "ANTHROPIC_AUTH_TOKEN": "proxy-token",
            "ANTHROPIC_TOKEN": "proxy-token",
            "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.example",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "OPENAI_BASE_URL": "https://uai-litellm.internal.unity.com/v1",
        }
    )

    assert env["ANTHROPIC_API_KEY"] == "direct-key"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_TOKEN" not in env
    assert "ANTHROPIC_VERTEX_BASE_URL" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert env["OPENAI_BASE_URL"] == "https://uai-litellm.internal.unity.com/v1"


def test_configure_direct_anthropic_process_env_preserves_other_providers(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "proxy-token")
    monkeypatch.setenv("OPENAI_API_KEY", "litellm-key")

    configure_direct_anthropic_process_env(api_key="direct-key")

    assert os.environ["ANTHROPIC_API_KEY"] == "direct-key"
    assert "ANTHROPIC_BASE_URL" not in os.environ
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ
    assert os.environ["OPENAI_API_KEY"] == "litellm-key"


def test_anthropic_model_detection_is_narrow() -> None:
    assert is_anthropic_model("claude-fable-5")
    assert is_anthropic_model("anthropic/claude-fable-5")
    assert is_anthropic_model("fable")
    assert is_anthropic_model("opus")
    assert is_anthropic_model("sonnet")
    assert is_anthropic_model("haiku")
    assert not is_anthropic_model("gpt-5.5")
    assert not is_anthropic_model("gemini-3.1-pro-preview")
