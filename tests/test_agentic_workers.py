from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess

import pytest

from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from supervisor.agentic_workers import (
    AgenticWorkerSpec,
    cleanup_agentic_workers_for_task,
    cleanup_orphaned_agentic_workers,
    discover_agentic_worker_receipts,
    discover_agentic_worker_runtime_records,
    run_agentic_worker,
    run_agentic_worker_fanout,
    worker_log_ref,
    worker_runtime_ref,
)
from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts
from supervisor.provider_routing import ANTHROPIC_PROXY_ENV_KEYS
from supervisor.runtime_execution import RuntimeExecution


def _runtime_execution(
    task: AgentTask,
    *,
    runtime: str,
    status: str = "completed",
    output: str = "runtime output",
    failure_reason: str = "",
) -> RuntimeExecution:
    event_kind = {
        "completed": "run.completed",
        "cancelled": "run.cancelled",
    }.get(status, "run.failed")
    event_payload = {"type": event_kind}
    if failure_reason:
        event_payload["reason"] = failure_reason
        event_payload["error"] = failure_reason
    event = RuntimeEvent(
        kind=event_kind,
        payload=event_payload,
        ts_ms=2,
    )
    handle = AgentRunHandle(
        run_id=f"run-{task.metadata['worker_id']}",
        task_id=task.task_id,
        runtime=runtime,
        session_id=f"session-{task.metadata['worker_id']}",
        capabilities={"cancel": True, "stream": True},
    )
    result = AgentRunResult(
        run_id=handle.run_id,
        task_id=task.task_id,
        runtime=runtime,
        session_id=handle.session_id,
        status=status,
        output=output,
        events=(event,),
        started_at_ms=1,
        ended_at_ms=2,
        cost_usd=0.125,
        resolved_model=f"{runtime}-model-v1",
        result_hash="d" * 64,
        token_usage={
            "input_tokens": 11,
            "output_tokens": 7,
            "tokens_in": 11,
            "tokens_out": 7,
        },
        model_provenance="fake.runtime",
        cost_provenance="fake.runtime",
        token_provenance="fake.runtime",
        metadata={
            "returncode": 0 if status == "completed" else 1,
            "environment": {
                "OPENAI_API_KEY": "must-not-persist",
                "ANTHROPIC_API_KEY": "must-not-persist",
            },
        },
    )
    return RuntimeExecution(handle=handle, events=(event,), result=result)


@pytest.mark.parametrize("runtime", ["claude_code", "codex"])
def test_agentic_worker_runtime_runner_has_provider_parity_and_normalized_receipt(
    monkeypatch,
    tmp_path: Path,
    runtime: str,
):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic-secret")
    seen_tasks: list[AgentTask] = []

    def fake_runtime_runner(task: AgentTask) -> RuntimeExecution:
        seen_tasks.append(task)
        return _runtime_execution(task, runtime=runtime)

    receipt = run_agentic_worker(
        AgenticWorkerSpec(
            task_id="workflow-1",
            worker_id="audit-1",
            role="codebase_audit",
            command=(),
            cwd=tmp_path,
            instruction="Inspect the implementation without writing files.",
            model=f"{runtime}-requested-model",
            timeout_s=30,
            budget_usd=0.25,
        ),
        runtime_runner=fake_runtime_runner,
    )

    assert len(seen_tasks) == 1
    task = seen_tasks[0]
    assert task.inherit_env is False
    assert dict(task.env) == {}
    assert task.timeout_s == 30
    assert task.metadata["worker_id"] == "audit-1"
    assert receipt["status"] == "passed"
    assert receipt["agent_runtime"] == runtime
    assert receipt["runtime"] == runtime
    assert receipt["runtime_run_id"] == "run-audit-1"
    assert receipt["session_id"] == "session-audit-1"
    assert receipt["resolved_model"] == f"{runtime}-model-v1"
    assert receipt["cost_usd"] == 0.125
    assert receipt["token_usage"]["tokens_in"] == 11
    assert receipt["result_hash"] == "d" * 64

    output = json.loads(
        (tmp_path / receipt["output_ref"]).read_text(encoding="utf-8")
    )
    assert output["agent_run_result"]["runtime"] == runtime
    assert output["agent_run_result"]["result_hash"] == "d" * 64
    assert "must-not-persist" not in json.dumps(output)


