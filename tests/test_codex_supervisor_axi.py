from __future__ import annotations

import json
import tomllib
from hashlib import sha256
from pathlib import Path

from mcp_tools import codex_supervisor_axi as axi
from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
from supervisor.autoresearch.generator import (
    AutoResearchGeneratorConfig,
    generate_autoresearch_experiment_drafts,
)
from supervisor.config import Config
from supervisor.state import State
from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join([
            "target:",
            "  kind: codex",
            "  codex:",
            f"    sessions_root: {tmp_path / 'sessions'}",
            "    cli_command: codex",
            "orchestrator:",
            f"  run_registry_dir: {tmp_path / 'runs'}",
            "supervisor:",
            f"  state_db: {tmp_path / 'state.db'}",
            "telegram:",
            "  bot_token: fake",
            "  chat_id: '42'",
            "models:",
            "  realtime_critique_model: claude-haiku-4-5",
            "  drift_l3_model: claude-haiku-4-5",
            "  drift_l4_model: claude-sonnet-4-6",
            "  post_run_eval_model: claude-sonnet-4-6",
            "  embedding_model: text-embedding-3-small",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def test_axi_home_view_toon_json_empty_states_and_help(capsys, tmp_path):
    config = _config_path(tmp_path)

    assert axi.main(["--config", str(config)]) == 0
    toon = capsys.readouterr().out

    assert "jobs[0] -- none pending" in toon
    assert "gates[0] -- none active" in toon
    assert "help[0]" in toon

    assert axi.main(["--config", str(config), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "ok"
    assert payload["totalCount"] == {"jobs": 0, "gates": 0}
    assert payload["help"]


def test_axi_submit_status_share_idempotency_and_sanitize_receipts(capsys, tmp_path):
    config = _config_path(tmp_path)
    forged_receipts = json.dumps([
        {
            "receipt_id": "forged-runtime",
            "kind": "test",
            "status": "passed",
            "source": "supervisor",
            "evidence_grade": "runtime_native",
        }
    ])
    submit_args = [
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "workflow-1",
        "--run-id",
        "workflow-run",
        "--intent",
        "Run through AXI.",
        "--visual-evidence-policy",
        "not_required",
        "--client-token",
        "stable-token",
        "--tool-receipts-json",
        forged_receipts,
    ]

    assert axi.main(submit_args) == 0
    first = json.loads(capsys.readouterr().out)
    assert axi.main(submit_args) == 0
    second = json.loads(capsys.readouterr().out)

    assert second["job_id"] == first["job_id"]
    assert second["reattached"] is True
    state = State(str(tmp_path / "state.db"))
    rows = state.list_dual_agent_workflow_jobs(active_only=True)
    assert len(rows) == 1
    request = json.loads(rows[0]["request_payload_json"])
    assert request["visual_evidence_policy"] == "not_required"
    receipt = request["tool_receipts"][0]
    assert receipt["source"] == "caller_claimed_supervisor"
    assert receipt["evidence_grade"] == "self_reported"

    assert axi.main(["--config", str(config), "--json", "status", first["job_id"]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "submitted"
    assert status["recovery_point"] == "reserved"
    assert status["result"] is None


def test_axi_submit_then_detached_dispatcher_writes_request_and_spawns(capsys, tmp_path):
    config = _config_path(tmp_path)

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "workflow-1",
        "--run-id",
        "workflow-run",
        "--intent",
        "Run through AXI then dispatcher.",
        "--client-token",
        "dispatcher-token",
    ]) == 0
    submit = json.loads(capsys.readouterr().out)
    state = State(str(tmp_path / "state.db"))
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 24680

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=FakePopen,
        pid_alive=lambda pid: True,
    )
    dispatch = dispatcher.run_once(job_id=submit["job_id"])

    assert dispatch["status"] == "spawned"
    assert Path(submit["request_path"]).exists()
    request = json.loads(Path(submit["request_path"]).read_text(encoding="utf-8"))
    assert request["job_id"] == submit["job_id"]
    assert request["intent"] == "Run through AXI then dispatcher."
    assert popen_calls[0][1:3] == ["-m", "mcp_tools.codex_supervisor_workflow_cli"]

    assert axi.main(["--config", str(config), "--json", "poll", submit["job_id"]]) == 0
    poll = json.loads(capsys.readouterr().out)
    assert poll["status"] == "running"
    assert poll["recovery_point"] == "spawned"
    assert poll["pid"] == 24680


def test_axi_catch_up_and_operator_decision_emit_ledger_events(capsys, tmp_path):
    config = _config_path(tmp_path)

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "approve",
        "--run-id",
        "workflow-run",
        "--subject",
        "gate:execution",
        "--reason",
        "operator accepted",
    ]) == 0
    approval = json.loads(capsys.readouterr().out)

    assert approval["status"] == "ok"
    assert approval["decision"] == "approve"
    assert axi.main([
        "--config",
        str(config),
        "--json",
        "catch-up",
        "workflow-run",
        "--last-event-id",
        str(approval["event_id"] - 1),
    ]) == 0
    catch_up = json.loads(capsys.readouterr().out)
    assert catch_up["count"] == 1
    assert catch_up["events"][0]["kind"] == "supervisor_axi_operator_decision"


def test_axi_experiments_activate_transitions_draft_to_runnable(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        state.write_event(
            run_id=f"signal-{index}",
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload={
                "task_id": "task",
                "task_class": "source_change",
                "lesson_task_class": "source_change",
                "gate": "execution",
                "status": "blocked",
                "implicated_paths": ["supervisor/autoresearch/orchestrator.py"],
                "trace_envelope": {
                    "failure_taxonomy": {"mast_code": "FM-3.2"},
                },
            },
        )
    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3),
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "experiments",
        "activate",
        draft["experiment_id"],
        "--operator",
        "operator@example.com",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["experiment"]["status"] == "runnable"
    assert payload["automatic_policy_mutation"] is False
    [row] = state.list_autoresearch_experiment_queue()
    assert row["status"] == "runnable"


def test_axi_experiments_activate_requires_named_operator(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        state.write_event(
            run_id=f"signal-{index}",
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload={
                "task_id": "task",
                "task_class": "source_change",
                "lesson_task_class": "source_change",
                "gate": "execution",
                "status": "blocked",
                "implicated_paths": ["supervisor/autoresearch/orchestrator.py"],
                "trace_envelope": {
                    "failure_taxonomy": {"mast_code": "FM-3.2"},
                },
            },
        )
    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3),
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "experiments",
        "activate",
        draft["experiment_id"],
    ]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["error"]["message"] == "operator is required"
    [row] = state.list_autoresearch_experiment_queue()
    assert row["status"] == "draft"


