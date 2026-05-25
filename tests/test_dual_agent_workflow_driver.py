from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from supervisor.config import Config
from supervisor.cursor_agent import CursorInvocationResult
from supervisor.dual_agent import Outcome, ProbeResult
from supervisor.dual_agent_workflow import workflow_visual_evidence_policy
from supervisor.state import State


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


class _FakeMCP:
    def __init__(self, name: str):
        self.name = name
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


class _Notifier:
    def __init__(self):
        self.messages: list[str] = []
        self.prompts: list[dict] = []

    async def send_message(self, text: str, **kwargs):
        self.messages.append(text)
        return {"ok": True}

    async def send_approval_prompt(self, **kwargs):
        self.prompts.append(kwargs)
        return {"ok": True}


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


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


def _outcome_block(task_id: str, *, decision: str = "accept", claims: list[str] | None = None) -> str:
    payload = {
        "task_id": task_id,
        "summary": "Workflow response complete.",
        "specialists": [{"name": "Planner", "decision": decision}],
        "decisions": [decision],
        "objections": [] if decision == "accept" else ["Needs another revision."],
        "changed_files": ["supervisor/dual_agent_workflow.py", "tests/test_dual_agent_workflow_driver.py"],
        "tests": ["uv run pytest tests/test_dual_agent_workflow_driver.py"],
        "test_status": "passed",
        "confidence": 0.94,
        "claims": claims or ["tests passed", "implemented"],
    }
    return f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"


def _tiny_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )


def _tool_receipts(*, include_push: bool = False) -> list[dict]:
    receipts = [
        *_skill_receipts(),
        {
            "receipt_id": "pytest-focused",
            "kind": "test",
            "status": "passed",
            "command": "uv run pytest tests/test_dual_agent_workflow_driver.py",
            "claims": ["tests passed"],
        },
        {
            "receipt_id": "git-diff",
            "kind": "git_diff",
            "status": "present",
            "changed_files": [
                "supervisor/dual_agent_workflow.py",
                "tests/test_dual_agent_workflow_driver.py",
            ],
            "claims": ["implemented"],
        },
    ]
    if include_push:
        receipts.append({
            "receipt_id": "unity-push",
            "kind": "git_remote",
            "status": "pushed",
            "remote": "unity",
            "claims": ["pushed"],
        })
    return receipts


def _skill_receipts() -> list[dict]:
    return [
        {
            "receipt_id": f"skill-{stage}",
            "kind": "skill_run",
            "status": "passed",
            "skill": skill,
            "stage": stage,
            "claims": ["prd-tdd skill executed"],
        }
        for stage, skill in [
            ("to_prd", "to-prd"),
            ("prd_grill", "grill-with-docs"),
            ("to_issues", "to-issues"),
            ("tdd", "tdd"),
            ("tdd_grill", "grill-with-docs"),
        ]
    ]


def _server(
    tmp_path: Path,
    *,
    decision: str = "accept",
    claims: list[str] | None = None,
    notifier=None,
    cursor_runner=None,
):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1", decision=decision, claims=claims),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        cursor_runner=cursor_runner,
        notifier=notifier,
    )
    return server, state


