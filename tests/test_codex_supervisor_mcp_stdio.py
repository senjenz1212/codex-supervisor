from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
import threading
from dataclasses import replace
from hashlib import sha256
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 support
    import tomli as tomllib
from pathlib import Path

import pytest

from supervisor.config import Config
from supervisor.agent_runtime import AgentRunHandle, AgentRunResult, AgentTask
from supervisor.autoresearch.policy_evolution import PolicyClaimAuthority
from supervisor.dual_agent import ProbeResult
from supervisor.dual_agent_lead import LeadInvocationResult, PlanningArtifact
from supervisor.dual_agent_runner import DualAgentGateResult, DualAgentGateSpec
from supervisor.runtime_execution import RuntimeExecution
from supervisor.state import State
from supervisor.target.types import ScopeContract
from tests.test_claim_gate import (
    _authoritative_causal_bundle,
    _claim_gate_kwargs,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


def _evaluator_quality_controls() -> dict:
    return {
        "source": "supervisor_control_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "candidate_affects_evaluated_path": True,
        "determinism": {
            "source": "repeated_execution",
            "evidence_grade": "runtime_native",
            "supervisor_runtime_origin": "run_evaluator_quality_controls",
            "output_hashes": ["stable-output", "stable-output"],
        },
        "controls": {
            "noop": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": 0.0,
            },
            "harmful": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": -0.1,
            },
            "known_good": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": 0.2,
            },
        },
    }


def _claim_gate_authorize_policy_report(
    report: dict,
    *,
    authority: PolicyClaimAuthority,
) -> dict:
    return authority.derive_report(report)


def _policy_claim_authority(evidence_root: Path) -> PolicyClaimAuthority:
    evidence, ledger_resolver = _authoritative_causal_bundle(evidence_root)
    claim_gate_kwargs = _claim_gate_kwargs(ledger_resolver)
    return PolicyClaimAuthority(
        evidence_bundle=evidence,
        evidence_root=evidence_root,
        ledger_verification_resolver=claim_gate_kwargs.get(
            "ledger_verification_resolver"
        ),
        grade_authority=claim_gate_kwargs.get("grade_authority"),
        trusted_verifier_attestors=claim_gate_kwargs.get(
            "trusted_verifier_attestors"
        ),
        trusted_external_authorities=claim_gate_kwargs.get(
            "trusted_external_authorities"
        ),
    )


def _cfg(tmp_path) -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
    })


def test_harness_v1_execution_gate_requires_pinned_trace_closure(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        State(str(tmp_path / "state.db")),
    )
    spec = api._gate_spec(
        task_id="runtime-001-seams-20260711",
        run_id="run-trace-default",
        gate="execution",
        instruction="Execute only with a closed trace.",
        cwd=str(tmp_path),
        expected_specialists=None,
        expected_decisions=None,
        expected_objections=None,
        quality="best",
        model=None,
        budget_usd=1.0,
        timeout_s=30,
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        agentic_policy={
            "agentic_lead_policy": "off",
            "min_subagents": 0,
            "required_roles": (),
            "solo_exception_for_artifact_only_gates": False,
            "required_evidence_grade": "self_reported",
        },
        planning_artifacts=None,
    )

    assert spec.trace_closure_required is True
    assert spec.trace_graph is None
    assert spec.trace_now is not None


def test_harness_v1_workflow_rejects_explicit_trace_closure_downgrade(
    tmp_path,
):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    with pytest.raises(
        ValueError,
        match="harness-v1 tasks cannot disable trace closure",
    ):
        api.submit_dual_agent_workflow_job(
            cwd=str(tmp_path),
            task_id="trace-001-graph-20260711",
            run_id="run-trace-downgrade",
            intent="Attempt to disable canonical trace closure.",
            trace_closure_required=False,
        )

    assert state.list_dual_agent_workflow_jobs(active_only=True) == []


def test_required_gate_records_complete_post_execution_trace(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    plan = tmp_path / "implementation-plan.md"
    plan.write_text("# Implementation Plan\n\nExecute the pinned plan.\n")
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    probe = ProbeResult(
        "P2",
        "green",
        "worker_orchestration_invocation_ok",
    )
    result = DualAgentGateResult(
        task_id="runtime-001-seams-20260711",
        gate="execution",
        status="accepted",
        probes={"P2": probe},
        handoff_packet_path=tmp_path / ".handoff" / "task.json",
        lead_result=LeadInvocationResult(
            probe=probe,
            outcome=None,
            command=["codex"],
            stdout="accepted",
            stderr="",
            stdout_bytes=8,
            stderr_bytes=0,
            transcript="accepted",
            model="provider/model",
            runtime="codex",
            runtime_run_id="runtime-run-1",
            runtime_session_id="runtime-session-1",
            runtime_result_hash="a" * 64,
        ),
        attempts=1,
    )
    spec = DualAgentGateSpec(
        task_id=result.task_id,
        run_id="workflow-run-1",
        gate="execution",
        instruction="Execute the pinned Harness v1 task.",
        cwd=tmp_path,
        planning_artifacts=(
            PlanningArtifact(
                path=plan,
                kind="implementation_plan",
            ),
        ),
        trace_closure_required=True,
    )
    payload = {
        "task_id": result.task_id,
        "gate": "execution",
        "status": "accepted",
        "attempts": 1,
        "outcome": None,
        "tool_calls": [{
            "name": "invoke_runtime_lead",
            "runtime": "codex",
            "runtime_run_id": "runtime-run-1",
            "runtime_session_id": "runtime-session-1",
            "runtime_result_hash": "a" * 64,
            "model": "provider/model",
        }],
        "target_run_registrations": [{
            "target_run_id": "target-run-1",
            "target_session_id": "runtime-session-1",
            "runtime_run_id": "runtime-run-1",
            "runtime_result_hash": "a" * 64,
        }],
        "trace_closure_binding": {
            "schema_version": "supervisor-trace-closure-binding/v1",
            "task_id": result.task_id,
            "run_id": "workflow-run-1",
            "gate": "execution",
            "planning_artifacts": [{
                "kind": "implementation_plan",
                "path": str(plan.resolve()),
                "sha256": sha256(plan.read_bytes()).hexdigest(),
            }],
        },
        "production_trace_workspace_root": str(tmp_path.resolve()),
    }
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=payload,
    )

    receipt = api._record_gate_production_trace(
        spec=spec,
        source_event_id=source_event_id,
    )

    assert receipt is not None
    assert receipt["claim_cap"] == "L1"
    assert receipt["closure"]["status"] == "accepted"
    assert receipt["evidence"]["runtime_provenance"]["assignment_id"] == (
        "target-run-1"
    )
    assert Path(receipt["trace_store_path"]).is_file()
    assert Path(receipt["gradebook_path"]).is_file()
    events = state.read_dual_agent_gate_events(spec.run_id)
    trace_events = [
        event
        for event in events
        if event["kind"] == "dual_agent_production_trace_recorded"
    ]
    assert len(trace_events) == 1
    trace_payload = json.loads(trace_events[0]["payload_json"])
    assert trace_payload["receipt"]["trace_graph"] == receipt["trace_graph"]


def _production_trace_gate_fixture(
    tmp_path: Path,
) -> tuple[
    object,
    State,
    DualAgentGateSpec,
    DualAgentGateResult,
    dict,
]:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    plan = tmp_path / "fixture-implementation-plan.md"
    plan.write_text("# Implementation Plan\n\nExecute the pinned plan.\n")
    state = State(str(tmp_path / "fixture-state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    probe = ProbeResult(
        "P2",
        "green",
        "worker_orchestration_invocation_ok",
    )
    result = DualAgentGateResult(
        task_id="runtime-001-seams-20260711",
        gate="execution",
        status="accepted",
        probes={"P2": probe},
        handoff_packet_path=tmp_path / ".handoff" / "fixture.json",
        lead_result=LeadInvocationResult(
            probe=probe,
            outcome=None,
            command=["codex"],
            stdout="accepted",
            stderr="",
            stdout_bytes=8,
            stderr_bytes=0,
            transcript="accepted",
            model="provider/model",
            runtime="codex",
            runtime_run_id="fixture-runtime-run",
            runtime_session_id="fixture-runtime-session",
            runtime_result_hash="b" * 64,
        ),
        attempts=1,
    )
    spec = DualAgentGateSpec(
        task_id=result.task_id,
        run_id="fixture-workflow-run",
        gate="execution",
        instruction="Execute the pinned Harness v1 fixture.",
        cwd=tmp_path,
        planning_artifacts=(
            PlanningArtifact(
                path=plan,
                kind="implementation_plan",
            ),
        ),
        trace_closure_required=True,
    )
    payload = {
        "task_id": result.task_id,
        "gate": "execution",
        "status": "accepted",
        "attempts": 1,
        "outcome": None,
        "tool_calls": [{
            "name": "invoke_runtime_lead",
            "runtime": "codex",
            "runtime_run_id": "fixture-runtime-run",
            "runtime_session_id": "fixture-runtime-session",
            "runtime_result_hash": "b" * 64,
            "model": "provider/model",
        }],
        "target_run_registrations": [{
            "target_run_id": "fixture-target-run",
            "target_session_id": "fixture-runtime-session",
            "runtime_run_id": "fixture-runtime-run",
            "runtime_result_hash": "b" * 64,
        }],
        "trace_closure_binding": {
            "schema_version": "supervisor-trace-closure-binding/v1",
            "task_id": result.task_id,
            "run_id": spec.run_id,
            "gate": "execution",
            "planning_artifacts": [{
                "kind": "implementation_plan",
                "path": str(plan.resolve()),
                "sha256": sha256(plan.read_bytes()).hexdigest(),
            }],
        },
        "production_trace_workspace_root": str(tmp_path.resolve()),
    }
    return api, state, spec, result, payload


def test_production_trace_uses_the_persisted_event_as_dynamic_authority(
    tmp_path: Path,
) -> None:
    api, state, spec, result, accepted_payload = (
        _production_trace_gate_fixture(tmp_path)
    )
    persisted_payload = {
        **accepted_payload,
        "status": "rejected",
    }
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=persisted_payload,
    )

    parameters = inspect.signature(
        api._record_gate_production_trace
    ).parameters
    receipt = api._record_gate_production_trace(
        spec=spec,
        source_event_id=source_event_id,
    )

    assert "result" not in parameters
    assert "payload" not in parameters
    assert result.status == "accepted"
    assert accepted_payload["status"] == "accepted"
    assert receipt is not None
    assert receipt["evidence"]["source_event_state"] == "completed"
    assert receipt["grade_revision"]["passed"] is False
    assert (
        receipt["grade_revision"]["failure_classification"] == "gate_failed"
    )
    assert (
        receipt["evidence"]["final_gate_result"]["status"]
        == "rejected"
    )