def test_agentic_worker_fanout_runtime_factory_creates_fresh_runtime_per_worker(
    tmp_path: Path,
):
    created: list["_FactoryRuntime"] = []
    started_tasks: list[AgentTask] = []

    class _FactoryRuntime:
        kind = "codex"

        def __init__(self) -> None:
            self.task: AgentTask | None = None

        async def start(self, task: AgentTask) -> AgentRunHandle:
            self.task = task
            started_tasks.append(task)
            return AgentRunHandle(
                run_id=f"run-{task.metadata['worker_id']}",
                task_id=task.task_id,
                runtime=self.kind,
                session_id=f"session-{task.metadata['worker_id']}",
                capabilities={"cancel": True, "stream": True},
            )

        async def resume(
            self,
            handle: AgentRunHandle,
            instruction: str,
        ) -> None:
            raise AssertionError("fan-out workers must not resume sessions")

        async def cancel(self, handle: AgentRunHandle) -> None:
            return None

        async def stream(self, handle: AgentRunHandle):
            yield RuntimeEvent(
                kind="run.completed",
                payload={"type": "run.completed"},
                ts_ms=2,
            )

        async def collect(self, handle: AgentRunHandle) -> AgentRunResult:
            assert self.task is not None
            return _runtime_execution(
                self.task,
                runtime=self.kind,
            ).result

    def runtime_factory() -> _FactoryRuntime:
        runtime = _FactoryRuntime()
        created.append(runtime)
        return runtime

    receipts = run_agentic_worker_fanout(
        [
            AgenticWorkerSpec(
                task_id="workflow-1",
                worker_id="audit",
                role="codebase_audit",
                command=(),
                cwd=tmp_path,
                instruction="Audit.",
                model="codex-model",
            ),
            AgenticWorkerSpec(
                task_id="workflow-1",
                worker_id="review",
                role="independent_reviewer",
                command=(),
                cwd=tmp_path,
                instruction="Review.",
                model="codex-model",
            ),
        ],
        runtime_factory=runtime_factory,
    )

    assert len(created) == 2
    assert {task.metadata["worker_id"] for task in started_tasks} == {
        "audit",
        "review",
    }
    assert [receipt["worker_id"] for receipt in receipts] == [
        "audit",
        "review",
    ]
    assert [receipt["runtime"] for receipt in receipts] == ["codex", "codex"]


def test_agentic_worker_runtime_fanout_preserves_failure_cancellation_and_timeout(
    tmp_path: Path,
):
    runtime_status = {
        "ok": ("completed", ""),
        "failed": ("failed", "provider failure"),
        "cancelled": ("cancelled", ""),
        "timeout": ("failed", "timeout"),
    }

    def fake_runtime_runner(task: AgentTask) -> RuntimeExecution:
        status, reason = runtime_status[str(task.metadata["worker_id"])]
        return _runtime_execution(
            task,
            runtime="claude_code",
            status=status,
            output=f"{task.metadata['worker_id']} output",
            failure_reason=reason,
        )

    receipts = run_agentic_worker_fanout(
        [
            AgenticWorkerSpec(
                task_id="workflow-1",
                worker_id=worker_id,
                role=worker_id,
                command=(),
                cwd=tmp_path,
                instruction=f"Run {worker_id}.",
                model="claude-model",
                timeout_s=10,
            )
            for worker_id in ("ok", "failed", "cancelled", "timeout")
        ],
        runtime_runner=fake_runtime_runner,
    )

    assert [receipt["worker_id"] for receipt in receipts] == [
        "ok",
        "failed",
        "cancelled",
        "timeout",
    ]
    assert [receipt["status"] for receipt in receipts] == [
        "passed",
        "failed",
        "cancelled",
        "timeout",
    ]
    assert all((tmp_path / receipt["output_ref"]).is_file() for receipt in receipts)
    assert all(
        (tmp_path / receipt["transcript_ref"]).is_file()
        for receipt in receipts
    )


