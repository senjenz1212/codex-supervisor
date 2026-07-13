from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from supervisor.agentic_executor import (
    AgenticWorkerRosterItem,
    _extract_roster_payload,
    plan_agentic_worker_roster,
    produce_agentic_worker_receipts,
    validate_agentic_worker_roster,
)
from supervisor.provider_routing import (
    ANTHROPIC_PROXY_ENV_KEYS,
    configure_direct_anthropic_process_env,
)
from supervisor.runtime_execution import RuntimeExecution


def _planner_or_worker_execution(
    task: AgentTask,
    *,
    runtime: str,
    output: str,
    status: str = "completed",
) -> RuntimeExecution:
    kind = {
        "completed": "run.completed",
        "cancelled": "run.cancelled",
    }.get(status, "run.failed")
    event = RuntimeEvent(
        kind=kind,
        payload={"type": kind},
        ts_ms=2,
    )
    execution_kind = str(task.metadata["agentic_execution"]["kind"])
    subject_id = str(
        task.metadata.get("worker_id")
        or task.metadata["agentic_execution"]["run_id"]
    )
    handle = AgentRunHandle(
        run_id=f"run-{execution_kind}-{subject_id}",
        task_id=task.task_id,
        runtime=runtime,
        session_id=f"session-{execution_kind}-{subject_id}",
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
        cost_usd=0.2,
        resolved_model=f"{runtime}-resolved-model",
        result_hash="e" * 64,
        token_usage={
            "input_tokens": 13,
            "output_tokens": 5,
            "tokens_in": 13,
            "tokens_out": 5,
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
def test_agentic_roster_planner_runtime_runner_has_provider_parity(
    monkeypatch,
    tmp_path: Path,
    runtime: str,
):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic-secret")
    seen_tasks: list[AgentTask] = []

    def fake_runtime_runner(task: AgentTask) -> RuntimeExecution:
        seen_tasks.append(task)
        return _planner_or_worker_execution(
            task,
            runtime=runtime,
            output='{"workers":[]}',
        )

    production = plan_agentic_worker_roster(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Plan isolated workers.",
        min_subagents=0,
        required_roles=[],
        timeout_s=60,
        budget_usd=0.25,
        quality="best",
        runtime_runner=fake_runtime_runner,
        runtime_model=f"{runtime}-requested-model",
    )

    assert production.status == "passed"
    assert len(seen_tasks) == 1
    task = seen_tasks[0]
    assert task.inherit_env is False
    assert dict(task.env) == {}
    assert task.model == f"{runtime}-requested-model"
    assert task.metadata["agentic_execution"]["kind"] == "planner"
    assert production.planner["runtime"] == runtime
    assert production.planner["session_id"].startswith("session-planner-")
    assert production.planner["resolved_model"] == f"{runtime}-resolved-model"
    assert production.planner["cost_usd"] == 0.2
    assert production.planner["token_usage"]["tokens_out"] == 5
    assert production.planner["result_hash"] == "e" * 64
    assert "must-not-persist" not in json.dumps(production.planner)


def test_produce_agentic_worker_receipts_runs_planner_and_fanout_via_runtime(
    tmp_path: Path,
):
    roster = {
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "prompt": "Inspect implementation boundaries.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            },
            {
                "worker_id": "review-1",
                "role": "independent_reviewer",
                "prompt": "Review runtime evidence.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            },
        ]
    }
    seen_tasks: list[AgentTask] = []

    def fake_runtime_runner(task: AgentTask) -> RuntimeExecution:
        seen_tasks.append(task)
        kind = task.metadata["agentic_execution"]["kind"]
        output = json.dumps(roster) if kind == "planner" else "worker complete"
        return _planner_or_worker_execution(
            task,
            runtime="codex",
            output=output,
        )

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Plan and execute two isolated workers.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 2,
            "required_roles": ["codebase_audit", "independent_reviewer"],
        },
        existing_receipts=[],
        timeout_s=60,
        budget_usd=0.25,
        runtime_runner=fake_runtime_runner,
        runtime_model="codex-requested-model",
    )

    assert production.status == "passed"
    assert len(seen_tasks) == 3
    assert [
        task.metadata["agentic_execution"]["kind"]
        for task in seen_tasks
    ].count("planner") == 1
    assert {
        task.metadata["worker_id"]
        for task in seen_tasks
        if task.metadata["agentic_execution"]["kind"] == "worker"
    } == {"audit-1", "review-1"}
    assert [receipt["worker_id"] for receipt in production.receipts] == [
        "audit-1",
        "review-1",
    ]
    assert all(receipt["runtime"] == "codex" for receipt in production.receipts)
    assert all(
        receipt["session_id"].startswith("session-worker-")
        for receipt in production.receipts
    )
    assert all(receipt["result_hash"] == "e" * 64 for receipt in production.receipts)