def test_persisted_trace_binding_cannot_be_suppressed_by_caller_spec(
    tmp_path: Path,
) -> None:
    api, state, spec, _result, payload = _production_trace_gate_fixture(
        tmp_path
    )
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=payload,
    )

    receipt = api._record_gate_production_trace(
        spec=replace(spec, trace_closure_required=False),
        source_event_id=source_event_id,
    )

    assert receipt is not None
    assert receipt["evidence"]["source_event_hash"]


def test_production_trace_storage_and_event_are_replay_idempotent(
    tmp_path: Path,
) -> None:
    api, state, spec, _result, payload = _production_trace_gate_fixture(
        tmp_path
    )
    caller_workspace = tmp_path / "different-caller-workspace"
    caller_workspace.mkdir()
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=payload,
    )
    replay_spec = replace(spec, cwd=caller_workspace)

    first = api._record_gate_production_trace(
        spec=replay_spec,
        source_event_id=source_event_id,
    )
    second = api._record_gate_production_trace(
        spec=replay_spec,
        source_event_id=source_event_id,
    )
    trace_events = [
        event
        for event in state.read_dual_agent_gate_events(spec.run_id)
        if event["kind"] == "dual_agent_production_trace_recorded"
    ]

    assert first == second
    assert first is not None
    assert Path(first["trace_store_path"]).is_relative_to(tmp_path.resolve())
    assert not Path(first["trace_store_path"]).is_relative_to(
        caller_workspace.resolve()
    )
    assert len(trace_events) == 1


def test_production_trace_concurrent_retry_emits_one_recorded_event(
    tmp_path: Path,
) -> None:
    api, state, spec, _result, payload = _production_trace_gate_fixture(
        tmp_path
    )
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=payload,
    )
    barrier = threading.Barrier(2)
    receipts: list[dict] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def record() -> None:
        try:
            barrier.wait(timeout=5)
            receipt = api._record_gate_production_trace(
                spec=spec,
                source_event_id=source_event_id,
            )
            assert receipt is not None
            with lock:
                receipts.append(receipt)
        except BaseException as exc:  # pragma: no cover - asserted below.
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=record) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    assert errors == []
    assert len(receipts) == 2
    assert receipts[0] == receipts[1]
    trace_events = [
        event
        for event in state.read_dual_agent_gate_events(spec.run_id)
        if event["kind"] == "dual_agent_production_trace_recorded"
    ]
    assert len(trace_events) == 1


def test_production_trace_rejects_wrong_source_event_kind(
    tmp_path: Path,
) -> None:
    api, state, spec, result, payload = _production_trace_gate_fixture(
        tmp_path
    )
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_interaction_message",
        payload=payload,
    )

    with pytest.raises(
        RuntimeError,
        match="persisted dual-agent gate-result event",
    ):
        api._record_gate_production_trace(
            spec=spec,
            source_event_id=source_event_id,
        )


def test_production_trace_rejects_broken_source_event_chain(
    tmp_path: Path,
) -> None:
    api, state, spec, result, payload = _production_trace_gate_fixture(
        tmp_path
    )
    source_event_id = state.write_event(
        run_id=spec.run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=payload,
    )
    state._conn.execute("DROP TRIGGER IF EXISTS events_no_update")
    state._conn.execute(
        "UPDATE events SET event_hash=? WHERE event_id=?",
        ("0" * 64, source_event_id),
    )
    state._conn.commit()

    with pytest.raises(
        RuntimeError,
        match="source event ledger is invalid",
    ):
        api._record_gate_production_trace(
            spec=spec,
            source_event_id=source_event_id,
        )


def test_gate_runtime_registration_fails_closed_without_parent_run(
    tmp_path: Path,
) -> None:
    api, _state, spec, result, _payload = _production_trace_gate_fixture(
        tmp_path
    )

    with pytest.raises(
        RuntimeError,
        match="workflow run is not registered",
    ):
        api._register_gate_runtime_sessions(
            run_id=spec.run_id,
            task_id=spec.task_id,
            task=spec.instruction,
            gate=str(spec.gate),
            cwd=str(spec.cwd),
            result=result,
        )


