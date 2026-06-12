from __future__ import annotations

import json
import tomllib
from hashlib import sha256
from pathlib import Path

from mcp_tools import codex_supervisor_axi as axi
from supervisor.autoresearch.generator import (
    AutoResearchGeneratorConfig,
    generate_autoresearch_experiment_drafts,
)
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
        run_id="trend-run",
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
