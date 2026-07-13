"""Legacy Claude CLI subprocess edge for dual-agent lead execution.

Provider-neutral lead orchestration lives in ``dual_agent_lead``.  This module
keeps the historical argv and environment contract used by injected fake
subprocess runners and by callers that have not migrated to ``AgentRuntime``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .provider_routing import DEFAULT_ANTHROPIC_MODEL, direct_anthropic_env


CLAUDE_PRIMARY_MODEL = DEFAULT_ANTHROPIC_MODEL
CLAUDE_OPUS_ULTIMATE_MODEL = "opus"
CLAUDE_OPUS_UNDERLYING_MODEL = "claude-opus-4-8"
CLAUDE_OPUS_SAFE_OVERRIDE_MODEL = "claude-opus-4-6"
CLAUDE_CHEAP_MODEL = "haiku"
CLAUDE_OPUS_ULTIMATE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "xhigh"},
}
CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "max"},
}
REPORT_ONLY_EXECUTION_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "LS",
    "Edit",
    "MultiEdit",
    "Write",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(*.venv/bin/python -m pytest*)",
    "Bash(python -m pytest*)",
    "Bash(python3 -m pytest*)",
    "Bash(*.venv/bin/python -m cortex.vela_eval.runner*)",
    "Bash(python -m cortex.vela_eval.runner*)",
    "Bash(python3 -m cortex.vela_eval.runner*)",
    "Bash(curl http://127.0.0.1:5173*)",
    "Bash(curl http://localhost:5173*)",
)
REPORT_ONLY_EXECUTION_PERMISSION_MODE = "dontAsk"

Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_legacy_claude_command(
    *,
    cli_command: str,
    prompt: str,
    model: str,
    budget_usd: float,
    permission_mode: str,
    effort: str | None,
    tools: str,
    allowed_tools: Sequence[str] = (),
) -> list[str]:
    """Build the historical one-shot Claude CLI command."""

    command = [
        cli_command,
        "--no-session-persistence",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-budget-usd",
        _format_budget(budget_usd),
        "--permission-mode",
        permission_mode,
    ]
    if effort:
        command.extend(["--effort", effort])
    command.extend(["--tools", tools])
    if allowed_tools:
        command.extend(["--allowedTools", *allowed_tools])
    return command


def build_legacy_claude_environment(
    *,
    explicit_env: Mapping[str, str],
    requested_model: str,
    gate: str,
) -> dict[str, str]:
    """Build the direct-Anthropic environment for the legacy Claude CLI."""

    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in explicit_env.items()})
    env = direct_anthropic_env(env)
    if uses_adaptive_opus_effort(requested_model):
        # r-2026-06-10: the pinned claude-opus-4-8 route broke headless
        # write permissions. Execution therefore defaults to the CLI's Opus
        # route while planning/review keeps an operator-overridable quality pin.
        pin = _underlying_opus_model_for_gate(gate)
        if pin is None:
            env.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        else:
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = pin
        env["CLAUDE_CODE_EXTRA_BODY"] = json.dumps(_opus_extra_body_for_pin(pin))
    else:
        env.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        env.pop("CLAUDE_CODE_EXTRA_BODY", None)
    return env


def run_legacy_claude(
    command: list[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str],
    timeout_s: int,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Execute the historical subprocess contract."""

    return runner(
        command,
        cwd=str(Path(cwd)),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def uses_adaptive_opus_effort(model: str) -> bool:
    return (
        model == CLAUDE_OPUS_ULTIMATE_MODEL
        or model == CLAUDE_OPUS_UNDERLYING_MODEL
        or model.startswith(f"{CLAUDE_OPUS_UNDERLYING_MODEL}-")
    )


def _underlying_opus_model_for_gate(gate: str) -> str | None:
    if gate == "execution":
        override = _opus_pin_override("CODEX_SUPERVISOR_EXECUTION_OPUS_MODEL")
        return override or None
    override = _opus_pin_override("CODEX_SUPERVISOR_PLANNING_OPUS_MODEL")
    return override or CLAUDE_OPUS_UNDERLYING_MODEL


def _opus_pin_override(env_key: str) -> str:
    value = os.environ.get(env_key, "").strip()
    if value and not value.startswith("claude-opus-"):
        return CLAUDE_OPUS_SAFE_OVERRIDE_MODEL
    return value


def _opus_extra_body_for_pin(pin: str | None) -> dict[str, Any]:
    if pin and pin.startswith(CLAUDE_OPUS_SAFE_OVERRIDE_MODEL):
        return CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY
    return CLAUDE_OPUS_ULTIMATE_EXTRA_BODY


def _format_budget(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)