def test_gate_runtime_registration_rejects_conflicting_same_session_attempts(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-run",
        target_session_id="",
        task_id="task-1",
        task="Execute the task.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )
    probe = ProbeResult("P2", "green", "runtime completed")
    result = DualAgentGateResult(
        task_id="task-1",
        gate="execution",
        status="accepted",
        probes={"P2": probe},
        handoff_packet_path=tmp_path / ".handoff" / "task.json",
        tool_calls=(
            {
                "runtime": "codex",
                "runtime_run_id": "runtime-run-1",
                "runtime_session_id": "runtime-session-1",
                "runtime_result_hash": "a" * 64,
            },
            {
                "runtime": "codex",
                "runtime_run_id": "runtime-run-2",
                "runtime_session_id": "runtime-session-1",
                "runtime_result_hash": "b" * 64,
            },
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="conflicting run/result provenance",
    ):
        api._register_gate_runtime_sessions(
            run_id="workflow-run",
            task_id="task-1",
            task="Execute the task.",
            gate="execution",
            cwd=str(tmp_path),
            result=result,
        )


def test_runtime_evidence_records_bind_agentic_and_reviewer_sessions(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-runtime-evidence",
        target_session_id="",
        task_id="task-runtime-evidence",
        task="Bind every controlled runtime result.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )

    registrations = api._register_runtime_evidence_records(
        run_id="workflow-runtime-evidence",
        task_id="task-runtime-evidence",
        task="Bind every controlled runtime result.",
        gate="workflow_start",
        cwd=str(tmp_path),
        source="controlled_runtime_result",
        records=(
            {
                "runtime": "codex",
                "runtime_run_id": "planner-runtime-run",
                "session_id": "planner-runtime-session",
                "result_hash": "c" * 64,
            },
            {
                "diagnostics": {
                    "agent_runtime": {
                        "runtime": "claude_code",
                        "run_id": "reviewer-runtime-run",
                        "session_id": "reviewer-runtime-session",
                        "result_hash": "d" * 64,
                    },
                },
            },
        ),
    )

    assert {
        registration["target_session_id"]
        for registration in registrations
    } == {
        "planner-runtime-session",
        "reviewer-runtime-session",
    }
    for registration in registrations:
        snapshot = state.get_run_snapshot(registration["target_run_id"])
        assert snapshot is not None
        config = json.loads(snapshot["config_json"])
        assert config["workflow_run_id"] == "workflow-runtime-evidence"
        assert config["runtime_run_id"]
        assert len(config["runtime_result_hash"]) == 64


def test_runtime_evidence_records_fail_closed_on_partial_provenance(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-partial-runtime",
        target_session_id="",
        task_id="task-partial-runtime",
        task="Reject incomplete runtime provenance.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )

    with pytest.raises(
        RuntimeError,
        match="requires exact runtime, run, session, and result-hash provenance",
    ):
        api._register_runtime_evidence_records(
            run_id="workflow-partial-runtime",
            task_id="task-partial-runtime",
            task="Reject incomplete runtime provenance.",
            gate="workflow_start",
            cwd=str(tmp_path),
            source="controlled_runtime_result",
            records=({
                "runtime": "codex",
                "runtime_run_id": "runtime-run",
                "session_id": "runtime-session",
                "result_hash": "not-a-sha256",
            },),
        )

    assert state.get_run_by_session("runtime-session") is None


def test_runtime_evidence_records_do_not_register_local_fallback_session(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-fallback-runtime",
        target_session_id="",
        task_id="task-fallback-runtime",
        task="Do not invent a provider session.",
        target_kind="claude_code",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )

    registrations = api._register_runtime_evidence_records(
        run_id="workflow-fallback-runtime",
        task_id="task-fallback-runtime",
        task="Do not invent a provider session.",
        gate="workflow_start",
        cwd=str(tmp_path),
        source="controlled_runtime_result",
        records=({
            "runtime": "claude_code",
            "runtime_run_id": "local-runtime-id",
            "session_id": "local-runtime-id",
            "result_hash": "e" * 64,
        },),
    )

    assert registrations == []
    assert state.get_run_by_session("local-runtime-id") is None


def test_runtime_evidence_records_ignore_non_runtime_receipts(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-non-runtime-receipt",
        target_session_id="",
        task_id="task-non-runtime-receipt",
        task="Ignore policy receipts at the runtime-session boundary.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )

    registrations = api._register_runtime_evidence_records(
        run_id="workflow-non-runtime-receipt",
        task_id="task-non-runtime-receipt",
        task="Ignore policy receipts at the runtime-session boundary.",
        gate="workflow_start",
        cwd=str(tmp_path),
        source="controlled_runtime_result",
        records=({
            "receipt_id": "policy-only",
            "kind": "dynamic_subagent_result",
            "status": "passed",
        },),
    )

    assert registrations == []


def test_trace_graph_store_requires_sha256_pin(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.trace_graph import TraceGraphStore

    trace_path = tmp_path / "trace.db"
    with TraceGraphStore(trace_path):
        pass
    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        State(str(tmp_path / "state.db")),
    )

    with pytest.raises(ValueError, match="explicit sha256 pin"):
        api._gate_spec(
            task_id="runtime-001-seams-20260711",
            run_id="run-trace-unpinned",
            gate="execution",
            instruction="Execute only with a pinned trace.",
            cwd=str(tmp_path),
            expected_specialists=None,
            expected_decisions=None,
            expected_objections=None,
            quality="best",
            model=None,
            budget_usd=1.0,
            timeout_s=30,
            execution_layer_mode="lead_direct",
            dynamic_workflow_task_class=None,
            agentic_policy={
                "agentic_lead_policy": "off",
                "min_subagents": 0,
                "required_roles": (),
                "solo_exception_for_artifact_only_gates": False,
                "required_evidence_grade": "self_reported",
            },
            planning_artifacts=None,
            trace_graph_store_path=str(trace_path),
        )


def test_codex_supervisor_mcp_stdio_tools_call_keeps_protocol_stream_clean(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(_cfg(tmp_path).model_dump_json(), encoding="utf-8")
    request = "\n".join([
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "stdio-smoke", "version": "0"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "check_budget",
                "arguments": {"rounds": [], "per_gate_cap": 1, "task_budget": 1},
            },
        }),
        "",
    ])

    completed = subprocess.run(
        [sys.executable, "-m", "mcp_tools.codex_supervisor_stdio", "--config", str(config_path)],
        input=request,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 2
    assert "Processing request of type" not in completed.stderr
    assert completed.stderr.strip() == ""
    for line in stdout_lines:
        payload = json.loads(line)
        assert payload["jsonrpc"] == "2.0"


def _outcome_block(
    task_id: str = "gate-1",
    decision: str = "accept plan",
    critical_review: dict | None = None,
    *,
    tests: list[str] | None = None,
    test_status: str = "unknown",
) -> str:
    payload = {
        "task_id": task_id,
        "summary": "Implemented through /lead.",
        "specialists": [{"name": "Planner", "decision": decision}],
        "decisions": [decision],
        "objections": [],
        "changed_files": ["supervisor/dual_agent.py"],
        "tests": tests if tests is not None else [],
        "test_status": test_status,
        "confidence": 0.94,
    }
    if critical_review is not None:
        payload["critical_review"] = critical_review
    return f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"


class _FakeMCP:
    def __init__(self, name: str):
        self.name = name
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _run_dual_agent_workflow_direct(server, **kwargs):
    return server._codex_supervisor_tool_api.run_dual_agent_workflow(**kwargs)


def _init_runtime_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_runtime_file(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tiny_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )


def test_injected_cursor_runner_does_not_build_ambient_reviewer_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_tools.codex_supervisor_stdio as stdio

    builds: list[Config | None] = []

    def fake_builder(cfg: Config | None):
        builds.append(cfg)
        return object()

    def fake_cursor_runner(request):
        raise AssertionError("constructor test must not invoke the cursor runner")

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-be-consumed")
    monkeypatch.setattr(stdio, "_build_reviewer_model_client", fake_builder)

    api = stdio.CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        State(str(tmp_path / "state.db")),
        cursor_runner=fake_cursor_runner,
    )

    assert builds == []
    assert api.reviewer_model_client is None
    assert api.cursor_runner is fake_cursor_runner


def test_explicit_reviewer_model_client_wins_with_injected_cursor_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_tools.codex_supervisor_stdio as stdio

    def builder_must_not_run(cfg: Config | None):
        raise AssertionError("explicit reviewer_model_client must win")

    def fake_cursor_runner(request):
        raise AssertionError("constructor test must not invoke the cursor runner")

    explicit_client = object()
    monkeypatch.setattr(
        stdio,
        "_build_reviewer_model_client",
        builder_must_not_run,
    )

    api = stdio.CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        State(str(tmp_path / "state.db")),
        reviewer_model_client=explicit_client,
        cursor_runner=fake_cursor_runner,
    )

    assert api.reviewer_model_client is explicit_client
    assert api.cursor_runner is fake_cursor_runner


def _write_planning_artifacts(tmp_path: Path, *, include_implementation_plan: bool = True) -> list[dict]:
    artifact_dir = tmp_path / "docs" / "dual-agent" / "gate-1"
    artifact_dir.mkdir(parents=True)
    files = {
        "prd": (artifact_dir / "prd.md", FIXTURE_ROOT / "prd" / "good.md"),
        "tdd_plan": (artifact_dir / "tdd.md", FIXTURE_ROOT / "tdd_plan" / "good.md"),
        "grill_findings": (
            artifact_dir / "grill-findings.md",
            FIXTURE_ROOT / "grill_findings" / "good.md",
        ),
        "issues": (artifact_dir / "issues.md", FIXTURE_ROOT / "issues" / "good.md"),
    }
    if include_implementation_plan:
        files["implementation_plan"] = (
            artifact_dir / "implementation-plan.md",
            FIXTURE_ROOT / "implementation_plan" / "good.md",
        )
    artifacts = []
    for kind, (path, fixture) in files.items():
        path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
        artifacts.append({"path": str(path), "kind": kind, "mutable_by_worker": False})
    return artifacts


def _stub_prd_artifact() -> list[dict]:
    return [{
        "path": str(FIXTURE_ROOT / "prd" / "sneaky.md"),
        "kind": "prd",
        "mutable_by_worker": False,
    }]


def test_tdd_grill_findings_kind_alias_resolves_to_grill_findings(tmp_path):
    from mcp_tools.codex_supervisor_stdio import (
        _maybe_artifact,
        _normalise_artifact_kind,
        _planning_artifact_role,
    )

    path = tmp_path / "source" / "grill-findings-tdd.md"
    path.parent.mkdir()
    path.write_text(
        (FIXTURE_ROOT / "grill_findings" / "good.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    for kind in ("grill-findings-tdd", "grill_findings_tdd", "grill.findings.tdd"):
        payload = {"path": str(path), "kind": kind, "mutable_by_worker": False}
        assert _normalise_artifact_kind(kind) == "grill_findings"
        assert _planning_artifact_role(payload) == "grill_findings"
        assert _maybe_artifact(payload).kind == "grill_findings"


def test_artifact_kind_normalisation_preserves_existing_kinds():
    from mcp_tools.codex_supervisor_stdio import _normalise_artifact_kind

    assert _normalise_artifact_kind("prd") == "prd"
    assert _normalise_artifact_kind("tdd_plan") == "tdd_plan"
    assert _normalise_artifact_kind("grill_findings") == "grill_findings"
    assert _normalise_artifact_kind("implementation_plan") == "implementation_plan"
    assert _normalise_artifact_kind("unknown_kind") == "unknown_kind"


def _write_accepted_gate(
    state: State,
    *,
    run_id: str = "run-1",
    task_id: str = "gate-1",
    gate: str,
) -> None:
    state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": task_id,
            "gate": gate,
            "status": "accepted",
            "attempts": 1,
            "handoff_packet_path": f"/tmp/.handoff/{task_id}.json",
            "probes": {},
            "outcome": {
                "task_id": task_id,
                "summary": f"{gate} accepted.",
                "specialists": [{"name": "Reviewer", "decision": "accept"}],
                "decisions": ["accept"],
                "objections": [],
                "changed_files": [],
                "tests": [],
                "test_status": "passed",
                "confidence": 0.95,
                "claims": [],
            },
            "escalation": None,
        },
    )


def _skill_receipts() -> list[dict]:
    return [
        {
            "receipt_id": f"skill-{stage}",
            "kind": "skill_run",
            "status": "passed",
            "skill": skill,
            "stage": stage,
        }
        for stage, skill in [
            ("to_prd", "to-prd"),
            ("prd_grill", "grill-with-docs"),
            ("to_issues", "to-issues"),
            ("tdd", "tdd"),
            ("tdd_grill", "grill-with-docs"),
        ]
    ]


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_exposes_dual_agent_gate_tools(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    assert set(server.tools) >= {
        "start_dual_agent_gate",
        "record_gate_round",
        "read_gate_transcript",
        "read_outcome",
        "export_gate_artifacts",
        "create_autoresearch_policy_proposals",
        "approve_autoresearch_policy_proposal",
        "deny_autoresearch_policy_proposal",
        "rollback_autoresearch_policy_proposal",
        "run_dual_agent_workflow",
        "submit_dual_agent_workflow_job",
        "poll_dual_agent_workflow_job",
        "catch_up_dual_agent_workflow",
        "read_dual_agent_workflow_resume_prompt",
        "check_budget",
        "escalate_deadlock",
        "poll_resume_signal",
        "start_codex_session",
    }

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="prd_review",
        instruction="Run the gate.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=_write_planning_artifacts(tmp_path),
    ))

    assert result["status"] == "accepted"
    assert result["outcome"]["task_id"] == "gate-1"

    outcome = await _maybe_await(server.tools["read_outcome"](
        run_id="run-1",
        task_id="gate-1",
    ))
    assert outcome["status"] == "ok"
    assert outcome["result"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_export_gate_artifacts_reconstructs_stored_execution_provenance(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    run_id = "run-public-export-provenance"
    task_id = "public-export-provenance"
    repo = tmp_path / "repo"
    repo.mkdir()
    handoff = repo / ".handoff" / f"{task_id}.json"
    handoff.parent.mkdir()
    handoff_content = json.dumps({
        "task_id": task_id,
        "cwd": str(repo),
        "planning_artifacts": [],
    })
    handoff.write_text(handoff_content, encoding="utf-8")

    state = State(str(tmp_path / "state.db"))
    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        reviewer_adapters=[],
    )
    export_parameters = inspect.signature(
        server.tools["export_gate_artifacts"]
    ).parameters
    assert {
        "provider_model_resolutions",
        "canonical_tool_contracts",
        "runtime_component_receipts",
    }.isdisjoint(export_parameters)
    interaction_event_id = state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_interaction_message",
        payload={
            "task_id": task_id,
            "gate": "outcome_review",
            "message_type": "gate_request",
            "content": "Review the execution-time provenance.",
            "requested_model": "default",
            "model": "default",
            "runtime": "custom",
            "provider_family": "provider",
            "container_digest": "b" * 64,
            "trace_envelope": {
                "tool_calls": [
                    {
                        "name": "invoke_custom",
                        "args": {
                            "runtime": "custom",
                            "cli_command": "custom-cli",
                        },
                    },
                    {
                        "name": "verify_result",
                        "args": {},
                    },
                ],
            },
        },
    )
    contract_bytes = {
        name: json.dumps(
            {"name": name, "inputSchema": {"type": "object"}},
            sort_keys=True,
            separators=(",", ":"),
        )
        for name in ("invoke_custom", "verify_result")
    }
    provider_resolution = {
        "event_id": interaction_event_id,
        "gate": "outcome_review",
        "lane": "dual_agent_interaction_message",
        "runtime": "custom",
        "provider_family": "provider",
        "requested_model": "default",
        "resolved_model": "provider/model-v1-20260713",
        "provider_response_receipt_ref": (
            "receipt://provider-response/public-export"
        ),
    }
    canonical_contracts = [
        {
            "tool_name": name,
            "canonical_bytes": content,
            "sha256": sha256(content.encode()).hexdigest(),
            "receipt_ref": f"receipt://tool-contract/{name}",
            "capture_source": "execution_time",
            "source": "runtime_tool_registry",
        }
        for name, content in contract_bytes.items()
    ]
    container_receipt = {
        "category": "containers",
        "component_id": "container:container_digest",
        "sha256": "b" * 64,
        "receipt_ref": "receipt://runtime-component/container/main",
        "capture_source": "execution_time",
        "source": "runtime_component_receipt",
    }
    cli_receipt = {
        "category": "cli",
        "component_id": "cli:invoke_custom",
        "canonical_bytes": "custom cli executable bytes",
        "sha256": sha256(b"custom cli executable bytes").hexdigest(),
        "receipt_ref": "receipt://runtime-component/cli/custom",
        "capture_source": "execution_time",
        "source": "runtime_component_receipt",
    }
    evaluator_receipt = {
        "category": "evaluators",
        "component_id": "evaluator:verify_result",
        "canonical_bytes": "verify result evaluator bytes",
        "sha256": sha256(b"verify result evaluator bytes").hexdigest(),
        "receipt_ref": (
            "receipt://runtime-component/evaluator/verify-result"
        ),
        "capture_source": "execution_time",
        "source": "runtime_component_receipt",
    }
    state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_runtime_evidence",
        payload={
            "task_id": "different-task",
            "gate": "outcome_review",
            "provider_model_resolutions": [{
                **provider_resolution,
                "resolved_model": "provider/conflicting-model-v2",
            }],
        },
    )
    state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_runtime_evidence",
        payload={
            "task_id": task_id,
            "gate": "outcome_review",
            "provider_model_resolutions": [provider_resolution],
            "replay_provenance": {
                "canonical_tool_contracts": canonical_contracts,
                "runtime_component_receipts": [container_receipt],
            },
            "receipts": [cli_receipt, evaluator_receipt],
            "tool_receipts": [
                {
                    **provider_resolution,
                    "receipt_type": "provider_model_resolution",
                    "resolved_model": "provider/caller-forged-model-v3",
                    "provider_response_receipt_ref": (
                        "receipt://caller/provider-response"
                    ),
                },
                {
                    "receipt_type": "canonical_tool_contract",
                    "tool_name": "invoke_custom",
                    "canonical_bytes": "caller-forged contract",
                    "sha256": sha256(b"caller-forged contract").hexdigest(),
                    "receipt_ref": "receipt://caller/tool-contract",
                    "capture_source": "execution_time",
                },
                {
                    "category": "cli",
                    "component_id": "cli:invoke_custom",
                    "canonical_bytes": "caller-forged cli",
                    "sha256": sha256(b"caller-forged cli").hexdigest(),
                    "receipt_ref": "receipt://caller/runtime-component",
                    "capture_source": "execution_time",
                },
            ],
        },
    )
    state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": task_id,
            "gate": "outcome_review",
            "status": "accepted",
            "attempts": 1,
            "handoff_packet_path": str(handoff),
            "probes": {},
            "outcome": None,
            "escalation": None,
            "acceptance_evidence": {
                "handoff_packet": {
                    "path": str(handoff),
                    "status": "captured",
                    "sha256": sha256(handoff_content.encode()).hexdigest(),
                    "content": handoff_content,
                },
                "workspace_snapshot": {
                    "status": "captured",
                    "capture_source": "accepted_gate_event",
                    "root": str(repo),
                    "git": {"head_sha": "a" * 40},
                    "file_tree_sha256": "d" * 64,
                    "immutable_snapshot": {
                        "status": "captured",
                        "sha256": "e" * 64,
                    },
                },
            },
        },
    )

    output_dir = tmp_path / "public-export"
    result = await _maybe_await(server.tools["export_gate_artifacts"](
        run_id=run_id,
        task_id=task_id,
        cwd=str(repo),
        output_dir=str(output_dir),
    ))

    manifest = json.loads(
        (output_dir / "replay" / "manifest.json").read_text()
    )
    provenance = manifest["execution_provenance"]
    assert result["task_id"] == task_id
    assert provenance["status"] == "complete"
    assert provenance["unresolved_model_lanes"] == []
    assert provenance["missing_component_categories"] == []
    assert provenance["model_resolutions"][0]["resolved_model"] == (
        "provider/model-v1-20260713"
    )
    assert provenance["model_resolutions"][0][
        "provider_response_source"
    ] == "receipt://provider-response/public-export"
    assert {
        component["details"]["receipt_ref"]
        for component in provenance["component_hashes"]["tool_contracts"]
    } == {
        "receipt://tool-contract/invoke_custom",
        "receipt://tool-contract/verify_result",
    }
    assert {
        component["details"]["receipt_ref"]
        for category in ("containers", "cli", "evaluators")
        for component in provenance["component_hashes"][category]
    } == {
        "receipt://runtime-component/container/main",
        "receipt://runtime-component/cli/custom",
        "receipt://runtime-component/evaluator/verify-result",
    }