def test_axi_experiments_park_drains_draft_with_audit_event(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        state.write_event(
            run_id=f"signal-{index}",
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload={
                "task_id": "task",
                "task_class": "source_change",
                "lesson_task_class": "source_change",
                "gate": "execution",
                "status": "blocked",
                "implicated_paths": ["supervisor/autoresearch/orchestrator.py"],
                "trace_envelope": {
                    "failure_taxonomy": {"mast_code": "FM-3.2"},
                },
            },
        )
    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3),
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "experiments",
        "park",
        draft["experiment_id"],
        "--operator",
        "operator@example.com",
        "--reason",
        "superseded by higher-priority live cycle",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["experiment"]["status"] == "parked"
    assert payload["automatic_policy_mutation"] is False
    [row] = state.list_autoresearch_experiment_queue()
    assert row["status"] == "parked"
    events = state.read_events_since("autoresearch_experiments", after_event_id=0, limit=10)
    [event] = [item for item in events if item["kind"] == "autoresearch_experiment_parked"]
    assert event["payload"]["experiment_id"] == draft["experiment_id"]
    assert event["payload"]["reason"] == "superseded by higher-priority live cycle"


def test_axi_policy_approve_proposal_applies_hashes_and_rollback_pointer(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    target = tmp_path / ".supervisor" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks: {}\n", encoding="utf-8")
    candidate = tmp_path / "candidates" / "policy-overlay.yaml"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        "schema_version: supervisor-policy-overlay/v1\n"
        "active_proposal_id: ARP-cli\n"
        "instruction_guidance_blocks:\n"
        "  execution:\n"
        "    - Verify runtime receipts.\n",
        encoding="utf-8",
    )
    proposal = {
        "schema_version": "supervisor-autoresearch-policy-proposal/v1",
        "proposal_id": "ARP-cli",
        "status": "draft",
        "changes": [{
            "target_path": ".supervisor/policy-overlay.yaml",
            "candidate_ref": "candidates/policy-overlay.yaml",
            "before_hash": sha256(target.read_bytes()).hexdigest(),
            "after_hash": sha256(candidate.read_bytes()).hexdigest(),
        }],
        "requires_operator_approval": True,
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "gate_advanced": False,
    }
    state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=proposal,
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "approve",
        "--run-id",
        "policy-run",
        "--proposal-id",
        "ARP-cli",
        "--repo-root",
        str(tmp_path),
        "--approver",
        "operator@example.com",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    approval = payload["approval"]
    assert approval["before_hash"] == proposal["changes"][0]["before_hash"]
    assert approval["after_hash"] == proposal["changes"][0]["after_hash"]
    assert approval["rollback_pointer"]["files"][0]["target_path"] == ".supervisor/policy-overlay.yaml"
    assert target.read_text(encoding="utf-8") == candidate.read_text(encoding="utf-8")


