"""Provider routing policy shared by Supervisor model boundaries."""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal


DEFAULT_ANTHROPIC_MODEL = "claude-fable-5"
DEFAULT_ANTHROPIC_EFFORT: Literal["medium"] = "medium"
COMPLEX_ANTHROPIC_EFFORT: Literal["high"] = "high"
DIRECT_ANTHROPIC_API_KEY_FD_ENV = "CODEX_SUPERVISOR_ANTHROPIC_API_KEY_FD"

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

_direct_anthropic_api_key: str | None = None


def read_direct_anthropic_api_key_fd() -> str:
    """Consume a direct Anthropic key from an inherited anonymous pipe."""
    raw_fd = os.environ.pop(DIRECT_ANTHROPIC_API_KEY_FD_ENV, "").strip()
    if not raw_fd:
        return ""
    try:
        fd = int(raw_fd)
    except ValueError as exc:
        raise RuntimeError(
            f"{DIRECT_ANTHROPIC_API_KEY_FD_ENV} must name an open file descriptor"
        ) from exc
    try:
        with os.fdopen(fd, encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError as exc:
        raise RuntimeError(
            f"could not read {DIRECT_ANTHROPIC_API_KEY_FD_ENV}"
        ) from exc


def is_anthropic_model(model: str | None) -> bool:
    value = str(model or "").strip().lower()
    if not value:
        return False
    if value in {"claude", "fable", "opus", "sonnet", "haiku"}:
        return True
    if value.startswith("anthropic/"):
        return True
    bare = value.rsplit("/", 1)[-1]
    return (
        bare.startswith(("claude-", "claude_", "claude."))
        or bare.startswith(
            ("anthropic.claude-", "anthropic.claude_", "anthropic.claude.")
        )
        or ".anthropic.claude-" in bare
        or ".anthropic.claude_" in bare
        or ".anthropic.claude." in bare
    )


def direct_anthropic_env(
    source: Mapping[str, str] | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, str]:
    """Return an environment that cannot inherit an Anthropic proxy route."""
    env = dict(os.environ if source is None else source)
    source_api_key = env.pop("ANTHROPIC_API_KEY", None)
    for key in ANTHROPIC_PROXY_ENV_KEYS:
        env.pop(key, None)
    selected_api_key = (
        api_key
        if api_key is not None
        else source_api_key or _direct_anthropic_api_key
    )
    if selected_api_key:
        env["ANTHROPIC_API_KEY"] = selected_api_key
    return env


def configure_direct_anthropic_process_env(*, api_key: str | None = None) -> None:
    """Capture the direct key and remove Anthropic routing from the daemon env."""
    global _direct_anthropic_api_key

    _direct_anthropic_api_key = api_key or None
    os.environ.pop("ANTHROPIC_API_KEY", None)
    for key in ANTHROPIC_PROXY_ENV_KEYS:
        os.environ.pop(key, None)