@pytest.mark.asyncio
async def test_mcp_gate_uses_runtime_lead_and_persists_runtime_provenance(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    runtime_tasks: list[AgentTask] = []

    def legacy_runner_must_not_run(*args, **kwargs):
        raise AssertionError("runtime-backed MCP gate invoked the legacy runner")

    def fake_runtime_runner(task: AgentTask) -> RuntimeExecution:
        runtime_tasks.append(task)
        handle = AgentRunHandle(
            run_id="runtime-lead-run",
            task_id=task.task_id,
            runtime="claude_code",
            session_id="runtime-lead-session",
            capabilities={"cancel": True, "stream": True},
        )
        result = AgentRunResult(
            run_id=handle.run_id,
            task_id=task.task_id,
            runtime=handle.runtime,
            session_id=handle.session_id,
            status="completed",
            output=_outcome_block(task.task_id),
            events=(),
            started_at_ms=100,
            ended_at_ms=125,
            cost_usd=0.031,
            resolved_model="claude-runtime-model",
            result_hash="a" * 64,
            token_usage={"tokens_in": 40, "tokens_out": 20},
            model_provenance="fake_runtime.model",
            cost_provenance="fake_runtime.cost",
            token_provenance="fake_runtime.usage",
            metadata={"returncode": 0, "stderr": ""},
        )
        return RuntimeExecution(handle=handle, events=(), result=result)

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=legacy_runner_must_not_run,
        lead_runtime_runner=fake_runtime_runner,
    )

    result = await _maybe_await(
        server.tools["start_dual_agent_gate"](
            task_id="gate-runtime-lead",
            run_id="run-runtime-lead",
            gate="prd_review",
            instruction="Run through the neutral runtime.",
            cwd=str(tmp_path),
            expected_specialists=["Planner"],
            expected_decisions=["accept plan"],
            expected_objections=[],
            planning_artifacts=_write_planning_artifacts(tmp_path),
        )
    )

    assert result["status"] == "accepted"
    assert len(runtime_tasks) == 1
    runtime_call = next(
        call
        for call in result["tool_calls"]
        if call["name"] == "invoke_runtime_lead"
    )
    assert runtime_call["runtime"] == "claude_code"
    assert runtime_call["runtime_result_hash"] == "a" * 64
    assert runtime_call["model_provenance"] == "fake_runtime.model"
    assert runtime_call["cost_provenance"] == "fake_runtime.cost"
    assert runtime_call["token_provenance"] == "fake_runtime.usage"

    gate_event = next(
        row
        for row in state.read_dual_agent_gate_events("run-runtime-lead")
        if row["kind"] == "dual_agent_gate_result"
    )
    persisted = json.loads(gate_event["payload_json"])
    persisted_call = next(
        call
        for call in persisted["tool_calls"]
        if call["name"] == "invoke_runtime_lead"
    )
    assert persisted_call["runtime_result_hash"] == "a" * 64


@pytest.mark.asyncio
async def test_mcp_gate_persists_each_runtime_retry_and_aggregate_usage(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    runtime_tasks: list[AgentTask] = []

    def fake_runtime_runner(task: AgentTask) -> RuntimeExecution:
        runtime_tasks.append(task)
        attempt = len(runtime_tasks)
        handle = AgentRunHandle(
            run_id=f"runtime-retry-{attempt}",
            task_id=task.task_id,
            runtime="claude_code",
            session_id=f"runtime-retry-session-{attempt}",
            capabilities={"cancel": True, "stream": True},
        )
        output = (
            "<dual_agent_outcome>{bad}</dual_agent_outcome>"
            if attempt == 1
            else _outcome_block(task.task_id)
        )
        result = AgentRunResult(
            run_id=handle.run_id,
            task_id=task.task_id,
            runtime=handle.runtime,
            session_id=handle.session_id,
            status="completed",
            output=output,
            events=(),
            started_at_ms=attempt * 100,
            ended_at_ms=(attempt * 100) + 25,
            cost_usd=attempt / 100,
            resolved_model=f"claude-runtime-model-{attempt}",
            result_hash=str(attempt) * 64,
            token_usage={
                "tokens_in": attempt * 10,
                "tokens_out": attempt * 5,
            },
            model_provenance=f"fake_runtime.model.{attempt}",
            cost_provenance=f"fake_runtime.cost.{attempt}",
            token_provenance=f"fake_runtime.usage.{attempt}",
            metadata={"returncode": 0, "stderr": ""},
        )
        return RuntimeExecution(handle=handle, events=(), result=result)

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        lead_runtime_runner=fake_runtime_runner,
    )

    result = await _maybe_await(
        server.tools["start_dual_agent_gate"](
            task_id="gate-runtime-retry",
            run_id="run-runtime-retry",
            gate="prd_review",
            instruction="Run through the neutral runtime.",
            cwd=str(tmp_path),
            expected_specialists=["Planner"],
            expected_decisions=["accept plan"],
            expected_objections=[],
            planning_artifacts=_write_planning_artifacts(tmp_path),
        )
    )

    runtime_calls = [
        call
        for call in result["tool_calls"]
        if call["name"] == "invoke_runtime_lead"
    ]
    assert [call["runtime_result_hash"] for call in runtime_calls] == [
        "1" * 64,
        "2" * 64,
    ]
    assert [call["cost_usd"] for call in runtime_calls] == [0.01, 0.02]
    gate_call = result["tool_calls"][0]
    assert gate_call["result_summary"]["lead_attempt_count"] == 2
    assert gate_call["result_summary"]["lead_total_cost_usd"] == pytest.approx(
        0.03
    )
    assert gate_call["result_summary"]["lead_tokens_in"] == 30
    assert gate_call["result_summary"]["lead_tokens_out"] == 15

    gate_event = next(
        row
        for row in state.read_dual_agent_gate_events("run-runtime-retry")
        if row["kind"] == "dual_agent_gate_result"
    )
    persisted = json.loads(gate_event["payload_json"])
    persisted_runtime_calls = [
        call
        for call in persisted["tool_calls"]
        if call["name"] == "invoke_runtime_lead"
    ]
    assert [call["runtime_run_id"] for call in persisted_runtime_calls] == [
        "runtime-retry-1",
        "runtime-retry-2",
    ]


