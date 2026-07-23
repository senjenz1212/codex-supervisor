"""Provider routing policy shared by Supervisor model boundaries."""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal


DEFAULT_ANTHROPIC_MODEL = "claude-fable-5"
DEFAULT_ANTHROPIC_EFFORT: Literal["medium"] = "medium"
COMPLEX_ANTHROPIC_EFFORT: Literal["high"] = "high"
XHIGH_ANTHROPIC_EFFORT: Literal["xhigh"] = "xhigh"
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

DIRECT_ANTHROPIC_SAFE_CONTROL_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_EXTRA_BODY",
)

ANTHROPIC_OPERATOR_CONTROL_ENV_KEYS: tuple[str, ...] = (
    "CODEX_SUPERVISOR_EXECUTION_OPUS_MODEL",
    "CODEX_SUPERVISOR_PLANNING_OPUS_MODEL",
)

# Child processes receive only execution essentials plus the one provider key
# they are authorized to use.  In particular, credentials for OpenAI, GitHub,
# cloud providers, package registries, and arbitrary supervisor integrations
# must never cross into a Claude child merely because they exist in the daemon.
DIRECT_ANTHROPIC_CHILD_ENV_KEYS: tuple[str, ...] = (
    "HOME",
    "PATH",
    "SHELL",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "COLORTERM",
    "NO_COLOR",
    "FORCE_COLOR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "CLAUDE_CONFIG_DIR",
    "SUPERVISOR_TARGET_KIND",
    "SUPERVISOR_MEMORY_ROOT",
    "SUPERVISOR_LESSON_ROOT",
    *DIRECT_ANTHROPIC_SAFE_CONTROL_ENV_KEYS,
)

# Non-secret supervisor launch attribution and per-run isolation namespaces
# layered by callers (workflow dispatch, arm isolation) stay with the child.
DIRECT_ANTHROPIC_CHILD_ENV_PREFIXES: tuple[str, ...] = (
    "SUPERVISOR_LAUNCH_",
    "SUPERVISOR_WORKFLOW_",
    "XDG_",
)

CLAUDE_OPUS_ULTIMATE_MODEL = "opus"
CLAUDE_OPUS_UNDERLYING_MODEL = "claude-opus-4-8"
CLAUDE_OPUS_SAFE_OVERRIDE_MODEL = "claude-opus-4-6"
CLAUDE_OPUS_ULTIMATE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "xhigh"},
}
CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "max"},
}

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
    """Return a least-privilege environment for a direct Anthropic child."""
    source_env = dict(os.environ if source is None else source)
    source_api_key = source_env.get("ANTHROPIC_API_KEY")
    env = {
        key: value
        for key, value in source_env.items()
        if value
        and (
            key in DIRECT_ANTHROPIC_CHILD_ENV_KEYS
            or key.startswith(DIRECT_ANTHROPIC_CHILD_ENV_PREFIXES)
        )
    }
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


def uses_adaptive_opus_effort(model: str) -> bool:
    return (
        model == CLAUDE_OPUS_ULTIMATE_MODEL
        or model == CLAUDE_OPUS_UNDERLYING_MODEL
        or model.startswith(f"{CLAUDE_OPUS_UNDERLYING_MODEL}-")
    )


def underlying_opus_model_for_gate(
    source: Mapping[str, str],
    gate: str,
) -> str | None:
    """Resolve the Opus pin for one gate from operator control variables.

    This is the single source of the planning-versus-execution pin policy;
    provider edges must not fork their own copies.
    """
    if gate == "execution":
        override = _opus_pin_override(
            source.get("CODEX_SUPERVISOR_EXECUTION_OPUS_MODEL")
        )
        return override or None
    override = _opus_pin_override(
        source.get("CODEX_SUPERVISOR_PLANNING_OPUS_MODEL")
    )
    return override or CLAUDE_OPUS_UNDERLYING_MODEL


def _opus_pin_override(value: str | None) -> str:
    selected = str(value or "").strip()
    if selected and not selected.startswith("claude-opus-"):
        return CLAUDE_OPUS_SAFE_OVERRIDE_MODEL
    return selected


def opus_extra_body_for_pin(pin: str | None) -> dict[str, object]:
    if pin and pin.startswith(CLAUDE_OPUS_SAFE_OVERRIDE_MODEL):
        return CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY
    return CLAUDE_OPUS_ULTIMATE_EXTRA_BODY