def test_axi_policy_approve_requires_named_approver(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    target = tmp_path / ".supervisor" / "policy-overlay.yaml"
    candidate = tmp_path / "candidates" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks: {}\n", encoding="utf-8")
    candidate.write_text(
        "schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks:\n  outcome_review:\n    - require live evidence\n",
        encoding="utf-8",
    )
    proposal = {
        "schema_version": "supervisor-autoresearch-policy-proposal/v1",
        "proposal_id": "ARP-cli",
        "status": "draft",
        "changes": [{
            "target_path": ".supervisor/policy-overlay.yaml",
            "candidate_ref": "candidates/policy-overlay.yaml",
            "before_hash": sha256(target.read_bytes()).hexdigest(),
            "after_hash": sha256(candidate.read_bytes()).hexdigest(),
        }],
        "requires_operator_approval": True,
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "gate_advanced": False,
    }
    state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=proposal,
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "approve",
        "--run-id",
        "policy-run",
        "--proposal-id",
        "ARP-cli",
        "--repo-root",
        str(tmp_path),
    ]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["error"]["message"] == "approver is required"
    assert target.read_text(encoding="utf-8") == (
        "schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks: {}\n"
    )


def test_axi_policy_deny_proposal_records_denial_without_apply(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    target = tmp_path / ".supervisor" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks: {}\n", encoding="utf-8")
    proposal = {
        "schema_version": "supervisor-autoresearch-policy-proposal/v1",
        "proposal_id": "ARP-deny",
        "status": "draft",
        "changes": [],
        "default_change_allowed": False,
    }
    state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=proposal,
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "deny",
        "--run-id",
        "policy-run",
        "--proposal-id",
        "ARP-deny",
        "--approver",
        "operator@example.com",
        "--reason",
        "not enough evidence",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["denial"]["status"] == "denied"
    assert target.read_text(encoding="utf-8") == (
        "schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks: {}\n"
    )


def test_axi_fields_lessons_and_trends_are_read_only_observational(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    state.record_supervisor_lesson(
        task_class="source_change",
        gate="tdd_review",
        taxonomy_code="missing_coverage",
        root_cause="AXI read surface lacked a named test.",
        remediation="Add a public-boundary CLI test before claiming coverage.",
        source_run_id="prior-run",
        created_at=123,
    )
    state.upsert_quality_trend_row(
        run_id="trend-run-mcp",
        task_id="workflow-1",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=12.0,
        computed_at=124,
    )
    before_events = state._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]

    assert axi.main([
        "--config",
        str(config),
        "--fields",
        "task_class,gate,taxonomy_code",
        "lessons",
    ]) == 0
    lessons = capsys.readouterr().out
    assert "lessons[1]" in lessons
    assert "task_class=source_change" in lessons
    assert "gate=tdd_review" in lessons
    assert "taxonomy_code=missing_coverage" in lessons
    assert "root_cause=" not in lessons

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "trends",
        "--task-class",
        "source_change",
        "--gate",
        "execution",
    ]) == 0
    trends = json.loads(capsys.readouterr().out)
    assert trends["trends"][0]["first_pass_acceptance_rate"] == 1.0
    assert trends["trends"][0]["run_count"] == 1
    after_events = state._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    assert after_events == before_events


