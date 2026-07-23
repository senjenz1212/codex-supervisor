from __future__ import annotations

import asyncio
import ast
import contextlib
import hashlib
import json
import logging
import os
import signal
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psutil
import pytest

from supervisor import agent_runtime as agent_runtime_module
from supervisor import process_containment as process_containment_module
from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    ClaudeCodeRuntime,
    CommandAgentRuntime,
    CodexRuntime,
    PiRuntime,
    RuntimeCapabilityEvidence,
    RuntimeTransportResult,
    SubprocessRuntimeTransport,
    normalize_runtime_event,
)
from supervisor.runtime_cleanup import cancel_runtime_after_failure
from supervisor.runtime_execution import runtime_task_runner
from supervisor.task_environment import default_task_platform


_REAL_OS_KILL = os.kill
_REAL_OS_KILLPG = os.killpg
_REAL_PSUTIL_PIDS = psutil.pids
_REAL_PSUTIL_PROCESS = psutil.Process


class _TaggedRuntimeCleanup:
    def __init__(self, request: pytest.FixtureRequest) -> None:
        self._containment_ids: set[str] = set()
        self._known_identities: dict[int, float] = {}
        self._known_groups: set[int] = set()
        self._inspection_failures: set[str] = set()
        request.addfinalizer(self._finalize)

    def register(self, containment_id: str) -> None:
        normalized = str(containment_id).strip()
        assert normalized
        self._containment_ids.add(normalized)

    def watch_pid(
        self,
        pid: int,
        *,
        pgid: int | None = None,
    ) -> None:
        pid = int(pid)
        if pgid is not None:
            self._known_groups.add(int(pgid))
        try:
            self._known_identities[pid] = float(
                _REAL_PSUTIL_PROCESS(pid).create_time()
            )
        except psutil.AccessDenied:
            self._inspection_failures.add(f"access_denied:{pid}")
        except (
            OSError,
            ValueError,
            psutil.NoSuchProcess,
            psutil.ZombieProcess,
            ProcessLookupError,
        ):
            pass

    def snapshot(
        self,
        containment_id: str | None = None,
    ) -> tuple[process_containment_module.ProcessIdentity, ...]:
        ids = (
            {str(containment_id)}
            if containment_id is not None
            else self._containment_ids
        )
        found: dict[
            tuple[int, float],
            process_containment_module.ProcessIdentity,
        ] = {}
        for pid in _REAL_PSUTIL_PIDS():
            if pid == os.getpid():
                continue
            try:
                process = _REAL_PSUTIL_PROCESS(pid)
                started_at = float(process.create_time())
                if (
                    not process.is_running()
                    or process.status() == psutil.STATUS_ZOMBIE
                ):
                    continue
                marker = str(
                    process.environ().get(
                        process_containment_module.CONTAINMENT_ENV_VAR,
                        "",
                    )
                )
            except psutil.AccessDenied:
                if pid in self._known_identities:
                    self._inspection_failures.add(
                        f"access_denied:{pid}"
                    )
                    started_at = self._known_identities[pid]
                    found[(pid, started_at)] = (
                        process_containment_module.ProcessIdentity(
                            pid=pid,
                            started_at=started_at,
                        )
                    )
                continue
            except (
                OSError,
                ValueError,
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
                ProcessLookupError,
            ):
                continue
            if marker in ids:
                identity = process_containment_module.ProcessIdentity(
                    pid=pid,
                    started_at=started_at,
                )
                found[(identity.pid, identity.started_at)] = identity
        for pid, expected_started_at in self._known_identities.items():
            if (pid, expected_started_at) in found:
                continue
            try:
                process = _REAL_PSUTIL_PROCESS(pid)
                observed_started_at = float(process.create_time())
                if abs(observed_started_at - expected_started_at) > 0.001:
                    continue
                if (
                    process.is_running()
                    and process.status() != psutil.STATUS_ZOMBIE
                ):
                    found[(pid, expected_started_at)] = (
                        process_containment_module.ProcessIdentity(
                            pid=pid,
                            started_at=expected_started_at,
                        )
                    )
            except psutil.AccessDenied:
                self._inspection_failures.add(f"access_denied:{pid}")
                found[(pid, expected_started_at)] = (
                    process_containment_module.ProcessIdentity(
                        pid=pid,
                        started_at=expected_started_at,
                    )
                )
            except (
                OSError,
                ValueError,
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
                ProcessLookupError,
            ):
                continue
        return tuple(sorted(found.values()))

    def cleanup(self) -> tuple[process_containment_module.ProcessIdentity, ...]:
        for pgid in sorted(self._known_groups):
            if pgid <= 0 or pgid == os.getpgrp():
                continue
            try:
                _REAL_OS_KILLPG(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        for pid, expected_started_at in self._known_identities.items():
            try:
                process = _REAL_PSUTIL_PROCESS(pid)
                if abs(
                    float(process.create_time()) - expected_started_at
                ) <= 0.001:
                    _REAL_OS_KILL(pid, signal.SIGKILL)
            except psutil.AccessDenied:
                self._inspection_failures.add(f"access_denied:{pid}")
            except (
                OSError,
                ValueError,
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
                ProcessLookupError,
            ):
                pass
        deadline = time.monotonic() + 3
        quiet_scans = 0
        survivors: tuple[
            process_containment_module.ProcessIdentity,
            ...,
        ] = ()
        while time.monotonic() < deadline:
            survivors = self.snapshot()
            if not survivors:
                quiet_scans += 1
                if quiet_scans >= 2:
                    break
            else:
                quiet_scans = 0
                for identity in survivors:
                    try:
                        process = _REAL_PSUTIL_PROCESS(identity.pid)
                        if abs(
                            float(process.create_time())
                            - identity.started_at
                        ) <= 0.001:
                            _REAL_OS_KILL(identity.pid, signal.SIGKILL)
                    except psutil.AccessDenied:
                        self._inspection_failures.add(
                            f"access_denied:{identity.pid}"
                        )
                    except (
                        OSError,
                        psutil.NoSuchProcess,
                        psutil.ZombieProcess,
                        ProcessLookupError,
                    ):
                        pass
            time.sleep(0.02)
        return self.snapshot()

    def _finalize(self) -> None:
        survivors = self.cleanup()
        assert self._inspection_failures == set(), (
            "access denied while inspecting known descendants: "
            f"{sorted(self._inspection_failures)!r}"
        )
        assert survivors == (), (
            "tagged runtime survivors after fallback cleanup: "
            f"{survivors!r}"
        )


@pytest.fixture
def tagged_runtime_cleanup(
    request: pytest.FixtureRequest,
) -> _TaggedRuntimeCleanup:
    return _TaggedRuntimeCleanup(request)


def test_runtime_leak_helper_retains_access_denied_known_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRequest:
        @staticmethod
        def addfinalizer(_finalizer: object) -> None:
            return None

    cleanup = _TaggedRuntimeCleanup(FakeRequest())  # type: ignore[arg-type]
    cleanup._known_identities[50019] = 200.0
    monkeypatch.setattr(
        sys.modules[__name__],
        "_REAL_PSUTIL_PIDS",
        lambda: [50019],
    )

    def denied_process(pid: int) -> object:
        raise psutil.AccessDenied(pid=pid)

    monkeypatch.setattr(
        sys.modules[__name__],
        "_REAL_PSUTIL_PROCESS",
        denied_process,
    )

    survivors = cleanup.snapshot()

    assert survivors == (
        process_containment_module.ProcessIdentity(
            pid=50019,
            started_at=200.0,
        ),
    )
    assert cleanup._inspection_failures == {"access_denied:50019"}


def test_experiment_and_task_core_do_not_import_provider_sdks() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    core_modules = (
        "supervisor/experiment_kernel.py",
        "supervisor/arm_executor.py",
        "supervisor/task_environment.py",
        "supervisor/harness_tracer.py",
        "supervisor/pilot_readiness.py",
        "supervisor/claim_gate.py",
        "supervisor/grade_revisions.py",
        "supervisor/trace_graph.py",
        "supervisor/evidence_committer.py",
    )
    forbidden_roots = {
        "anthropic",
        "claude_agent_sdk",
        "cursor",
        "cursor_agent_sdk",
        "litellm",
        "openai",
    }
    violations: list[str] = []
    for relative_path in core_modules:
        path = repo_root / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for module in imported:
                if module.split(".", 1)[0] in forbidden_roots:
                    violations.append(
                        f"{relative_path}:{node.lineno} imports {module}"
                    )
    assert violations == []


def test_agent_tasks_default_to_no_environment_inheritance(tmp_path: Path) -> None:
    task = AgentTask(
        task_id="isolated-default",
        instruction="Run",
        cwd=tmp_path,
        model="test-model",
    )

    assert task.inherit_env is False


def test_task_complete_is_turn_terminal_not_run_terminal() -> None:
    assert normalize_runtime_event(
        {"type": "task_complete"}
    ).kind == "turn.completed"


class RecordingTransport:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.resumed: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._events: dict[str, list[dict[str, Any]]] = {}
        self.filesystem_isolation_supported = False

    def supports_filesystem_isolation(self, mode: str) -> bool:
        return (
            mode == "workspace_only"
            and self.filesystem_isolation_supported
        )

    async def start(
        self,
        *,
        run_id: str,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout_s: int,
        metadata: dict[str, Any],
    ) -> str:
        self.started.append(
            {
                "run_id": run_id,
                "argv": argv,
                "cwd": cwd,
                "env": env,
                "timeout_s": timeout_s,
                "metadata": metadata,
            }
        )
        self._events[run_id] = [
            {"type": "run.started", "session_id": f"session-{run_id}"},
            {
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "message": "done",
                },
            },
            {"type": "run.completed"},
        ]
        return run_id

    async def resume(
        self,
        token: str,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        timeout_s: int,
        metadata: dict[str, Any],
    ) -> None:
        self.resumed.append(
            {
                "token": token,
                "argv": argv,
                "cwd": cwd,
                "env": env,
                "timeout_s": timeout_s,
                "metadata": metadata,
            }
        )

    async def cancel(self, token: str) -> None:
        self.cancelled.append(token)

    async def stream(self, token: str) -> AsyncIterator[dict[str, Any]]:
        for event in self._events[token]:
            await asyncio.sleep(0)
            yield event

    async def collect(self, token: str) -> RuntimeTransportResult:
        return RuntimeTransportResult(
            returncode=0,
            stdout='{"type":"run.completed"}\n',
            stderr="",
            raw_events=tuple(self._events[token]),
            started_at_ms=10,
            ended_at_ms=20,
            cost_usd=0.125,
            resolved_model="served-model-v2",
            token_usage={
                "input_tokens": 11,
                "output_tokens": 7,
                "tokens_in": 11,
                "tokens_out": 7,
            },
            model_provenance="recording_transport.result",
            cost_provenance="recording_transport.result",
            token_provenance="recording_transport.result",
        )