@pytest.mark.asyncio
async def test_async_mcp_gate_does_not_block_the_event_loop(
    tmp_path: Path,
) -> None:
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    started = threading.Event()
    release = threading.Event()

    def slow_runtime_runner(task: AgentTask) -> RuntimeExecution:
        started.set()
        if not release.wait(timeout=1):
            raise TimeoutError("test did not release the fake runtime")
        handle = AgentRunHandle(
            run_id="responsive-runtime",
            task_id=task.task_id,
            runtime="claude_code",
            session_id="responsive-runtime-session",
            capabilities={"cancel": True, "stream": True},
        )
        result = AgentRunResult(
            run_id=handle.run_id,
            task_id=task.task_id,
            runtime=handle.runtime,
            session_id=handle.session_id,
            status="completed",
            output=_outcome_block(task.task_id),
            events=(),
            started_at_ms=100,
            ended_at_ms=125,
            cost_usd=0.0,
            resolved_model="claude-runtime-model",
            result_hash="f" * 64,
            token_usage={},
            metadata={"returncode": 0, "stderr": ""},
        )
        return RuntimeExecution(handle=handle, events=(), result=result)

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        lead_runtime_runner=slow_runtime_runner,
    )
    timer = threading.Timer(0.3, release.set)
    timer.start()
    loop = asyncio.get_running_loop()
    began = loop.time()
    gate_task = asyncio.create_task(
        _maybe_await(
            server.tools["start_dual_agent_gate"](
                task_id="gate-responsive",
                run_id="run-responsive-gate",
                gate="prd_review",
                instruction="Run without blocking other MCP requests.",
                cwd=str(tmp_path),
                expected_specialists=["Planner"],
                expected_decisions=["accept plan"],
                expected_objections=[],
                planning_artifacts=_write_planning_artifacts(tmp_path),
            )
        )
    )
    try:
        await asyncio.sleep(0.03)
        heartbeat_elapsed = loop.time() - began
        assert heartbeat_elapsed < 0.15
        assert started.is_set()
        release.set()
        result = await gate_task
    finally:
        release.set()
        timer.cancel()

    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_autoresearch_policy_evolution_tools_apply_only_after_operator_approval(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.autoresearch.policy_evolution import PolicyEvolutionError

    state = State(str(tmp_path / "state.db"))
    claim_authority = _policy_claim_authority(
        tmp_path / "claim-authority-explicit"
    )
    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        policy_claim_authority_resolver=(
            lambda _report, _repo_root: claim_authority
        ),
    )
    target = tmp_path / ".supervisor" / "policy-overlay.yaml"
    candidate = tmp_path / "candidates" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    target.write_text("before prompt\n", encoding="utf-8")
    candidate.write_text("after prompt\n", encoding="utf-8")
    report_path = tmp_path / "autoresearch-report.json"
    report_path.write_text(
        json.dumps(
            _claim_gate_authorize_policy_report(
                {
                    "schema_version": "supervisor-autoresearch-summary/v1",
                    "report_sha256": "report-sha",
                    "records": [{
                        "experiment_id": "exp-policy-1",
                        "task_id": "task-policy-1",
                        "attempt_id": "attempt-policy-1",
                        "validation_status": "accepted",
                        "recommendation": "candidate needs operator approval",
                        "metric_name": "reviewer_evidence_score",
                        "metric_trials": [0.74, 0.82, 0.86],
                        "metric_median": 0.82,
                        "metric_iqr": 0.12,
                        "metric_source": "evaluator_execution",
                        "evaluator_run_ref": (
                            "docs/dual-agent/run/evaluator-runs/"
                            "attempt-policy-1.json"
                        ),
                        "evaluator_run_hash": "evaluator-run-hash",
                        "changed_files": ["candidates/policy-overlay.yaml"],
                        "artifact_hashes": {
                            "candidates/policy-overlay.yaml": sha256(
                                candidate.read_bytes()
                            ).hexdigest(),
                        },
                        "evaluator_quality": _evaluator_quality_controls(),
                        "gaming_flags": [],
                        "validation_errors": [],
                        "cost_usd": 0.19,
                        "wall_clock_s": 12.5,
                        "default_change_allowed": False,
                        "policy_mutated": False,
                        "gate_advanced": False,
                    }],
                },
                authority=claim_authority,
            )
        ),
        encoding="utf-8",
    )

    created = await _maybe_await(server.tools["create_autoresearch_policy_proposals"](
        report_path=str(report_path),
        repo_root=str(tmp_path),
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml"},
        affected_gates=["outcome_review"],
        run_id="policy-run",
    ))

    assert created["proposal_count"] == 1
    assert target.read_text(encoding="utf-8") == "before prompt\n"
    proposal = created["proposals"][0]

    with pytest.raises(PolicyEvolutionError):
        await _maybe_await(server.tools["approve_autoresearch_policy_proposal"](
            proposal=proposal,
            repo_root=str(tmp_path),
            run_id="policy-run",
            approver="",
            approval_channel="codex_desktop",
        ))
    assert target.read_text(encoding="utf-8") == "before prompt\n"

    denial = await _maybe_await(server.tools["deny_autoresearch_policy_proposal"](
        proposal=proposal,
        repo_root=str(tmp_path),
        run_id="policy-run",
        approver="sam.zhang",
        approval_channel="codex_desktop",
        reason="needs more evidence",
    ))
    assert denial["denial"]["status"] == "denied"
    assert target.read_text(encoding="utf-8") == "before prompt\n"

    approval = await _maybe_await(server.tools["approve_autoresearch_policy_proposal"](
        proposal_event_id=proposal["proposal_event_id"],
        repo_root=str(tmp_path),
        run_id="policy-run",
        approver="sam.zhang",
        approval_channel="codex_desktop",
    ))
    assert approval["approval"]["operator_approved"] is True
    assert approval["approval"]["default_change_allowed"] is False
    assert target.read_text(encoding="utf-8") == "after prompt\n"

    rollback = await _maybe_await(server.tools["rollback_autoresearch_policy_proposal"](
        rollback_pointer=approval["approval"]["rollback_pointer"],
        repo_root=str(tmp_path),
        run_id="policy-run",
        approver="sam.zhang",
        approval_channel="codex_desktop",
        reason="operator revert",
    ))
    assert target.read_text(encoding="utf-8") == "before prompt\n"
    assert rollback["rollback"]["gate_authority"] == "unchanged"
    assert rollback["rollback"]["reviewer_panel_authority"] == "unchanged"
    assert rollback["rollback"]["typed_outcome_authority"] == "unchanged"
    assert rollback["rollback"]["gate_advanced"] is False

    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
        "autoresearch_policy_proposal_denied",
        "autoresearch_policy_proposal_approved",
        "autoresearch_policy_proposal_rolled_back",
    ]


@pytest.mark.asyncio
async def test_autoresearch_policy_proposal_tool_derives_from_report_without_candidate_changes(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    claim_authority = _policy_claim_authority(
        tmp_path / "claim-authority-derived"
    )
    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        policy_claim_authority_resolver=(
            lambda _report, _repo_root: claim_authority
        ),
    )
    target = tmp_path / ".supervisor" / "policy-overlay.yaml"
    candidate = tmp_path / "candidates" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    target.write_text("before prompt\n", encoding="utf-8")
    candidate.write_text("after prompt\n", encoding="utf-8")
    report_path = tmp_path / "autoresearch-report.json"
    report_path.write_text(
        json.dumps(
            _claim_gate_authorize_policy_report(
                {
                    "schema_version": "supervisor-autoresearch-summary/v1",
                    "report_sha256": "report-sha",
                    "records": [{
                        "experiment_id": "exp-policy-1",
                        "task_id": "task-policy-1",
                        "attempt_id": "attempt-policy-1",
                        "validation_status": "accepted",
                        "recommendation": "candidate needs operator approval",
                        "metric_name": "reviewer_evidence_score",
                        "metric_trials": [0.74, 0.82, 0.86],
                        "metric_median": 0.82,
                        "metric_iqr": 0.12,
                        "metric_before": 0.7,
                        "metric_after": 0.82,
                        "metric_delta": 0.12,
                        "empty_floor_comparison": {
                            "metric_source": "evaluator_execution",
                            "empty_floor_metric": 0.7,
                            "candidate_metric": 0.82,
                            "metric_delta": 0.12,
                            "k_trials": 3,
                        },
                        "quality_unstable_across_trials": True,
                        "metric_source": "evaluator_execution",
                        "evaluator_run_ref": (
                            "docs/dual-agent/run/evaluator-runs/"
                            "attempt-policy-1.json"
                        ),
                        "evaluator_run_hash": "evaluator-run-hash",
                        "changed_files": ["candidates/policy-overlay.yaml"],
                        "artifact_hashes": {
                            "candidates/policy-overlay.yaml": sha256(
                                candidate.read_bytes()
                            ).hexdigest(),
                        },
                        "policy_candidate_changes": {
                            ".supervisor/policy-overlay.yaml": (
                                "candidates/policy-overlay.yaml"
                            ),
                        },
                        "evaluator_quality": _evaluator_quality_controls(),
                        "gaming_flags": [],
                        "validation_errors": [],
                        "cost_usd": 0.19,
                        "wall_clock_s": 12.5,
                        "default_change_allowed": False,
                        "policy_mutated": False,
                        "gate_advanced": False,
                    }],
                },
                authority=claim_authority,
            )
        ),
        encoding="utf-8",
    )

    created = await _maybe_await(server.tools["create_autoresearch_policy_proposals"](
        report_path=str(report_path),
        repo_root=str(tmp_path),
        affected_gates=["outcome_review"],
        run_id="policy-run",
    ))

    assert created["mode"] == "report_derived"
    assert created["proposal_count"] == 1
    proposal = created["proposals"][0]
    assert proposal["source"] == "autoresearch_deriver"
    assert proposal["status"] == "draft"
    assert proposal["derivation"]["metric_delta"] == 0.12
    assert target.read_text(encoding="utf-8") == "before prompt\n"