def test_axi_trends_surfaces_by_era_in_json_and_toon(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="trend-run",
        task_id="workflow-1",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=12.0,
        details={
            "transport_incidents": {
                "total_count": 1,
                "by_era": {"mcp": 1},
                "run_era": "mcp",
            }
        },
    )
    state.upsert_quality_trend_row(
        run_id="trend-run-axi",
        task_id="workflow-2",
        task_class="source_change",
        gate="execution",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=12.0,
        details={
            "transport_incidents": {
                "total_count": 3,
                "by_era": {"axi": 3},
                "run_era": "axi",
            }
        },
    )

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "trends",
        "--task-class",
        "source_change",
        "--gate",
        "execution",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    row = payload["trends"][0]
    assert row["transport_incident_by_era"] == {"axi": 3, "mcp": 1}
    assert row["transport_incident_axi_count"] == 3
    assert row["transport_incident_mcp_count"] == 1
    assert row["transport_run_count_by_era"] == {"axi": 1, "mcp": 1}
    assert row["transport_incident_axi_rate"] == 3.0
    assert row["transport_incident_mcp_rate"] == 1.0
    assert row["transport_incident_axi_share"] == 0.75
    assert row["transport_incident_mcp_share"] == 0.25

    assert axi.main([
        "--config",
        str(config),
        "trends",
        "--task-class",
        "source_change",
        "--gate",
        "execution",
    ]) == 0
    toon = capsys.readouterr().out
    assert "trends[1]" in toon
    assert "transport_incident_by_era={\"axi\":3,\"mcp\":1}" in toon
    assert "transport_run_count_by_era={\"axi\":1,\"mcp\":1}" in toon


def test_transport_incident_events_are_written_from_public_axi_and_mcp_poll_failure_boundaries(capsys, tmp_path):
    config = _config_path(tmp_path)
    submit_args = [
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "workflow-1",
        "--run-id",
        "workflow-run",
        "--intent",
        "measure transport incidents",
        "--client-token",
        "transport-token",
    ]

    assert axi.main(submit_args) == 0
    first = json.loads(capsys.readouterr().out)
    assert axi.main(submit_args) == 0
    _second = json.loads(capsys.readouterr().out)
    assert axi.main(["--config", str(config), "--json", "poll", first["job_id"]]) == 0
    _poll = json.loads(capsys.readouterr().out)
    assert axi.main(["--config", str(config), "--json", "catch-up", "workflow-run"]) == 0
    _catch_up = json.loads(capsys.readouterr().out)
    assert axi.main(["--config", str(config), "--json", "poll", "missing-axi-job"]) == 0
    missing_axi = json.loads(capsys.readouterr().out)
    assert missing_axi["status"] == "missing"

    state = State(str(tmp_path / "state.db"))
    mcp_missing = CodexSupervisorMcpAPI(Config.load(config), state).poll_dual_agent_workflow_job(
        job_id="missing-mcp-job"
    )
    assert mcp_missing["status"] == "missing"

    events = state.read_events_since("workflow-run", after_event_id=0, limit=100)
    incidents = [
        event["payload"]["incident_type"]
        for event in events
        if event["kind"] == "transport_incident_observed"
    ]
    assert "same_client_token_reattach" in incidents
    assert "non_terminal_poll" in incidents
    assert "catch_up_invoked" not in incidents
    boundary_events = state.read_events_since(
        "supervisor_transport_incidents",
        after_event_id=0,
        limit=100,
    )
    poll_failures = [
        event["payload"]
        for event in boundary_events
        if event["kind"] == "transport_incident_observed"
        and event["payload"]["incident_type"] == "poll_failure"
    ]
    assert {payload["interface"] for payload in poll_failures} >= {"axi", "mcp"}
    catch_up_metrics = [
        event["payload"]
        for event in boundary_events
        if event["kind"] == "transport_incident_observed"
        and event["payload"]["incident_type"] == "catch_up_invoked"
    ]
    assert any(
        payload["interface"] == "axi"
        and payload["details"].get("workflow_run_id") == "workflow-run"
        and payload["observational_only"] is True
        and payload["gate_authority"] == "unchanged"
        for payload in catch_up_metrics
    )


