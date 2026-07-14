"""Claude Agent SDK transport adapter for the provider-neutral runtime seam."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent_runtime import RuntimeTransportResult
from .process_containment import (
    CONTAINMENT_ENV_VAR,
    ContainmentSnapshot,
    ProcessIdentity,
    new_containment_id,
    process_identity,
    scan_containment,
    terminate_containment,
)
from .redaction import redact_for_telegram as _redact_secret_text


class ClaudeAgentSdkPreflightError(RuntimeError):
    """The selected SDK runtime cannot satisfy its production contract."""


class MissingClaudeAgentSdk(ClaudeAgentSdkPreflightError):
    """Raised when the optional Claude Agent SDK runtime is selected."""


class ClaudeAgentSdkContainmentError(RuntimeError):
    """The SDK child tree could not be proven empty."""


SdkLoader = Callable[[], tuple[Any, Any]]
ContainmentScanner = Callable[..., ContainmentSnapshot]
ContainmentTerminator = Callable[..., dict[str, Any]]
_SDK_LAUNCH_CONFIG_ENV = "CODEX_SUPERVISOR_SDK_LAUNCH_CONFIG"
_SDK_LAUNCH_SCHEMA = "supervisor-claude-sdk-launch/v1"
_SDK_LAUNCH_ROOT_PREFIX = "codex-supervisor-claude-sdk-"
_SDK_STALE_LAUNCH_ROOT_AGE_S = 24 * 60 * 60
_SENSITIVE_ENV_KEY_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?key|secret|token|"
    r"passw(?:or)?d|credential|private[_-]?key)"
)


def _is_sensitive_env_entry(key: str, value: str) -> bool:
    if _SENSITIVE_ENV_KEY_PATTERN.search(key):
        return True
    return _redact_secret_text(value) != value
_SDK_ATTESTATION_SCHEMA = "supervisor-claude-sdk-attestation/v1"
_LAUNCHER_SOURCE = f"""#!{sys.executable}
import hashlib
import json
import os
import sys
import tempfile

CONFIG_ENV = {_SDK_LAUNCH_CONFIG_ENV!r}
CONTAINMENT_ENV = {CONTAINMENT_ENV_VAR!r}
CONFIG_SCHEMA = {_SDK_LAUNCH_SCHEMA!r}
ATTESTATION_SCHEMA = {_SDK_ATTESTATION_SCHEMA!r}

config_path = os.environ.get(CONFIG_ENV, "")
if not config_path:
    raise SystemExit("missing SDK launch configuration")
with open(config_path, "r", encoding="utf-8") as stream:
    config = json.load(stream)
if config.get("schema_version") != CONFIG_SCHEMA:
    raise SystemExit("invalid SDK launch configuration")
final_env = {{
    str(key): str(value)
    for key, value in dict(config.get("environment") or {{}}).items()
}}
for sensitive_key in config.get("sensitive_env_keys") or []:
    sensitive_key = str(sensitive_key)
    sensitive_value = os.environ.get(sensitive_key)
    if sensitive_value is None:
        raise SystemExit(
            "missing sensitive SDK launch environment value: " + sensitive_key
        )
    final_env[sensitive_key] = sensitive_value
containment_id = str(config.get("containment_id") or "")
if not containment_id:
    raise SystemExit("missing SDK containment id")