class TerminalRecordingTransport(RecordingTransport):
    def is_active(self, token: str) -> bool:
        return False


class ManifestRecordingTransport(RecordingTransport):
    def __init__(self, *, endpoint: str) -> None:
        super().__init__()
        self.endpoint = endpoint

    def identity_manifest(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "protocol": "test-transport/v1",
            "complete": True,
        }


def test_runtime_manifest_pins_provider_binary_transport_and_tools(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "claude"
    binary.write_bytes(b"fake-claude-binary")
    binary.chmod(0o755)
    runtime = ClaudeCodeRuntime(
        transport=ManifestRecordingTransport(endpoint="direct"),
        binary=str(binary),
    )

    manifest = runtime.runtime_manifest(
        AgentTask(
            task_id="manifest",
            instruction="Run",
            cwd=tmp_path,
            model="claude-test-exact",
            metadata={"tools": ["Read", "Edit"]},
        )
    )

    assert manifest["complete"] is True
    assert manifest["provider_route"]["route_kind"] == "anthropic_direct"
    assert manifest["binary"]["sha256"]
    assert manifest["transport"]["configuration"]["endpoint"] == "direct"
    assert manifest["tools"] == ["Read", "Edit"]
    assert len(manifest["manifest_sha256"]) == 64


def test_runtime_manifest_hashes_final_symlink_target_and_records_both_paths(
    tmp_path: Path,
) -> None:
    target = tmp_path / "versions" / "claude-v1"
    target.parent.mkdir()
    target.write_bytes(b"fake-claude-target")
    target.chmod(0o755)
    invoked = tmp_path / "bin" / "claude"
    invoked.parent.mkdir()
    invoked.symlink_to(target)
    runtime = ClaudeCodeRuntime(
        transport=ManifestRecordingTransport(endpoint="direct"),
        binary=str(invoked),
    )

    manifest = runtime.runtime_manifest(
        AgentTask(
            task_id="manifest-symlink",
            instruction="Run",
            cwd=tmp_path,
            model="claude-test-exact",
            metadata={"tools": ["Read"]},
        )
    )

    binary = manifest["binary"]
    assert manifest["complete"] is True
    assert binary["invoked_path"] == str(invoked.absolute())
    assert binary["resolved_target"] == str(target.resolve())
    assert binary["resolved_path"] == str(target.resolve())
    assert binary["sha256"] == hashlib.sha256(
        target.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize(
    ("kind", "expected_error"),
    (
        ("broken", "binary_target_missing"),
        ("unreadable", "binary_target_unreadable"),
        ("relative_escape", "binary_target_escapes_invocation_root"),
    ),
)
def test_runtime_manifest_rejects_unsafe_executable_targets(
    tmp_path: Path,
    kind: str,
    expected_error: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    binary: Path | str
    cleanup_target: Path | None = None
    if kind == "broken":
        binary = tmp_path / "broken-claude"
        binary.symlink_to(tmp_path / "missing-claude")
    elif kind == "unreadable":
        binary = tmp_path / "unreadable-claude"
        binary.write_bytes(b"unreadable")
        binary.chmod(0o111)
        cleanup_target = binary
    else:
        outside = tmp_path / "outside-claude"
        outside.write_bytes(b"outside")
        outside.chmod(0o755)
        (workspace / "claude").symlink_to(outside)
        binary = "./claude"
    runtime = ClaudeCodeRuntime(
        transport=ManifestRecordingTransport(endpoint="direct"),
        binary=str(binary),
    )

    try:
        manifest = runtime.runtime_manifest(
            AgentTask(
                task_id=f"manifest-{kind}",
                instruction="Run",
                cwd=workspace,
                model="claude-test-exact",
                metadata={"tools": ["Read"]},
            )
        )
    finally:
        if cleanup_target is not None:
            cleanup_target.chmod(0o755)

    assert manifest["complete"] is False
    assert manifest["binary"]["error"] == expected_error
    assert manifest["binary"]["sha256"] == ""


def test_runtime_manifest_distinguishes_hidden_transport_routes(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codex"
    binary.write_bytes(b"fake-codex-binary")
    binary.chmod(0o755)
    task = AgentTask(
        task_id="manifest-route",
        instruction="Run",
        cwd=tmp_path,
        model="gpt-test-exact",
        metadata={
            "tools": ["shell"],
            "provider_route": {
                "provider": "openai",
                "route_kind": "gateway",
                "endpoint": "https://gateway.example/v1",
                "configuration_sha256": "a" * 64,
            },
        },
    )
    first = CodexRuntime(
        transport=ManifestRecordingTransport(endpoint="route-a"),
        binary=str(binary),
    ).runtime_manifest(task)
    second = CodexRuntime(
        transport=ManifestRecordingTransport(endpoint="route-b"),
        binary=str(binary),
    ).runtime_manifest(task)

    assert first["manifest_sha256"] != second["manifest_sha256"]


def test_runtime_manifest_rejects_transport_secrets(tmp_path: Path) -> None:
    class SecretTransport(ManifestRecordingTransport):
        def identity_manifest(self) -> dict[str, Any]:
            return {
                "endpoint": "direct",
                "api_key": "must-not-enter-manifest",
            }

    runtime = ClaudeCodeRuntime(
        transport=SecretTransport(endpoint="direct"),
        binary=str(tmp_path / "missing-claude"),
    )

    with pytest.raises(ValueError, match="must not contain secret field"):
        runtime.runtime_manifest(
            AgentTask(
                task_id="manifest-secret",
                instruction="Run",
                cwd=tmp_path,
                model="claude-test-exact",
                metadata={"tools": ["Read"]},
            )
        )


@pytest.mark.asyncio
async def test_runtime_retains_terminal_run_until_successful_collect(
    tmp_path: Path,
) -> None:
    runtime = CodexRuntime(transport=TerminalRecordingTransport())
    handles = [
        await runtime.start(
            AgentTask(
                task_id=f"retained-terminal-{index}",
                instruction="Run",
                cwd=tmp_path,
                model="gpt-test",
            )
        )
        for index in range(
            agent_runtime_module._MAX_RETAINED_TERMINAL_RUNS + 2
        )
    ]

    result = await runtime.collect(handles[0])

    assert result.status == "completed"
    assert result.task_id == "retained-terminal-0"


@pytest.mark.asyncio
async def test_runtime_resume_requires_new_collect_before_eviction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_runtime_module,
        "_MAX_RETAINED_TERMINAL_RUNS",
        1,
    )
    runtime = CodexRuntime(transport=TerminalRecordingTransport())
    resumed = await runtime.start(
        AgentTask(
            task_id="resumed-retention",
            instruction="Run",
            cwd=tmp_path,
            model="gpt-test",
        )
    )
    await runtime.collect(resumed)
    await runtime.resume(resumed, "Continue")
    for index in range(2):
        await runtime.start(
            AgentTask(
                task_id=f"retention-pressure-{index}",
                instruction="Run",
                cwd=tmp_path,
                model="gpt-test",
            )
        )

    result = await runtime.collect(resumed)

    assert result.task_id == "resumed-retention"


@pytest.mark.asyncio
async def test_runtime_evicts_collected_terminal_run_at_retention_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_runtime_module,
        "_MAX_RETAINED_TERMINAL_RUNS",
        1,
    )
    runtime = CodexRuntime(transport=TerminalRecordingTransport())
    collected = await runtime.start(
        AgentTask(
            task_id="collected-terminal",
            instruction="Run",
            cwd=tmp_path,
            model="gpt-test",
        )
    )
    await runtime.collect(collected)

    await runtime.start(
        AgentTask(
            task_id="replacement-run",
            instruction="Run",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    with pytest.raises(KeyError, match="unknown runtime handle"):
        await runtime.collect(collected)


class GenerationRecordingTransport(RecordingTransport):
    def __init__(
        self,
        *,
        generations: list[list[dict[str, Any]]],
        returncodes: list[int],
    ) -> None:
        super().__init__()
        self._generations = generations
        self._returncodes = returncodes
        self._generation_index: dict[str, int] = {}
        self._stream_offsets: dict[str, int] = {}

    async def start(self, **kwargs: Any) -> str:
        token = await super().start(**kwargs)
        self._events[token] = list(self._generations[0])
        self._generation_index[token] = 0
        self._stream_offsets[token] = 0
        return token

    async def resume(self, token: str, **kwargs: Any) -> None:
        await super().resume(token, **kwargs)
        generation_index = self._generation_index[token] + 1
        self._generation_index[token] = generation_index
        self._stream_offsets[token] = len(self._events[token])
        self._events[token].extend(self._generations[generation_index])

    async def stream(self, token: str) -> AsyncIterator[dict[str, Any]]:
        for event in self._events[token][self._stream_offsets[token] :]:
            await asyncio.sleep(0)
            yield event

    async def collect(self, token: str) -> RuntimeTransportResult:
        result = await super().collect(token)
        return RuntimeTransportResult(
            returncode=self._returncodes[self._generation_index[token]],
            stdout=result.stdout,
            stderr=result.stderr,
            raw_events=result.raw_events,
            started_at_ms=result.started_at_ms,
            ended_at_ms=result.ended_at_ms,
            cost_usd=result.cost_usd,
            resolved_model=result.resolved_model,
            token_usage=result.token_usage,
            model_provenance=result.model_provenance,
            cost_provenance=result.cost_provenance,
            token_provenance=result.token_provenance,
            metadata=result.metadata,
        )


def _generation(
    message: str,
    terminal: str | None,
    *,
    session_id: str = "",
) -> list[dict[str, Any]]:
    started: dict[str, Any] = {"type": "run.started"}
    if session_id:
        started["session_id"] = session_id
    events = [
        started,
        {
            "type": "response_item",
            "payload": {
                "type": "agent_message",
                "message": message,
            },
        },
    ]
    if terminal is not None:
        events.append({"type": terminal})
    return events


@pytest.mark.asyncio
async def test_blocking_runtime_runner_works_inside_an_active_event_loop(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runner = runtime_task_runner(
        lambda: CodexRuntime(transport=transport)
    )

    execution = runner(
        AgentTask(
            task_id="sync-bridge",
            instruction="Reply.",
            cwd=tmp_path,
            model="test-model",
        )
    )

    assert execution.result.status == "completed"
    assert execution.result.output == "done"
    assert execution.handle.runtime == "codex"
    assert [event.kind for event in execution.events] == [
        "run.started",
        "agent.message",
        "run.completed",
    ]


@pytest.mark.asyncio
async def test_runtime_cleanup_failure_does_not_mask_caller_cancellation() -> None:
    class FailingCancellationRuntime:
        kind = "failing-cancellation"

        async def cancel(self, handle: AgentRunHandle) -> None:
            raise RuntimeError("cleanup failed")

    handle = AgentRunHandle(
        run_id="cleanup-run",
        task_id="cleanup-task",
        runtime="failing-cancellation",
        session_id="cleanup-session",
        capabilities={},
    )

    async def blocked() -> None:
        try:
            await asyncio.Future()
        except BaseException:
            await cancel_runtime_after_failure(
                FailingCancellationRuntime(),
                handle,
                logger=logging.getLogger(__name__),
            )
            raise

    task = asyncio.create_task(blocked())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runtime_cleanup_deadline_returns_unconfirmed_without_abandoning_reaper(
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class StuckCancellationRuntime:
        kind = "stuck-cancellation"

        async def cancel(self, handle: AgentRunHandle) -> None:
            started.set()
            await release.wait()
            completed.set()

    handle = AgentRunHandle(
        run_id="cleanup-deadline-run",
        task_id="cleanup-deadline-task",
        runtime="stuck-cancellation",
        session_id="cleanup-deadline-session",
        capabilities={},
    )
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    result = await cancel_runtime_after_failure(
        StuckCancellationRuntime(),
        handle,
        logger=logging.getLogger(__name__),
        deadline_s=0.01,
    )

    assert loop.time() - started_at < 0.5
    assert result.confirmed is False
    assert result.reason == "cleanup_deadline_exceeded"
    assert started.is_set()
    assert completed.is_set() is False

    release.set()
    await asyncio.wait_for(completed.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_runtime_cleanup_reports_confirmed_only_after_cancel_finishes(
) -> None:
    completed = asyncio.Event()

    class ConfirmedCancellationRuntime:
        kind = "confirmed-cancellation"

        async def cancel(self, handle: AgentRunHandle) -> None:
            await asyncio.sleep(0)
            completed.set()

    handle = AgentRunHandle(
        run_id="cleanup-confirmed-run",
        task_id="cleanup-confirmed-task",
        runtime="confirmed-cancellation",
        session_id="cleanup-confirmed-session",
        capabilities={},
    )

    result = await cancel_runtime_after_failure(
        ConfirmedCancellationRuntime(),
        handle,
        logger=logging.getLogger(__name__),
        deadline_s=1,
    )

    assert completed.is_set()
    assert result.confirmed is True
    assert result.reason == "containment_reap_confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_cls", "binary"),
    ((ClaudeCodeRuntime, "claude"), (CodexRuntime, "codex")),
)
async def test_provider_runtimes_return_the_same_target_independent_result_schema(
    tmp_path: Path,
    runtime_cls: type[ClaudeCodeRuntime] | type[CodexRuntime],
    binary: str,
) -> None:
    transport = RecordingTransport()
    runtime = runtime_cls(transport=transport)
    task = AgentTask(
        task_id="task-1",
        instruction="Fix the public behavior.",
        cwd=tmp_path,
        model="test-model",
        timeout_s=30,
    )

    handle = await runtime.start(task)
    events = [event async for event in runtime.stream(handle)]
    result = await runtime.collect(handle)

    assert transport.started[0]["argv"][0] == binary
    if runtime_cls is ClaudeCodeRuntime:
        assert "--verbose" in transport.started[0]["argv"]
    assert [event.kind for event in events] == [
        "run.started",
        "agent.message",
        "run.completed",
    ]
    assert isinstance(result, AgentRunResult)
    assert result.schema_version == "supervisor-agent-run-result/v1"
    assert result.task_id == "task-1"
    assert result.status == "completed"
    assert result.resolved_model == "served-model-v2"
    assert result.cost_usd == 0.125
    assert result.token_usage["tokens_in"] == 11
    assert result.model_provenance == "recording_transport.result"
    assert result.cost_provenance == "recording_transport.result"
    assert result.token_provenance == "recording_transport.result"
    assert result.result_hash
    assert result.to_dict().keys() == {
        "schema_version",
        "run_id",
        "task_id",
        "runtime",
        "session_id",
        "status",
        "output",
        "events",
        "started_at_ms",
        "ended_at_ms",
        "duration_ms",
        "cost_usd",
        "token_usage",
        "resolved_model",
        "model_provenance",
        "cost_provenance",
        "token_provenance",
        "result_hash",
        "metadata",
    }


@pytest.mark.asyncio
async def test_codex_runtime_normalizes_captured_live_jsonl_and_marks_missing_provenance(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = tmp_path / "captured-codex"
    script.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"type":"thread.started","thread_id":"thread-live-1"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"id":"item_0",'
        '"type":"agent_message","text":"OK"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":17,'
        '"cached_input_tokens":3,"output_tokens":5,'
        '"reasoning_output_tokens":0}}\n'
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    runtime = CodexRuntime(binary=str(script))
    handle = await runtime.start(
        AgentTask(
            task_id="codex-live-jsonl",
            instruction="reply",
            cwd=repo,
            model="requested-model-alias",
            timeout_s=30,
        )
    )

    events = [event async for event in runtime.stream(handle)]
    result = await runtime.collect(handle)

    assert [event.kind for event in events] == [
        "run.started",
        "turn.started",
        "agent.message",
        "turn.completed",
    ]
    assert result.status == "completed"
    assert result.output == "OK"
    assert result.session_id == "thread-live-1"
    assert result.token_usage["tokens_in"] == 20
    assert result.token_usage["tokens_out"] == 5
    assert result.resolved_model == ""
    assert result.model_provenance == ""
    assert result.cost_provenance == ""
    assert result.metadata["provenance_support"] == {
        "resolved_model": False,
        "cost_usd": False,
        "token_usage": True,
    }
    assert result.metadata["provenance_blockers"] == [
        "resolved_model",
        "cost_provenance",
    ]


@pytest.mark.asyncio
async def test_runtime_resume_and_cancel_stay_behind_the_runtime_seam(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = CodexRuntime(transport=transport)
    handle = await runtime.start(
        AgentTask(
            task_id="task-2",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    await runtime.resume(handle, "Continue from the saved session.")
    await runtime.cancel(handle)

    assert transport.resumed[0]["token"] == handle.run_id
    assert "resume" in transport.resumed[0]["argv"]
    assert transport.cancelled == [handle.run_id]


@pytest.mark.asyncio
async def test_claude_cli_runtime_keeps_sessions_resumable_and_applies_controls(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = ClaudeCodeRuntime(transport=transport)
    handle = await runtime.start(
        AgentTask(
            task_id="claude-controls",
            instruction="Start",
            cwd=tmp_path,
            model="claude-test",
            metadata={
                "system_prompt": "Stay inside the task.",
                "allowed_tools": ["Read", "Edit"],
                "disallowed_tools": ["WebFetch"],
                "permission_mode": "dontAsk",
                "effort": "high",
            },
        )
    )

    start_argv = transport.started[0]["argv"]
    assert "--no-session-persistence" not in start_argv
    assert start_argv[start_argv.index("--system-prompt") + 1] == (
        "Stay inside the task."
    )
    assert start_argv[start_argv.index("--allowed-tools") + 1] == "Read,Edit"
    assert start_argv[start_argv.index("--disallowed-tools") + 1] == "WebFetch"
    assert start_argv[start_argv.index("--permission-mode") + 1] == "dontAsk"
    assert start_argv[start_argv.index("--effort") + 1] == "high"

    _ = [event async for event in runtime.stream(handle)]
    await runtime.collect(handle)
    await runtime.resume(handle, "Continue")
    resume_argv = transport.resumed[0]["argv"]
    assert "--no-session-persistence" not in resume_argv
    assert resume_argv[resume_argv.index("--resume") + 1] == (
        f"session-{handle.run_id}"
    )


@pytest.mark.asyncio
async def test_claude_cli_rejects_untyped_extra_args(
    tmp_path: Path,
) -> None:
    runtime = ClaudeCodeRuntime(transport=RecordingTransport())

    with pytest.raises(
        ValueError,
        match="extra_args are unsupported",
    ):
        await runtime.start(
            AgentTask(
                task_id="claude-extra-args",
                instruction="Start",
                cwd=tmp_path,
                model="claude-test",
                metadata={"extra_args": ["--debug"]},
            )
        )


@pytest.mark.asyncio
async def test_runtime_rejects_caller_supplied_backend_attestation(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = CodexRuntime(transport=transport)

    with pytest.raises(
        ValueError,
        match="backend attestation metadata is transport-owned",
    ):
        await runtime.start(
            AgentTask(
                task_id="forged-attestation",
                instruction="Start",
                cwd=tmp_path,
                model="gpt-test",
                metadata={
                    "result_metadata": {
                        "execution_environment_attestation": {
                            "mode": "operational",
                            "enforced": True,
                        }
                    }
                },
            )
        )

    assert transport.started == []


@pytest.mark.asyncio
async def test_oversized_runtime_stream_line_fails_explicitly() -> None:
    stream = asyncio.StreamReader(limit=8)
    stream.feed_data(b"x" * 32 + b"\n")
    stream.feed_eof()

    with pytest.raises(
        RuntimeError,
        match="stream-json line exceeds",
    ):
        await agent_runtime_module._read_stream_line(stream)


@pytest.mark.asyncio
async def test_real_oversized_subprocess_line_fails_before_runtime_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_runtime_cleanup: _TaggedRuntimeCleanup,
) -> None:
    monkeypatch.setattr(
        agent_runtime_module,
        "_SUBPROCESS_STREAM_LIMIT",
        64 * 1024,
    )
    containment_id = process_containment_module.new_containment_id()
    tagged_runtime_cleanup.register(containment_id)
    monkeypatch.setattr(
        agent_runtime_module,
        "new_containment_id",
        lambda: containment_id,
    )
    transport = SubprocessRuntimeTransport()
    started_at = asyncio.get_running_loop().time()
    child_path = tmp_path / "oversized-child.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import subprocess
import sys
import time
from pathlib import Path

subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not Path(sys.argv[2]).exists():
    time.sleep(0.01)
sys.stdout.write("x" * int(sys.argv[3]) + "\\n")
sys.stdout.flush()
time.sleep(30)
"""
    token = await transport.start(
        run_id="oversized-real-subprocess",
        argv=(
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(child_path),
            str(agent_runtime_module._SUBPROCESS_STREAM_LIMIT + 1),
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=30,
        metadata={},
    )
    root_pid = transport._get(token).process.pid
    tagged_runtime_cleanup.watch_pid(root_pid, pgid=root_pid)
    child_pid = 0

    try:
        expected_error = "stream-json line exceeds"
        with pytest.raises(
            RuntimeError,
            match=expected_error,
        ):
            _ = [
                event
                async for event in transport.stream(token)
            ]
        with pytest.raises(
            RuntimeError,
            match=expected_error,
        ):
            await asyncio.wait_for(transport.collect(token), timeout=10)

        child_pid = int(
            json.loads(child_path.read_text(encoding="utf-8"))["pid"]
        )
        tagged_runtime_cleanup.watch_pid(child_pid, pgid=child_pid)
        product_survivors = tagged_runtime_cleanup.snapshot(containment_id)
        assert asyncio.get_running_loop().time() - started_at < 10
        if psutil.pid_exists(root_pid):
            assert psutil.Process(root_pid).status() == psutil.STATUS_ZOMBIE
        assert not psutil.pid_exists(child_pid)
        assert product_survivors == ()
    finally:
        if child_pid <= 0 and child_path.exists():
            with contextlib.suppress(
                json.JSONDecodeError,
                KeyError,
                ValueError,
            ):
                child_pid = int(
                    json.loads(
                        child_path.read_text(encoding="utf-8")
                    )["pid"]
                )
        if transport.is_active(token):
            with contextlib.suppress(Exception):
                await transport.cancel(token)
        with contextlib.suppress(Exception):
            await transport.collect(token)
        if psutil.pid_exists(root_pid):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(root_pid, signal.SIGKILL)
        if child_pid and psutil.pid_exists(child_pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
        tagged_runtime_cleanup.cleanup()


@pytest.mark.asyncio
async def test_subprocess_timeout_wins_over_stream_failure_during_cleanup(
    tmp_path: Path,
) -> None:
    class DelayedStreamFailureTransport(SubprocessRuntimeTransport):
        async def _pump(
            self,
            item: agent_runtime_module._SubprocessToken,
        ) -> int:
            await asyncio.sleep(0.05)
            raise RuntimeError(
                "runtime stream-json line exceeds the configured limit"
            )

    transport = DelayedStreamFailureTransport()
    token = await transport.start(
        run_id="timeout-precedence",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=0.01,
        metadata={},
    )

    try:
        result = await transport.collect(token)
        assert result.returncode == 124
        assert [event async for event in transport.stream(token)] == []
    finally:
        if transport.is_active(token):
            await transport.cancel(token)
        with contextlib.suppress(Exception):
            await transport.collect(token)


@pytest.mark.asyncio
async def test_isolated_cli_runtimes_enable_workspace_execution_controls(
    tmp_path: Path,
) -> None:
    hidden_root = tmp_path.parent / "hidden-verifier"
    hidden_root.mkdir()
    isolation = {
        "mode": "workspace_only",
        "workspace": str(tmp_path),
        "deny_paths": [str(hidden_root)],
        "network_policy": "disabled",
        "required": True,
    }
    claude_transport = RecordingTransport()
    claude_transport.filesystem_isolation_supported = True
    await ClaudeCodeRuntime(transport=claude_transport).start(
        AgentTask(
            task_id="claude-isolated",
            instruction="Edit the task.",
            cwd=tmp_path,
            model="claude-test",
            metadata={"filesystem_isolation": isolation},
        )
    )
    claude_argv = claude_transport.started[0]["argv"]
    assert claude_argv[claude_argv.index("--permission-mode") + 1] == (
        "bypassPermissions"
    )

    codex_transport = RecordingTransport()
    codex_transport.filesystem_isolation_supported = True
    await CodexRuntime(transport=codex_transport).start(
        AgentTask(
            task_id="codex-isolated",
            instruction="Edit the task.",
            cwd=tmp_path,
            model="gpt-test",
            metadata={"filesystem_isolation": isolation},
        )
    )
    codex_argv = codex_transport.started[0]["argv"]
    assert codex_argv[codex_argv.index("--sandbox") + 1] == "workspace-write"


@pytest.mark.asyncio
async def test_collect_after_partial_stream_keeps_remaining_events_once(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = CodexRuntime(transport=transport)
    handle = await runtime.start(
        AgentTask(
            task_id="task-partial-stream",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )
    repeated_message = {
        "type": "response_item",
        "payload": {
            "type": "agent_message",
            "message": "same",
        },
    }
    transport._events[handle.run_id] = [
        {"type": "run.started", "session_id": f"session-{handle.run_id}"},
        repeated_message,
        dict(repeated_message),
        {"type": "run.completed"},
    ]

    stream = runtime.stream(handle)
    assert (await anext(stream)).kind == "run.started"
    assert (await anext(stream)).kind == "agent.message"
    await stream.aclose()

    result = await runtime.collect(handle)

    assert [event.kind for event in result.events] == [
        "run.started",
        "agent.message",
        "agent.message",
        "run.completed",
    ]
    assert result.output == "same\nsame"


@pytest.mark.asyncio
async def test_collect_then_resume_streams_and_collects_cumulative_events_once(
    tmp_path: Path,
) -> None:
    transport = GenerationRecordingTransport(
        generations=[
            _generation(
                "one",
                "run.completed",
                session_id="resume-session-one",
            ),
            _generation("two", "run.completed"),
        ],
        returncodes=[0, 0],
    )
    runtime = CodexRuntime(transport=transport)
    handle = await runtime.start(
        AgentTask(
            task_id="resume-cumulative-output",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    first = await runtime.collect(handle)
    await runtime.resume(handle, "Continue")
    resumed_events = [event async for event in runtime.stream(handle)]
    resumed = await runtime.collect(handle)

    assert first.output == "one"
    assert first.session_id == "resume-session-one"
    assert "resume-session-one" in transport.resumed[0]["argv"]
    assert [event.kind for event in resumed_events] == [
        "run.started",
        "agent.message",
        "run.completed",
    ]
    assert [event.kind for event in resumed.events] == [
        "run.started",
        "agent.message",
        "run.completed",
        "run.started",
        "agent.message",
        "run.completed",
    ]
    assert resumed.output == "one\ntwo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "historical_terminal",
        "historical_returncode",
        "current_terminal",
        "current_returncode",
        "expected_status",
    ),
    (
        ("run.failed", 1, "run.completed", 0, "completed"),
        ("run.cancelled", 130, "run.completed", 0, "completed"),
        ("run.completed", 0, "run.failed", 1, "failed"),
        ("run.completed", 0, "run.cancelled", 130, "cancelled"),
        ("run.failed", 1, None, 0, "completed"),
    ),
)
async def test_resumed_status_uses_only_the_current_generation(
    tmp_path: Path,
    historical_terminal: str,
    historical_returncode: int,
    current_terminal: str | None,
    current_returncode: int,
    expected_status: str,
) -> None:
    transport = GenerationRecordingTransport(
        generations=[
            _generation("one", historical_terminal),
            _generation("two", current_terminal),
        ],
        returncodes=[historical_returncode, current_returncode],
    )
    runtime = CodexRuntime(transport=transport)
    handle = await runtime.start(
        AgentTask(
            task_id="resume-current-terminal",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    await runtime.collect(handle)
    await runtime.resume(handle, "Continue")
    result = await runtime.collect(handle)

    assert result.output == "one\ntwo"
    assert result.status == expected_status


@pytest.mark.asyncio
async def test_resume_without_prior_collect_excludes_historical_terminal(
    tmp_path: Path,
) -> None:
    transport = GenerationRecordingTransport(
        generations=[
            _generation("historical", "run.failed"),
            _generation("current", "run.completed"),
        ],
        returncodes=[1, 0],
    )
    runtime = CodexRuntime(transport=transport)
    handle = await runtime.start(
        AgentTask(
            task_id="resume-without-first-collect",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    await runtime.resume(handle, "Continue")
    result = await runtime.collect(handle)

    assert result.status == "completed"
    assert result.output == "historical\ncurrent"
    assert [event.kind for event in result.events] == [
        "run.started",
        "agent.message",
        "run.failed",
        "run.started",
        "agent.message",
        "run.completed",
    ]


@pytest.mark.asyncio
async def test_claude_runtime_keeps_only_direct_anthropic_and_safe_opus_env(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = ClaudeCodeRuntime(transport=transport)

    await runtime.start(
        AgentTask(
            task_id="claude-opus-planning-env",
            instruction="Review the plan.",
            cwd=tmp_path,
            model="opus",
            inherit_env=False,
            env={
                "HOME": "/tmp/claude-home",
                "PATH": "/usr/bin",
                "ANTHROPIC_API_KEY": "direct-anthropic-key",
                "ANTHROPIC_BASE_URL": "https://proxy.example",
                "ANTHROPIC_AUTH_TOKEN": "proxy-token",
                "OPENAI_API_KEY": "other-provider-secret",
                "GITHUB_TOKEN": "github-secret",
                "ARBITRARY_SECRET": "arbitrary-secret",
                "CODEX_SUPERVISOR_PLANNING_OPUS_MODEL": "claude-opus-4-6",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "stale-opus-pin",
                "CLAUDE_CODE_EXTRA_BODY": '{"thinking":{"type":"old"}}',
            },
            metadata={
                "lead_invocation": {
                    "gate": "prd_review",
                }
            },
        )
    )

    child_env = transport.started[0]["env"]
    assert child_env["HOME"] == "/tmp/claude-home"
    assert child_env["PATH"] == "/usr/bin"
    assert child_env["ANTHROPIC_API_KEY"] == "direct-anthropic-key"
    assert child_env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-6"
    assert json.loads(child_env["CLAUDE_CODE_EXTRA_BODY"]) == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "max"},
    }
    assert "ANTHROPIC_BASE_URL" not in child_env
    assert "ANTHROPIC_AUTH_TOKEN" not in child_env
    assert "OPENAI_API_KEY" not in child_env
    assert "GITHUB_TOKEN" not in child_env
    assert "ARBITRARY_SECRET" not in child_env
    assert "CODEX_SUPERVISOR_PLANNING_OPUS_MODEL" not in child_env


@pytest.mark.asyncio
async def test_codex_runtime_never_forwards_unrelated_host_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "authorized-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("NPM_TOKEN", "npm-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "slack-secret")
    transport = RecordingTransport()
    runtime = CodexRuntime(transport=transport)

    await runtime.start(
        AgentTask(
            task_id="codex-hostile-ambient-env",
            instruction="Implement the task.",
            cwd=tmp_path,
            model="gpt-test",
            inherit_env=True,
        )
    )

    child_env = transport.started[0]["env"]
    assert child_env["OPENAI_API_KEY"] == "authorized-openai-key"
    for forbidden in (
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "NPM_TOKEN",
        "SLACK_BOT_TOKEN",
    ):
        assert forbidden not in child_env


@pytest.mark.asyncio
async def test_claude_runtime_execution_opus_uses_unpinned_adaptive_controls(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = ClaudeCodeRuntime(transport=transport)

    await runtime.start(
        AgentTask(
            task_id="claude-opus-execution-env",
            instruction="Implement the task.",
            cwd=tmp_path,
            model="opus",
            inherit_env=False,
            env={
                "HOME": "/tmp/claude-home",
                "PATH": "/usr/bin",
                "ANTHROPIC_API_KEY": "direct-anthropic-key",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "stale-opus-pin",
                "CLAUDE_CODE_EXTRA_BODY": '{"thinking":{"type":"old"}}',
            },
            metadata={
                "lead_invocation": {
                    "gate": "execution",
                }
            },
        )
    )

    child_env = transport.started[0]["env"]
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" not in child_env
    assert json.loads(child_env["CLAUDE_CODE_EXTRA_BODY"]) == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "xhigh"},
    }


@pytest.mark.asyncio
async def test_codex_runtime_scrubs_anthropic_credentials_and_controls(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = CodexRuntime(transport=transport)

    await runtime.start(
        AgentTask(
            task_id="codex-provider-env",
            instruction="Implement the task.",
            cwd=tmp_path,
            model="gpt-test",
            inherit_env=False,
            env={
                "HOME": "/tmp/codex-home",
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "openai-key",
                "ANTHROPIC_API_KEY": "anthropic-key",
                "ANTHROPIC_BASE_URL": "https://proxy.example",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6",
                "CLAUDE_CODE_EXTRA_BODY": '{"thinking":{"type":"adaptive"}}',
                "CODEX_SUPERVISOR_PLANNING_OPUS_MODEL": "claude-opus-4-6",
            },
        )
    )

    child_env = transport.started[0]["env"]
    assert child_env["OPENAI_API_KEY"] == "openai-key"
    assert not any(
        key.startswith(("ANTHROPIC_", "CLAUDE_CODE_"))
        for key in child_env
    )
    assert "CODEX_SUPERVISOR_PLANNING_OPUS_MODEL" not in child_env


def test_pi_runtime_start_argv_defaults_to_fable_xhigh(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-argv",
        instruction="implement the fix",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
    )
    assert runtime.preview_start_argv(task) == (
        "pi",
        "-p",
        "--mode",
        "json",
        "--model",
        "anthropic/claude-fable-5",
        "--thinking",
        "xhigh",
        "implement the fix",
    )


def test_pi_runtime_argv_honors_effort_metadata_and_provider_prefix(
    tmp_path: Path,
) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-argv-effort",
        instruction="review",
        cwd=tmp_path,
        model="anthropic/claude-fable-5",
        timeout_s=60,
        metadata={"effort": "high"},
    )
    argv = runtime.preview_start_argv(task)
    assert argv[argv.index("--model") + 1] == "anthropic/claude-fable-5"
    assert argv[argv.index("--thinking") + 1] == "high"
    task_reasoning = AgentTask(
        task_id="pi-argv-reasoning",
        instruction="review",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
        metadata={"reasoning_effort": "medium", "effort": "low"},
    )
    argv = runtime.preview_start_argv(task_reasoning)
    assert argv[argv.index("--thinking") + 1] == "medium"


def test_pi_runtime_read_only_review_restricts_tools(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-read-only",
        instruction="review only",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
        metadata={"read_only_review": True},
    )
    argv = runtime.preview_start_argv(task)
    assert argv[argv.index("--tools") + 1] == "read,grep,find,ls"
    resume = runtime._resume_argv(
        task, session_id="sess-1", instruction="continue"
    )
    assert resume[resume.index("--tools") + 1] == "read,grep,find,ls"
    assert (
        runtime._route_identity_manifest(task)["sandbox_posture"]
        == "tools-allowlist"
    )


def test_pi_runtime_worktree_isolation_reports_sandbox_posture(
    tmp_path: Path,
) -> None:
    runtime = PiRuntime(binary="pi")
    hidden_root = tmp_path.parent / "hidden-verifier"
    isolation = {
        "mode": "workspace_only",
        "workspace": str(tmp_path),
        "deny_paths": [str(hidden_root)],
        "network_policy": "disabled",
        "required": True,
    }
    task = AgentTask(
        task_id="pi-worktree-isolation",
        instruction="x",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
        metadata={"filesystem_isolation": isolation},
    )
    assert (
        runtime._route_identity_manifest(task)["sandbox_posture"]
        == "worktree-only"
    )


def test_pi_runtime_resume_argv_uses_session_flag(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-resume",
        instruction="start",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
    )
    assert runtime._resume_argv(
        task, session_id="sess-42", instruction="continue"
    ) == (
        "pi",
        "--session",
        "sess-42",
        "-p",
        "--mode",
        "json",
        "--thinking",
        "xhigh",
        "continue",
    )


@pytest.mark.asyncio
async def test_pi_runtime_env_forwards_only_anthropic_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "leaked-openai")
    monkeypatch.setenv("CODEX_HOME", "/tmp/codex-home")
    monkeypatch.setenv("CLAUDE_CODE_EXTRA_BODY", "{}")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    transport = RecordingTransport()
    runtime = PiRuntime(transport=transport)

    await runtime.start(
        AgentTask(
            task_id="pi-hostile-ambient-env",
            instruction="x",
            cwd=tmp_path,
            model="claude-fable-5",
            timeout_s=60,
            inherit_env=True,
        )
    )

    child_env = transport.started[0]["env"]
    assert child_env["ANTHROPIC_API_KEY"] == "anthropic-secret"
    for forbidden in (
        "OPENAI_API_KEY",
        "CODEX_HOME",
        "CLAUDE_CODE_EXTRA_BODY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
    ):
        assert forbidden not in child_env


def test_pi_runtime_route_identity_manifest_is_complete(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-route",
        instruction="x",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
    )
    manifest = runtime._route_identity_manifest(task)
    assert manifest["provider"] == "anthropic"
    assert manifest["route_kind"] == "pi_cli"
    assert manifest["endpoint"] == "pi-cli-configured-route"
    assert manifest["sandbox_posture"] == "none"
    assert manifest["complete"] is True


@pytest.mark.asyncio
async def test_pi_runtime_normalizes_documented_jsonl_stream(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = tmp_path / "captured-pi"
    script.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"type":"session","id":"pi-sess-1","version":"0.0.0"}\n'
        '{"type":"agent_start"}\n'
        '{"type":"turn_start"}\n'
        '{"type":"tool_execution_start","toolName":"read"}\n'
        '{"type":"tool_execution_end","toolName":"read","isError":false}\n'
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"OK done"}]}}\n'
        '{"type":"turn_end"}\n'
        '{"type":"agent_end"}\n'
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    runtime = PiRuntime(binary=str(script))
    handle = await runtime.start(
        AgentTask(
            task_id="pi-live-jsonl",
            instruction="reply",
            cwd=repo,
            model="claude-fable-5",
            timeout_s=30,
        )
    )
    kinds = [event.kind async for event in runtime.stream(handle)]
    assert "run.started" in kinds
    assert "turn.started" in kinds
    assert "tool.started" in kinds
    assert "tool.completed" in kinds
    assert "agent.message" in kinds
    assert "turn.completed" in kinds
    assert "run.completed" in kinds
    result = await runtime.collect(handle)
    assert result.status == "completed"
    assert "OK done" in result.output
    # Session id from pi's stream header must drive resume.
    assert runtime._session_ids[handle.run_id] == "pi-sess-1"


@pytest.mark.asyncio
async def test_experiment_result_rejects_unresolved_transport_provenance(
    tmp_path: Path,
) -> None:
    class UnresolvedTransport(RecordingTransport):
        async def collect(self, token: str) -> RuntimeTransportResult:
            result = await super().collect(token)
            return RuntimeTransportResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                raw_events=result.raw_events,
                started_at_ms=result.started_at_ms,
                ended_at_ms=result.ended_at_ms,
                cost_usd=result.cost_usd,
            )

    runtime = CodexRuntime(transport=UnresolvedTransport())
    handle = await runtime.start(
        AgentTask(
            task_id="experiment-provenance",
            instruction="Start",
            cwd=tmp_path,
            model="requested-alias",
            metadata={
                "experiment": {
                    "arm": "compute_matched_direct",
                    "assignment_id": "assignment-1",
                }
            },
        )
    )

    with pytest.raises(
        ValueError,
        match="experiment runtime provenance is incomplete",
    ):
        await runtime.collect(handle)


@pytest.mark.asyncio
async def test_subprocess_transport_retains_terminal_token_until_collect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_runtime_module,
        "_MAX_RETAINED_TERMINAL_RUNS",
        1,
    )
    transport = SubprocessRuntimeTransport()

    async def start_terminal(run_id: str) -> str:
        token = await transport.start(
            run_id=run_id,
            argv=(sys.executable, "-c", "pass"),
            cwd=tmp_path,
            env=dict(os.environ),
            timeout_s=10,
            metadata={},
        )
        while transport.is_active(token):
            await asyncio.sleep(0.01)
        return token

    retained = await start_terminal("unharvested-terminal")
    await start_terminal("retention-pressure-0")
    await start_terminal("retention-pressure-1")

    result = await transport.collect(retained)

    assert result.returncode == 0


@pytest.mark.asyncio
async def test_subprocess_transport_evicts_collected_token_at_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_runtime_module,
        "_MAX_RETAINED_TERMINAL_RUNS",
        1,
    )
    transport = SubprocessRuntimeTransport()
    collected = await transport.start(
        run_id="collected-subprocess-token",
        argv=(sys.executable, "-c", "pass"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=10,
        metadata={},
    )
    await transport.collect(collected)

    replacement = await transport.start(
        run_id="replacement-subprocess-token",
        argv=(sys.executable, "-c", "pass"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=10,
        metadata={},
    )
    await transport.collect(replacement)

    with pytest.raises(KeyError, match="unknown runtime transport token"):
        await transport.collect(collected)


@pytest.mark.asyncio
async def test_subprocess_runtime_resume_resets_transport_harvest_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PythonRuntime(CommandAgentRuntime):
        kind = "python-test"
        capabilities = {
            "resume": True,
            "cancel": True,
            "stream": True,
            "cost_reporting": False,
        }

        def _start_argv(self, task: AgentTask) -> tuple[str, ...]:
            return (sys.executable, "-c", "pass")

        def _resume_argv(
            self,
            task: AgentTask,
            *,
            session_id: str,
            instruction: str,
        ) -> tuple[str, ...]:
            return (sys.executable, "-c", "pass")

    monkeypatch.setattr(
        agent_runtime_module,
        "_MAX_RETAINED_TERMINAL_RUNS",
        1,
    )
    runtime = PythonRuntime(
        transport=SubprocessRuntimeTransport(),
        binary=sys.executable,
    )
    resumed = await runtime.start(
        AgentTask(
            task_id="subprocess-resumed-retention",
            instruction="Run",
            cwd=tmp_path,
            model="python-test",
        )
    )
    _ = [event async for event in runtime.stream(resumed)]

    await runtime.resume(resumed, "Continue")
    _ = [event async for event in runtime.stream(resumed)]
    for index in range(2):
        pressure = await runtime.start(
            AgentTask(
                task_id=f"subprocess-retention-pressure-{index}",
                instruction="Run",
                cwd=tmp_path,
                model="python-test",
            )
        )
        _ = [event async for event in runtime.stream(pressure)]

    result = await runtime.collect(resumed)

    assert result.status == "completed"


@pytest.mark.asyncio
async def test_subprocess_failed_resume_requires_recollect_before_eviction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_runtime_module,
        "_MAX_RETAINED_TERMINAL_RUNS",
        1,
    )
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="subprocess-failed-resume",
        argv=(sys.executable, "-c", "pass"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=10,
        metadata={},
    )
    await transport.collect(token)

    with pytest.raises(FileNotFoundError):
        await transport.resume(
            token,
            argv=(str(tmp_path / "missing-runtime"),),
            cwd=tmp_path,
            env=dict(os.environ),
            timeout_s=10,
            metadata={},
        )

    replacement = await transport.start(
        run_id="subprocess-after-failed-resume",
        argv=(sys.executable, "-c", "pass"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=10,
        metadata={},
    )
    await transport.collect(replacement)

    assert (await transport.collect(token)).returncode == 0


@pytest.mark.asyncio
async def test_subprocess_timeout_budget_starts_when_process_starts(
    tmp_path: Path,
) -> None:
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="timeout-from-start",
        argv=(
            sys.executable,
            "-c",
            "import time; time.sleep(1.3)",
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=1,
        metadata={},
    )

    await asyncio.sleep(1.1)
    result = await transport.collect(token)

    assert result.returncode == 124


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-tree cancellation requires POSIX process groups",
)
async def test_subprocess_cancel_kills_setsid_descendant_and_records_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_runtime_cleanup: _TaggedRuntimeCleanup,
) -> None:
    ready_path = tmp_path / "runtime-tree.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({
        "child_pid": os.getpid(),
        "parent_pid": os.getppid(),
        "pgid": os.getpgrp(),
    }),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import signal
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([
    sys.executable,
    "-c",
    sys.argv[1],
    sys.argv[2],
])
while True:
    time.sleep(1)
"""
    containment_id = process_containment_module.new_containment_id()
    tagged_runtime_cleanup.register(containment_id)
    monkeypatch.setattr(
        agent_runtime_module,
        "new_containment_id",
        lambda: containment_id,
    )
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="cancel-setsid-tree",
        argv=(
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(ready_path),
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=30,
        metadata={},
    )
    root_pid = transport._get(token).process.pid
    tagged_runtime_cleanup.watch_pid(root_pid, pgid=root_pid)
    child_pid = 0
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            if ready_path.exists():
                try:
                    child_pid = int(
                        json.loads(
                            ready_path.read_text(encoding="utf-8")
                        )["child_pid"]
                    )
                    break
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
            await asyncio.sleep(0.01)
        assert child_pid > 0
        tagged_runtime_cleanup.watch_pid(child_pid, pgid=child_pid)
        assert os.getpgid(child_pid) == child_pid

        await transport.cancel(token)
        result = await transport.collect(token)
        product_survivors = tagged_runtime_cleanup.snapshot(
            containment_id
        )
        assert {"type": "run.cancelled"} in result.raw_events
        for pid in (root_pid, child_pid):
            if not psutil.pid_exists(pid):
                continue
            process = psutil.Process(pid)
            assert process.status() == psutil.STATUS_ZOMBIE
        assert product_survivors == ()
    finally:
        for pid in (root_pid, child_pid):
            if pid <= 0:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        tagged_runtime_cleanup.cleanup()


@pytest.mark.asyncio
async def test_subprocess_resume_rejects_overlapping_process_generation(
    tmp_path: Path,
) -> None:
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="overlapping-resume",
        argv=(sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=60,
        metadata={},
    )
    try:
        with pytest.raises(
            RuntimeError,
            match="previous runtime generation is active",
        ):
            await transport.resume(
                token,
                argv=(sys.executable, "-c", "print('replacement')"),
                cwd=tmp_path,
                env=dict(os.environ),
                timeout_s=5,
                metadata={},
            )
    finally:
        await transport.cancel(token)
        await transport.collect(token)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-tree cancellation requires POSIX process groups",
)
async def test_subprocess_collect_reaps_detached_child_after_root_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_runtime_cleanup: _TaggedRuntimeCleanup,
) -> None:
    child_path = tmp_path / "normal-exit-child.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import subprocess
import sys
import time
from pathlib import Path

subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not Path(sys.argv[2]).exists():
    time.sleep(0.01)
print('root-complete')
"""
    containment_id = process_containment_module.new_containment_id()
    tagged_runtime_cleanup.register(containment_id)
    monkeypatch.setattr(
        agent_runtime_module,
        "new_containment_id",
        lambda: containment_id,
    )
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="normal-exit-detached-child",
        argv=(
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(child_path),
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=10,
        metadata={},
    )
    child_pid = 0
    try:
        result = await transport.collect(token)
        deadline = asyncio.get_running_loop().time() + 2
        while (
            asyncio.get_running_loop().time() < deadline
            and not child_path.exists()
        ):
            await asyncio.sleep(0.01)
        child_pid = int(
            json.loads(child_path.read_text(encoding="utf-8"))["pid"]
        )
        tagged_runtime_cleanup.watch_pid(child_pid, pgid=child_pid)
        product_survivors = tagged_runtime_cleanup.snapshot(containment_id)
        assert result is not None
        assert result.returncode == 0
        assert not psutil.pid_exists(child_pid)
        assert product_survivors == ()
    finally:
        if child_pid and psutil.pid_exists(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        tagged_runtime_cleanup.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="process-tree cancellation requires POSIX process groups",
)
async def test_subprocess_cancel_reaps_child_spawned_on_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_runtime_cleanup: _TaggedRuntimeCleanup,
) -> None:
    ready_path = tmp_path / "runtime-term-ready.json"
    child_path = tmp_path / "runtime-term-child.json"
    child_code = """
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
"""
    parent_code = """
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

def on_term(_signum, _frame):
    child = subprocess.Popen(
        [sys.executable, "-c", sys.argv[1], sys.argv[3]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + 2
    while (
        time.monotonic() < deadline
        and not Path(sys.argv[3]).exists()
        and child.poll() is None
    ):
        time.sleep(0.005)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, on_term)
Path(sys.argv[2]).write_text(
    json.dumps({"pid": os.getpid()}),
    encoding="utf-8",
)
while True:
    time.sleep(0.01)
"""
    containment_id = process_containment_module.new_containment_id()
    tagged_runtime_cleanup.register(containment_id)
    monkeypatch.setattr(
        agent_runtime_module,
        "new_containment_id",
        lambda: containment_id,
    )
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="spawn-on-term-runtime",
        argv=(
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(ready_path),
            str(child_path),
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=30,
        metadata={},
    )
    child_pid = 0
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while (
            asyncio.get_running_loop().time() < deadline
            and not ready_path.exists()
        ):
            await asyncio.sleep(0.01)
        assert ready_path.exists()

        await transport.cancel(token)
        result = await transport.collect(token)

        deadline = asyncio.get_running_loop().time() + 2
        while (
            asyncio.get_running_loop().time() < deadline
            and not child_path.exists()
        ):
            await asyncio.sleep(0.01)
        if child_path.exists():
            child_pid = int(
                json.loads(child_path.read_text(encoding="utf-8"))["pid"]
            )
            tagged_runtime_cleanup.watch_pid(child_pid, pgid=child_pid)
        product_survivors = tagged_runtime_cleanup.snapshot(containment_id)
        assert child_pid > 0
        assert result is not None
        assert {"type": "run.cancelled"} in result.raw_events
        assert not psutil.pid_exists(child_pid)
        assert product_survivors == ()
    finally:
        if child_pid and psutil.pid_exists(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        tagged_runtime_cleanup.cleanup()


@pytest.mark.asyncio
async def test_subprocess_transport_extracts_provider_served_usage_provenance(
    tmp_path: Path,
) -> None:
    payload = (
        '{"type":"result","total_cost_usd":0.25,'
        '"usage":{"input_tokens":3,"cache_read_input_tokens":4,'
        '"output_tokens":5},'
        '"modelUsage":{"served-model-v3":{"inputTokens":3,'
        '"cacheReadInputTokens":4,"outputTokens":5,"costUSD":0.25}}}'
    )
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="served-provenance",
        argv=(sys.executable, "-c", f"print({payload!r})"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=5,
        metadata={},
    )

    result = await transport.collect(token)

    assert result.resolved_model == "served-model-v3"
    assert result.cost_usd == 0.25
    assert result.token_usage["tokens_in"] == 7
    assert result.token_usage["tokens_out"] == 5
    assert result.model_provenance.endswith(".model_usage")
    assert result.cost_provenance.endswith(".total_cost_usd")
    assert result.token_provenance.endswith(".usage")
    assert result.metadata["provenance_blockers"] == []


@pytest.mark.asyncio
async def test_local_subprocess_attestation_names_unenforced_operational_pins(
    tmp_path: Path,
) -> None:
    if not SubprocessRuntimeTransport().supports_filesystem_isolation(
        "workspace_only"
    ):
        pytest.skip("local workspace isolation backend is unavailable")
    architecture, os_name = default_task_platform()
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="local-operational-attestation",
        argv=(
            sys.executable,
            "-c",
            "print('{\"type\":\"run.completed\"}')",
        ),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=5,
        metadata={
            "execution_mode": "operational",
            "experiment": {
                "compute_resource_hash": "a" * 64,
                "runtime_plan": {
                    "container_digest": "sha256:" + ("b" * 64),
                    "architecture": architecture,
                    "os_name": os_name,
                    "network_policy": "disabled",
                    "resource_limits": {
                        "max_tokens": 100,
                        "max_cost_usd": 1.0,
                        "timeout_s": 5,
                        "max_retries": 0,
                    },
                },
            },
            "filesystem_isolation": {
                "mode": "workspace_only",
                "workspace": str(tmp_path),
                "deny_paths": [str(hidden)],
                "network_policy": "disabled",
            },
        },
    )

    result = await transport.collect(token)

    attestation = result.metadata["execution_environment_attestation"]
    assert attestation["attestation_source"] == "runtime_transport"
    assert attestation["mode"] == "operational"
    assert attestation["backend"] == (
        "local-subprocess/macos-sandbox-exec"
    )
    assert attestation["image_digest"] == ""
    assert attestation["architecture"] == architecture
    assert attestation["os_name"] == os_name
    assert attestation["network_policy"] == "disabled"
    assert attestation["enforced"] is False
    assert attestation["backend_evidence"]["container_backend"] is False
    assert (
        attestation["backend_evidence"]["network_isolation_enforced"]
        is True
    )
    assert "image_digest:no_container_backend" in attestation["unmet_pins"]
    assert (
        "resource_limits.max_tokens:not_enforced_by_local_subprocess"
        in attestation["unmet_pins"]
    )
    assert (
        "resource_limits.max_cost_usd:not_enforced_by_local_subprocess"
        in attestation["unmet_pins"]
    )
    attestation_body = {
        key: value
        for key, value in attestation.items()
        if key != "attestation_id"
    }
    assert attestation["attestation_id"] == hashlib.sha256(
        json.dumps(
            attestation_body,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert result.metadata["provenance_blockers"] == [
        "resolved_model",
        "cost_provenance",
        "token_provenance",
        "token_usage",
    ]


def test_raw_event_provenance_sums_explicit_delta_usage() -> None:
    from supervisor.agent_runtime import _provenance_from_raw_events

    result = _provenance_from_raw_events(
        (
            {
                "type": "usage",
                "usage_semantics": "delta",
                "usage": {"input_tokens": 10, "output_tokens": 2},
                "cost_usd": 1.0,
            },
            {
                "type": "usage",
                "usage_semantics": "delta",
                "usage": {"input_tokens": 20, "output_tokens": 3},
                "cost_usd": 2.0,
            },
        )
    )

    assert result["token_usage"]["tokens_in"] == 30
    assert result["token_usage"]["tokens_out"] == 5
    assert result["cost_usd"] == 3.0
    assert result["token_provenance"] == "transport_events.usage[delta_sum]"
    assert result["cost_provenance"] == "transport_events.cost[delta_sum]"


def test_raw_event_provenance_uses_latest_monotonic_cumulative_usage() -> None:
    from supervisor.agent_runtime import _provenance_from_raw_events

    result = _provenance_from_raw_events(
        (
            {
                "usage_semantics": "cumulative",
                "usage": {"input_tokens": 10, "output_tokens": 2},
                "cost_usd": 1.0,
            },
            {
                "usage_semantics": "cumulative",
                "usage": {"input_tokens": 30, "output_tokens": 5},
                "cost_usd": 3.0,
            },
        )
    )

    assert result["token_usage"]["tokens_in"] == 30
    assert result["token_usage"]["tokens_out"] == 5
    assert result["cost_usd"] == 3.0
    assert (
        result["token_provenance"]
        == "transport_events.usage[cumulative_latest]"
    )


def test_raw_event_provenance_rejects_ambiguous_multi_event_usage() -> None:
    from supervisor.agent_runtime import _provenance_from_raw_events

    result = _provenance_from_raw_events(
        (
            {"usage": {"input_tokens": 10}, "cost_usd": 1.0},
            {"usage": {"input_tokens": 20}, "cost_usd": 2.0},
        )
    )

    assert result["token_usage"] == {}
    assert result["token_provenance"] == ""
    assert result["cost_usd"] == 0.0
    assert result["cost_provenance"] == ""


@pytest.mark.asyncio
async def test_runtime_advertises_transport_filesystem_isolation_capability(
    tmp_path: Path,
) -> None:
    unsupported_transport = RecordingTransport()
    unsupported = await CodexRuntime(transport=unsupported_transport).start(
        AgentTask(
            task_id="isolation-unsupported",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )
    supported_transport = RecordingTransport()
    supported_transport.filesystem_isolation_supported = True
    supported = await CodexRuntime(transport=supported_transport).start(
        AgentTask(
            task_id="isolation-supported",
            instruction="Start",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    assert unsupported.capabilities["filesystem_isolation"] is False
    assert supported.capabilities["filesystem_isolation"] is True


@pytest.mark.asyncio
async def test_runtime_capability_evidence_separates_declared_discovered_and_exercised(
    tmp_path: Path,
) -> None:
    class CapabilityTransport(RecordingTransport):
        def runtime_capabilities(self) -> dict[str, bool]:
            return {
                "cancel": True,
                "resume": False,
                "transport_preflight": True,
            }

    runtime = CodexRuntime(transport=CapabilityTransport())
    handle = await runtime.start(
        AgentTask(
            task_id="capability-evidence",
            instruction="Exercise only stream and collect.",
            cwd=tmp_path,
            model="gpt-test",
        )
    )

    assert isinstance(handle.capability_evidence, RuntimeCapabilityEvidence)
    assert handle.declared_capabilities["subagents"] is True
    assert handle.declared_capabilities["images"] is True
    assert handle.discovered_capabilities == {
        "cancel": True,
        "resume": False,
        "transport_preflight": True,
        "filesystem_isolation": False,
    }
    assert handle.exercised_capabilities == {"start": True}
    assert "subagents" not in handle.observed_capabilities
    assert "images" not in handle.observed_capabilities
    assert handle.capabilities["resume"] is False

    events = [event async for event in runtime.stream(handle)]
    result = await runtime.collect(handle)

    assert events
    assert handle.exercised_capabilities["stream"] is True
    assert handle.exercised_capabilities["collect"] is True
    assert handle.exercised_capabilities["cost_reporting"] is True
    assert handle.observed_capabilities["stream"] is True
    assert handle.observed_capabilities["cost_reporting"] is True
    assert "subagents" not in handle.observed_capabilities
    assert result.metadata["capability_evidence"] == (
        handle.capability_evidence.to_dict()
    )


@pytest.mark.asyncio
async def test_runtime_fails_before_transport_launch_when_isolation_is_unsupported(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    runtime = CodexRuntime(transport=transport)

    with pytest.raises(
        RuntimeError,
        match="cannot enforce filesystem isolation mode workspace_only",
    ):
        await runtime.start(
            AgentTask(
                task_id="isolation-required",
                instruction="Start",
                cwd=tmp_path,
                model="gpt-test",
                metadata={
                    "filesystem_isolation": {
                        "mode": "workspace_only",
                        "deny_paths": [tmp_path / "hidden-verifier"],
                    }
                },
            )
        )

    assert transport.started == []


@pytest.mark.asyncio
async def test_subprocess_isolation_fails_closed_when_sandbox_exec_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "launched"
    monkeypatch.setattr(
        "supervisor.agent_runtime._MACOS_SANDBOX_EXEC",
        tmp_path / "missing-sandbox-exec",
    )
    transport = SubprocessRuntimeTransport()

    with pytest.raises(
        RuntimeError,
        match="cannot enforce filesystem isolation mode workspace_only",
    ):
        await transport.start(
            run_id="isolation-unavailable",
            argv=(
                sys.executable,
                "-c",
                "from pathlib import Path; "
                f"Path({str(marker)!r}).write_text('launched')",
            ),
            cwd=tmp_path,
            env=dict(os.environ),
            timeout_s=5,
            metadata={
                "filesystem_isolation": {
                    "mode": "workspace_only",
                    "deny_paths": [tmp_path / "hidden-verifier"],
                }
            },
        )

    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform != "darwin"
    or not os.access("/usr/bin/sandbox-exec", os.X_OK),
    reason="requires macOS sandbox-exec",
)
async def test_subprocess_workspace_only_blocks_hidden_verifier_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hidden_root = workspace / ".hidden-verifier"
    hidden_root.mkdir()
    hidden_verifier = hidden_root / "secret.txt"
    hidden_verifier.write_text("hidden-verifier-secret")
    unrelated_secret = tmp_path / "unlisted-secret.txt"
    unrelated_secret.write_text("default-allow-leak")
    workspace_output = workspace / "agent-output.txt"
    child = """
import json
import sys
from pathlib import Path

hidden = Path(sys.argv[1])
unrelated = Path(sys.argv[2])
workspace_output = Path(sys.argv[3])
read_blocked = False
write_blocked = False
unrelated_read_blocked = False
leaked = ""
try:
    leaked = hidden.read_text()
except OSError:
    read_blocked = True
try:
    hidden.write_text("tampered")
except OSError:
    write_blocked = True
try:
    unrelated.read_text()
except OSError:
    unrelated_read_blocked = True
workspace_output.write_text("workspace-write-ok")
print(json.dumps({
    "type": "filesystem.isolation.probe",
    "read_blocked": read_blocked,
    "write_blocked": write_blocked,
    "unrelated_read_blocked": unrelated_read_blocked,
    "leaked": leaked,
}))
raise SystemExit(
    0
    if read_blocked and write_blocked and unrelated_read_blocked
    else 9
)
"""
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="workspace-only-isolation",
        argv=(
            sys.executable,
            "-c",
            child,
            str(hidden_verifier),
            str(unrelated_secret),
            str(workspace_output),
        ),
        cwd=workspace,
        env=dict(os.environ),
        timeout_s=5,
        metadata={
            "filesystem_isolation": {
                "mode": "workspace_only",
                "deny_paths": [hidden_root],
            }
        },
    )

    result = await transport.collect(token)

    assert result.returncode == 0
    assert result.raw_events == (
        {
            "type": "filesystem.isolation.probe",
            "read_blocked": True,
            "write_blocked": True,
            "unrelated_read_blocked": True,
            "leaked": "",
        },
    )
    assert workspace_output.read_text() == "workspace-write-ok"
    assert hidden_verifier.read_text() == "hidden-verifier-secret"
    assert unrelated_secret.read_text() == "default-allow-leak"


def test_containment_scan_scopes_unreadable_processes_to_launched_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supervisor import process_containment as process_containment_module

    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    def unreadable(pid: int, ppid: int) -> object:
        class UnreadableProcess:
            info = {
                "pid": pid,
                "ppid": ppid,
                "username": "test-user",
                "create_time": 200.0,
            }

            def environ(self) -> dict[str, str]:
                raise psutil.AccessDenied(pid=pid)

        UnreadableProcess.pid = pid
        return UnreadableProcess()

    unrelated = unreadable(50010, 1)
    descendant = unreadable(50011, 41003)

    class MatchingRoot:
        pid = 41003
        info = {
            "pid": 41003,
            "ppid": 1,
            "username": "test-user",
            "create_time": 100.0,
        }

        def environ(self) -> dict[str, str]:
            return {}

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda _identity: False,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )
    root_identity = process_containment_module.ProcessIdentity(
        pid=41003,
        started_at=100.0,
    )

    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [unrelated],
    )
    default_scope = process_containment_module.scan_containment(
        "containment-transport-scope",
        root_identity=root_identity,
    )
    transport_scope = process_containment_module.scan_containment(
        "containment-transport-scope",
        root_identity=root_identity,
        unreadable_scope="containment_tree",
    )

    # The strict default still fails closed on any unreadable same-user
    # process started at or after the root.
    assert default_scope.scan_complete is False
    assert default_scope.errors == ("access_denied:50010",)
    # The launched-tree scope rules the unrelated process out.
    assert transport_scope.scan_complete is True
    assert transport_scope.errors == ()

    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [MatchingRoot(), descendant],
    )
    descendant_scope = process_containment_module.scan_containment(
        "containment-transport-scope",
        root_identity=root_identity,
        unreadable_scope="containment_tree",
    )

    # An unreadable descendant of the launched tree still fails closed.
    assert descendant_scope.scan_complete is False
    assert descendant_scope.errors == ("access_denied:50011",)

    with pytest.raises(ValueError, match="unreadable_scope"):
        process_containment_module.scan_containment(
            "containment-transport-scope",
            root_identity=root_identity,
            unreadable_scope="everything",
        )


def test_containment_scan_keeps_unknown_principal_as_proof_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentProcess:
        def username(self) -> str:
            return "test-user"

    class UnknownPrincipalTaggedProcess:
        pid = 50012
        info = {
            "pid": 50012,
            "ppid": 1,
            "username": "",
            "create_time": 200.0,
        }

        def environ(self) -> dict[str, str]:
            return {
                process_containment_module.CONTAINMENT_ENV_VAR: (
                    "containment-unknown-principal"
                )
            }

    monkeypatch.setattr(
        process_containment_module.psutil,
        "Process",
        lambda *_args, **_kwargs: CurrentProcess(),
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [UnknownPrincipalTaggedProcess()],
    )
    monkeypatch.setattr(
        process_containment_module,
        "same_process",
        lambda identity: identity.pid == UnknownPrincipalTaggedProcess.pid,
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )

    snapshot = process_containment_module.scan_containment(
        "containment-unknown-principal",
        root_identity=process_containment_module.ProcessIdentity(
            pid=41003,
            started_at=100.0,
        ),
    )

    assert snapshot.processes == ()
    assert snapshot.scan_complete is False
    assert snapshot.errors == ("unknown_principal:50012",)
    assert snapshot.unreadable_identities[0].relation == "unknown_principal"


def test_containment_scan_requires_matching_uid_when_both_are_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CandidateUids:
        real = 999
        effective = 999

        def __getitem__(self, _index: int) -> int:
            return self.effective

    class OtherUidTaggedProcess:
        pid = 50013
        info = {
            "pid": 50013,
            "ppid": 1,
            "username": "test-user",
            "create_time": 200.0,
            "uids": CandidateUids(),
        }

        def environ(self) -> dict[str, str]:
            return {
                process_containment_module.CONTAINMENT_ENV_VAR: (
                    "containment-other-uid"
                )
            }

    monkeypatch.setattr(
        process_containment_module,
        "_current_principal",
        lambda: process_containment_module._ProcessPrincipal(
            uid=501,
            username="test-user",
        ),
    )
    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [OtherUidTaggedProcess()],
    )
    monkeypatch.setattr(
        process_containment_module.os,
        "getpid",
        lambda: 99999,
    )

    snapshot = process_containment_module.scan_containment(
        "containment-other-uid",
        root_identity=process_containment_module.ProcessIdentity(
            pid=41003,
            started_at=100.0,
        ),
    )

    assert snapshot.processes == ()
    assert snapshot.scan_complete is True
    assert snapshot.errors == ()


@pytest.mark.asyncio
async def test_subprocess_collect_ignores_unreadable_unrelated_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_runtime_cleanup: _TaggedRuntimeCleanup,
) -> None:
    current_username = psutil.Process().username()

    class UnreadableUnrelatedProcess:
        pid = 3_999_999

        info = {
            "pid": 3_999_999,
            "ppid": 1,
            "username": current_username,
            "create_time": 4_000_000_000.0,
        }

        def environ(self) -> dict[str, str]:
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        process_containment_module.psutil,
        "process_iter",
        lambda _attrs: [UnreadableUnrelatedProcess()],
    )

    containment_id = process_containment_module.new_containment_id()
    tagged_runtime_cleanup.register(containment_id)
    monkeypatch.setattr(
        agent_runtime_module,
        "new_containment_id",
        lambda: containment_id,
    )
    transport = SubprocessRuntimeTransport()
    token = await transport.start(
        run_id="unreadable-unrelated-process",
        argv=(sys.executable, "-c", "print('ok')"),
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_s=10,
        metadata={},
    )

    try:
        result = await transport.collect(token)
        product_survivors = tagged_runtime_cleanup.snapshot(containment_id)

        assert result.returncode == 0
        assert product_survivors == ()
    finally:
        if transport.is_active(token):
            with contextlib.suppress(Exception):
                await transport.cancel(token)
        with contextlib.suppress(Exception):
            await transport.collect(token)
        tagged_runtime_cleanup.cleanup()


def test_codex_runtime_read_only_review_pins_read_only_sandbox(
    tmp_path: Path,
) -> None:
    runtime = CodexRuntime()
    task = AgentTask(
        task_id="read-only-review",
        instruction="Review only.",
        cwd=tmp_path,
        model="gpt-5.5",
        metadata={
            "reasoning_effort": "xhigh",
            "read_only_review": True,
        },
    )

    argv = runtime.preview_start_argv(task)

    assert 'reasoning_effort="xhigh"' in argv
    sandbox_index = argv.index("--sandbox")
    assert argv[sandbox_index + 1] == "read-only"
    assert "workspace-write" not in argv


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg"),
    reason="provider runtime tree cancellation requires POSIX process groups",
)
@pytest.mark.parametrize(
    ("runtime_cls", "binary_name", "model"),
    (
        (ClaudeCodeRuntime, "claude", "claude-test-exact"),
        (CodexRuntime, "codex", "gpt-test-exact"),
    ),
)
async def test_provider_command_shim_cancellation_reaps_detached_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tagged_runtime_cleanup: _TaggedRuntimeCleanup,
    runtime_cls: type[ClaudeCodeRuntime] | type[CodexRuntime],
    binary_name: str,
    model: str,
) -> None:
    root_path = tmp_path / f"{binary_name}-root.json"
    child_path = tmp_path / f"{binary_name}-child.json"
    shim = tmp_path / binary_name
    shim.write_text(
        f"""#!{sys.executable}
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root_path = Path({str(root_path)!r})
child_path = Path({str(child_path)!r})
child_code = '''
import json
import os
import sys
import time
from pathlib import Path

os.setsid()
Path(sys.argv[1]).write_text(
    json.dumps({{"pid": os.getpid(), "pgid": os.getpgrp()}}),
    encoding="utf-8",
)
while True:
    time.sleep(1)
'''

signal.signal(signal.SIGTERM, signal.SIG_IGN)
root_path.write_text(
    json.dumps({{"pid": os.getpid(), "argv": sys.argv[1:]}}),
    encoding="utf-8",
)
subprocess.Popen(
    [sys.executable, "-c", child_code, str(child_path)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    containment_id = process_containment_module.new_containment_id()
    tagged_runtime_cleanup.register(containment_id)
    monkeypatch.setattr(
        agent_runtime_module,
        "new_containment_id",
        lambda: containment_id,
    )
    runtime = runtime_cls(binary=str(shim))
    handle = await runtime.start(
        AgentTask(
            task_id=f"{binary_name}-provider-shaped-cancel",
            instruction="Remain active until cancellation.",
            cwd=tmp_path,
            model=model,
            timeout_s=30,
        )
    )
    root_pid = 0
    child_pid = 0
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            if root_path.exists() and child_path.exists():
                try:
                    root_payload = json.loads(
                        root_path.read_text(encoding="utf-8")
                    )
                    root_pid = int(root_payload["pid"])
                    child_pid = int(
                        json.loads(child_path.read_text(encoding="utf-8"))["pid"]
                    )
                    break
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
            await asyncio.sleep(0.01)
        assert root_pid > 0
        assert child_pid > 0
        tagged_runtime_cleanup.watch_pid(root_pid, pgid=root_pid)
        tagged_runtime_cleanup.watch_pid(child_pid, pgid=child_pid)
        assert os.getpgid(child_pid) == child_pid
        argv = root_payload["argv"]
        if binary_name == "claude":
            assert argv[:2] == ["-p", "Remain active until cancellation."]
            assert "--output-format" in argv
            assert "--model" in argv
        else:
            assert argv[:2] == ["exec", "--json"]
            assert "-m" in argv
            assert argv[-1] == "Remain active until cancellation."

        await runtime.cancel(handle)
        result = await runtime.collect(handle)

        product_survivors = tagged_runtime_cleanup.snapshot(containment_id)
        assert result is not None
        assert result.status == "cancelled"
        assert handle.exercised_capabilities["cancel"] is True
        assert (
            result.metadata["capability_evidence"]["exercised"]["cancel"]
            is True
        )
        for pid in (root_pid, child_pid):
            if not psutil.pid_exists(pid):
                continue
            assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        assert product_survivors == ()
    finally:
        for pid in (root_pid, child_pid):
            if pid <= 0:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        tagged_runtime_cleanup.cleanup()