@pytest.mark.asyncio
async def test_autoresearch_policy_proposal_tool_empty_candidate_changes_stays_explicit(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
    )
    target = tmp_path / ".supervisor" / "policy-overlay.yaml"
    candidate = tmp_path / "candidates" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    target.write_text("before prompt\n", encoding="utf-8")
    candidate.write_text("after prompt\n", encoding="utf-8")
    report_path = tmp_path / "autoresearch-report.json"
    report_path.write_text(json.dumps({
        "schema_version": "supervisor-autoresearch-summary/v1",
        "report_sha256": "report-sha",
        "records": [{
            "experiment_id": "exp-policy-1",
            "task_id": "task-policy-1",
            "attempt_id": "attempt-policy-1",
            "validation_status": "accepted",
            "recommendation": "candidate needs operator approval",
            "metric_name": "reviewer_evidence_score",
            "metric_trials": [0.74, 0.82, 0.86],
            "metric_median": 0.82,
            "metric_iqr": 0.12,
            "metric_before": 0.7,
            "metric_after": 0.82,
            "metric_delta": 0.12,
            "metric_source": "evaluator_execution",
            "evaluator_run_ref": "docs/dual-agent/run/evaluator-runs/attempt-policy-1.json",
            "evaluator_run_hash": "evaluator-run-hash",
            "changed_files": ["candidates/policy-overlay.yaml"],
            "policy_candidate_changes": {
                ".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml",
            },
            "gaming_flags": [],
            "validation_errors": [],
            "default_change_allowed": False,
            "policy_mutated": False,
            "gate_advanced": False,
        }],
    }), encoding="utf-8")

    created = await _maybe_await(server.tools["create_autoresearch_policy_proposals"](
        report_path=str(report_path),
        repo_root=str(tmp_path),
        affected_gates=["outcome_review"],
        candidate_changes={},
        run_id="policy-run",
    ))

    assert created["mode"] == "explicit_candidate_changes"
    assert created["proposal_count"] == 0
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []
    assert target.read_text(encoding="utf-8") == "before prompt\n"


@pytest.mark.asyncio
async def test_start_dual_agent_gate_blocks_lead_reported_revision(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                _outcome_block(
                    task_id="gate-critical-review",
                    critical_review={
                        "decision": "revise",
                        "severity": "important",
                        "strongest_objection": "publish set still contains raw debug reference",
                    },
                )
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-critical-review",
        run_id="run-critical-review",
        gate="prd_review",
        instruction="Run the gate.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=_write_planning_artifacts(tmp_path),
        required_planning_kinds=[],
    ))

    assert result["status"] == "blocked"
    assert result["supervisor_final_status"] == "blocked"
    assert result["claude_gate_status"] == "blocked"
    assert result["probes"]["P4"]["reason"] == "outcome_critical_review_blocked"


@pytest.mark.asyncio
async def test_start_dual_agent_gate_blocks_dynamic_preview_without_p13_receipts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="prd_review",
        instruction="Run a dynamic workflow preview gate.",
        cwd=str(tmp_path),
        execution_layer_mode="dynamic-workflow-preview",
        dynamic_workflow_task_class="codebase_audit",
        planning_artifacts=_write_planning_artifacts(tmp_path),
    ))

    assert runner_calls == []
    assert result["status"] == "blocked"
    assert result["probes"]["P13"]["reason"] == "missing_dynamic_workflow_receipts"
    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="run-1",
        task_id="gate-1",
    ))
    assert transcript["dynamic_workflow_receipt_validations"][0]["probe"]["status"] == "red"


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_blocks_strict_outcome_gate_without_required_artifacts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="outcome_review",
        instruction="Review the implementation outcome.",
        cwd=str(tmp_path),
    ))

    assert runner_calls == []
    assert result["status"] == "blocked"
    assert result["escalation"]["type"] == "artifact_rigor"
    assert result["artifact_rigor"]["status"] == "blocked"
    assert result["artifact_rigor"]["missing_artifacts"] == [
        "prd",
        "tdd_plan",
        "grill_findings",
        "issues",
        "implementation_plan",
    ]
    assert result["artifact_export"]["status"] == "incomplete"
    assert result["artifact_export"]["ledger_authoritative"] is False
    assert len(result["artifact_export"]["export_root_sha256"]) == 64
    assert len(result["artifact_export"]["ledger_head_hash"]) == 64
    assert (
        tmp_path
        / "docs"
        / "dual-agent"
        / "gate-1"
        / "release"
        / "outcome-review.md"
    ).exists()

    outcome = await _maybe_await(server.tools["read_outcome"](
        run_id="run-1",
        task_id="gate-1",
    ))
    assert outcome["result"]["artifact_rigor"]["missing_artifacts"] == result["artifact_rigor"]["missing_artifacts"]


@pytest.mark.asyncio
async def test_start_dual_agent_gate_relaxed_artifact_policy_still_blocks_stub_planning(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=str(tmp_path),
        planning_artifacts=_stub_prd_artifact(),
        artifact_policy="relaxed",
    ))

    assert runner_calls == []
    assert result["status"] == "blocked"
    assert result["artifact_rigor"]["reason"] == "artifact_policy_relaxed"
    assert result["probes"]["P_planning"]["reason"] == "planning_validation_failed"


@pytest.mark.asyncio
async def test_start_dual_agent_gate_forwards_route_specific_requirements(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    runner_calls = []
    prd_path = tmp_path / "prd.md"
    prd_path.write_text((FIXTURE_ROOT / "prd" / "good.md").read_text(encoding="utf-8"), encoding="utf-8")

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="execution",
        instruction="Run a reduced-route execution gate.",
        cwd=str(tmp_path),
        planning_artifacts=[{
            "path": str(prd_path),
            "kind": "prd",
            "mutable_by_worker": False,
        }],
        required_artifacts=["prd"],
        required_prerequisite_gates=[],
        required_planning_kinds=["prd"],
    ))

    assert runner_calls
    assert result["status"] == "accepted"
    assert result["artifact_rigor"]["required_artifacts"] == ["prd"]
    assert result["artifact_rigor"]["required_prerequisite_gates"] == []


@pytest.mark.asyncio
async def test_read_gate_transcript_includes_planning_validation_receipts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=str(tmp_path),
        planning_artifacts=_stub_prd_artifact(),
        artifact_policy="relaxed",
    ))
    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="run-1",
        task_id="gate-1",
    ))

    assert result["status"] == "blocked"
    assert transcript["status"] == "ok"
    assert transcript["planning_validations"]
    receipt = transcript["planning_validations"][0]
    assert receipt["verdict"] == "blocked"
    assert "PRD-002" in receipt["checks"]