def test_agentic_worker_runtime_runner_cancellation_records_then_reraises(
    tmp_path: Path,
):
    def cancelled_runtime_runner(task: AgentTask) -> RuntimeExecution:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run_agentic_worker(
            AgenticWorkerSpec(
                task_id="workflow-1",
                worker_id="cancelled",
                role="independent_reviewer",
                command=(),
                cwd=tmp_path,
                instruction="Review.",
                model="runtime-model",
                timeout_s=10,
            ),
            runtime_runner=cancelled_runtime_runner,
        )

    runtime_ref = worker_runtime_ref(
        cwd=tmp_path,
        task_id="workflow-1",
        worker_id="cancelled",
    )
    record = json.loads((tmp_path / runtime_ref).read_text(encoding="utf-8"))
    assert record["status"] == "cancelled"
    assert record["ended_at_s"] >= record["started_at_s"]


def test_agentic_worker_spawn_uses_scrubbed_direct_anthropic_env(
    monkeypatch,
    tmp_path: Path,
):
    ambient_secrets = {
        **{key: f"secret-{key}" for key in ANTHROPIC_PROXY_ENV_KEYS},
        "OPENAI_API_KEY": "openai-key",
        "OPENAI_BASE_URL": "https://litellm.example/v1",
        "LITELLM_API_KEY": "litellm-key",
        "LITELLM_MASTER_KEY": "litellm-master-key",
        "CODEX_API_KEY": "codex-key",
        "CODEX_HOME": "/secret/codex-home",
        "GITHUB_TOKEN": "github-token",
        "UNRELATED_SECRET": "must-not-leak",
    }
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("HOME", "/safe/home")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "direct-key")
    for key, value in ambient_secrets.items():
        monkeypatch.setenv(key, value)
    runner_kwargs: dict[str, object] = {}

    def fake_runner(argv, **kwargs):
        runner_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="audit ok\n", stderr="")

    receipt = run_agentic_worker(
        AgenticWorkerSpec(
            task_id="workflow-1:audit",
            worker_id="audit-1",
            role="codebase_audit",
            command=("claude", "--print", "audit"),
            cwd=tmp_path,
            timeout_s=30,
            budget_usd=0.25,
        ),
        runner=fake_runner,
    )

    assert receipt["status"] == "passed"
    child_env = runner_kwargs["env"]
    assert isinstance(child_env, dict)
    assert child_env["ANTHROPIC_API_KEY"] == "direct-key"
    assert child_env["PATH"] == "/safe/bin"
    assert child_env["HOME"] == "/safe/home"
    assert set(child_env).isdisjoint(ambient_secrets)
    assert set(child_env) <= {
        "ANTHROPIC_API_KEY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "TMPDIR",
    }


def test_orphaned_agentic_worker_cleanup_records_timeout_and_log_refs(tmp_path: Path):
    log_ref = worker_log_ref(cwd=tmp_path, task_id="workflow-1", worker_id="audit-1")
    worker = {
        "worker_id": "audit-1",
        "pid": 43210,
        "started_at_s": 100,
        "timeout_s": 30,
        "budget_usd": 1.5,
        "log_ref": log_ref,
    }
    killed: list[tuple[int, int]] = []

    result = cleanup_orphaned_agentic_workers(
        cwd=tmp_path,
        task_id="workflow-1",
        workers=[worker],
        now_s=200,
        is_pid_alive=lambda pid: pid == 43210,
        terminate=lambda pid, sig: killed.append((pid, sig)),
    )

    assert result["status"] == "cleanup_completed"
    assert result["cleaned"][0]["reason"] == "timeout_exceeded"
    assert result["cleaned"][0]["timeout_s"] == 30
    assert result["cleaned"][0]["budget_usd"] == 1.5
    assert result["cleaned"][0]["log_ref"] == log_ref
    assert killed[0][0] == 43210


