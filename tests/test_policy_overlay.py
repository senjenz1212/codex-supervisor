from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from mcp_tools.codex_supervisor_stdio import (
    CodexSupervisorMcpAPI,
    _planning_rubric_threshold_for_gate,
)
from supervisor.config import Config, PLANNING_RUBRIC_MIN_THRESHOLD
from supervisor.policy_overlay import (
    draft_policy_regression_rollback_if_needed,
    load_policy_overlay,
)
from supervisor.state import State


def _cfg(tmp_path: Path) -> Config:
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


def _write_overlay(root: Path) -> Path:
    path = root / ".supervisor" / "policy-overlay.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "schema_version: supervisor-policy-overlay/v1\n"
        "active_proposal_id: ARP-live\n"
        "lesson_limit: 2\n"
        "instruction_guidance_blocks:\n"
        "  all:\n"
        "    - Cite runtime receipts before claiming completion.\n"
        "  execution:\n"
        "    - Re-run declared tests in the validation worktree.\n",
        encoding="utf-8",
    )
    return path


def test_applied_overlay_changes_next_gate_instruction_and_records_hash(tmp_path):
    overlay_path = _write_overlay(tmp_path)
    overlay_hash = sha256(overlay_path.read_bytes()).hexdigest()
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    kwargs = api._workflow_gate_start_kwargs(
        run_id="overlay-run",
        task_id="task-1",
        gate="execution",
        intent="Implement the feature.",
        corrective_context="",
        lesson_task_class="large",
        cwd=tmp_path,
        round_index=1,
    )

    assert "Supervisor policy overlay guidance" in kwargs["instruction"]
    assert "Re-run declared tests" in kwargs["instruction"]
    assert kwargs["policy_overlay_hash"] == overlay_hash
    assert kwargs["policy_proposal_id"] == "ARP-live"
    assert kwargs["policy_overlay_block_sha256"] == sha256(
        kwargs["policy_overlay_block"].encode("utf-8")
    ).hexdigest()
    [event] = [
        item for item in state.read_events_since("overlay-run", after_event_id=0, limit=20)
        if item["kind"] == "supervisor_policy_overlay_snapshot"
    ]
    assert event["payload"]["policy_overlay_hash"] == overlay_hash
    assert event["payload"]["policy_proposal_id"] == "ARP-live"


def test_policy_overlay_loader_hashes_absent_overlay_for_replay(tmp_path):
    overlay = load_policy_overlay(tmp_path)

    assert overlay.exists is False
    assert overlay.content_hash == sha256(b"").hexdigest()
    assert overlay.to_event_payload(gate="execution")["block_sha256"] == sha256(b"").hexdigest()


def test_policy_overlay_rubric_threshold_cannot_disable_planning_floor(tmp_path):
    cfg = _cfg(tmp_path)

    threshold = _planning_rubric_threshold_for_gate(
        cfg,
        {"execution": 0.0},
        gate="execution",
    )

    assert threshold == PLANNING_RUBRIC_MIN_THRESHOLD


def test_explicit_gate_rubric_threshold_cannot_disable_planning_floor(tmp_path):
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), State(str(tmp_path / "state.db")))

    spec = api._gate_spec(
        task_id="floor-task",
        run_id="floor-run",
        gate="execution",
        instruction="Review implementation.",
        cwd=str(tmp_path),
        expected_specialists=[],
        expected_decisions=[],
        expected_objections=[],
        quality="best",
        model=None,
        budget_usd=1.0,
        timeout_s=30,
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        agentic_policy={
            "agentic_lead_policy": "off",
            "min_subagents": 0,
            "required_roles": [],
            "solo_exception_for_artifact_only_gates": False,
            "required_evidence_grade": "self_reported",
        },
        planning_artifacts=[],
        planning_rubric_threshold=0.0,
    )

    assert spec.planning_rubric_threshold == PLANNING_RUBRIC_MIN_THRESHOLD