@pytest.mark.asyncio
async def test_read_gate_transcript_includes_skill_receipt_validation(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    test_command = "python -m pytest tests/test_skill_receipt_fixture.py -q"
    source_dir = tmp_path / "docs" / "dual-agent" / "workflow-1" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    for kind, filename in {
        "prd": "prd.md",
        "grill_findings": "grill-findings.md",
        "issues": "issues.md",
    }.items():
        (source_dir / filename).write_text(
            (FIXTURE_ROOT / kind / "good.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (source_dir / "tdd.md").write_text(
        "\n".join([
            "# Skill Receipt TDD",
            "",
            "## Public Boundary",
            "Use `run_dual_agent_workflow` with skill receipts.",
            "",
            "## Test Cases",
            "",
            "### test_skill_receipt_fixture",
            "Maps to: ISS-1, P1",
            "RED: The named test must be supervisor-executed.",
            "GREEN: Runtime evidence records the pytest result.",
            "",
            "### test_skill_receipt_fixture_extra",
            "Maps to: ISS-1, P1",
            "RED: The second named test must also be supervisor-executed.",
            "GREEN: Runtime evidence records the pytest result.",
            "",
            "## RED/GREEN Plan",
            "RED: Missing runtime execution blocks the gate.",
            "GREEN: The skill receipt transcript remains visible after runtime checks pass.",
            "",
        ]),
        encoding="utf-8",
    )
    (source_dir / "implementation-plan.md").write_text(
        "\n".join([
            "# Skill Receipt Implementation Plan",
            "",
            "## Files / Modules To Touch",
            "- `supervisor/dual_agent.py`",
            "- `tests/test_skill_receipt_fixture.py`",
            "",
            "## Risks",
            "- Skill receipt validation can be hidden from transcripts.",
            "- Runtime evidence can miss a named TDD test.",
            "",
            "## Traceability",
            "- P1 -> test_skill_receipt_fixture",
            "- P1 -> test_skill_receipt_fixture_extra",
            "",
            "## Steps",
            "1. Run the supervisor workflow.",
            "2. Validate skill receipts.",
            "3. Preserve validation in the transcript.",
            "",
        ]),
        encoding="utf-8",
    )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, "supervisor/dual_agent.py", "RUNTIME_FIXTURE = True\n")
    _write_runtime_file(
        tmp_path,
        "tests/test_skill_receipt_fixture.py",
        "\n".join([
            "def test_skill_receipt_fixture():",
            "    assert True",
            "",
            "def test_skill_receipt_fixture_extra():",
            "    assert True",
            "",
        ]),
    )

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                _outcome_block("workflow-1", tests=[test_command], test_status="passed")
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(_run_dual_agent_workflow_direct(server,
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run with skill receipts.",
        max_rounds_per_gate=1,
        cursor_review=False,
        tool_receipts=[
            *_skill_receipts(),
            {
                "receipt_id": "pytest-focused",
                "kind": "test",
                "status": "passed",
                "claims": ["tests passed"],
            },
            {
                "receipt_id": "git-diff",
                "kind": "git_diff",
                "status": "present",
                "claims": ["implemented"],
                "changed_files": ["supervisor/dual_agent.py"],
            },
        ],
    ))
    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))

    assert result["status"] == "accepted"
    assert transcript["skill_receipt_validations"][0]["probe"]["status"] == "green"
    assert transcript["skill_receipt_validations"][0]["trace_envelope"]["policy_verdict"] == "accepted"


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_accepts_strict_outcome_gate_with_required_artifacts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")
    _write_accepted_gate(state, gate="implementation_plan")
    _write_accepted_gate(state, gate="execution")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="outcome_review",
        instruction="Review the implementation outcome.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=planning_artifacts,
    ))

    assert result["status"] == "accepted"
    assert result["artifact_rigor"]["status"] == "ok"
    assert result["artifact_rigor"]["required_artifacts"] == [
        "prd",
        "tdd_plan",
        "grill_findings",
        "issues",
        "implementation_plan",
    ]
    assert (
        "docs/dual-agent/gate-1/release/index.md"
        in result["artifact_export"]["files"]
    )

    outcome = await _maybe_await(server.tools["read_outcome"](
        run_id="run-1",
        task_id="gate-1",
    ))
    assert outcome["result"]["artifact_rigor"]["status"] == "ok"


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_blocks_user_facing_gate_without_screenshots(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")
    _write_accepted_gate(state, gate="implementation_plan")
    _write_accepted_gate(state, gate="execution")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="outcome_review",
        instruction="Review the user-facing implementation outcome.",
        cwd=str(tmp_path),
        planning_artifacts=planning_artifacts,
        user_facing=True,
    ))

    assert result["status"] == "blocked"
    assert result["artifact_rigor"]["missing_artifacts"] == ["screenshots"]
    assert result["artifact_rigor"]["user_facing"] is True


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_accepts_user_facing_gate_with_screenshots(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")
    _write_accepted_gate(state, gate="implementation_plan")
    _write_accepted_gate(state, gate="execution")
    screenshot = tmp_path / "final-state.png"
    screenshot.write_bytes(_tiny_png())

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="outcome_review",
        instruction="Review the user-facing implementation outcome.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=planning_artifacts,
        user_facing=True,
        screenshots=[{
            "path": str(screenshot),
            "label": "Final state",
            "note": "Captured by Codex before outcome review.",
            "source": "computer_use",
            "validation": {
                "status": "passed",
                "notes": "Codex reviewed the captured UI state against the acceptance criteria.",
            },
        }],
    ))

    assert result["status"] == "accepted"
    assert result["artifact_rigor"]["status"] == "ok"
    assert result["artifact_rigor"]["visual_validation"]["status"] == "ok"
    assert (
        "docs/dual-agent/gate-1/release/screenshots.md"
        in result["artifact_export"]["files"]
    )
    assert (
        "docs/dual-agent/gate-1/release/screenshots/01-final-state.png"
        in result["artifact_export"]["files"]
    )


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_blocks_user_facing_gate_without_visual_validation(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")
    _write_accepted_gate(state, gate="implementation_plan")
    _write_accepted_gate(state, gate="execution")
    screenshot = tmp_path / "final-state.png"
    screenshot.write_bytes(_tiny_png())

    def fake_runner(argv, **kwargs):
        raise AssertionError("user-facing gate must block before Claude without visual validation")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="outcome_review",
        instruction="Review the user-facing implementation outcome.",
        cwd=str(tmp_path),
        planning_artifacts=planning_artifacts,
        user_facing=True,
        screenshots=[{
            "path": str(screenshot),
            "label": "Final state",
            "note": "Screenshot without Browser/Computer Use validation metadata.",
        }],
    ))

    assert result["status"] == "blocked"
    assert result["artifact_rigor"]["missing_artifacts"] == ["visual_validation"]
    assert result["artifact_rigor"]["visual_validation"]["status"] == "blocked"
    reasons = {
        failure["reason"]
        for failure in result["artifact_rigor"]["visual_validation"]["failures"]
    }
    assert reasons == {
        "missing_or_unsupported_capture_source",
        "visual_review_not_passed",
    }


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_runs_issues_review_after_prd_is_accepted(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="issues_review",
        instruction="Grill the issue slicing.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=planning_artifacts,
    ))

    assert result["status"] == "accepted"
    assert result["artifact_rigor"]["accepted_prerequisite_gates"] == ["prd_review"]


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_blocks_implementation_plan_until_prd_issues_tdd_are_accepted(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="implementation_plan",
        instruction="Approve implementation plan.",
        cwd=str(tmp_path),
        planning_artifacts=planning_artifacts,
    ))

    assert runner_calls == []
    assert result["status"] == "blocked"
    assert result["escalation"]["type"] == "artifact_rigor"
    assert result["escalation"]["reason"] == "gate_prerequisites_missing"
    assert result["artifact_rigor"]["missing_prerequisite_gates"] == [
        "prd_review",
        "issues_review",
        "tdd_review",
    ]


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_runs_implementation_plan_after_prd_issues_tdd_are_accepted(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block()),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="implementation_plan",
        instruction="Approve implementation plan.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=planning_artifacts,
    ))

    assert result["status"] == "accepted"
    assert result["artifact_rigor"]["accepted_prerequisite_gates"] == [
        "prd_review",
        "issues_review",
        "tdd_review",
    ]
    assert result["artifact_rigor"]["missing_prerequisite_gates"] == []


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_blocks_execution_until_implementation_plan_is_accepted(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="execution",
        instruction="Run implementation.",
        cwd=str(tmp_path),
        planning_artifacts=planning_artifacts,
    ))

    assert result["status"] == "blocked"
    assert result["escalation"]["reason"] == "gate_prerequisites_missing"
    assert result["artifact_rigor"]["missing_prerequisite_gates"] == ["implementation_plan"]


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_blocks_outcome_review_until_execution_is_accepted(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)
    _write_accepted_gate(state, gate="prd_review")
    _write_accepted_gate(state, gate="issues_review")
    _write_accepted_gate(state, gate="tdd_review")
    _write_accepted_gate(state, gate="implementation_plan")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="outcome_review",
        instruction="Review implementation outcome.",
        cwd=str(tmp_path),
        planning_artifacts=planning_artifacts,
    ))

    assert result["status"] == "blocked"
    assert result["escalation"]["reason"] == "gate_prerequisites_missing"
    assert result["artifact_rigor"]["missing_prerequisite_gates"] == ["execution"]


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_reads_clean_gate_transcript(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block("transcript-task")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    for round_index, codex_decision, claude_decision, objection in [
        (1, "deny", "accept", "No tests added."),
        (2, "revise", "revise", "Acceptance criteria still vague."),
        (3, "accept", "accept", None),
    ]:
        await _maybe_await(server.tools["record_gate_round"](
            run_id="transcript-run",
            task_id="transcript-task",
            gate="prd_review",
            round_index=round_index,
            codex_decision=codex_decision,
            claude_decision=claude_decision,
            codex_confidence=0.9 + (round_index / 100),
            claude_confidence=0.8 + (round_index / 100),
            objection=objection,
        ))

    gate_result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="transcript-task",
        run_id="transcript-run",
        gate="prd_review",
        instruction="Finish the gate.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=_write_planning_artifacts(tmp_path),
    ))

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="transcript-run",
        task_id="transcript-task",
    ))

    assert transcript["status"] == "ok"
    assert transcript["run_id"] == "transcript-run"
    assert transcript["task_id"] == "transcript-task"
    assert [r["round_index"] for r in transcript["rounds"]] == [1, 2, 3]
    assert transcript["rounds"][0]["codex_decision"] == "deny"
    assert transcript["rounds"][0]["claude_decision"] == "accept"
    assert transcript["rounds"][0]["objection"] == "No tests added."
    assert transcript["rounds"][0]["event_id"] < transcript["rounds"][1]["event_id"]
    first_round_row = state.get_event(
        run_id="transcript-run",
        event_id=transcript["rounds"][0]["event_id"],
    )
    first_round_payload = json.loads(first_round_row["payload_json"])
    assert first_round_payload["trace_envelope"]["tool_calls"][0]["name"] == "record_gate_round"
    assert {"started_at_ms", "ended_at_ms", "duration_ms"} <= set(
        first_round_payload["trace_envelope"]["tool_calls"][0]
    )
    assert transcript["result"]["status"] == "accepted"
    assert transcript["result"]["outcome"]["task_id"] == "transcript-task"
    assert transcript["handoff_packet_path"] == gate_result["handoff_packet_path"]


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_records_rounds_checks_budget_and_polls_resume(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import (
        build_lead_replay_stdout,
        request_deadlock_escalation,
        resolve_deadlock_escalation,
    )
    from supervisor.dual_agent import GateRound

    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block("gate-resume")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    recorded = await _maybe_await(server.tools["record_gate_round"](
        run_id="run-resume",
        task_id="gate-resume",
        gate="prd_review",
        round_index=1,
        codex_decision="deny",
        claude_decision="accept",
        codex_confidence=0.96,
        claude_confidence=0.95,
        objection="Missing acceptance criteria.",
    ))
    budget = await _maybe_await(server.tools["check_budget"](
        rounds=[recorded["round"]],
        per_gate_cap=1,
        task_budget=1,
    ))
    assert budget["probe"]["reason"] == "paused_for_human"

    class FakeNotifier:
        async def send_approval_prompt(self, **kwargs):
            return {"ok": True}

    escalation = await request_deadlock_escalation(
        state=state,
        notifier=FakeNotifier(),
        run_id="run-resume",
        task_id="gate-resume",
        gate="prd_review",
        rounds=[
            GateRound(
                round_index=1,
                codex_decision="deny",
                claude_decision="accept",
                codex_confidence=0.96,
                claude_confidence=0.95,
                objection="Missing acceptance criteria.",
            )
        ],
        per_gate_cap=1,
        task_budget=1,
    )
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    resolve_deadlock_escalation(
        state=state,
        ask_id=escalation.ask_id,
        answer="Continue",
        nonce=escalation.nonce,
        action_row=action,
    )

    resumed = await _maybe_await(server.tools["poll_resume_signal"](
        task_id="gate-resume",
        run_id="run-resume",
        gate="prd_review",
        instruction="Resume.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
        planning_artifacts=_write_planning_artifacts(tmp_path),
    ))

    assert resumed["status"] == "accepted"
    assert state._conn.execute("SELECT status FROM actions").fetchone()["status"] == "resumed"


def test_codex_supervisor_mcp_start_codex_session_can_dry_run_or_execute_with_runner(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    calls: list[AgentTask] = []

    def fake_codex_runtime_runner(task: AgentTask) -> RuntimeExecution:
        calls.append(task)
        handle = AgentRunHandle(
            run_id="codex-runtime-run",
            task_id=task.task_id,
            runtime="codex",
            session_id="codex-runtime-session",
            capabilities={"cancel": True, "stream": True},
        )
        result = AgentRunResult(
            run_id=handle.run_id,
            task_id=task.task_id,
            runtime=handle.runtime,
            session_id=handle.session_id,
            status="completed",
            output="done",
            events=(),
            started_at_ms=100,
            ended_at_ms=120,
            cost_usd=0.0,
            resolved_model="gpt-5.5",
            result_hash=sha256(b"codex-result").hexdigest(),
            token_usage={"tokens_in": 3, "tokens_out": 1},
            model_provenance="fake.model",
            token_provenance="fake.usage",
            metadata={"returncode": 0, "stderr": ""},
        )
        return RuntimeExecution(handle=handle, events=(), result=result)

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        state,
        codex_runtime_runner=fake_codex_runtime_runner,
    )

    dry_run = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=False,
    )
    with pytest.raises(
        ValueError,
        match="require workflow_run_id and task_id",
    ):
        api.start_codex_session(
            prompt="Implement the slice.",
            cwd=str(tmp_path),
            execute=True,
            timeout_s=30,
        )
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-run",
        target_session_id="",
        task_id="task-1",
        task="Implement the slice.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )
    executed = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=True,
        timeout_s=30,
        workflow_run_id="workflow-run",
        task_id="task-1",
    )

    assert dry_run["status"] == "dry_run"
    assert dry_run["argv"][:2] == ["codex", "exec"]
    assert dry_run["argv"][dry_run["argv"].index("-m") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="xhigh"' in dry_run["argv"]
    assert calls[0].model == "gpt-5.5"
    assert calls[0].metadata["reasoning_effort"] == "xhigh"
    assert executed["status"] == "completed"
    assert executed["runtime"] == "codex"
    assert executed["result_hash"] == sha256(b"codex-result").hexdigest()
    registration = json.loads(
        Path(executed["session_registration_ref"]).read_text()
    )
    assert registration["runtime_run_id"] == "codex-runtime-run"
    assert registration["runtime_result_hash"] == executed["result_hash"]


def test_start_codex_session_dry_run_defaults_to_pi_fable_xhigh(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    dry_run = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=False,
    )

    assert dry_run["status"] == "dry_run"
    assert dry_run["runtime"] == "pi"
    argv = dry_run["argv"]
    assert argv[0] == "pi"
    assert argv[argv.index("--model") + 1] == "anthropic/claude-fable-5"
    assert argv[argv.index("--thinking") + 1] == "xhigh"


def test_start_codex_session_honors_executor_kind_codex(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    cfg = _cfg(tmp_path)
    cfg.executor.kind = "codex"
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(cfg, state)

    dry_run = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=False,
    )

    assert dry_run["runtime"] == "codex"
    argv = dry_run["argv"]
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("-m") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="xhigh"' in " ".join(argv)


def test_codex_supervisor_mcp_start_codex_session_releases_receipt_on_timeout(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        LaunchReceiptError,
        consume_launch_receipt,
        register_submitted_workflow,
    )

    launch_credentials: dict[str, str] = {}

    def timing_out_runner(task: AgentTask) -> RuntimeExecution:
        launch_credentials.update({
            "launch_id": task.env["SUPERVISOR_LAUNCH_ID"],
            "nonce": task.env["SUPERVISOR_LAUNCH_NONCE"],
        })
        raise subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=30)

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        state,
        codex_runtime_runner=timing_out_runner,
    )
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-timeout",
        target_session_id="",
        task_id="task-timeout",
        task="Implement the slice.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )

    outcome = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=True,
        timeout_s=30,
        workflow_run_id="workflow-timeout",
        task_id="task-timeout",
    )

    assert outcome["status"] == "timeout"
    receipt_root = (
        Path(api.cfg.orchestrator.run_registry_dir) / ".launch-receipts"
    )
    assert list((receipt_root / "pending").glob("*.json")) == []
    released = [
        json.loads(path.read_text())
        for path in (receipt_root / "consumed").glob("*.json")
    ]
    assert [payload["status"] for payload in released] == ["released"]
    assert released[0]["launch_id"] == launch_credentials["launch_id"]
    with pytest.raises(LaunchReceiptError):
        consume_launch_receipt(
            state=state,
            registry_dir=api.cfg.orchestrator.run_registry_dir,
            launch_id=launch_credentials["launch_id"],
            nonce=launch_credentials["nonce"],
            workflow_run_id="workflow-timeout",
            task_id="task-timeout",
            target_kind="codex",
            target_session_id="late-session",
            cwd=tmp_path,
        )