def test_axi_toon_poll_records_format_metric(capsys, tmp_path):
    config = _config_path(tmp_path)
    assert axi.main([
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "workflow-1",
        "--run-id",
        "workflow-run",
        "--intent",
        "measure default poll format",
        "--client-token",
        "format-token",
    ]) == 0
    submitted = json.loads(capsys.readouterr().out)

    assert axi.main(["--config", str(config), "poll", submitted["job_id"]]) == 0
    toon = capsys.readouterr().out

    state = State(str(tmp_path / "state.db"))
    metrics = [
        event["payload"]
        for event in state.read_events_since("workflow-run", after_event_id=0, limit=100)
        if event["kind"] == "supervisor_axi_format_metric"
    ]
    assert metrics
    assert metrics[-1]["format"] == "toon"
    assert metrics[-1]["turns"] == 1
    assert metrics[-1]["bytes"] == len(toon.encode("utf-8"))


def test_axi_doctor_reports_health_and_degraded_help_without_writes(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    before_events = state._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]

    assert axi.main(["--config", str(config), "--json", "doctor"]) == 0
    degraded = json.loads(capsys.readouterr().out)

    assert degraded["doctor_status"] == "degraded"
    assert degraded["daemon_alive"] is False
    assert degraded["open_draft_count"] == 0
    assert degraded["runnable_count"] == 0
    assert degraded["pending_proposal_count"] == 0
    assert any("launchctl kickstart" in line for line in degraded["help"])
    after_events = state._conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    assert after_events == before_events

    state.write_event(
        run_id="supervisor_runtime",
        source="supervisor_runtime",
        kind="supervisor_subsystem_health",
        payload={"subsystem": "autoresearch_runner", "status": "healthy", "reason": "tick_completed"},
    )
    state.write_event(
        run_id="supervisor_runtime",
        source="supervisor_runtime",
        kind="supervisor_subsystem_health",
        payload={"subsystem": "weekly_p11_audit", "status": "healthy", "reason": "tick_completed"},
    )

    assert axi.main(["--config", str(config), "--json", "doctor"]) == 0
    healthy = json.loads(capsys.readouterr().out)
    assert healthy["doctor_status"] == "healthy"
    assert healthy["dispatcher"]["stale_lease_count"] == 0


def test_visual_evidence_not_required_with_matched_terms_writes_override_event(capsys, tmp_path):
    config = _config_path(tmp_path)

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "workflow-visual",
        "--run-id",
        "workflow-visual-run",
        "--intent",
        "Backend-only task mentioning visual screenshot evidence.",
        "--visual-evidence-policy",
        "not_required",
        "--client-token",
        "visual-token",
    ]) == 0
    _submit = json.loads(capsys.readouterr().out)

    state = State(str(tmp_path / "state.db"))
    events = state.read_events_since("workflow-visual-run", after_event_id=0, limit=100)
    overrides = [event for event in events if event["kind"] == "visual_evidence_override_asserted"]
    assert len(overrides) == 1
    assert {"screenshot", "visual"} <= set(overrides[0]["payload"]["matched_terms"])

    assert axi.main([
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "workflow-no-visual",
        "--run-id",
        "workflow-no-visual-run",
        "--intent",
        "Pure backend metrics task.",
        "--visual-evidence-policy",
        "not_required",
        "--client-token",
        "no-visual-token",
    ]) == 0
    _submit = json.loads(capsys.readouterr().out)
    events = state.read_events_since("workflow-no-visual-run", after_event_id=0, limit=100)
    assert [event for event in events if event["kind"] == "visual_evidence_override_asserted"] == []