def _write_good_workflow_source_artifacts(tmp_path: Path, task_id: str = "workflow-1") -> None:
    source_dir = tmp_path / "docs" / "dual-agent" / task_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "prd": "prd.md",
        "grill_findings": "grill-findings.md",
        "issues": "issues.md",
        "tdd_plan": "tdd.md",
        "implementation_plan": "implementation-plan.md",
    }
    for kind, filename in mapping.items():
        target = source_dir / filename
        target.write_text(
            (FIXTURE_ROOT / kind / "good.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_workflow_visual_evidence_policy_requires_evidence_for_vela_only_intent():
    policy = workflow_visual_evidence_policy(
        intent="Fix Vela colleague behavior without claiming live success prematurely.",
        task_id="workflow-1",
        user_facing=False,
        planning_artifacts=[],
    )

    assert policy["required"] is True
    assert policy["source"] == "auto_live_surface"
    assert "vela" in policy["matched_terms"]


def test_workflow_visual_evidence_policy_scans_planning_artifacts(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text(
        "This PRD touches Slack-visible output and requires screenshots.",
        encoding="utf-8",
    )

    policy = workflow_visual_evidence_policy(
        intent="Refactor internal helper.",
        task_id="workflow-1",
        user_facing=False,
        planning_artifacts=[{
            "path": str(prd),
            "kind": "prd",
            "mutable_by_worker": False,
        }],
    )

    assert policy["required"] is True
    assert {"slack", "screenshot"} <= set(policy["matched_terms"])
    assert policy["artifact_matches"][0]["kind"] == "prd"


def _cursor_review_result(task_id: str, *, decision: str = "accept") -> CursorInvocationResult:
    outcome = Outcome(
        task_id=task_id,
        summary="Cursor independently reviewed the gate.",
        specialists=[{"name": "Cursor Reviewer", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else ["Cursor found an unresolved concern."],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.91,
        claims=[],
    )
    return CursorInvocationResult(
        probe=ProbeResult("CURSOR", "green", "cursor_review_ok"),
        outcome=outcome,
        transcript=f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>",
        agent_id="agent-test",
        run_id="run-cursor",
        status="finished",
        model="composer-2.5",
        duration_ms=10,
    )


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_happy_path_owns_full_lifecycle(tmp_path):
    notifier = _Notifier()
    server, state = _server(tmp_path, notifier=notifier)
    _write_good_workflow_source_artifacts(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert [step["gate"] for step in result["steps"]] == [
        "prd_review",
        "issues_review",
        "tdd_review",
        "implementation_plan",
        "execution",
        "outcome_review",
    ]
    assert all(step["status"] == "accepted" for step in result["steps"])
    assert result["mandatory_artifacts"]["status"] == "ok"
    for relative in [
        "source/prd.md",
        "source/grill-findings.md",
        "source/issues.md",
        "source/tdd.md",
        "source/implementation-plan.md",
        "interactions.md",
        "outcome-review.md",
        "transcript.md",
    ]:
        assert (tmp_path / "docs" / "dual-agent" / "workflow-1" / relative).exists()

    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "accepted"
    steps = state.list_dual_agent_workflow_steps(run_id="workflow-run", task_id="workflow-1")
    assert [step["gate"] for step in steps] == [step["gate"] for step in result["steps"]]
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert all(round_payload["objection"] == "both agents accepted" for round_payload in rounds)
    assert any("dual-agent workflow started" in message for message in notifier.messages)
    assert any("dual-agent workflow accepted" in message for message in notifier.messages)


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_passes_budget_to_each_lead_gate(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    runner_calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run workflow with a non-default live probe budget.",
        max_rounds_per_gate=1,
        budget_usd=42.5,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert len(runner_calls) == 6
    for argv in runner_calls:
        budget_flag_index = argv.index("--max-budget-usd")
        assert argv[budget_flag_index + 1] == "42.5"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_auto_seeded_planning_stubs(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Should not run.\n" + _outcome_block("workflow-1")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["final_gate_result"]["probes"]["P_planning"]["reason"] == "planning_validation_failed"
    assert runner_calls == []


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_requires_prd_tdd_skill_receipts(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Attempt workflow without PRD/TDD skill receipts.",
        max_rounds_per_gate=1,
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert result["final_gate_result"]["probes"]["P12"]["reason"] == "missing_prd_tdd_skill_receipts"
    assert result["final_gate_result"]["trace_envelope"]["failure_taxonomy"]["category"] == "governance"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_records_skill_receipt_validation(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run workflow with PRD/TDD skill receipts.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_skill_receipt_validation"
    ]
    assert events
    assert events[0]["probe"]["status"] == "green"
    assert events[0]["trace_envelope"]["policy_verdict"] == "accepted"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_with_cursor_review_accepts_all_gates(tmp_path):
    cursor_calls = []

    def fake_cursor_runner(request):
        cursor_calls.append(request)
        return _cursor_review_result(request.task_id)

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Use Cursor as the third reviewer.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert len(cursor_calls) == 6
    cursor_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "tri_agent_cursor_review"
    ]
    assert len(cursor_events) == 6
    assert all(event["cursor_review"]["accepted"] for event in cursor_events)

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert len(transcript["cursor_reviews"]) == 6


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection(tmp_path):
    def fake_cursor_runner(request):
        return _cursor_review_result(request.task_id, decision="revise")

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Cursor should block unresolved concerns.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["final_gate_result"]["cursor_review"]["accepted"] is False
    assert result["final_gate_result"]["escalation"]["reason"] == "agents_not_converged"
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["objection"] == "cursor_review_failed: cursor_review_ok"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_enforces_v5_without_prompt_following(tmp_path):
    notifier = _Notifier()
    server, state = _server(tmp_path, decision="revise", notifier=notifier)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Force a disagreement fixture.",
        max_rounds_per_gate=2,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["steps"] == [{"gate": "prd_review", "status": "blocked", "attempt_count": 2}]
    assert result["final_gate_result"]["status"] == "blocked"
    assert result["final_gate_result"]["escalation"]["reason"] == "agents_not_converged"
    assert result["final_gate_result"]["workflow_deadlock_escalation"]["status"] == "paused_for_human"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"
    assert notifier.prompts
    assert any("dual-agent workflow needs_user_input gate=prd_review" in message for message in notifier.messages)
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert len(rounds) == 2
    assert rounds[-1]["objection"] == "max_rounds_per_gate exhausted without both agents accepting"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_retries_malformed_outcome_once(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        stdout = "Worker forgot the typed block." if calls == 1 else _outcome_block("workflow-1")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(stdout),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Retry a malformed worker outcome.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert calls == 7


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_on_claude_failure_and_requests_input(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    notifier = _Notifier()

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="claude failed",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        notifier=notifier,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Surface worker failure.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["final_gate_result"]["status"] == "blocked"
    assert any("dual-agent workflow gate_blocked gate=prd_review" in message for message in notifier.messages)
    assert any("dual-agent workflow needs_user_input gate=prd_review" in message for message in notifier.messages)


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_can_rerun_after_corrective_input(tmp_path):
    decision = "revise"

    def decide():
        return decision

    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1", decision=decide()),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
    )

    first = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="First run blocks, second run accepts after correction.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))
    assert first["status"] == "blocked"

    decision = "accept"
    second = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Correction supplied; rerun safely.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert second["status"] == "accepted"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "accepted"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_user_facing_without_visual_evidence(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Review a user-facing UI change.",
        user_facing=True,
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["escalation"]["reason"] == "required_artifacts_missing"
    assert "screenshots" in result["final_gate_result"]["artifact_rigor"]["missing_artifacts"]
    steps = state.list_dual_agent_workflow_steps(run_id="workflow-run", task_id="workflow-1")
    assert [step["status"] for step in steps[:-1]] == ["accepted"] * 5
    assert steps[-1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_auto_requires_visual_evidence_for_vela_live_surfaces(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent=(
            "Fix Vela Slack meeting and Google Calendar degraded-path behavior. "
            "Do not claim live provider success without screenshots."
        ),
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["visual_evidence_policy"]["required"] is True
    assert result["visual_evidence_policy"]["source"] == "auto_live_surface"
    assert {"slack", "calendar", "google calendar", "screenshot"} <= set(
        result["visual_evidence_policy"]["matched_terms"]
    )
    assert result["final_gate_result"]["artifact_rigor"]["user_facing"] is True
    assert "screenshots" in result["final_gate_result"]["artifact_rigor"]["missing_artifacts"]
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["user_facing"] == 1


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_auto_visual_policy_accepts_computer_use_evidence(tmp_path):
    server, state = _server(tmp_path)
    screenshot = tmp_path / "vela-slack-final-state.png"
    screenshot.write_bytes(_tiny_png())

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Fix Vela Slack meeting degraded-path behavior.",
        screenshots=[{
            "path": str(screenshot),
            "label": "Vela Slack final state",
            "note": "Captured by Codex with Computer Use before outcome review.",
            "source": "computer_use",
            "validation": {
                "status": "passed",
                "notes": "Slack-visible result was reviewed against the receipt-gating acceptance criteria.",
            },
        }],
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["visual_evidence_policy"]["required"] is True
    assert result["final_gate_result"]["artifact_rigor"]["visual_validation"]["status"] == "ok"
    assert "docs/dual-agent/workflow-1/screenshots/01-vela-slack-final-state.png" in (
        result["artifact_export"]["files"]
    )
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["user_facing"] == 1


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_verifies_final_claims(tmp_path):
    server, state = _server(tmp_path, claims=["pushed"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Claim remote push without evidence.",
        max_rounds_per_gate=1,
        verified_claims=["pushed"],
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["claim_verification"]["reason"] == "workflow_claim_verification_failed"
    assert "pushed_without_remote_receipt" in result["final_gate_result"]["claim_verification"]["details"]["failures"]
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_requires_test_and_diff_receipts_for_claims(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Claim tests and implementation without receipts.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "tests_passed_without_test_receipt" in failures
    assert "implemented_without_diff_receipt" in failures


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_rejects_unrelated_receipts_for_claims(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    unrelated_receipts = _tool_receipts()
    for receipt in unrelated_receipts:
        receipt["claims"] = ["unrelated cleanup"]

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject stale receipts that do not map to the current claims.",
        max_rounds_per_gate=1,
        tool_receipts=unrelated_receipts,
    ))

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "tests_passed_without_test_receipt" in failures
    assert "implemented_without_diff_receipt" in failures


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accepts_receipt_backed_claims(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented", "pushed"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Accept only with receipts.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(include_push=True),
    ))

    assert result["status"] == "accepted"
    receipt_payload = result["final_gate_result"]["claim_verification"]["details"]["receipts"]
    assert {receipt["kind"] for receipt in receipt_payload} >= {"test", "git_diff", "git_remote"}


@pytest.mark.asyncio
async def test_workflow_resume_prompt_tool_is_state_derived(tmp_path):
    server, _state = _server(tmp_path, decision="revise")
    await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Create a blocked workflow.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))

    prompt = await _maybe_await(server.tools["read_dual_agent_workflow_resume_prompt"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))

    assert prompt["status"] == "ok"
    assert "Continue run_id=workflow-run" in prompt["prompt"]
    assert "task_id=workflow-1" in prompt["prompt"]
    assert "next_safe_action=inspect blocker" in prompt["prompt"]
    assert prompt["latest_event_id"] > 0
    assert prompt["steps"][0]["gate"] == "prd_review"
    assert prompt["blocker"]["gate"] == "prd_review"
    assert prompt["artifact_output_dir"].endswith("docs/dual-agent/workflow-1")
    assert "read_gate_transcript" in prompt["prompt"]
