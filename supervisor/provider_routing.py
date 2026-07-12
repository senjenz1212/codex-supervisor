"""Provider routing policy shared by Supervisor model boundaries."""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal


DEFAULT_ANTHROPIC_MODEL = "claude-fable-5"
DEFAULT_ANTHROPIC_EFFORT: Literal["medium"] = "medium"
COMPLEX_ANTHROPIC_EFFORT: Literal["high"] = "high"

ANTHROPIC_PROXY_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_TOKEN",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GATEWAY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_VERTEX",
)


def is_anthropic_model(model: str | None) -> bool:
    value = str(model or "").strip().lower()
    if not value:
        return False
    if value in {"claude", "fable", "opus", "sonnet", "haiku"}:
        return True
    if value.startswith("anthropic/"):
        return True
    bare = value.split("/", 1)[1] if "/" in value else value
    return (
        "claude" in bare
        or bare.startswith("anthropic.")
        or ".anthropic." in bare
    )


def direct_anthropic_env(
    source: Mapping[str, str] | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, str]:
    """Return an environment that cannot inherit an Anthropic proxy route."""
    env = dict(os.environ if source is None else source)
    for key in ANTHROPIC_PROXY_ENV_KEYS:
        env.pop(key, None)
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    return env


def configure_direct_anthropic_process_env(*, api_key: str | None = None) -> None:
    """Apply the direct-only Anthropic boundary to the current process."""
    for key in ANTHROPIC_PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