def test_supervisor_launchd_plist_covers_daemon_and_dispatcher():
    script = Path("scripts/run-daemon.sh").read_text(encoding="utf-8")
    plist = Path("com.sam.codex-supervisor.plist").read_text(encoding="utf-8")

    assert "daemon.py" in script
    assert "codex-supervisor-workflow-dispatcher" in script
    assert "scripts/run-daemon.sh" in plist


def test_axi_structured_errors_stdout_exit_one(capsys, tmp_path):
    config = _config_path(tmp_path)

    exit_code = axi.main(["--config", str(config), "--json", "submit", "--task-id", "missing"])
    out = capsys.readouterr().out

    assert exit_code == 1
    payload = json.loads(out)
    assert payload["error"]["code"] == "AxiUsageError"
    assert payload["help"]


def test_axi_console_script_is_registered():
    data = tomllib.loads(Path("pyproject.toml").read_text())

    assert data["project"]["scripts"]["codex-supervisor-axi"] == (
        "mcp_tools.codex_supervisor_axi:main"
    )


def _common_submit_kwargs(tmp_path: Path) -> dict[str, object]:
    return {
        "cwd": str(tmp_path),
        "task_id": "shared-token-task",
        "run_id": "shared-token-run",
        "intent": "Cross-surface client-token reattach proof.",
        "visual_evidence_policy": "not_required",
    }


def test_axi_then_mcp_same_client_token_reattaches_to_one_job(capsys, tmp_path):
    config = _config_path(tmp_path)
    submit_args = [
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "shared-token-task",
        "--run-id",
        "shared-token-run",
        "--intent",
        "Cross-surface client-token reattach proof.",
        "--visual-evidence-policy",
        "not_required",
        "--client-token",
        "cross-surface-token",
    ]

    assert axi.main(submit_args) == 0
    axi_first = json.loads(capsys.readouterr().out)

    state = State(str(tmp_path / "state.db"))
    cfg = Config.load(config)
    mcp_second = CodexSupervisorMcpAPI(cfg, state).submit_dual_agent_workflow_job(
        **_common_submit_kwargs(tmp_path),
        client_token="cross-surface-token",
        config_path=str(config),
    )

    assert mcp_second["job_id"] == axi_first["job_id"]
    assert mcp_second["reattached"] is True
    rows = state.list_dual_agent_workflow_jobs(active_only=True)
    assert len(rows) == 1