final_env[CONTAINMENT_ENV] = containment_id
environment_sha256 = hashlib.sha256(
    json.dumps(
        final_env,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
if environment_sha256 != str(config.get("environment_sha256") or ""):
    raise SystemExit("SDK launch environment hash mismatch")
attestation = {{
    "schema_version": ATTESTATION_SCHEMA,
    "containment_id": containment_id,
    "pid": os.getpid(),
    "ppid": os.getppid(),
    "environment_sha256": environment_sha256,
    "executable": str(config.get("executable") or ""),
}}
attestation_path = str(config.get("attestation_path") or "")
directory = os.path.dirname(attestation_path)
fd, temporary = tempfile.mkstemp(prefix=".attestation-", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(attestation, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, attestation_path)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
executable = str(config.get("executable") or "")
os.execve(executable, [executable, *sys.argv[1:]], final_env)
"""


@dataclass(frozen=True)
class ClaudeAgentSdkCapabilities:
    sdk_available: bool
    environment_isolation: bool
    process_containment: bool
    safe_cancellation: bool
    production_ready: bool
    sdk_version: str
    cli_path: str
    test_only_uncontained: bool = False


@dataclass
class _SdkContainment:
    containment_id: str
    root: Path
    launcher_path: Path
    config_path: Path
    attestation_path: Path
    environment_sha256: str
    sensitive_env: dict[str, str] = field(default_factory=dict)
    root_identity: ProcessIdentity | None = None
    attested: bool = False


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
    containment: _SdkContainment | None = None
    sdk_connected: bool = False


class ClaudeAgentSdkTransport:
    """Claude SDK adapter with eager capability and process-tree preflight."""

    def __init__(
        self,
        *,
        sdk_loader: SdkLoader | None = None,
        claude_cli_path: str | Path | None = None,
        allow_uncontained_test_transport: bool = False,
        containment_scanner: ContainmentScanner = scan_containment,
        containment_terminator: ContainmentTerminator = terminate_containment,
    ) -> None:
        self._sdk_loader = sdk_loader or _load_claude_agent_sdk
        self._explicit_cli_path = (
            Path(claude_cli_path).expanduser()
            if claude_cli_path is not None
            else None
        )
        self._allow_uncontained_test_transport = bool(
            allow_uncontained_test_transport
        )
        self._containment_scanner = containment_scanner
        self._containment_terminator = containment_terminator
        self._sdk_types: tuple[Any, Any] | None = None
        self._capabilities: ClaudeAgentSdkCapabilities | None = None
        self._executions: dict[str, _SdkExecution] = {}
        self.preflight()

    def preflight(self) -> ClaudeAgentSdkCapabilities:
        """Synchronously prove dependency and cancellation capabilities."""

        if self._capabilities is not None:
            return self._capabilities
        try:
            client_cls, options_cls = self._sdk_loader()
        except ClaudeAgentSdkPreflightError:
            raise
        except ModuleNotFoundError as exc:
            raise MissingClaudeAgentSdk(
                "claude_agent_sdk is optional; install codex-supervisor[agent]"
            ) from exc
        except Exception as exc:
            raise ClaudeAgentSdkPreflightError(
                "Claude Agent SDK dependency preflight failed"
            ) from exc
        _validate_sdk_types(client_cls, options_cls)
        self._sdk_types = (client_cls, options_cls)
        sdk_version = _sdk_version(client_cls)
        if self._allow_uncontained_test_transport:
            self._capabilities = ClaudeAgentSdkCapabilities(
                sdk_available=True,
                environment_isolation=False,
                process_containment=False,
                safe_cancellation=False,
                production_ready=False,
                sdk_version=sdk_version,
                cli_path="",
                test_only_uncontained=True,
            )
            return self._capabilities

        _scrub_stale_launch_roots()
        cli_path = _resolve_claude_cli_path(
            explicit=self._explicit_cli_path,
            options_cls=options_cls,
        )
        probe_id = new_containment_id()
        snapshot = self._containment_scanner(probe_id)
        if not snapshot.scan_complete or snapshot.processes:
            raise ClaudeAgentSdkPreflightError(
                "Claude SDK containment scan preflight could not prove an "
                f"empty process set: {snapshot}"
            )
        _preflight_launcher(cli_path)
        self._explicit_cli_path = cli_path
        self._capabilities = ClaudeAgentSdkCapabilities(
            sdk_available=True,
            environment_isolation=True,
            process_containment=True,
            safe_cancellation=True,
            production_ready=True,
            sdk_version=sdk_version,
            cli_path=str(cli_path),
        )
        return self._capabilities

    def identity_manifest(self) -> dict[str, Any]:
        capabilities = self.preflight()
        cli_sha256 = ""
        if capabilities.cli_path:
            cli_sha256 = hashlib.sha256(
                Path(capabilities.cli_path).read_bytes()
            ).hexdigest()
        return {
            "adapter": "claude_agent_sdk",
            "sdk_version": capabilities.sdk_version,
            "environment_isolation": capabilities.environment_isolation,
            "process_containment": capabilities.process_containment,
            "safe_cancellation": capabilities.safe_cancellation,
            "test_only_uncontained": capabilities.test_only_uncontained,
            "cli_sha256": cli_sha256,
            "complete": capabilities.production_ready,
        }

    def runtime_capabilities(self) -> Mapping[str, bool]:
        capabilities = self.preflight()
        return {
            "cancel": capabilities.safe_cancellation,
            "safe_process_cancellation": capabilities.safe_cancellation,
            "process_containment": capabilities.process_containment,
            "environment_isolation": capabilities.environment_isolation,
        }

    def is_active(self, token: str) -> bool:
        return not self._get(token).task.done()

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
        containment = self._new_containment(env) if self._contained else None
        execution = _SdkExecution(
            queue=asyncio.Queue(),
            raw_events=[],
            outputs=[],
            started_at_ms=int(time.time() * 1000),
            task=asyncio.create_task(asyncio.sleep(0, result=0)),
            containment=containment,
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
            self._cleanup_containment(execution.containment)
            execution.containment = (
                self._new_containment(env) if self._contained else None
            )
            execution.sdk_connected = False
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
            await asyncio.shield(execution.task)
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
        returncode = 1
        terminal_event: dict[str, Any] | None = None
        cancelled = False
        try:
            try:
                returncode = await asyncio.wait_for(
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
                returncode = 124
                execution.error = (
                    "TimeoutError: Claude Agent SDK execution exceeded "
                    f"timeout_s={timeout_s}"
                )
                terminal_event = {
                    "type": "run.failed",
                    "error": execution.error,
                    "reason": "timeout",
                    "timeout_s": timeout_s,
                }
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                returncode = 1
                execution.error = f"{type(exc).__name__}: {exc}"
                terminal_event = {
                    "type": "run.failed",
                    "error": execution.error,
                }

            try:
                await self._finalize_containment(execution)
            except Exception as exc:
                execution.error = f"{type(exc).__name__}: {exc}"
                terminal_event = {
                    "type": "run.failed",
                    "error": execution.error,
                    "reason": "containment_proof_failed",
                }
                if cancelled:
                    execution.raw_events.append(terminal_event)
                    await execution.queue.put(terminal_event)
                    raise
                returncode = 1

            if cancelled:
                terminal_event = {"type": "run.cancelled"}
            elif returncode == 0 and terminal_event is None:
                terminal_event = {"type": "run.completed"}
            elif terminal_event is None:
                terminal_event = {
                    "type": "run.failed",
                    "error": execution.error,
                }
            execution.raw_events.append(terminal_event)
            await execution.queue.put(terminal_event)
            if cancelled:
                raise asyncio.CancelledError
            return returncode
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
        if self._sdk_types is None:
            raise ClaudeAgentSdkPreflightError(
                "Claude Agent SDK was not preflighted"
            )
        client_cls, options_cls = self._sdk_types
        option_environment = dict(env)
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
            "env": option_environment,
        }
        if execution.containment is not None:
            options_kwargs["cli_path"] = execution.containment.launcher_path
            options_kwargs["env"] = {
                **execution.containment.sensitive_env,
                _SDK_LAUNCH_CONFIG_ENV: str(
                    execution.containment.config_path
                ),
                CONTAINMENT_ENV_VAR: execution.containment.containment_id,
            }
        if metadata.get("max_budget_usd") is not None:
            options_kwargs["max_budget_usd"] = float(
                metadata["max_budget_usd"]
            )
        if resume_session_id:
            options_kwargs["resume"] = resume_session_id
        options = options_cls(**options_kwargs)
        async with client_cls(options=options) as client:
            execution.sdk_connected = True
            if execution.containment is not None:
                await self._attest_containment(execution.containment)
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
        return 0

    @property
    def _contained(self) -> bool:
        return self.preflight().production_ready

    def _new_containment(self, env: Mapping[str, str]) -> _SdkContainment:
        if self._explicit_cli_path is None:
            raise ClaudeAgentSdkPreflightError(
                "Claude CLI path was not resolved during preflight"
            )
        root = Path(
            tempfile.mkdtemp(prefix=_SDK_LAUNCH_ROOT_PREFIX)
        )
        root.chmod(0o700)
        launcher_path = root / "claude-sdk-launcher"
        config_path = root / "launch.json"
        attestation_path = root / "attestation.json"
        launcher_path.write_text(_LAUNCHER_SOURCE, encoding="utf-8")
        launcher_path.chmod(
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        )
        containment_id = new_containment_id()
        final_environment = {
            str(key): str(value)
            for key, value in env.items()
            if str(value)
        }
        final_environment[CONTAINMENT_ENV_VAR] = containment_id
        environment_sha256 = _environment_sha256(final_environment)
        sensitive_env = {
            key: value
            for key, value in final_environment.items()
            if key != CONTAINMENT_ENV_VAR
            and _is_sensitive_env_entry(key, value)
        }
        payload = {
            "schema_version": _SDK_LAUNCH_SCHEMA,
            "containment_id": containment_id,
            "executable": str(self._explicit_cli_path),
            "environment": {
                key: value
                for key, value in final_environment.items()
                if key != CONTAINMENT_ENV_VAR
                and key not in sensitive_env
            },
            "sensitive_env_keys": sorted(sensitive_env),
            "environment_sha256": environment_sha256,
            "attestation_path": str(attestation_path),
        }
        _write_private_json(config_path, payload)
        return _SdkContainment(
            containment_id=containment_id,
            root=root,
            launcher_path=launcher_path,
            config_path=config_path,
            attestation_path=attestation_path,
            environment_sha256=environment_sha256,
            sensitive_env=sensitive_env,
        )

    async def _attest_containment(
        self,
        containment: _SdkContainment,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                payload = json.loads(
                    containment.attestation_path.read_text(
                        encoding="utf-8"
                    )
                )
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                await asyncio.sleep(0.01)
                continue
            if (
                payload.get("schema_version") != _SDK_ATTESTATION_SCHEMA
                or payload.get("containment_id")
                != containment.containment_id
                or payload.get("environment_sha256")
                != containment.environment_sha256
            ):
                raise ClaudeAgentSdkContainmentError(
                    "Claude SDK launcher attestation did not match"
                )
            try:
                pid = int(payload["pid"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ClaudeAgentSdkContainmentError(
                    "Claude SDK launcher attestation omitted its pid"
                ) from exc
            containment.root_identity = process_identity(pid)
            containment.attested = True
            return
        raise ClaudeAgentSdkContainmentError(
            "Claude SDK launcher did not attest process containment"
        )

    async def _finalize_containment(
        self,
        execution: _SdkExecution,
    ) -> None:
        containment = execution.containment
        if containment is None:
            return
        await asyncio.to_thread(
            self._terminate_and_prove_empty,
            containment,
            execution.sdk_connected,
        )
        self._cleanup_containment(containment)

    def _terminate_and_prove_empty(
        self,
        containment: _SdkContainment,
        sdk_connected: bool,
    ) -> None:
        if sdk_connected and not containment.attested:
            raise ClaudeAgentSdkContainmentError(
                "Claude SDK connected without launcher attestation"
            )
        snapshot = self._containment_scanner(
            containment.containment_id,
            root_identity=containment.root_identity,
        )
        if not snapshot.scan_complete:
            raise ClaudeAgentSdkContainmentError(
                "Claude SDK containment scan was incomplete: "
                f"{snapshot.errors}"
            )
        if snapshot.processes:
            root = (
                containment.root_identity
                if containment.root_identity in snapshot.processes
                else snapshot.processes[0]
            )
            try:
                process_group_id = os.getpgid(root.pid)
            except OSError:
                process_group_id = root.pid
            termination = self._containment_terminator(
                root_pid=root.pid,
                expected_root_started_at=root.started_at,
                expected_process_group_id=process_group_id,
                containment_id=containment.containment_id,
                term_timeout_s=5.0,
                kill_timeout_s=5.0,
            )
            if not bool(termination.get("safe_to_finalize")):
                raise ClaudeAgentSdkContainmentError(
                    "Claude SDK process tree could not be safely reaped: "
                    f"{termination}"
                )
        final = self._containment_scanner(
            containment.containment_id,
            root_identity=containment.root_identity,
        )
        if not final.scan_complete or final.processes:
            raise ClaudeAgentSdkContainmentError(
                "Claude SDK process tree is not provably empty: "
                f"{final}"
            )

    @staticmethod
    def _cleanup_containment(
        containment: _SdkContainment | None,
    ) -> None:
        if containment is None:
            return
        shutil.rmtree(containment.root, ignore_errors=True)

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
        except ClaudeAgentSdkContainmentError:
            raise
        except BaseException:
            return
    if task.cancelled():
        return
    try:
        task.result()
    except ClaudeAgentSdkContainmentError:
        raise
    except BaseException:
        return


def _validate_sdk_types(client_cls: Any, options_cls: Any) -> None:
    required_option_fields = {
        "system_prompt",
        "model",
        "max_turns",
        "mcp_servers",
        "allowed_tools",
        "disallowed_tools",
        "permission_mode",
        "effort",
        "cwd",
        "env",
        "resume",
        "cli_path",
    }
    try:
        signature = inspect.signature(options_cls)
    except (TypeError, ValueError) as exc:
        raise ClaudeAgentSdkPreflightError(
            "ClaudeAgentOptions signature is not inspectable"
        ) from exc
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if not accepts_kwargs:
        missing = sorted(
            required_option_fields - set(signature.parameters)
        )
        if missing:
            raise ClaudeAgentSdkPreflightError(
                "ClaudeAgentOptions lacks required runtime capabilities: "
                + ", ".join(missing)
            )
    for name in (
        "__aenter__",
        "__aexit__",
        "query",
        "receive_response",
    ):
        if not callable(getattr(client_cls, name, None)):
            raise ClaudeAgentSdkPreflightError(
                f"ClaudeSDKClient lacks required capability: {name}"
            )


def _resolve_claude_cli_path(
    *,
    explicit: Path | None,
    options_cls: Any,
) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    located = shutil.which("claude")
    if located:
        candidates.append(Path(located))
    try:
        module = sys.modules.get(str(options_cls.__module__))
        module_file = Path(str(getattr(module, "__file__", ""))).resolve()
        package_root = module_file.parent
        candidates.append(package_root / "_bundled" / "claude")
    except (OSError, ValueError):
        pass
    for candidate in candidates:
        path = candidate.expanduser().resolve(strict=False)
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise ClaudeAgentSdkPreflightError(
        "Claude Agent SDK preflight could not resolve an executable Claude CLI"
    )


def _sdk_version(client_cls: Any) -> str:
    module_name = str(getattr(client_cls, "__module__", "")).split(".", 1)[0]
    if not module_name:
        return ""
    try:
        module = __import__(module_name)
    except Exception:
        return ""
    return str(getattr(module, "__version__", "") or "")


def _environment_sha256(environment: Mapping[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            {str(key): str(value) for key, value in environment.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        stat.S_IRUSR | stat.S_IWUSR,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                dict(payload),
                stream,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _scrub_stale_launch_roots(
    *,
    now: float | None = None,
    max_age_s: float = _SDK_STALE_LAUNCH_ROOT_AGE_S,
) -> None:
    current = time.time() if now is None else float(now)
    try:
        temp_root = Path(tempfile.gettempdir())
        entries = list(temp_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(_SDK_LAUNCH_ROOT_PREFIX):
            continue
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
            if current - entry.stat().st_mtime < max_age_s:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)


def _preflight_launcher(cli_path: Path) -> None:
    root = Path(tempfile.mkdtemp(prefix="codex-supervisor-sdk-preflight-"))
    try:
        root.chmod(0o700)
        launcher = root / "launcher"
        launcher.write_text(_LAUNCHER_SOURCE, encoding="utf-8")
        launcher.chmod(
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        )
        attestation = root / "attestation.json"
        containment_id = new_containment_id()
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            CONTAINMENT_ENV_VAR: containment_id,
        }
        config = root / "launch.json"
        _write_private_json(
            config,
            {
                "schema_version": _SDK_LAUNCH_SCHEMA,
                "containment_id": containment_id,
                "executable": "/usr/bin/true",
                "environment": {
                    key: value
                    for key, value in environment.items()
                    if key != CONTAINMENT_ENV_VAR
                },
                "environment_sha256": _environment_sha256(environment),
                "attestation_path": str(attestation),
            },
        )
        completed = subprocess.run(
            [str(launcher)],
            env={_SDK_LAUNCH_CONFIG_ENV: str(config)},
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        if completed.returncode != 0 or not attestation.is_file():
            raise ClaudeAgentSdkPreflightError(
                "Claude SDK containment launcher self-test failed: "
                f"{completed.stderr.strip()}"
            )
        payload = json.loads(attestation.read_text(encoding="utf-8"))
        if (
            payload.get("schema_version") != _SDK_ATTESTATION_SCHEMA
            or payload.get("containment_id") != containment_id
            or payload.get("environment_sha256")
            != _environment_sha256(environment)
        ):
            raise ClaudeAgentSdkPreflightError(
                "Claude SDK containment launcher self-test did not attest"
            )
        if not cli_path.is_file():
            raise ClaudeAgentSdkPreflightError(
                "Claude SDK CLI disappeared during preflight"
            )
    except ClaudeAgentSdkPreflightError:
        raise
    except Exception as exc:
        raise ClaudeAgentSdkPreflightError(
            "Claude SDK containment launcher self-test failed"
        ) from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