def test_start_codex_session_rejects_sparse_session_namespace_before_runtime(
    tmp_path,
):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        LaunchReceiptError,
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    calls: list[AgentTask] = []

    def should_not_run(task: AgentTask) -> RuntimeExecution:
        calls.append(task)
        raise AssertionError("runtime must not start with a sparse sidecar")

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        state,
        codex_runtime_runner=should_not_run,
    )
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-sparse-sidecar",
        target_session_id="",
        task_id="task-sparse-sidecar",
        task="Implement the slice.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )
    registry = Path(api.cfg.orchestrator.run_registry_dir)
    (registry / "untrusted-session.json").write_text(
        '{"workflow_run_id":"workflow-sparse-sidecar"}',
        encoding="utf-8",
    )

    with pytest.raises(
        LaunchReceiptError,
        match="session registry contains malformed authority sidecar",
    ):
        api.start_codex_session(
            prompt="Implement the slice.",
            cwd=str(tmp_path),
            execute=True,
            timeout_s=30,
            workflow_run_id="workflow-sparse-sidecar",
            task_id="task-sparse-sidecar",
        )

    assert calls == []


def test_codex_supervisor_mcp_start_codex_session_releases_receipt_on_provenance_failure(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
    from supervisor.run_registry import (
        PENDING_SESSION_SOURCE,
        register_submitted_workflow,
    )

    def sessionless_runner(task: AgentTask) -> RuntimeExecution:
        handle = AgentRunHandle(
            run_id="codex-runtime-run",
            task_id=task.task_id,
            runtime="codex",
            session_id="codex-runtime-run",
            capabilities={},
        )
        result = AgentRunResult(
            run_id=handle.run_id,
            task_id=task.task_id,
            runtime=handle.runtime,
            session_id=handle.session_id,
            status="completed",
            output="done",
            events=(),
            started_at_ms=100,
            ended_at_ms=120,
            cost_usd=0.0,
            resolved_model="gpt-5.5",
            result_hash=sha256(b"codex-result").hexdigest(),
            token_usage={},
            model_provenance="fake.model",
            token_provenance="fake.usage",
            metadata={"returncode": 0, "stderr": ""},
        )
        return RuntimeExecution(handle=handle, events=(), result=result)

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        state,
        codex_runtime_runner=sessionless_runner,
    )
    register_submitted_workflow(
        state=state,
        registry_dir=api.cfg.orchestrator.run_registry_dir,
        workflow_run_id="workflow-noprov",
        target_session_id="",
        task_id="task-noprov",
        task="Implement the slice.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source=PENDING_SESSION_SOURCE,
    )

    with pytest.raises(
        RuntimeError,
        match="distinct target session id",
    ):
        api.start_codex_session(
            prompt="Implement the slice.",
            cwd=str(tmp_path),
            execute=True,
            timeout_s=30,
            workflow_run_id="workflow-noprov",
            task_id="task-noprov",
        )

    receipt_root = (
        Path(api.cfg.orchestrator.run_registry_dir) / ".launch-receipts"
    )
    assert list((receipt_root / "pending").glob("*.json")) == []
    statuses = [
        json.loads(path.read_text())["status"]
        for path in (receipt_root / "consumed").glob("*.json")
    ]
    assert statuses == ["released"]


@pytest.mark.asyncio
async def test_start_gate_rejects_sparse_registration_before_runtime(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    runner_calls = []
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="sparse-run",
        session_id="",
        rollout_path="",
        task=None,
        scope=ScopeContract(),
        target_kind=None,
        config_snapshot=None,
    )

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    with pytest.raises(
        RuntimeError,
        match="workflow run registration session_id is missing",
    ):
        await _maybe_await(server.tools["start_dual_agent_gate"](
            task_id="task-sparse",
            run_id="sparse-run",
            gate="prd_review",
            instruction="Review the sparse registration.",
            cwd=str(tmp_path),
            expected_specialists=["Planner"],
            expected_decisions=["accept plan"],
            expected_objections=[],
            planning_artifacts=_write_planning_artifacts(tmp_path),
            required_planning_kinds=[],
        ))

    assert runner_calls == []


def test_submit_parks_reserved_job_when_registration_fails(
    tmp_path,
    monkeypatch,
):
    import mcp_tools.codex_supervisor_stdio as stdio_module
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    def failing_registration(**_kwargs):
        raise ValueError("target session id is invalid")

    monkeypatch.setattr(
        stdio_module,
        "register_submitted_workflow",
        failing_registration,
    )

    with pytest.raises(ValueError, match="target session id is invalid"):
        api.submit_dual_agent_workflow_job(
            cwd=str(tmp_path),
            task_id="task-reg-fail",
            run_id="run-reg-fail",
            intent="Submit with failing registration.",
        )

    jobs = state.list_dual_agent_workflow_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "parked"
    assert str(jobs[0]["parked_reason"]).startswith(
        "workflow_run_registration_failed"
    )
    assert state.list_dual_agent_workflow_jobs(active_only=True) == []


def test_ensure_workflow_run_registration_rejects_conflicting_snapshot(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    state.register_run(
        run_id="conflict-run",
        session_id="pending:conflict-run",
        rollout_path="pending://codex/conflict-run",
        task="Conflicting task",
        scope=ScopeContract(),
        target_kind="codex",
        config_snapshot={
            "source": "workflow_submission",
            "schema_version": "supervisor-run-registration/v2",
            "workflow_run_id": "conflict-run",
            "target_session_id": None,
            "task_id": "some-other-task",
            "target_kind": "codex",
            "cwd": str(tmp_path.resolve()),
            "session_id_source": "pending_runtime_receipt",
            "completion_policy": "workflow_aggregate",
        },
    )

    with pytest.raises(
        RuntimeError,
        match="workflow run registration task_id mismatch",
    ):
        api._ensure_workflow_run_registration(
            run_id="conflict-run",
            task_id="task-conflict",
            task="Conflicting task",
            cwd=str(tmp_path),
        )


def test_codex_supervisor_mcp_console_script_is_registered():
    data = tomllib.loads(Path("pyproject.toml").read_text())

    assert data["project"]["scripts"]["codex-supervisor-mcp"] == (
        "mcp_tools.codex_supervisor_stdio:main"
    )
