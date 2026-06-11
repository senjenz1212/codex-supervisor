from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from supervisor.config import Config
from supervisor.state import State
from supervisor.target.types import ScopeContract


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


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
) -> str:
    payload = {
        "task_id": task_id,
        "summary": "Implemented through /lead.",
        "specialists": [{"name": "Planner", "decision": decision}],
        "decisions": [decision],
        "objections": [],
        "changed_files": ["supervisor/dual_agent.py"],
        "tests": [],
        "test_status": "unknown",
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
    state.register_run(
        run_id="run-1",
        session_id="session-1",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Dual agent gate",
        scope=ScopeContract(allowed_paths=(".",)),
        target_kind="codex",
    )

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
async def test_autoresearch_policy_evolution_tools_apply_only_after_operator_approval(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.autoresearch.policy_evolution import PolicyEvolutionError

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
            "metric_source": "evaluator_execution",
            "evaluator_run_ref": "docs/dual-agent/run/evaluator-runs/attempt-policy-1.json",
            "evaluator_run_hash": "evaluator-run-hash",
                "changed_files": ["candidates/policy-overlay.yaml"],
            "gaming_flags": [],
            "validation_errors": [],
            "cost_usd": 0.19,
            "wall_clock_s": 12.5,
            "default_change_allowed": False,
            "policy_mutated": False,
            "gate_advanced": False,
        }],
    }), encoding="utf-8")

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
        proposal=proposal,
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
        "autoresearch_policy_proposal_created",
        "autoresearch_policy_proposal_denied",
        "autoresearch_policy_proposal_approved",
        "autoresearch_policy_proposal_rolled_back",
    ]


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
    assert result["artifact_export"]["status"] == "ok"
    assert (tmp_path / "docs" / "dual-agent" / "gate-1" / "outcome-review.md").exists()

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
    source_dir = tmp_path / "docs" / "dual-agent" / "workflow-1" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    for kind, filename in {
        "prd": "prd.md",
        "grill_findings": "grill-findings.md",
        "issues": "issues.md",
        "tdd_plan": "tdd.md",
        "implementation_plan": "implementation-plan.md",
    }.items():
        (source_dir / filename).write_text(
            (FIXTURE_ROOT / kind / "good.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, "supervisor/dual_agent.py", "RUNTIME_FIXTURE = True\n")

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(_outcome_block("workflow-1")),
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
    assert "docs/dual-agent/gate-1/index.md" in result["artifact_export"]["files"]

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
    assert "docs/dual-agent/gate-1/screenshots.md" in result["artifact_export"]["files"]
    assert "docs/dual-agent/gate-1/screenshots/01-final-state.png" in result["artifact_export"]["files"]


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

    calls = []

    def fake_codex_runner(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout='{"type":"turn.completed"}\n', stderr="")

    api = CodexSupervisorMcpAPI(
        _cfg(tmp_path),
        State(str(tmp_path / "state.db")),
        codex_runner=fake_codex_runner,
    )

    dry_run = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=False,
    )
    executed = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=True,
        timeout_s=30,
    )

    assert dry_run["status"] == "dry_run"
    assert dry_run["argv"][:2] == ["codex", "exec"]
    assert dry_run["argv"][dry_run["argv"].index("-m") + 1] == "gpt-5.5"
    assert 'reasoning_effort="xhigh"' in dry_run["argv"]
    assert calls[0]["argv"][:2] == ["codex", "exec"]
    assert calls[0]["argv"][calls[0]["argv"].index("-m") + 1] == "gpt-5.5"
    assert 'reasoning_effort="xhigh"' in calls[0]["argv"]
    assert executed["status"] == "completed"


def test_codex_supervisor_mcp_console_script_is_registered():
    data = tomllib.loads(Path("pyproject.toml").read_text())

    assert data["project"]["scripts"]["codex-supervisor-mcp"] == (
        "mcp_tools.codex_supervisor_stdio:main"
    )
