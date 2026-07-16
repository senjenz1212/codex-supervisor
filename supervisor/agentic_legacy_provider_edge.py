"""Explicit legacy Claude subprocess edge for agentic planner/worker tests.

Production orchestration should inject a provider-neutral ``RuntimeTaskRunner``
or ``RuntimeFactory``.  This module keeps the old subprocess contract isolated
for callers and tests that still provide a runner explicitly.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, Callable

from .agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from .provider_routing import direct_anthropic_env
from .redaction import redact
from .runtime_execution import RuntimeExecution


LegacyRunner = Callable[..., subprocess.CompletedProcess[str]]

# Keep this allowlist intentionally small: every added key crosses the child
# credential boundary.
_SAFE_AGENTIC_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TMPDIR",
)


def run_agentic_claude_subprocess(
    runner: LegacyRunner,
    argv: list[str],
    *,
    inherit_env: bool,
    task_env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run the legacy Claude edge without exposing ambient credentials."""
    if inherit_env is not False:
        raise ValueError("agentic Claude subprocesses must not inherit ambient env")
    source = {
        key: os.environ[key]
        for key in _SAFE_AGENTIC_ENV_KEYS
        if key in os.environ
    }
    if "ANTHROPIC_API_KEY" in os.environ:
        source["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
    source.update({
        str(key): str(value)
        for key, value in (task_env or {}).items()
    })
    return runner(
        argv,
        env=direct_anthropic_env(source),
        **kwargs,
    )


def execute_legacy_agent_task(
    task: AgentTask,
    *,
    runner: LegacyRunner,
    command: Sequence[str] = (),
) -> RuntimeExecution:
    """Execute one task through the legacy subprocess edge and normalize it."""
    if task.inherit_env:
        raise ValueError("legacy agentic tasks must disable ambient env inheritance")

    argv = list(command or legacy_claude_argv(task))
    started_at_ms = int(time.time() * 1000)
    run_id = f"legacy-{uuid.uuid4().hex}"
    stdout = ""
    stderr = ""
    returncode: int | None = None
    failure_reason = ""
    error = ""
    try:
        completed = run_agentic_claude_subprocess(
            runner,
            argv,
            inherit_env=False,
            task_env=task.env,
            cwd=str(task.cwd),
            capture_output=True,
            text=True,
            timeout=max(0.001, float(task.timeout_s)),
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        returncode = int(completed.returncode)
        if returncode != 0:
            failure_reason = "subprocess_nonzero"
            error = stderr.strip() or f"legacy subprocess exited with {returncode}"
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_stream(exc.output)
        stderr = _decode_stream(exc.stderr)
        returncode = 124
        failure_reason = "timeout"
        error = f"timeout after {task.timeout_s:g}s"
    except OSError as exc:
        failure_reason = "subprocess_start_failed"
        error = str(exc)

    ended_at_ms = int(time.time() * 1000)
    wrapper = _json_object(stdout)
    if returncode == 0 and not failure_reason and bool(wrapper.get("is_error")):
        failure_reason = str(wrapper.get("subtype") or "result_is_error")
        error = f"legacy result reported is_error with subtype {failure_reason}"
    stderr = redact(stderr)
    error = redact(error)
    output = (
        str(wrapper.get("result"))
        if isinstance(wrapper.get("result"), str)
        else stdout
    )
    session_id = str(
        wrapper.get("session_id")
        or wrapper.get("sessionId")
        or run_id
    )
    status = "completed" if returncode == 0 and not failure_reason else "failed"
    event_kind = "run.completed" if status == "completed" else "run.failed"
    event_payload: dict[str, Any] = {
        "type": event_kind,
        "legacy_provider_edge": True,
    }
    if failure_reason:
        event_payload["reason"] = failure_reason
    if error:
        event_payload["error"] = error
    event = RuntimeEvent(
        kind=event_kind,
        payload=event_payload,
        ts_ms=ended_at_ms,
    )
    runtime = "claude_code"
    handle = AgentRunHandle(
        run_id=run_id,
        task_id=task.task_id,
        runtime=runtime,
        session_id=session_id,
        capabilities={
            "resume": False,
            "cancel": False,
            "stream": False,
            "legacy_provider_edge": True,
        },
    )
    resolved_model = str(
        wrapper.get("resolved_model")
        or wrapper.get("model")
        or task.model
        or "legacy-unresolved"
    )
    cost_usd = _non_negative_float(
        wrapper.get("total_cost_usd")
        if wrapper.get("total_cost_usd") is not None
        else wrapper.get("cost_usd"),
    )
    token_usage = _token_usage(wrapper)
    metadata = {
        "returncode": returncode,
        "stderr": stderr,
        "legacy_provider_edge": True,
        "failure_reason": failure_reason,
        "error": error,
    }
    result_payload = {
        "run_id": run_id,
        "task_id": task.task_id,
        "runtime": runtime,
        "session_id": session_id,
        "status": status,
        "output": output,
        "events": [event.to_dict()],
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "cost_usd": cost_usd,
        "resolved_model": resolved_model,
        "token_usage": token_usage,
        "metadata": metadata,
    }
    result = AgentRunResult(
        run_id=run_id,
        task_id=task.task_id,
        runtime=runtime,
        session_id=session_id,
        status=status,
        output=output,
        events=(event,),
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        cost_usd=cost_usd,
        resolved_model=resolved_model,
        result_hash=_sha256_json(result_payload),
        token_usage=token_usage,
        model_provenance=(
            "legacy_subprocess.stdout"
            if wrapper.get("resolved_model") or wrapper.get("model")
            else "legacy_subprocess.request"
        ),
        cost_provenance=(
            "legacy_subprocess.stdout"
            if wrapper.get("total_cost_usd") is not None
            or wrapper.get("cost_usd") is not None
            else "legacy_subprocess.unreported"
        ),
        token_provenance=(
            "legacy_subprocess.stdout"
            if token_usage
            else "legacy_subprocess.unreported"
        ),
        metadata=metadata,
    )
    return RuntimeExecution(
        handle=handle,
        events=(event,),
        result=result,
    )


def legacy_claude_argv(task: AgentTask) -> tuple[str, ...]:
    """Translate a provider-neutral task at the explicit legacy edge."""
    metadata = task.metadata
    argv = [
        "claude",
        "--no-session-persistence",
        "-p",
        task.instruction,
        "--output-format",
        "json",
        "--model",
        task.model,
    ]
    if metadata.get("max_budget_usd") is not None:
        argv.extend((
            "--max-budget-usd",
            _format_budget(float(metadata["max_budget_usd"])),
        ))
    permission_mode = str(metadata.get("permission_mode") or "plan").strip()
    if permission_mode:
        argv.extend(("--permission-mode", permission_mode))
    allowed_tools = _string_sequence(metadata.get("allowed_tools"))
    if allowed_tools:
        argv.extend(("--tools", ",".join(allowed_tools)))
    disallowed_tools = _string_sequence(metadata.get("disallowed_tools"))
    if disallowed_tools:
        argv.extend(("--disallowedTools", ",".join(disallowed_tools)))
    return tuple(argv)


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _token_usage(wrapper: Mapping[str, Any]) -> dict[str, Any]:
    usage = wrapper.get("usage")
    if isinstance(usage, Mapping):
        return _normalize_usage(usage)
    model_usage = wrapper.get("modelUsage")
    if not isinstance(model_usage, Mapping):
        model_usage = wrapper.get("model_usage")
    if not isinstance(model_usage, Mapping):
        return {}
    aggregate: dict[str, int] = {}
    for value in model_usage.values():
        if not isinstance(value, Mapping):
            continue
        for key, count in _normalize_usage(value).items():
            if isinstance(count, int):
                aggregate[key] = aggregate.get(key, 0) + count
    return _with_token_totals(aggregate)


def _normalize_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "cacheCreationInputTokens": "cache_creation_input_tokens",
        "cacheReadInputTokens": "cache_read_input_tokens",
    }
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in usage.items():
        key = aliases.get(str(raw_key), str(raw_key))
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, (int, float)):
            numeric = float(raw_value)
            if math.isfinite(numeric) and numeric >= 0 and numeric.is_integer():
                normalized[key] = int(numeric)
    return _with_token_totals(normalized)


def _with_token_totals(usage: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(usage)
    if "tokens_in" not in normalized and any(
        key in normalized
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    ):
        normalized["tokens_in"] = sum(
            int(normalized.get(key) or 0)
            for key in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
    if "tokens_out" not in normalized and "output_tokens" in normalized:
        normalized["tokens_out"] = int(normalized["output_tokens"])
    return normalized


def _non_negative_float(value: Any) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(parsed) or parsed < 0:
        return 0.0
    return parsed


def _string_sequence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _decode_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _format_budget(value: float) -> str:
    return f"{max(0.01, float(value)):.2f}"


def _sha256_json(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "LegacyRunner",
    "execute_legacy_agent_task",
    "legacy_claude_argv",
    "run_agentic_claude_subprocess",
]
