from __future__ import annotations

from pathlib import Path
import subprocess

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