def test_agentic_worker_spawn_captures_supervisor_owned_runtime_native_receipt(tmp_path: Path):
    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="audit ok\n", stderr="note\n")

    receipt = run_agentic_worker(
        AgenticWorkerSpec(
            task_id="workflow-1:audit",
            worker_id="audit-1",
            role="codebase_audit",
            command=("claude", "--print", "audit"),
            cwd=tmp_path,
            agent_runtime="claude_code",
            agent_id="agent-audit-1",
            permission_mode="readOnly",
            tool_pins=("rg", "sed"),
            timeout_s=30,
            budget_usd=1.5,
        ),
        runner=fake_runner,
    )

    assert receipt["kind"] == "dynamic_subagent_result"
    assert receipt["status"] == "passed"
    assert receipt["decision"] == "accept"
    assert receipt["transcript_ref"].startswith(".handoff/agentic-workers/workflow-1-audit/audit-1/")
    assert receipt["output_ref"].startswith(".handoff/agentic-workers/workflow-1-audit/audit-1/")
    assert receipt["runtime_ref"].startswith(".handoff/agentic-workers/workflow-1-audit/audit-1/")
    assert (tmp_path / receipt["transcript_ref"]).exists()
    assert (tmp_path / receipt["output_ref"]).exists()
    assert (tmp_path / receipt["runtime_ref"]).exists()

    discovered = discover_agentic_worker_receipts(cwd=tmp_path, task_id="workflow-1:audit")
    assert len(discovered) == 1
    assert discovered[0]["worker_id"] == "audit-1"
    assert discovered[0]["output_sha256"] == receipt["output_sha256"]

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "green"
    assert probe.details["agentic_policy"]["subagents"][0]["evidence_grade"] == "runtime_native"


def test_agentic_timeout_receipt_blocks_runtime_native_and_preserves_failed_refs(tmp_path: Path):
    def timeout_runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=1, output="partial\n", stderr="late\n")

    receipt = run_agentic_worker(
        AgenticWorkerSpec(
            task_id="workflow-1:review",
            worker_id="review-1",
            role="independent_reviewer",
            command=("claude", "--print", "review"),
            cwd=tmp_path,
            timeout_s=1,
            budget_usd=0.2,
        ),
        runner=timeout_runner,
    )

    assert receipt["status"] == "timeout"
    assert receipt["decision"] == "revise"
    assert receipt["timeout_s"] == 1
    assert receipt["budget_usd"] == 0.2
    assert (tmp_path / receipt["transcript_ref"]).exists()
    assert (tmp_path / receipt["output_ref"]).exists()
    assert (tmp_path / receipt["stderr_ref"]).exists()
    assert "partial" in (tmp_path / receipt["stdout_ref"]).read_text(encoding="utf-8")
    assert "late" in (tmp_path / receipt["stderr_ref"]).read_text(encoding="utf-8")

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["independent_reviewer"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "red"
    policy = probe.details["agentic_policy"]
    assert policy["subagents"][0]["status"] == "timeout"
    assert "subagent_status_not_passing" in str(policy["blocking_findings"])


def test_agentic_worker_fanout_returns_receipts_in_spec_order(tmp_path: Path):
    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=f"{argv[-1]}\n", stderr="")

    receipts = run_agentic_worker_fanout(
        [
            AgenticWorkerSpec(
                task_id="workflow-1:audit",
                worker_id="audit",
                role="codebase_audit",
                command=("worker", "audit"),
                cwd=tmp_path,
            ),
            AgenticWorkerSpec(
                task_id="workflow-1:review",
                worker_id="review",
                role="independent_reviewer",
                command=("worker", "review"),
                cwd=tmp_path,
            ),
        ],
        runner=fake_runner,
    )

    assert [receipt["worker_id"] for receipt in receipts] == ["audit", "review"]