def test_policy_regression_drafts_one_rollback_and_does_not_apply(tmp_path):
    state = State(str(tmp_path / "state.db"))
    overlay_path = _write_overlay(tmp_path)
    before_overlay = overlay_path.read_text(encoding="utf-8")
    for index in range(3):
        state.upsert_quality_trend_row(
            run_id=f"before-{index}",
            task_id="task",
            task_class="source_change",
            gate="execution",
            accepted=True,
            first_pass_accepted=True,
            revision_rounds=0,
            time_to_accepted_outcome_s=10.0,
            computed_at=100 + index,
        )
        state.upsert_quality_trend_row(
            run_id=f"after-{index}",
            task_id="task",
            task_class="source_change",
            gate="execution",
            accepted=True,
            first_pass_accepted=False,
            revision_rounds=2,
            time_to_accepted_outcome_s=30.0,
            policy_overlay_hash="overlay-sha",
            policy_proposal_id="ARP-live",
            computed_at=200 + index,
        )
    rollback_pointer = {
        "schema_version": "supervisor-autoresearch-policy-rollback/v1",
        "proposal_id": "ARP-live",
        "files": [{
            "target_path": ".supervisor/policy-overlay.yaml",
            "backup_ref": ".handoff/policy-rollbacks/ARP-live/policy-overlay.before",
            "before_hash": "before",
            "after_hash": "after",
        }],
    }

    first = draft_policy_regression_rollback_if_needed(
        state,
        run_id="regression-run",
        proposal_id="ARP-live",
        rollback_pointer=rollback_pointer,
        task_class="source_change",
        gate="execution",
        min_runs=3,
    )
    second = draft_policy_regression_rollback_if_needed(
        state,
        run_id="regression-run",
        proposal_id="ARP-live",
        rollback_pointer=rollback_pointer,
        task_class="source_change",
        gate="execution",
        min_runs=3,
    )

    assert first["status"] == "rollback_drafted"
    assert second["status"] == "already_drafted"
    assert overlay_path.read_text(encoding="utf-8") == before_overlay
    events = state.read_events_since("regression-run", after_event_id=0, limit=20)
    assert [event["kind"] for event in events].count("policy_regression_detected") == 1
    assert [event["kind"] for event in events].count("autoresearch_policy_rollback_proposal_drafted") == 1
    draft = [event for event in events if event["kind"] == "autoresearch_policy_rollback_proposal_drafted"][0]
    assert draft["payload"]["status"] == "draft"
    assert draft["payload"]["automatic_policy_mutation"] is False


def test_workflow_result_drafts_policy_regression_rollback_from_recorded_trends(tmp_path):
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    rollback_pointer = {
        "schema_version": "supervisor-autoresearch-policy-rollback/v1",
        "proposal_id": "ARP-live",
        "files": [{
            "target_path": ".supervisor/policy-overlay.yaml",
            "backup_ref": ".handoff/policy-rollbacks/ARP-live/policy-overlay.before",
            "before_hash": "before",
            "after_hash": "after",
        }],
    }
    state.write_event(
        run_id="policy-approval-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_approved",
        payload={
            "proposal_id": "ARP-live",
            "rollback_pointer": rollback_pointer,
            "automatic_policy_mutation": False,
            "gate_authority": "unchanged",
        },
    )
    for index in range(3):
        state.upsert_quality_trend_row(
            run_id=f"before-{index}",
            task_id="task",
            task_class="source_change",
            gate="execution",
            accepted=True,
            first_pass_accepted=True,
            revision_rounds=0,
            time_to_accepted_outcome_s=10.0,
            computed_at=100 + index,
        )
    for index in range(2):
        state.upsert_quality_trend_row(
            run_id=f"after-{index}",
            task_id="task",
            task_class="source_change",
            gate="execution",
            accepted=True,
            first_pass_accepted=False,
            revision_rounds=2,
            time_to_accepted_outcome_s=30.0,
            policy_overlay_hash="overlay-sha",
            policy_proposal_id="ARP-live",
            computed_at=200 + index,
        )
    state.write_event(
        run_id="live-run",
        source="dual_agent",
        kind="dual_agent_workflow_route",
        payload={
            "task_id": "task",
            "run_id": "live-run",
            "lesson_task_class": "source_change",
            "cwd": str(tmp_path),
        },
    )
    state.write_event(
        run_id="live-run",
        source="supervisor",
        kind="supervisor_policy_overlay_snapshot",
        payload={
            "gate": "execution",
            "policy_overlay_hash": "overlay-sha",
            "policy_proposal_id": "ARP-live",
            "block_sha256": "block-sha",
        },
    )
    state.write_event(
        run_id="live-run",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "gate": "execution",
            "status": "accepted",
            "attempts": 2,
            "outcome": {"changed_files": []},
        },
    )

    result = api._workflow_result(
        run_id="live-run",
        task_id="task",
        status="accepted",
        current_gate="outcome_review",
        steps=[],
        final_gate_result=None,
        cwd=str(tmp_path),
        screenshots=[],
        visual_evidence_policy={},
        workflow_route={"lesson_task_class": "source_change"},
        mandatory_artifacts={"status": "not_checked"},
    )

    rollbacks = result["quality_trends"]["policy_regression_rollbacks"]
    assert rollbacks[0]["status"] == "rollback_drafted"
    assert rollbacks[0]["proposal_id"] == "ARP-live"
    events = state.read_events_since("live-run", after_event_id=0, limit=50)
    assert [event["kind"] for event in events].count("policy_regression_detected") == 1
    assert [event["kind"] for event in events].count("autoresearch_policy_rollback_proposal_drafted") == 1