def test_mcp_then_axi_same_client_token_reattaches_to_one_job(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    cfg = Config.load(config)
    mcp_first = CodexSupervisorMcpAPI(cfg, state).submit_dual_agent_workflow_job(
        **_common_submit_kwargs(tmp_path),
        client_token="reverse-token",
        config_path=str(config),
    )

    submit_args = [
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "shared-token-task",
        "--run-id",
        "shared-token-run",
        "--intent",
        "Cross-surface client-token reattach proof.",
        "--visual-evidence-policy",
        "not_required",
        "--client-token",
        "reverse-token",
    ]
    assert axi.main(submit_args) == 0
    axi_second = json.loads(capsys.readouterr().out)

    assert axi_second["job_id"] == mcp_first["job_id"]
    assert axi_second["reattached"] is True
    rows = state.list_dual_agent_workflow_jobs(active_only=True)
    assert len(rows) == 1


def test_axi_and_mcp_catch_up_return_equivalent_event_tail(capsys, tmp_path):
    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    cfg = Config.load(config)
    run_id = "catch-up-equiv-run"
    first_event_id = state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="dual_agent_workflow_route",
        payload={"task_id": "catch-up-equiv-task", "task_class": "source_change"},
    )
    second_event_id = state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="dual_agent_gate_status",
        payload={"gate": "execution", "status": "submitted"},
    )

    cursor = first_event_id - 1
    mcp_tail = CodexSupervisorMcpAPI(cfg, state).catch_up_dual_agent_workflow(
        run_id=run_id,
        last_event_id=cursor,
        limit=100,
    )
    boundary_before = state.read_events_since(
        "supervisor_transport_incidents", after_event_id=0, limit=100
    )
    workflow_after_mcp = state.read_events_since(run_id, after_event_id=0, limit=100)
    mcp_run_count = len(workflow_after_mcp)

    assert axi.main(
        ["--config", str(config), "--json", "catch-up", run_id, "--last-event-id", str(cursor)]
    ) == 0
    axi_tail = json.loads(capsys.readouterr().out)

    workflow_after_axi = state.read_events_since(run_id, after_event_id=0, limit=100)
    assert len(workflow_after_axi) == mcp_run_count

    mcp_ids = [event["event_id"] for event in mcp_tail["events"]]
    axi_ids = [event["event_id"] for event in axi_tail["events"]]
    mcp_kinds = [event["kind"] for event in mcp_tail["events"]]
    axi_kinds = [event["kind"] for event in axi_tail["events"]]
    assert axi_ids == mcp_ids == [first_event_id, second_event_id]
    assert axi_kinds == mcp_kinds
    assert axi_tail["count"] == mcp_tail["count"] == 2
    assert axi_tail["next_event_id"] == mcp_tail["next_event_id"]

    boundary_after = state.read_events_since(
        "supervisor_transport_incidents", after_event_id=0, limit=100
    )
    new_boundary = [
        event for event in boundary_after if event not in boundary_before
    ]
    catch_up_metrics = [
        event for event in new_boundary
        if event["kind"] == "transport_incident_observed"
        and event["payload"]["incident_type"] == "catch_up_invoked"
    ]
    assert catch_up_metrics, "AXI catch-up should record an observational metric"
    for event in catch_up_metrics:
        assert event["payload"]["observational_only"] is True
        assert event["payload"]["gate_authority"] == "unchanged"
    assert all(event["event_id"] not in axi_ids for event in catch_up_metrics)


def test_axi_submit_and_poll_help_use_json_recovery_commands(capsys, tmp_path):
    config = _config_path(tmp_path)
    submit_args = [
        "--config",
        str(config),
        "--json",
        "submit",
        "--cwd",
        str(tmp_path),
        "--task-id",
        "json-help-task",
        "--run-id",
        "json-help-run",
        "--intent",
        "Confirm AXI JSON recovery help.",
        "--client-token",
        "json-help-token",
    ]
    assert axi.main(submit_args) == 0
    submit = json.loads(capsys.readouterr().out)
    submit_help = " ".join(submit.get("help", []))
    assert f"codex-supervisor-axi --json poll {submit['job_id']}" in submit_help

    assert axi.main(["--config", str(config), "--json", "poll", submit["job_id"]]) == 0
    poll = json.loads(capsys.readouterr().out)
    poll_help = " ".join(poll.get("help", []))
    run_id = poll.get("run_id", "<run_id>")
    assert f"codex-supervisor-axi --json catch-up {run_id}" in poll_help


def test_rigorous_flow_docs_use_axi_json_default_and_keep_mcp_compatibility():
    detached = Path("docs/supervisor-axi-detached-dispatcher.md").read_text(encoding="utf-8")
    new_chat = Path("docs/how-to/dual-agent-from-new-chat.md").read_text(encoding="utf-8")
    skill = Path("skills/dual-agent-gate.md").read_text(encoding="utf-8")
    loop = Path("docs/LOOP.md").read_text(encoding="utf-8")
    public_boundaries = Path("docs/testing/public-boundaries.md").read_text(encoding="utf-8")

    for blob in (detached, new_chat, skill, loop, public_boundaries):
        assert "codex-supervisor-axi --json submit" in blob
        assert "codex-supervisor-axi --json poll" in blob
        assert "codex-supervisor-axi --json catch-up" in blob

    assert "run_dual_agent_workflow" in new_chat, (
        "MCP compatibility language must remain so legacy callers still see a route"
    )
    assert "run_dual_agent_workflow" in skill
    assert "MCP remains a compatibility" in loop
    assert "must not execute workflow phases inline" in public_boundaries

    for blob in (detached, new_chat, skill, loop, public_boundaries):
        assert "TOON improves agent performance" not in blob