def test_agentic_worker_task_cleanup_discovers_and_reaps_stale_runtime_records(tmp_path: Path):
    task_id = "workflow-1"
    runtime_ref = worker_runtime_ref(cwd=tmp_path, task_id=task_id, worker_id="stale-1")
    runtime_path = tmp_path / runtime_ref
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        '{"task_id":"workflow-1","worker_id":"stale-1","pid":43210,'
        '"started_at_s":100,"timeout_s":30,"budget_usd":0.2,'
        '"log_ref":".handoff/agentic-workers/workflow-1/stale-1/worker.log"}\n',
        encoding="utf-8",
    )
    active_ref = worker_runtime_ref(cwd=tmp_path, task_id=task_id, worker_id="active-1")
    active_path = tmp_path / active_ref
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(
        '{"task_id":"workflow-1","worker_id":"active-1","pid":43211,'
        '"started_at_s":190,"timeout_s":30,"budget_usd":0.2}\n',
        encoding="utf-8",
    )
    dead_ref = worker_runtime_ref(cwd=tmp_path, task_id=task_id, worker_id="dead-1")
    dead_path = tmp_path / dead_ref
    dead_path.parent.mkdir(parents=True, exist_ok=True)
    dead_path.write_text(
        '{"task_id":"workflow-1","worker_id":"dead-1","pid":43212,'
        '"started_at_s":100,"timeout_s":30,"budget_usd":0.2}\n',
        encoding="utf-8",
    )
    killed: list[int] = []

    records = discover_agentic_worker_runtime_records(cwd=tmp_path, task_id=task_id)
    assert {record["worker_id"] for record in records} == {"stale-1", "active-1", "dead-1"}

    result = cleanup_agentic_workers_for_task(
        cwd=tmp_path,
        task_id=task_id,
        now_s=200,
        is_pid_alive=lambda pid: pid in {43210, 43211},
        terminate=lambda pid, sig: killed.append(pid),
    )

    assert killed == [43210]
    assert [item["worker_id"] for item in result["cleaned"]] == ["stale-1"]
    assert [item["worker_id"] for item in result["active"]] == ["active-1"]
    assert [item["worker_id"] for item in result["skipped"]] == ["dead-1"]


def _bare_result(
    *,
    status: str,
    metadata: dict | None = None,
    events: tuple[RuntimeEvent, ...] = (),
) -> AgentRunResult:
    return AgentRunResult(
        run_id="run-status-probe",
        task_id="task-status-probe",
        runtime="claude_code",
        session_id="session-status-probe",
        status=status,
        output="",
        events=events,
        started_at_ms=1,
        ended_at_ms=2,
        cost_usd=0.0,
        resolved_model="claude-model",
        result_hash="f" * 64,
        token_usage={},
        metadata=metadata or {},
    )


def test_worker_status_ignores_timeout_text_without_structured_signal():
    from supervisor.agentic_workers import _runtime_timed_out, _worker_status

    chatter = RuntimeEvent(
        kind="agent.message",
        payload={
            "type": "agent.message",
            "error": "transient timeout while fetching docs",
        },
        ts_ms=1,
    )
    completed = _bare_result(
        status="completed",
        metadata={"returncode": 0},
        events=(chatter,),
    )
    failed = _bare_result(
        status="failed",
        metadata={
            "returncode": 1,
            "error": "connection timeout during provider call",
        },
        events=(chatter,),
    )

    assert _worker_status(completed) == "passed"
    assert not _runtime_timed_out(completed)
    assert _worker_status(failed) == "failed"


def test_worker_status_honours_structured_timeout_signals():
    from supervisor.agentic_workers import _worker_status

    by_status = _bare_result(status="timeout")
    by_returncode = _bare_result(
        status="failed",
        metadata={"returncode": 124},
    )
    by_failure_reason = _bare_result(
        status="failed",
        metadata={"returncode": 1, "failure_reason": "timeout"},
    )
    by_event_reason = _bare_result(
        status="failed",
        metadata={"returncode": 1},
        events=(
            RuntimeEvent(
                kind="run.failed",
                payload={"type": "run.failed", "reason": "timeout_exceeded"},
                ts_ms=1,
            ),
        ),
    )

    for result in (by_status, by_returncode, by_failure_reason, by_event_reason):
        assert _worker_status(result) == "timeout"