def test_agentic_roster_planner_uses_scrubbed_direct_anthropic_env(
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key")
    configure_direct_anthropic_process_env(api_key="direct-key")
    for key, value in ambient_secrets.items():
        monkeypatch.setenv(key, value)
    runner_kwargs: dict[str, object] = {}

    def fake_planner(argv, **kwargs):
        runner_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"workers":[]}',
            stderr="",
        )

    try:
        production = plan_agentic_worker_roster(
            cwd=tmp_path,
            task_id="workflow-1",
            run_id="workflow-run",
            intent="Plan isolated workers.",
            min_subagents=0,
            required_roles=[],
            timeout_s=60,
            budget_usd=0.25,
            quality="best",
            runner=fake_planner,
        )
    finally:
        configure_direct_anthropic_process_env()

    assert production.status == "passed"
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


def test_agentic_roster_validation_rejects_over_budget_or_timeout_before_launch(tmp_path: Path):
    roster = {
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "persona_id": "reviewer.codebase_audit",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Inspect code paths only.",
                "timeout_s": 600,
                "budget_usd": 5.0,
            }
        ]
    }
    launch_calls: list[list[str]] = []

    def fake_planner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>",
            stderr="",
        )

    def fail_if_launched(specs):
        launch_calls.append([spec.worker_id for spec in specs])
        return []

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Require bounded read-only agentic workers.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 1,
            "required_roles": ["codebase_audit"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[],
        timeout_s=60,
        budget_usd=0.25,
        runner=fake_planner,
        fanout_runner=fail_if_launched,
    )

    assert production.status == "blocked"
    assert launch_calls == []
    reasons = {finding["reason"] for finding in production.blocking_findings}
    assert "worker_timeout_out_of_bounds" in reasons
    assert "worker_budget_out_of_bounds" in reasons


def test_agentic_roster_parser_accepts_direct_or_embedded_json_roster():
    direct = _extract_roster_payload(json.dumps({
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "prompt": "Inspect code.",
            }
        ]
    }))
    embedded = _extract_roster_payload(json.dumps({
        "result": (
            "Roster:\n"
            "{\"workers\":[{\"worker_id\":\"audit-2\","
            "\"role\":\"codebase_audit\",\"prompt\":\"Inspect receipts.\"}]}"
        )
    }))

    assert direct is not None
    assert direct["workers"][0]["worker_id"] == "audit-1"
    assert embedded is not None
    assert embedded["workers"][0]["worker_id"] == "audit-2"


def test_agentic_roster_validation_rejects_writable_or_missing_required_roles():
    findings = validate_agentic_worker_roster(
        [
            AgenticWorkerRosterItem(
                worker_id="writer-1",
                role="codebase_audit",
                prompt="Inspect implementation boundaries.",
                permission_mode="bypassPermissions",
                timeout_s=30,
                budget_usd=0.1,
            )
        ],
        min_subagents=1,
        required_roles=["codebase_audit", "independent_reviewer"],
        timeout_s=60,
        budget_usd=0.25,
    )

    reasons = {finding["reason"] for finding in findings}
    assert "worker_permission_mode_not_read_only" in reasons
    assert "missing_required_roster_role" in reasons
    assert any(finding.get("role") == "independent_reviewer" for finding in findings)


