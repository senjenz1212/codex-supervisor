"""Provider-neutral lifecycle seam for coding-agent runtimes.

``TargetAgentAdapter`` remains the observation/steering seam.  This module owns
the separate start/resume/cancel/stream/collect lifecycle used to execute an
agent.  Core experiment code can therefore operate on one stable run/result
shape without importing a provider SDK.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import sys
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import psutil

from .process_containment import (
    containment_environment,
    new_containment_id,
    terminate_containment,
)
RUN_RESULT_SCHEMA_VERSION = "supervisor-agent-run-result/v1"
_MACOS_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_WORKSPACE_ONLY_ISOLATION_MODE = "workspace_only"
_PROCESS_ENV_KEYS = frozenset({
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
})
_SUPERVISOR_LAUNCH_ENV_KEYS = frozenset({
    "SUPERVISOR_LAUNCH_ID",
    "SUPERVISOR_LAUNCH_NONCE",
    "SUPERVISOR_WORKFLOW_RUN_ID",
    "SUPERVISOR_WORKFLOW_TASK_ID",
    "SUPERVISOR_TARGET_KIND",
})
_CLAUDE_ENV_KEYS = frozenset({
    *_PROCESS_ENV_KEYS,
    *_SUPERVISOR_LAUNCH_ENV_KEYS,
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_EXTRA_BODY",
    "CLAUDE_CONFIG_DIR",
})
_CODEX_ENV_KEYS = frozenset({
    *_PROCESS_ENV_KEYS,
    *_SUPERVISOR_LAUNCH_ENV_KEYS,
    "CODEX_HOME",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENAI_PROJECT_ID",
})
_CLAUDE_OPUS_ULTIMATE_MODEL = "opus"
_CLAUDE_OPUS_UNDERLYING_MODEL = "claude-opus-4-8"
_CLAUDE_OPUS_SAFE_OVERRIDE_MODEL = "claude-opus-4-6"
_CLAUDE_OPUS_ULTIMATE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "xhigh"},
}
_CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY = {
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "max"},
}


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    instruction: str
    cwd: Path
    model: str
    timeout_s: float = 900
    env: Mapping[str, str] = field(default_factory=dict)
    inherit_env: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunHandle:
    run_id: str
    task_id: str
    runtime: str
    session_id: str
    capabilities: Mapping[str, bool]


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    payload: Mapping[str, Any]
    ts_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": dict(self.payload),
            "ts_ms": self.ts_ms,
        }


@dataclass(frozen=True)
class RuntimeTransportResult:
    returncode: int
    stdout: str
    stderr: str
    raw_events: tuple[Mapping[str, Any], ...]
    started_at_ms: int
    ended_at_ms: int
    cost_usd: float = 0.0
    resolved_model: str = ""
    token_usage: Mapping[str, Any] = field(default_factory=dict)
    model_provenance: str = ""
    cost_provenance: str = ""
    token_provenance: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _FilesystemIsolationPolicy:
    mode: str
    workspace: Path
    deny_paths: tuple[Path, ...]
    runtime_read_paths: tuple[Path, ...]
    network_policy: str


class RuntimeProvenanceError(ValueError):
    """Transport completed without experiment-grade model/usage evidence."""

    def __init__(
        self,
        message: str,
        *,
        result: RuntimeTransportResult,
    ) -> None:
        super().__init__(message)
        self.attempts = 1
        self.cost_usd = float(result.cost_usd)
        self.latency_ms = max(0, result.ended_at_ms - result.started_at_ms)
        self.token_usage = dict(result.token_usage)
        self.attempt_records = (
            {
                "attempt_index": 0,
                "status": "failed",
                "cost_usd": self.cost_usd,
                "token_usage": dict(self.token_usage),
                "latency_ms": self.latency_ms,
                "error": message,
            },
        )
        self.failure_classification = "runtime_provenance_failure"


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    task_id: str
    runtime: str
    session_id: str
    status: str
    output: str
    events: tuple[RuntimeEvent, ...]
    started_at_ms: int
    ended_at_ms: int
    cost_usd: float
    resolved_model: str
    result_hash: str
    token_usage: Mapping[str, Any] = field(default_factory=dict)
    model_provenance: str = ""
    cost_provenance: str = ""
    token_provenance: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = RUN_RESULT_SCHEMA_VERSION

    @property
    def duration_ms(self) -> int:
        return max(0, self.ended_at_ms - self.started_at_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "runtime": self.runtime,
            "session_id": self.session_id,
            "status": self.status,
            "output": self.output,
            "events": [event.to_dict() for event in self.events],
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "duration_ms": self.duration_ms,
            "cost_usd": self.cost_usd,
            "token_usage": dict(self.token_usage),
            "resolved_model": self.resolved_model,
            "model_provenance": self.model_provenance,
            "cost_provenance": self.cost_provenance,
            "token_provenance": self.token_provenance,
            "result_hash": self.result_hash,
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class AgentRuntime(Protocol):
    kind: str

    def runtime_manifest(self, task: AgentTask) -> Mapping[str, Any]:
        ...

    async def start(self, task: AgentTask) -> AgentRunHandle:
        ...

    async def resume(self, handle: AgentRunHandle, instruction: str) -> None:
        ...

    async def cancel(self, handle: AgentRunHandle) -> None:
        ...

    def stream(self, handle: AgentRunHandle) -> AsyncIterator[RuntimeEvent]:
        ...

    async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
        ...


@runtime_checkable
class RuntimeTransport(Protocol):
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
        ...

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
        ...

    async def cancel(self, token: str) -> None:
        ...

    def stream(self, token: str) -> AsyncIterator[Mapping[str, Any]]:
        ...

    async def collect(self, token: str) -> RuntimeTransportResult:
        ...


class CommandAgentRuntime:
    """Deep command-runtime module shared by concrete provider adapters."""

    kind = "command"
    capabilities: Mapping[str, bool] = {
        "resume": False,
        "cancel": True,
        "stream": True,
        "cost_reporting": False,
    }

    def __init__(
        self,
        *,
        transport: RuntimeTransport | None = None,
        binary: str,
    ) -> None:
        self._transport = transport or SubprocessRuntimeTransport()
        self._binary = binary
        self._tokens: dict[str, str] = {}
        self._tasks: dict[str, AgentTask] = {}
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._generation_event_offsets: dict[str, int] = {}
        self._session_ids: dict[str, str] = {}

    def runtime_manifest(self, task: AgentTask) -> Mapping[str, Any]:
        binary_manifest = _binary_identity_manifest(self._binary)
        transport_manifest = _transport_identity_manifest(self._transport)
        route_manifest = self._route_identity_manifest(task)
        tools = task.metadata.get("tools")
        manifest = {
            "schema_version": "supervisor-agent-runtime-manifest/v1",
            "kind": self.kind,
            "implementation": (
                f"{type(self).__module__}.{type(self).__qualname__}"
            ),
            "provider_route": route_manifest,
            "binary": binary_manifest,
            "transport": transport_manifest,
            "tools": tools,
            "complete": bool(
                route_manifest.get("complete")
                and binary_manifest.get("complete")
                and transport_manifest.get("complete")
                and tools not in (None, "")
            ),
        }
        return {
            **manifest,
            "manifest_sha256": _sha256_json(manifest),
        }

    def _route_identity_manifest(
        self,
        task: AgentTask,
    ) -> Mapping[str, Any]:
        return {
            "provider": "",
            "route_kind": self.kind,
            "endpoint": "",
            "model_request": task.model,
            "complete": False,
        }

    async def start(self, task: AgentTask) -> AgentRunHandle:
        cwd = Path(task.cwd).resolve()
        isolation = _filesystem_isolation_policy(task.metadata, cwd=cwd)
        supports_isolation = _transport_supports_filesystem_isolation(
            self._transport,
            _WORKSPACE_ONLY_ISOLATION_MODE,
        )
        if isolation is not None and not supports_isolation:
            raise RuntimeError(
                "runtime transport cannot enforce filesystem isolation mode "
                f"{isolation.mode}"
            )
        run_id = str(uuid.uuid4())
        token = await self._transport.start(
            run_id=run_id,
            argv=self._start_argv(task),
            cwd=cwd,
            env=self._runtime_env(task),
            timeout_s=max(0.001, float(task.timeout_s)),
            metadata=dict(task.metadata),
        )
        self._tokens[run_id] = token
        self._tasks[run_id] = task
        self._events[run_id] = []
        self._generation_event_offsets[run_id] = 0
        self._session_ids[run_id] = run_id
        transport_capabilities: Mapping[str, bool] = {}
        capability_provider = getattr(
            self._transport,
            "runtime_capabilities",
            None,
        )
        if callable(capability_provider):
            provided = capability_provider()
            if not isinstance(provided, Mapping):
                raise ValueError(
                    "runtime transport capabilities must be a mapping"
                )
            transport_capabilities = {
                str(key): bool(value)
                for key, value in provided.items()
            }
        return AgentRunHandle(
            run_id=run_id,
            task_id=task.task_id,
            runtime=self.kind,
            session_id=run_id,
            capabilities={
                **dict(self.capabilities),
                **transport_capabilities,
                "filesystem_isolation": supports_isolation,
            },
        )

    async def resume(self, handle: AgentRunHandle, instruction: str) -> None:
        task = self._task_for(handle)
        token = self._token_for(handle)
        active_probe = getattr(self._transport, "is_active", None)
        if callable(active_probe) and bool(active_probe(token)):
            raise RuntimeError(
                "cannot resume while the previous runtime generation is active"
            )
        await self._synchronize_completed_generation(handle)
        session_id = self._session_ids.get(handle.run_id, handle.session_id)
        await self._transport.resume(
            token,
            argv=self._resume_argv(task, session_id=session_id, instruction=instruction),
            cwd=Path(task.cwd).resolve(),
            env=self._runtime_env(task),
            timeout_s=max(0.001, float(task.timeout_s)),
            metadata=dict(task.metadata),
        )
        self._generation_event_offsets[handle.run_id] = len(
            self._events[handle.run_id]
        )

    async def cancel(self, handle: AgentRunHandle) -> None:
        await self._transport.cancel(self._token_for(handle))

    async def stream(self, handle: AgentRunHandle) -> AsyncIterator[RuntimeEvent]:
        token = self._token_for(handle)
        async for raw in self._transport.stream(token):
            event = normalize_runtime_event(raw)
            self._events[handle.run_id].append(event)
            session_id = _session_id(raw)
            if session_id:
                self._session_ids[handle.run_id] = session_id
            yield event

    async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
        task = self._task_for(handle)
        transport_result = await self._transport.collect(self._token_for(handle))
        self._merge_transport_events(handle, transport_result)
        events = self._events[handle.run_id]
        session_id = self._session_ids.get(handle.run_id, handle.session_id)
        generation_start = self._generation_event_offsets.get(handle.run_id, 0)
        status = _terminal_status(
            events[generation_start:],
            transport_result.returncode,
        )
        output = _result_output(transport_result, events)
        (
            resolved_model,
            cost_usd,
            token_usage,
            model_provenance,
            cost_provenance,
            token_provenance,
        ) = _validated_transport_provenance(task, transport_result)
        result_payload = {
            "run_id": handle.run_id,
            "task_id": task.task_id,
            "runtime": self.kind,
            "session_id": session_id,
            "status": status,
            "output": output,
            "events": [event.to_dict() for event in events],
            "started_at_ms": transport_result.started_at_ms,
            "ended_at_ms": transport_result.ended_at_ms,
            "cost_usd": cost_usd,
            "token_usage": token_usage,
            "resolved_model": resolved_model,
            "model_provenance": model_provenance,
            "cost_provenance": cost_provenance,
            "token_provenance": token_provenance,
            "metadata": {
                **dict(task.metadata.get("result_metadata") or {}),
                **dict(transport_result.metadata),
                "returncode": transport_result.returncode,
                "stderr": transport_result.stderr,
            },
        }
        return AgentRunResult(
            run_id=handle.run_id,
            task_id=task.task_id,
            runtime=self.kind,
            session_id=session_id,
            status=status,
            output=output,
            events=tuple(events),
            started_at_ms=transport_result.started_at_ms,
            ended_at_ms=transport_result.ended_at_ms,
            cost_usd=cost_usd,
            resolved_model=resolved_model,
            result_hash=_sha256_json(result_payload),
            token_usage=token_usage,
            model_provenance=model_provenance,
            cost_provenance=cost_provenance,
            token_provenance=token_provenance,
            metadata=result_payload["metadata"],
        )

    async def _synchronize_completed_generation(
        self,
        handle: AgentRunHandle,
    ) -> None:
        result = await self._transport.collect(self._token_for(handle))
        self._merge_transport_events(handle, result)

    def _merge_transport_events(
        self,
        handle: AgentRunHandle,
        transport_result: RuntimeTransportResult,
    ) -> None:
        events = self._events[handle.run_id]
        # Streaming consumes a prefix of raw transport events. Merge by
        # position rather than equality so legitimately repeated events remain
        # intact.
        streamed_count = min(len(events), len(transport_result.raw_events))
        events.extend(
            normalize_runtime_event(raw)
            for raw in transport_result.raw_events[streamed_count:]
        )
        session_id = self._session_ids.get(handle.run_id, handle.session_id)
        for raw in transport_result.raw_events:
            discovered = _session_id(raw)
            if discovered:
                session_id = discovered
        self._session_ids[handle.run_id] = session_id

    def _task_for(self, handle: AgentRunHandle) -> AgentTask:
        try:
            return self._tasks[handle.run_id]
        except KeyError as exc:
            raise KeyError(f"unknown runtime handle: {handle.run_id}") from exc

    def _token_for(self, handle: AgentRunHandle) -> str:
        try:
            return self._tokens[handle.run_id]
        except KeyError as exc:
            raise KeyError(f"unknown runtime handle: {handle.run_id}") from exc

    def _runtime_env(self, task: AgentTask) -> dict[str, str]:
        inherited = (
            dict(os.environ)
            if task.inherit_env
            else {
                key: value
                for key, value in os.environ.items()
                if key in _PROCESS_ENV_KEYS
            }
        )
        return {
            **inherited,
            **{str(k): str(v) for k, v in task.env.items()},
        }

    def _start_argv(self, task: AgentTask) -> tuple[str, ...]:
        raise NotImplementedError

    def preview_start_argv(self, task: AgentTask) -> tuple[str, ...]:
        """Return the provider-edge launch command without starting a run."""

        return self._start_argv(task)

    def _resume_argv(
        self,
        task: AgentTask,
        *,
        session_id: str,
        instruction: str,
    ) -> tuple[str, ...]:
        raise NotImplementedError


def _allowlisted_environment(
    source: Mapping[str, str],
    *,
    allowed_keys: frozenset[str],
) -> dict[str, str]:
    """Copy only runtime necessities and credentials for one provider edge."""

    return {
        str(key): str(value)
        for key, value in source.items()
        if key in allowed_keys and str(value)
    }


def _direct_anthropic_runtime_env(
    source: Mapping[str, str],
    *,
    requested_model: str,
    lead_gate: str,
) -> dict[str, str]:
    """Build the least-privilege Claude child environment.

    Operator-only routing controls are consumed here and never forwarded. The
    resulting mapping is also safe when the caller accidentally supplied a
    complete daemon environment.
    """

    routed = {str(key): str(value) for key, value in source.items()}
    if _uses_adaptive_opus_effort(requested_model) and lead_gate:
        pin = _underlying_opus_model_for_gate(routed, lead_gate)
        if pin is None:
            routed.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        else:
            routed["ANTHROPIC_DEFAULT_OPUS_MODEL"] = pin
        routed["CLAUDE_CODE_EXTRA_BODY"] = json.dumps(
            _opus_extra_body_for_pin(pin),
            sort_keys=True,
            separators=(",", ":"),
        )
    elif not _uses_adaptive_opus_effort(requested_model):
        routed.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        routed.pop("CLAUDE_CODE_EXTRA_BODY", None)
    return _allowlisted_environment(
        routed,
        allowed_keys=_CLAUDE_ENV_KEYS,
    )


def _uses_adaptive_opus_effort(model: str) -> bool:
    return (
        model == _CLAUDE_OPUS_ULTIMATE_MODEL
        or model == _CLAUDE_OPUS_UNDERLYING_MODEL
        or model.startswith(f"{_CLAUDE_OPUS_UNDERLYING_MODEL}-")
    )


def _underlying_opus_model_for_gate(
    source: Mapping[str, str],
    gate: str,
) -> str | None:
    if gate == "execution":
        override = _opus_pin_override(
            source.get("CODEX_SUPERVISOR_EXECUTION_OPUS_MODEL")
        )
        return override or None
    override = _opus_pin_override(
        source.get("CODEX_SUPERVISOR_PLANNING_OPUS_MODEL")
    )
    return override or _CLAUDE_OPUS_UNDERLYING_MODEL


def _opus_pin_override(value: str | None) -> str:
    selected = str(value or "").strip()
    if selected and not selected.startswith("claude-opus-"):
        return _CLAUDE_OPUS_SAFE_OVERRIDE_MODEL
    return selected


def _opus_extra_body_for_pin(pin: str | None) -> dict[str, object]:
    if pin and pin.startswith(_CLAUDE_OPUS_SAFE_OVERRIDE_MODEL):
        return _CLAUDE_OPUS_SAFE_OVERRIDE_EXTRA_BODY
    return _CLAUDE_OPUS_ULTIMATE_EXTRA_BODY


class ClaudeCodeRuntime(CommandAgentRuntime):
    kind = "claude_code"
    capabilities = {
        "resume": True,
        "cancel": True,
        "stream": True,
        "cost_reporting": True,
        "subagents": True,
        "images": True,
    }

    def __init__(
        self,
        *,
        transport: RuntimeTransport | None = None,
        binary: str = "claude",
    ) -> None:
        super().__init__(transport=transport, binary=binary)

    def _runtime_env(self, task: AgentTask) -> dict[str, str]:
        candidate = super()._runtime_env(task)
        lead_invocation = task.metadata.get("lead_invocation")
        lead_gate = (
            str(lead_invocation.get("gate") or "")
            if isinstance(lead_invocation, Mapping)
            else ""
        )
        return _direct_anthropic_runtime_env(
            candidate,
            requested_model=str(task.model),
            lead_gate=lead_gate,
        )

    def _route_identity_manifest(
        self,
        task: AgentTask,
    ) -> Mapping[str, Any]:
        return {
            "provider": "anthropic",
            "route_kind": "anthropic_direct",
            "endpoint": "claude-code-direct-default",
            "model_request": task.model,
            "proxy_allowed": False,
            "complete": bool(str(task.model or "").strip()),
        }

    def _start_argv(self, task: AgentTask) -> tuple[str, ...]:
        argv = [
            self._binary,
            "-p",
            task.instruction,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            task.model,
        ]
        argv.extend(_claude_cli_controls(task))
        return tuple(argv)

    def _resume_argv(
        self,
        task: AgentTask,
        *,
        session_id: str,
        instruction: str,
    ) -> tuple[str, ...]:
        argv = [
            self._binary,
            "--resume",
            session_id,
            "-p",
            instruction,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            task.model,
        ]
        argv.extend(_claude_cli_controls(task))
        return tuple(argv)


class CodexRuntime(CommandAgentRuntime):
    kind = "codex"
    capabilities = {
        "resume": True,
        "cancel": True,
        "stream": True,
        "cost_reporting": False,
        "subagents": True,
        "images": True,
    }

    def _route_identity_manifest(
        self,
        task: AgentTask,
    ) -> Mapping[str, Any]:
        route = task.metadata.get("provider_route")
        if not isinstance(route, Mapping):
            route = {}
        provider = str(route.get("provider") or "openai").strip()
        route_kind = str(route.get("route_kind") or "codex_cli").strip()
        endpoint = str(
            route.get("endpoint") or "codex-cli-configured-route"
        ).strip()
        return {
            "provider": provider,
            "route_kind": route_kind,
            "endpoint": endpoint,
            "model_request": task.model,
            "configuration_sha256": str(
                route.get("configuration_sha256") or ""
            ).strip(),
            "complete": bool(
                provider
                and route_kind
                and endpoint
                and str(task.model or "").strip()
            ),
        }

    def __init__(
        self,
        *,
        transport: RuntimeTransport | None = None,
        binary: str = "codex",
    ) -> None:
        super().__init__(transport=transport, binary=binary)

    def _runtime_env(self, task: AgentTask) -> dict[str, str]:
        return _allowlisted_environment(
            super()._runtime_env(task),
            allowed_keys=_CODEX_ENV_KEYS,
        )

    def _start_argv(self, task: AgentTask) -> tuple[str, ...]:
        argv = [
            self._binary,
            "exec",
            "--json",
            "-C",
            str(Path(task.cwd).resolve()),
            "-m",
            task.model,
        ]
        reasoning_effort = str(
            task.metadata.get("reasoning_effort") or ""
        ).strip()
        if reasoning_effort:
            argv.extend(
                ("-c", f'reasoning_effort="{reasoning_effort}"')
            )
        if _filesystem_isolation_policy(
            task.metadata,
            cwd=Path(task.cwd).resolve(),
        ) is not None:
            argv.extend(("--sandbox", "workspace-write"))
        argv.append(task.instruction)
        return tuple(argv)

    def _resume_argv(
        self,
        task: AgentTask,
        *,
        session_id: str,
        instruction: str,
    ) -> tuple[str, ...]:
        argv = [
            self._binary,
            "exec",
            "resume",
            "--json",
            "-m",
            task.model,
        ]
        reasoning_effort = str(
            task.metadata.get("reasoning_effort") or ""
        ).strip()
        if reasoning_effort:
            argv.extend(
                ("-c", f'reasoning_effort="{reasoning_effort}"')
            )
        argv.extend((session_id, instruction))
        return tuple(argv)


def _claude_cli_controls(task: AgentTask) -> list[str]:
    metadata = task.metadata
    argv: list[str] = []
    if metadata.get("bare"):
        argv.append("--bare")
    if metadata.get("no_session_persistence"):
        argv.append("--no-session-persistence")

    system_prompt = str(metadata.get("system_prompt") or "").strip()
    if system_prompt:
        argv.extend(("--system-prompt", system_prompt))

    tools_mode = str(metadata.get("tools") or "").strip()
    if tools_mode:
        argv.extend(("--tools", tools_mode))

    for metadata_key, flag in (
        ("allowed_tools", "--allowed-tools"),
        ("disallowed_tools", "--disallowed-tools"),
    ):
        raw_tools = metadata.get(metadata_key)
        if not isinstance(raw_tools, Sequence) or isinstance(
            raw_tools,
            (str, bytes),
        ):
            continue
        tools = [str(tool).strip() for tool in raw_tools if str(tool).strip()]
        if tools:
            argv.extend((flag, ",".join(tools)))

    permission_mode = str(metadata.get("permission_mode") or "").strip()
    if not permission_mode:
        permission_mode = (
            "bypassPermissions"
            if _filesystem_isolation_policy(
                metadata,
                cwd=Path(task.cwd).resolve(),
            )
            is not None
            else "dontAsk"
        )
    argv.extend(("--permission-mode", permission_mode))

    effort = str(metadata.get("effort") or "").strip()
    if effort:
        argv.extend(("--effort", effort))
    if metadata.get("max_budget_usd") is not None:
        argv.extend(
            (
                "--max-budget-usd",
                str(float(metadata["max_budget_usd"])),
            )
        )
    raw_extra_args = metadata.get("extra_args")
    if isinstance(raw_extra_args, Sequence) and not isinstance(
        raw_extra_args,
        (str, bytes),
    ):
        argv.extend(
            str(value)
            for value in raw_extra_args
            if str(value).strip()
        )
    return argv


@dataclass
class _SubprocessToken:
    process: asyncio.subprocess.Process
    process_group_id: int
    process_started_at: float | None
    containment_id: str
    queue: asyncio.Queue[Mapping[str, Any] | None]
    raw_events: list[Mapping[str, Any]]
    stdout: list[str]
    stderr: list[str]
    started_at_ms: int
    timeout_s: float
    deadline: float
    done: asyncio.Task[int]
    cancel_requested: bool = False


def _subprocess_identity(pid: int) -> tuple[int, float | None]:
    try:
        process_group_id = os.getpgid(int(pid))
    except OSError:
        process_group_id = int(pid)
    try:
        started_at = float(psutil.Process(int(pid)).create_time())
    except (
        psutil.AccessDenied,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
    ):
        started_at = None
    return process_group_id, started_at


class SubprocessRuntimeTransport:
    """Async subprocess transport with process-group cancellation."""

    def __init__(self) -> None:
        self._tokens: dict[str, _SubprocessToken] = {}

    def supports_filesystem_isolation(self, mode: str) -> bool:
        return (
            mode == _WORKSPACE_ONLY_ISOLATION_MODE
            and sys.platform == "darwin"
            and _MACOS_SANDBOX_EXEC.is_file()
            and os.access(_MACOS_SANDBOX_EXEC, os.X_OK)
        )

    def is_active(self, token: str) -> bool:
        return not self._get(token).done.done()

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
        launch_argv = await self._prepare_launch_argv(
            argv,
            cwd=cwd,
            env=env,
            metadata=metadata,
        )
        containment_id = new_containment_id()
        process = await asyncio.create_subprocess_exec(
            *launch_argv,
            cwd=str(cwd),
            env=containment_environment(env, containment_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        process_group_id, process_started_at = _subprocess_identity(process.pid)
        token = _SubprocessToken(
            process=process,
            process_group_id=process_group_id,
            process_started_at=process_started_at,
            containment_id=containment_id,
            queue=asyncio.Queue(),
            raw_events=[],
            stdout=[],
            stderr=[],
            started_at_ms=int(time.time() * 1000),
            timeout_s=timeout_s,
            deadline=(
                asyncio.get_running_loop().time()
                + max(0.001, float(timeout_s))
            ),
            done=asyncio.create_task(asyncio.sleep(0, result=0)),
        )
        token.done = asyncio.create_task(self._run_with_timeout(token))
        self._tokens[run_id] = token
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
        current = self._get(token)
        if not current.done.done():
            raise RuntimeError(
                "cannot resume while the previous runtime generation is active"
            )
        await asyncio.shield(current.done)
        await self._terminate_process(current)
        launch_argv = await self._prepare_launch_argv(
            argv,
            cwd=cwd,
            env=env,
            metadata=metadata,
        )
        containment_id = new_containment_id()
        process = await asyncio.create_subprocess_exec(
            *launch_argv,
            cwd=str(cwd),
            env=containment_environment(env, containment_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        process_group_id, process_started_at = _subprocess_identity(process.pid)
        resumed = _SubprocessToken(
            process=process,
            process_group_id=process_group_id,
            process_started_at=process_started_at,
            containment_id=containment_id,
            queue=asyncio.Queue(),
            raw_events=current.raw_events,
            stdout=current.stdout,
            stderr=current.stderr,
            started_at_ms=current.started_at_ms,
            timeout_s=timeout_s,
            deadline=(
                asyncio.get_running_loop().time()
                + max(0.001, float(timeout_s))
            ),
            done=asyncio.create_task(asyncio.sleep(0, result=0)),
        )
        resumed.done = asyncio.create_task(self._run_with_timeout(resumed))
        self._tokens[token] = resumed

    async def cancel(self, token: str) -> None:
        item = self._get(token)
        if not item.cancel_requested:
            item.cancel_requested = True
            cancelled = {"type": "run.cancelled"}
            item.raw_events.append(cancelled)
            await item.queue.put(cancelled)
        await self._terminate_process(item)

    async def _terminate_process(self, item: _SubprocessToken) -> None:
        termination = await asyncio.to_thread(
            terminate_containment,
            root_pid=item.process.pid,
            expected_root_started_at=item.process_started_at,
            expected_process_group_id=item.process_group_id,
            containment_id=item.containment_id,
            term_timeout_s=5.0,
            kill_timeout_s=5.0,
        )
        if not termination["safe_to_finalize"]:
            raise RuntimeError(
                "runtime containment could not prove process-tree reap: "
                f"{termination}"
            )
        if item.process.returncode is None:
            try:
                await asyncio.wait_for(item.process.wait(), timeout=1.0)
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    "runtime root process was not reaped after containment "
                    f"termination: pid={item.process.pid}"
                ) from exc

    async def _prepare_launch_argv(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        metadata: Mapping[str, Any],
    ) -> tuple[str, ...]:
        policy = _filesystem_isolation_policy(metadata, cwd=cwd)
        if policy is None:
            return argv
        if not self.supports_filesystem_isolation(policy.mode):
            raise RuntimeError(
                "runtime transport cannot enforce filesystem isolation mode "
                f"{policy.mode}"
            )
        sandbox_prefix = _macos_sandbox_prefix(
            policy,
            argv=argv,
            env=env,
        )
        await self._preflight_sandbox(
            sandbox_prefix,
            cwd=cwd,
            env=env,
        )
        return (*sandbox_prefix, *argv)

    async def _preflight_sandbox(
        self,
        sandbox_prefix: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *sandbox_prefix,
                "/usr/bin/true",
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(
                "filesystem isolation preflight could not start sandbox-exec"
            ) from exc
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=5,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError(
                "filesystem isolation preflight timed out"
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "filesystem isolation preflight failed"
                + (f": {detail}" if detail else "")
            )

    async def stream(self, token: str) -> AsyncIterator[Mapping[str, Any]]:
        item = self._get(token)
        while True:
            event = await item.queue.get()
            if event is None:
                return
            yield event

    async def collect(self, token: str) -> RuntimeTransportResult:
        item = self._get(token)
        returncode = await asyncio.shield(item.done)
        provenance = _provenance_from_raw_events(item.raw_events)
        return RuntimeTransportResult(
            returncode=returncode,
            stdout="".join(item.stdout),
            stderr="".join(item.stderr),
            raw_events=tuple(item.raw_events),
            started_at_ms=item.started_at_ms,
            ended_at_ms=int(time.time() * 1000),
            cost_usd=provenance["cost_usd"],
            resolved_model=provenance["resolved_model"],
            token_usage=provenance["token_usage"],
            model_provenance=provenance["model_provenance"],
            cost_provenance=provenance["cost_provenance"],
            token_provenance=provenance["token_provenance"],
            metadata={
                "provenance_support": {
                    "resolved_model": bool(
                        provenance["resolved_model"]
                        and provenance["model_provenance"]
                    ),
                    "cost_usd": bool(provenance["cost_provenance"]),
                    "token_usage": bool(
                        provenance["token_usage"]
                        and provenance["token_provenance"]
                    ),
                }
            },
        )

    async def _run_with_timeout(self, item: _SubprocessToken) -> int:
        pump_task = asyncio.create_task(self._pump(item))
        remaining_s = max(
            0.0,
            item.deadline - asyncio.get_running_loop().time(),
        )
        try:
            returncode = await asyncio.wait_for(
                asyncio.shield(pump_task),
                timeout=remaining_s,
            )
            await self._terminate_process(item)
            return returncode
        except asyncio.TimeoutError:
            if pump_task.done():
                returncode = await pump_task
                await self._terminate_process(item)
                return returncode
            await self._terminate_process(item)
            await pump_task
            return 124
        finally:
            await item.queue.put(None)

    async def _pump(self, item: _SubprocessToken) -> int:
        stdout_task = asyncio.create_task(self._pump_stdout(item))
        stderr_task = asyncio.create_task(self._pump_stderr(item))
        returncode = await item.process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        return returncode

    async def _pump_stdout(self, item: _SubprocessToken) -> None:
        if item.process.stdout is None:
            return
        while line := await item.process.stdout.readline():
            text = line.decode("utf-8", errors="replace")
            item.stdout.append(text)
            raw = _parse_runtime_line(text)
            item.raw_events.append(raw)
            await item.queue.put(raw)

    async def _pump_stderr(self, item: _SubprocessToken) -> None:
        if item.process.stderr is None:
            return
        while line := await item.process.stderr.readline():
            item.stderr.append(line.decode("utf-8", errors="replace"))

    def _get(self, token: str) -> _SubprocessToken:
        try:
            return self._tokens[token]
        except KeyError as exc:
            raise KeyError(f"unknown runtime transport token: {token}") from exc


def normalize_runtime_event(raw: Mapping[str, Any]) -> RuntimeEvent:
    nested = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
    raw_kind = str(
        nested.get("type")
        or raw.get("type")
        or raw.get("kind")
        or raw.get("event")
        or "unknown"
    )
    item = raw.get("item") if isinstance(raw.get("item"), Mapping) else {}
    if raw_kind == "item.completed":
        item_kind = str(item.get("type") or "")
        if item_kind == "agent_message":
            raw_kind = "agent_message"
        elif item_kind in {
            "command_execution",
            "mcp_tool_call",
            "tool_call",
            "web_search",
        }:
            raw_kind = "tool.completed"
    elif raw_kind == "item.started":
        raw_kind = "tool.started"
    elif raw_kind == "result":
        raw_kind = "run.failed" if raw.get("is_error") else "run.completed"
    aliases = {
        "thread.started": "run.started",
        "session_start": "run.started",
        "turn.started": "turn.started",
        "tool_use": "tool.started",
        "tool.started": "tool.started",
        "tool_result": "tool.completed",
        "tool.completed": "tool.completed",
        "assistant": "agent.message",
        "agent_message": "agent.message",
        "message": "agent.message",
        "turn.completed": "turn.completed",
        "turn.failed": "turn.failed",
        "task_complete": "turn.completed",
        "thread.completed": "run.completed",
        "session_end": "run.completed",
        "run.completed": "run.completed",
        "run.failed": "run.failed",
        "run.cancelled": "run.cancelled",
    }
    kind = aliases.get(raw_kind, raw_kind)
    return RuntimeEvent(
        kind=kind,
        payload=dict(raw),
        ts_ms=_event_ts_ms(raw),
    )


def _terminal_status(events: list[RuntimeEvent], returncode: int) -> str:
    kinds = {event.kind for event in events}
    if "run.cancelled" in kinds:
        return "cancelled"
    if "run.failed" in kinds or returncode != 0:
        return "failed"
    if "run.completed" in kinds or returncode == 0:
        return "completed"
    return "unknown"


def _result_output(
    result: RuntimeTransportResult,
    events: list[RuntimeEvent],
) -> str:
    messages: list[str] = []
    for event in events:
        if event.kind != "agent.message":
            continue
        value = _runtime_message_text(event.payload)
        if value:
            messages.append(value)
    return "\n".join(messages) if messages else result.stdout


def _runtime_message_text(raw: Mapping[str, Any]) -> str:
    candidates: list[Mapping[str, Any]] = [raw]
    for key in ("payload", "item", "message"):
        nested = raw.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    for candidate in candidates:
        value = (
            candidate.get("message")
            or candidate.get("text")
            or candidate.get("content")
            or candidate.get("result")
        )
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            parts: list[str] = []
            for block in value:
                if not isinstance(block, Mapping):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            if parts:
                return "\n".join(parts)
    return ""


def _parse_runtime_line(text: str) -> Mapping[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"type": "agent_message", "message": text.rstrip("\n")}
    return value if isinstance(value, Mapping) else {"type": "unknown", "value": value}


def _session_id(raw: Mapping[str, Any]) -> str:
    nested = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
    return str(
        raw.get("session_id")
        or raw.get("thread_id")
        or nested.get("session_id")
        or nested.get("thread_id")
        or ""
    ).strip()


def _event_ts_ms(raw: Mapping[str, Any]) -> int:
    value = raw.get("ts_ms") or raw.get("timestamp_ms") or raw.get("ts")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(time.time() * 1000)
    return parsed * 1000 if parsed < 10_000_000_000 else parsed


def _validated_transport_provenance(
    task: AgentTask,
    result: RuntimeTransportResult,
) -> tuple[str, float, dict[str, Any], str, str, str]:
    resolved_model = str(result.resolved_model or "").strip()
    model_provenance = str(result.model_provenance or "").strip()
    cost_provenance = str(result.cost_provenance or "").strip()
    token_provenance = str(result.token_provenance or "").strip()
    try:
        cost_usd = float(result.cost_usd)
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime transport cost_usd must be numeric") from exc
    if not math.isfinite(cost_usd) or cost_usd < 0:
        raise ValueError("runtime transport cost_usd must be finite and non-negative")
    token_usage = _normalize_token_usage(result.token_usage)

    experiment = task.metadata.get("experiment")
    if isinstance(experiment, Mapping):
        missing: list[str] = []
        if not _is_exact_resolved_model(resolved_model):
            missing.append("resolved_model")
        if not model_provenance:
            missing.append("model_provenance")
        if not cost_provenance:
            missing.append("cost_provenance")
        if not token_provenance:
            missing.append("token_provenance")
        if "tokens_in" not in token_usage or "tokens_out" not in token_usage:
            missing.append("token_usage")
        if missing:
            raise RuntimeProvenanceError(
                (
                    "experiment runtime provenance is incomplete: "
                    + ", ".join(missing)
                ),
                result=result,
            )
    return (
        resolved_model,
        cost_usd,
        token_usage,
        model_provenance,
        cost_provenance,
        token_provenance,
    )


def _is_exact_resolved_model(value: str) -> bool:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return False
    unresolved_aliases = {
        "auto",
        "default",
        "haiku",
        "latest",
        "opus",
        "proxy-default",
        "sonnet",
    }
    if normalized in unresolved_aliases:
        return False
    route_tail = normalized.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return route_tail not in unresolved_aliases


def _provenance_from_raw_events(
    raw_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    resolved_model = ""
    model_provenance = ""
    usage_observations: list[
        tuple[str, dict[str, Any], str]
    ] = []
    cost_observations: list[tuple[str, float, str]] = []

    for event_index, raw in enumerate(raw_events):
        for path, candidate in _mapping_candidates(raw):
            source = f"transport_event[{event_index}]{path}"
            semantics = _usage_semantics(candidate)
            direct_model = str(
                candidate.get("resolved_model")
                or candidate.get("model")
                or ""
            ).strip()
            if direct_model:
                resolved_model = direct_model
                model_provenance = f"{source}.model"

            model_usage = candidate.get("modelUsage")
            if not isinstance(model_usage, Mapping):
                model_usage = candidate.get("model_usage")
            candidate_usage: dict[str, Any] = {}
            candidate_cost: float | None = None
            candidate_usage_source = ""
            candidate_cost_source = ""
            if isinstance(model_usage, Mapping) and model_usage:
                model_names = [
                    str(name).strip()
                    for name in model_usage
                    if str(name).strip()
                ]
                if len(model_names) == 1:
                    resolved_model = model_names[0]
                    model_provenance = f"{source}.model_usage"
                aggregated = _aggregate_model_usage(model_usage)
                if aggregated:
                    candidate_usage.update(aggregated)
                    candidate_usage_source = f"{source}.model_usage"
                model_cost = _model_usage_cost(model_usage)
                if model_cost is not None:
                    candidate_cost = model_cost
                    candidate_cost_source = f"{source}.model_usage"

            usage = candidate.get("usage")
            if isinstance(usage, Mapping):
                normalized = _normalize_token_usage(usage)
                if normalized:
                    candidate_usage.update(normalized)
                    candidate_usage_source = f"{source}.usage"

            for cost_key in ("total_cost_usd", "cost_usd", "costUSD"):
                if candidate.get(cost_key) is None:
                    continue
                try:
                    observed_cost = float(candidate[cost_key])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(observed_cost) and observed_cost >= 0:
                    candidate_cost = observed_cost
                    candidate_cost_source = f"{source}.{cost_key}"
                    break
            if candidate_usage:
                usage_observations.append(
                    (
                        semantics,
                        _with_token_totals(candidate_usage),
                        candidate_usage_source or source,
                    )
                )
            if candidate_cost is not None:
                cost_observations.append(
                    (
                        semantics,
                        candidate_cost,
                        candidate_cost_source or source,
                    )
                )

    token_usage, token_provenance = _resolve_usage_observations(
        usage_observations
    )
    cost_usd, cost_provenance = _resolve_cost_observations(
        cost_observations
    )

    return {
        "resolved_model": resolved_model,
        "cost_usd": cost_usd,
        "token_usage": token_usage,
        "model_provenance": model_provenance,
        "cost_provenance": cost_provenance,
        "token_provenance": token_provenance,
    }


def _usage_semantics(candidate: Mapping[str, Any]) -> str:
    raw = (
        candidate.get("usage_semantics")
        or candidate.get("usage_mode")
        or ""
    )
    value = str(raw).strip().casefold()
    if value in {"delta", "cumulative", "snapshot"}:
        return value
    return ""


def _dedupe_observations(
    observations: Sequence[tuple[str, Any, str]],
) -> list[tuple[str, Any, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, Any, str]] = []
    for semantics, value, source in observations:
        identity = _sha256_json(
            {
                "semantics": semantics,
                "value": value,
                "source": source if semantics == "delta" else "",
            }
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append((semantics, value, source))
    return deduped


def _resolve_usage_observations(
    observations: Sequence[tuple[str, dict[str, Any], str]],
) -> tuple[dict[str, Any], str]:
    values = _dedupe_observations(observations)
    if not values:
        return {}, ""
    semantics = {item[0] for item in values}
    if len(values) == 1 and semantics == {""}:
        _, usage, source = values[0]
        return dict(usage), source
    if "" in semantics or len(semantics) != 1:
        return {}, ""
    mode = next(iter(semantics))
    if mode == "snapshot":
        if len(values) != 1:
            return {}, ""
        _, usage, source = values[0]
        return dict(usage), source
    if mode == "delta":
        total: dict[str, Any] = {}
        for _, usage, _ in values:
            for key, value in usage.items():
                if isinstance(value, bool) or not isinstance(value, int):
                    total.setdefault(key, value)
                    continue
                if key in {"context_window", "max_output_tokens"}:
                    total[key] = max(int(total.get(key) or 0), value)
                else:
                    total[key] = int(total.get(key) or 0) + value
        return (
            _with_token_totals(total),
            "transport_events.usage[delta_sum]",
        )
    if mode == "cumulative":
        previous: Mapping[str, Any] | None = None
        for _, usage, _ in values:
            if previous is not None and not _usage_is_monotonic(
                previous,
                usage,
            ):
                return {}, ""
            previous = usage
        return (
            dict(values[-1][1]),
            "transport_events.usage[cumulative_latest]",
        )
    return {}, ""


def _usage_is_monotonic(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    for key, old_value in previous.items():
        new_value = current.get(key)
        if (
            isinstance(old_value, bool)
            or not isinstance(old_value, int)
            or isinstance(new_value, bool)
            or not isinstance(new_value, int)
            or key in {"context_window", "max_output_tokens"}
        ):
            continue
        if new_value < old_value:
            return False
    return True


def _resolve_cost_observations(
    observations: Sequence[tuple[str, float, str]],
) -> tuple[float, str]:
    values = _dedupe_observations(observations)
    if not values:
        return 0.0, ""
    semantics = {item[0] for item in values}
    if len(values) == 1 and semantics == {""}:
        _, cost, source = values[0]
        return float(cost), source
    if "" in semantics or len(semantics) != 1:
        return 0.0, ""
    mode = next(iter(semantics))
    if mode == "delta":
        return (
            sum(float(cost) for _, cost, _ in values),
            "transport_events.cost[delta_sum]",
        )
    if mode == "cumulative":
        previous = -1.0
        for _, cost, _ in values:
            if cost < previous:
                return 0.0, ""
            previous = cost
        return (
            float(values[-1][1]),
            "transport_events.cost[cumulative_latest]",
        )
    if mode == "snapshot" and len(values) == 1:
        _, cost, source = values[0]
        return float(cost), source
    return 0.0, ""


def _mapping_candidates(
    value: Mapping[str, Any],
    *,
    path: str = "",
) -> list[tuple[str, Mapping[str, Any]]]:
    candidates = [(path, value)]
    for key in ("payload", "message", "result", "item"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            candidates.extend(
                _mapping_candidates(
                    nested,
                    path=f"{path}.{key}",
                )
            )
    return candidates


def _aggregate_model_usage(model_usage: Mapping[str, Any]) -> dict[str, Any]:
    totals: dict[str, int] = {}
    for raw_usage in model_usage.values():
        if not isinstance(raw_usage, Mapping):
            continue
        normalized = _normalize_token_usage(raw_usage)
        for key, value in normalized.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return _with_token_totals(totals)


def _model_usage_cost(model_usage: Mapping[str, Any]) -> float | None:
    total = 0.0
    observed = False
    for raw_usage in model_usage.values():
        if not isinstance(raw_usage, Mapping):
            continue
        value = raw_usage.get("costUSD")
        if value is None:
            value = raw_usage.get("cost_usd")
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(parsed) or parsed < 0:
            continue
        total += parsed
        observed = True
    return total if observed else None


def _normalize_token_usage(raw_usage: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_usage, Mapping):
        raise ValueError("runtime transport token_usage must be a mapping")
    aliases = {
        "inputTokens": "input_tokens",
        "outputTokens": "output_tokens",
        "cacheReadInputTokens": "cache_read_input_tokens",
        "cacheCreationInputTokens": "cache_creation_input_tokens",
        "contextWindow": "context_window",
        "maxOutputTokens": "max_output_tokens",
        "cached_input_tokens": "cache_read_input_tokens",
    }
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in raw_usage.items():
        if str(raw_key) in {"costUSD", "cost_usd", "total_cost_usd"}:
            continue
        key = aliases.get(str(raw_key), str(raw_key))
        if isinstance(raw_value, bool):
            normalized[key] = raw_value
            continue
        if isinstance(raw_value, (int, float)):
            numeric = float(raw_value)
            if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
                raise ValueError(
                    "runtime transport token_usage values must be "
                    "finite non-negative integers"
                )
            normalized[key] = int(numeric)
        elif isinstance(raw_value, (str, Mapping, list, tuple, type(None))):
            normalized[key] = raw_value
        else:
            normalized[key] = str(raw_value)
    totals = _with_token_totals(normalized)
    for key in ("tokens_in", "tokens_out"):
        if key not in totals:
            continue
        value = totals[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(
                f"runtime transport token_usage {key} must be a "
                "non-negative integer"
            )
    return totals


def _with_token_totals(usage: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(usage)
    if "tokens_in" not in normalized:
        input_keys = (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
        if any(key in normalized for key in input_keys):
            normalized["tokens_in"] = sum(
                int(normalized.get(key) or 0)
                for key in input_keys
            )
    if "tokens_out" not in normalized and "output_tokens" in normalized:
        normalized["tokens_out"] = int(normalized["output_tokens"])
    return normalized


def _binary_identity_manifest(binary: str) -> dict[str, Any]:
    requested = str(binary or "").strip()
    resolved = ""
    if requested:
        candidate = Path(requested).expanduser()
        if candidate.is_absolute() or candidate.parent != Path("."):
            resolved = str(candidate.resolve(strict=False))
        else:
            resolved = str(shutil.which(requested) or "")
    digest = ""
    if resolved:
        path = Path(resolved)
        if path.is_file() and not path.is_symlink():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "requested": requested,
        "resolved_path": resolved,
        "sha256": digest,
        "complete": bool(requested and resolved and digest),
    }


def _transport_identity_manifest(
    transport: RuntimeTransport,
) -> dict[str, Any]:
    implementation = (
        f"{type(transport).__module__}.{type(transport).__qualname__}"
    )
    identity_provider = getattr(transport, "identity_manifest", None)
    if callable(identity_provider):
        raw = identity_provider()
        if not isinstance(raw, Mapping):
            raise ValueError(
                "runtime transport identity_manifest must return a mapping"
            )
        configuration = _non_secret_identity_mapping(raw)
        complete = bool(configuration.pop("complete", True))
        return {
            "implementation": implementation,
            "configuration": configuration,
            "configuration_sha256": _sha256_json(configuration),
            "complete": complete,
        }
    if isinstance(transport, SubprocessRuntimeTransport):
        configuration = {
            "launch": "asyncio.create_subprocess_exec",
            "process_group": "new_session",
        }
        return {
            "implementation": implementation,
            "configuration": configuration,
            "configuration_sha256": _sha256_json(configuration),
            "complete": True,
        }
    return {
        "implementation": implementation,
        "configuration": {},
        "configuration_sha256": "",
        "complete": False,
    }


def _non_secret_identity_mapping(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized = key.casefold()
        if any(
            marker in normalized
            for marker in ("api_key", "auth", "password", "secret", "token")
        ):
            raise ValueError(
                f"runtime identity manifest must not contain secret field {key}"
            )
        if isinstance(raw_value, Mapping):
            result[key] = _non_secret_identity_mapping(raw_value)
        elif isinstance(raw_value, (list, tuple)):
            result[key] = [
                (
                    _non_secret_identity_mapping(item)
                    if isinstance(item, Mapping)
                    else item
                )
                for item in raw_value
            ]
        else:
            result[key] = raw_value
    return result


def _sha256_json(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _filesystem_isolation_policy(
    metadata: Mapping[str, Any],
    *,
    cwd: Path,
) -> _FilesystemIsolationPolicy | None:
    raw = metadata.get("filesystem_isolation")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("filesystem_isolation must be a mapping")
    mode = str(raw.get("mode") or "").strip()
    if mode != _WORKSPACE_ONLY_ISOLATION_MODE:
        raise ValueError(
            "unsupported filesystem isolation mode: "
            f"{mode or '<missing>'}"
        )
    raw_deny_paths = raw.get("deny_paths")
    if (
        not isinstance(raw_deny_paths, Sequence)
        or isinstance(raw_deny_paths, (str, bytes))
        or not raw_deny_paths
    ):
        raise ValueError(
            "filesystem_isolation.deny_paths must be a non-empty sequence"
        )
    deny_paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_deny_paths:
        if not isinstance(raw_path, (str, os.PathLike)):
            raise ValueError(
                "filesystem_isolation.deny_paths entries must be paths"
            )
        if isinstance(raw_path, str) and not raw_path.strip():
            raise ValueError(
                "filesystem_isolation.deny_paths entries cannot be empty"
            )
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve(strict=False)
        key = os.fspath(resolved)
        if key in seen:
            continue
        seen.add(key)
        deny_paths.append(resolved)
    if not deny_paths:
        raise ValueError(
            "filesystem_isolation.deny_paths must contain at least one path"
        )
    raw_workspace = raw.get("workspace")
    workspace = cwd
    if raw_workspace not in (None, ""):
        candidate_workspace = Path(str(raw_workspace)).expanduser()
        if not candidate_workspace.is_absolute():
            candidate_workspace = cwd / candidate_workspace
        workspace = candidate_workspace.resolve(strict=False)
        if workspace != cwd:
            raise ValueError(
                "filesystem_isolation.workspace must match the runtime cwd"
            )
    runtime_read_paths = _path_sequence(
        raw.get("runtime_read_paths", ()),
        field_name="filesystem_isolation.runtime_read_paths",
        cwd=cwd,
        required=False,
    )
    network_policy = str(raw.get("network_policy") or "enabled").strip()
    if network_policy not in {"disabled", "restricted", "enabled"}:
        raise ValueError("filesystem_isolation.network_policy is invalid")
    if network_policy == "restricted":
        raise ValueError(
            "workspace_only isolation cannot enforce restricted network policy"
        )
    return _FilesystemIsolationPolicy(
        mode=mode,
        workspace=workspace,
        deny_paths=tuple(deny_paths),
        runtime_read_paths=runtime_read_paths,
        network_policy=network_policy,
    )


def _transport_supports_filesystem_isolation(
    transport: RuntimeTransport,
    mode: str,
) -> bool:
    supports = getattr(transport, "supports_filesystem_isolation", None)
    if not callable(supports):
        return False
    try:
        return bool(supports(mode))
    except Exception:
        return False


def _macos_sandbox_prefix(
    policy: _FilesystemIsolationPolicy,
    *,
    argv: tuple[str, ...],
    env: Mapping[str, str],
) -> tuple[str, ...]:
    runtime_read_paths = _runtime_read_paths(
        argv,
        env=env,
        configured=policy.runtime_read_paths,
    )
    rules = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process-info* (target self))",
        "(allow process-fork)",
        "(allow file-read-metadata file-test-existence)",
        (
            "(allow file-read* file-map-executable process-exec* "
            '(subpath "/bin") '
            '(subpath "/sbin") '
            '(subpath "/usr/bin") '
            '(subpath "/usr/sbin") '
            '(subpath "/usr/libexec"))'
        ),
        (
            "(allow file-read* file-write* process-exec* "
            '(subpath (param "WORKSPACE")))'
        ),
    ]
    if policy.network_policy != "disabled":
        rules.append("(allow network*)")
    arguments = [str(_MACOS_SANDBOX_EXEC)]
    for index, _ in enumerate(runtime_read_paths):
        parameter = f"RUNTIME_READ_PATH_{index}"
        rules.append(
            "(allow file-read* file-map-executable process-exec* "
            f'(subpath (param "{parameter}")))'
        )
    for index, _ in enumerate(policy.deny_paths):
        parameter = f"DENY_PATH_{index}"
        rules.append(
            "(deny file-read* file-write* "
            f'(subpath (param "{parameter}")))'
        )
    arguments.extend(("-p", "\n".join(rules)))
    arguments.extend(("-D", f"WORKSPACE={policy.workspace}"))
    for index, path in enumerate(runtime_read_paths):
        arguments.extend(("-D", f"RUNTIME_READ_PATH_{index}={path}"))
    for index, path in enumerate(policy.deny_paths):
        arguments.extend(("-D", f"DENY_PATH_{index}={path}"))
    return tuple(arguments)


def _path_sequence(
    raw_paths: Any,
    *,
    field_name: str,
    cwd: Path,
    required: bool,
) -> tuple[Path, ...]:
    if (
        not isinstance(raw_paths, Sequence)
        or isinstance(raw_paths, (str, bytes))
        or (required and not raw_paths)
    ):
        qualifier = "non-empty " if required else ""
        raise ValueError(f"{field_name} must be a {qualifier}sequence")
    resolved_paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        if not isinstance(raw_path, (str, os.PathLike)):
            raise ValueError(f"{field_name} entries must be paths")
        if isinstance(raw_path, str) and not raw_path.strip():
            raise ValueError(f"{field_name} entries cannot be empty")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve(strict=False)
        key = os.fspath(resolved)
        if key in seen:
            continue
        seen.add(key)
        resolved_paths.append(resolved)
    return tuple(resolved_paths)


def _runtime_read_paths(
    argv: tuple[str, ...],
    *,
    env: Mapping[str, str],
    configured: tuple[Path, ...],
) -> tuple[Path, ...]:
    if not argv or not str(argv[0]).strip():
        raise RuntimeError("filesystem isolation requires a runtime executable")
    executable_value = str(argv[0])
    executable = Path(executable_value).expanduser()
    if not executable.is_absolute():
        resolved_command = shutil.which(
            executable_value,
            path=str(env.get("PATH") or os.defpath),
        )
        if not resolved_command:
            raise RuntimeError(
                "filesystem isolation could not resolve runtime executable "
                f"{executable_value!r}"
            )
        executable = Path(resolved_command)

    lexical = executable.absolute()
    resolved = executable.resolve(strict=False)
    candidates: list[Path] = [
        *configured,
        lexical.parent,
        resolved.parent,
    ]
    if (lexical.parent.parent / "pyvenv.cfg").is_file():
        candidates.append(lexical.parent.parent)
    if resolved.parent.name in {"bin", "sbin"}:
        candidates.append(resolved.parent.parent)
    if resolved.name.startswith("python"):
        runtime_root = resolved.parent.parent
        candidates.append(runtime_root.parent)

    runtime_paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.resolve(strict=False)
        key = os.fspath(normalized)
        if key in seen:
            continue
        seen.add(key)
        runtime_paths.append(normalized)
    return tuple(runtime_paths)
