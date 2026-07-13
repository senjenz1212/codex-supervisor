"""Claude Agent SDK transport adapter for the provider-neutral runtime seam."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent_runtime import RuntimeTransportResult


class MissingClaudeAgentSdk(RuntimeError):
    """Raised when the optional Claude Agent SDK runtime is selected."""


SdkLoader = Callable[[], tuple[Any, Any]]


@dataclass
class _SdkExecution:
    queue: asyncio.Queue[Mapping[str, Any] | None]
    raw_events: list[Mapping[str, Any]]
    outputs: list[str]
    started_at_ms: int
    task: asyncio.Task[int]
    error: str = ""
    cost_usd: float = 0.0
    resolved_model: str = ""
    token_usage: dict[str, Any] | None = None
    model_provenance: str = ""
    cost_provenance: str = ""
    token_provenance: str = ""
    generation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClaudeAgentSdkTransport:
    """Keep all Claude SDK construction behind ``AgentRuntime``."""

    def __init__(self, *, sdk_loader: SdkLoader | None = None) -> None:
        self._sdk_loader = sdk_loader or _load_claude_agent_sdk
        self._executions: dict[str, _SdkExecution] = {}

    async def start(
        self,
        *,
        run_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
        metadata: Mapping[str, Any],
    ) -> str:
        execution = _SdkExecution(
            queue=asyncio.Queue(),
            raw_events=[],
            outputs=[],
            started_at_ms=int(time.time() * 1000),
            task=asyncio.create_task(asyncio.sleep(0, result=0)),
        )
        execution.task = asyncio.create_task(
            self._run_with_timeout(
                execution,
                instruction=_argument_after(argv, "-p"),
                model=_argument_after(argv, "--model"),
                cwd=cwd,
                env=env,
                metadata=metadata,
                resume_session_id=str(
                    metadata.get("resume_session_id") or ""
                ),
                timeout_s=timeout_s,
                deadline=(
                    asyncio.get_running_loop().time()
                    + max(0.001, float(timeout_s))
                ),
            )
        )
        self._executions[run_id] = execution
        return run_id

    async def resume(
        self,
        token: str,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
        metadata: Mapping[str, Any],
    ) -> None:
        execution = self._get(token)
        async with execution.generation_lock:
            if not execution.task.done():
                raise RuntimeError(
                    "cannot resume while the previous runtime generation is active"
                )
            await asyncio.shield(execution.task)
            # Streaming is generation-scoped even though collection retains the
            # cumulative transcript.  A caller may collect without first
            # draining the old queue, so never leave prior events or its
            # terminal sentinel in front of the resumed generation.
            execution.queue = asyncio.Queue()
            execution.task = asyncio.create_task(
                self._run_with_timeout(
                    execution,
                    instruction=_argument_after(argv, "-p"),
                    model=_argument_after(argv, "--model"),
                    cwd=cwd,
                    env=env,
                    metadata=metadata,
                    resume_session_id=_argument_after(argv, "--resume"),
                    timeout_s=timeout_s,
                    deadline=(
                        asyncio.get_running_loop().time()
                        + max(0.001, float(timeout_s))
                    ),
                )
            )

    async def cancel(self, token: str) -> None:
        execution = self._get(token)
        if execution.task.done():
            return
        execution.task.cancel()
        try:
            await execution.task
        except asyncio.CancelledError:
            pass

    async def stream(self, token: str) -> AsyncIterator[Mapping[str, Any]]:
        execution = self._get(token)
        while True:
            event = await execution.queue.get()
            if event is None:
                return
            yield event

    async def collect(self, token: str) -> RuntimeTransportResult:
        execution = self._get(token)
        try:
            returncode = await asyncio.shield(execution.task)
        except asyncio.CancelledError:
            # Shielding leaves the execution active when collect() itself is
            # cancelled.  A terminal cancelled execution remains collectable
            # as the compatibility return code below.
            if not execution.task.done():
                await _cancel_task_and_wait(execution.task)
                raise
            returncode = 130
        return RuntimeTransportResult(
            returncode=returncode,
            stdout="\n".join(execution.outputs),
            stderr=execution.error,
            raw_events=tuple(execution.raw_events),
            started_at_ms=execution.started_at_ms,
            ended_at_ms=int(time.time() * 1000),
            cost_usd=execution.cost_usd,
            resolved_model=execution.resolved_model,
            token_usage=dict(execution.token_usage or {}),
            model_provenance=execution.model_provenance,
            cost_provenance=execution.cost_provenance,
            token_provenance=execution.token_provenance,
        )

    async def _run_with_timeout(
        self,
        execution: _SdkExecution,
        *,
        instruction: str,
        model: str,
        cwd: Path,
        env: dict[str, str],
        metadata: Mapping[str, Any],
        resume_session_id: str,
        timeout_s: float,
        deadline: float,
    ) -> int:
        started = {"type": "run.started"}
        execution.raw_events.append(started)
        await execution.queue.put(started)
        try:
            return await asyncio.wait_for(
                self._execute(
                    execution,
                    instruction=instruction,
                    model=model,
                    cwd=cwd,
                    env=env,
                    metadata=metadata,
                    resume_session_id=resume_session_id,
                ),
                timeout=max(
                    0.0,
                    deadline - asyncio.get_running_loop().time(),
                ),
            )
        except asyncio.TimeoutError:
            execution.error = (
                "TimeoutError: Claude Agent SDK execution exceeded "
                f"timeout_s={timeout_s}"
            )
            failed = {
                "type": "run.failed",
                "error": execution.error,
                "reason": "timeout",
                "timeout_s": timeout_s,
            }
            execution.raw_events.append(failed)
            await execution.queue.put(failed)
            return 124
        except asyncio.CancelledError:
            cancelled = {"type": "run.cancelled"}
            execution.raw_events.append(cancelled)
            await execution.queue.put(cancelled)
            raise
        except Exception as exc:
            execution.error = f"{type(exc).__name__}: {exc}"
            failed = {"type": "run.failed", "error": execution.error}
            execution.raw_events.append(failed)
            await execution.queue.put(failed)
            return 1
        finally:
            await execution.queue.put(None)

    async def _execute(
        self,
        execution: _SdkExecution,
        *,
        instruction: str,
        model: str,
        cwd: Path,
        env: dict[str, str],
        metadata: Mapping[str, Any],
        resume_session_id: str,
    ) -> int:
        client_cls, options_cls = self._sdk_loader()
        options_kwargs: dict[str, Any] = {
            "system_prompt": str(metadata.get("system_prompt") or ""),
            "model": model,
            "max_turns": int(metadata.get("max_turns") or 12),
            "mcp_servers": dict(metadata.get("mcp_servers") or {}),
            "allowed_tools": list(metadata.get("allowed_tools") or []),
            "disallowed_tools": list(metadata.get("disallowed_tools") or []),
            "permission_mode": str(
                metadata.get("permission_mode") or "dontAsk"
            ),
            "effort": str(metadata.get("effort") or "medium"),
            "cwd": cwd,
            "env": env,
        }
        if metadata.get("max_budget_usd") is not None:
            options_kwargs["max_budget_usd"] = float(
                metadata["max_budget_usd"]
            )
        if resume_session_id:
            options_kwargs["resume"] = resume_session_id
        options = options_cls(**options_kwargs)
        async with client_cls(options=options) as client:
            await client.query(instruction)
            async for message in client.receive_response():
                _observe_sdk_provenance(execution, message)
                session_id = getattr(message, "session_id", None)
                for block in getattr(message, "content", ()) or ():
                    text = getattr(block, "text", None)
                    if not text:
                        continue
                    execution.outputs.append(str(text))
                    event = {
                        "type": "agent_message",
                        "message": str(text),
                    }
                    if isinstance(session_id, str) and session_id:
                        event["session_id"] = session_id
                    execution.raw_events.append(event)
                    await execution.queue.put(event)
        completed = {"type": "run.completed"}
        execution.raw_events.append(completed)
        await execution.queue.put(completed)
        return 0

    def _get(self, token: str) -> _SdkExecution:
        try:
            return self._executions[token]
        except KeyError as exc:
            raise KeyError(f"unknown Claude SDK runtime token: {token}") from exc


async def _cancel_task_and_wait(task: asyncio.Task[int]) -> None:
    """Cancel an SDK execution without letting caller cancellation race cleanup."""

    if not task.done():
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            return
    if task.cancelled():
        return
    try:
        task.result()
    except BaseException:
        return


def _argument_after(argv: tuple[str, ...], flag: str) -> str:
    try:
        return str(argv[argv.index(flag) + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"runtime command missing {flag}") from exc


def _load_claude_agent_sdk() -> tuple[Any, Any]:
    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            ClaudeAgentOptions,
            ClaudeSDKClient,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "claude_agent_sdk":
            raise MissingClaudeAgentSdk(
                "claude_agent_sdk is optional; install codex-supervisor[agent]"
            ) from exc
        raise
    return ClaudeSDKClient, ClaudeAgentOptions


def _observe_sdk_provenance(
    execution: _SdkExecution,
    message: Any,
) -> None:
    observed_model = str(getattr(message, "model", None) or "").strip()
    if observed_model:
        execution.resolved_model = observed_model
        execution.model_provenance = "claude_agent_sdk.message.model"

    model_usage = getattr(message, "model_usage", None)
    if isinstance(model_usage, Mapping) and model_usage:
        model_names = [
            str(name).strip()
            for name in model_usage
            if str(name).strip()
        ]
        if len(model_names) == 1 and not execution.resolved_model:
            execution.resolved_model = model_names[0]
            execution.model_provenance = (
                "claude_agent_sdk.result.model_usage"
            )
        if execution.token_usage is None:
            aggregate: dict[str, int] = {}
            total_cost = 0.0
            observed_cost = False
            for raw_usage in model_usage.values():
                if not isinstance(raw_usage, Mapping):
                    continue
                normalized = _normalize_sdk_usage(
                    {
                        "input_tokens": raw_usage.get("inputTokens", 0),
                        "cache_creation_input_tokens": raw_usage.get(
                            "cacheCreationInputTokens",
                            0,
                        ),
                        "cache_read_input_tokens": raw_usage.get(
                            "cacheReadInputTokens",
                            0,
                        ),
                        "output_tokens": raw_usage.get("outputTokens", 0),
                    }
                )
                for key, value in normalized.items():
                    aggregate[key] = aggregate.get(key, 0) + int(value)
                raw_cost = raw_usage.get("costUSD")
                if raw_cost is not None:
                    total_cost += float(raw_cost)
                    observed_cost = True
            if aggregate:
                execution.token_usage = aggregate
                execution.token_provenance = (
                    "claude_agent_sdk.result.model_usage"
                )
            if observed_cost and not execution.cost_provenance:
                execution.cost_usd = total_cost
                execution.cost_provenance = (
                    "claude_agent_sdk.result.model_usage"
                )

    total_cost = getattr(message, "total_cost_usd", None)
    if total_cost is not None:
        execution.cost_usd = float(total_cost)
        execution.cost_provenance = (
            "claude_agent_sdk.result.total_cost_usd"
        )

    usage = getattr(message, "usage", None)
    if isinstance(usage, Mapping):
        execution.token_usage = _normalize_sdk_usage(usage)
        execution.token_provenance = "claude_agent_sdk.result.usage"


def _normalize_sdk_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(usage)
    tokens_in = sum(
        int(normalized.get(key) or 0)
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    if any(
        key in normalized
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    ):
        normalized["tokens_in"] = tokens_in
    if "output_tokens" in normalized:
        normalized["tokens_out"] = int(normalized["output_tokens"])
    return normalized