def test_agentic_worker_timeout_cleanup_runs_after_fanout_timeout(tmp_path: Path):
    roster = {
        "workers": [
            {
                "worker_id": "review-1",
                "role": "independent_reviewer",
                "persona_id": "reviewer.independent",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Review the fanout receipts.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            }
        ]
    }
    cleanup_calls: list[dict[str, object]] = []

    def fake_planner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>",
            stderr="",
        )

    def timeout_fanout(specs):
        spec = specs[0]
        return [
            {
                "kind": "dynamic_subagent_result",
                "worker_id": spec.worker_id,
                "role": spec.role,
                "status": "timeout",
                "decision": "revise",
                "timeout_s": spec.timeout_s,
                "budget_usd": spec.budget_usd,
                "log_ref": ".handoff/agentic-workers/workflow-1/review-1/worker.log",
            }
        ]

    def cleanup_runner(**kwargs):
        cleanup_calls.append(kwargs)
        return {
            "schema_version": "agentic-worker-cleanup/v1",
            "status": "cleanup_completed",
            "cleaned": [{"worker_id": "review-1", "reason": "timeout_exceeded"}],
            "active": [],
            "skipped": [],
        }

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Require cleanup after timeout.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 1,
            "required_roles": ["independent_reviewer"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[],
        timeout_s=60,
        budget_usd=0.25,
        runner=fake_planner,
        fanout_runner=timeout_fanout,
        cleanup_runner=cleanup_runner,
        now_s=lambda: 100.0,
    )

    assert production.status == "passed"
    assert cleanup_calls
    assert cleanup_calls[0]["workers"][0]["worker_id"] == "review-1"
    assert cleanup_calls[0]["workers"][0]["started_at_s"] < 100.0
    assert production.cleanup is not None
    assert production.cleanup["cleaned"][0]["reason"] == "timeout_exceeded"


def test_produce_agentic_worker_receipts_reuses_completed_workers_and_spawns_missing_roles(tmp_path: Path):
    roster = {
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "persona_id": "reviewer.codebase_audit",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Inspect implementation boundaries.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            },
            {
                "worker_id": "review-1",
                "role": "independent_reviewer",
                "persona_id": "reviewer.independent",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Review hydrated evidence.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            },
        ]
    }
    existing = {
        "kind": "dynamic_subagent_result",
        "receipt_id": "agentic-worker-audit-1",
        "worker_id": "audit-1",
        "role": "codebase_audit",
        "persona_id": "reviewer.codebase_audit",
        "status": "passed",
        "decision": "accept",
        "agent_runtime": "claude_code",
        "agent_id": "audit-1",
        "permission_mode": "readOnly",
        "tool_pins": ["rg", "sed"],
    }
    launched: list[str] = []

    def fake_planner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>",
            stderr="",
        )

    def fanout_runner(specs):
        launched.extend(spec.worker_id for spec in specs)
        return [
            {
                "kind": "dynamic_subagent_result",
                "worker_id": spec.worker_id,
                "role": spec.role,
                "status": "passed",
                "decision": "accept",
            }
            for spec in specs
        ]

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reuse completed workers and run missing roles only.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 2,
            "required_roles": ["codebase_audit", "independent_reviewer"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[existing],
        timeout_s=60,
        budget_usd=0.25,
        runner=fake_planner,
        fanout_runner=fanout_runner,
    )

    assert production.status == "passed"
    assert launched == ["review-1"]
    assert production.planner["existing_completed_receipt_count"] == 1
    assert production.planner["skipped_completed_worker_ids"] == ["audit-1"]


def test_produce_agentic_worker_receipts_skips_when_existing_receipts_satisfy_policy(tmp_path: Path):
    existing = {
        "kind": "dynamic_subagent_result",
        "receipt_id": "agentic-worker-audit-1",
        "worker_id": "audit-1",
        "role": "codebase_audit",
        "status": "passed",
        "decision": "accept",
    }
    planner_calls: list[list[str]] = []

    def fail_if_planned(argv, **kwargs):
        planner_calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Existing receipt already satisfies required policy.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 1,
            "required_roles": ["codebase_audit"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[existing],
        timeout_s=60,
        budget_usd=0.25,
        runner=fail_if_planned,
    )

    assert production.status == "skipped_existing_receipts"
    assert planner_calls == []
