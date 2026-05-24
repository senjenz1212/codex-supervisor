from __future__ import annotations

import inspect
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from supervisor.config import Config
from supervisor.state import State
from supervisor.target.types import ScopeContract


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


def _outcome_block(task_id: str = "gate-1", decision: str = "accept plan") -> str:
    payload = {
        "task_id": task_id,
        "summary": "Implemented through /lead.",
        "specialists": [{"name": "Planner", "decision": decision}],
        "decisions": [decision],
        "objections": [],
        "changed_files": ["supervisor/dual_agent.py"],
        "tests": ["pytest tests/test_codex_supervisor_mcp_stdio.py"],
        "test_status": "passed",
        "confidence": 0.94,
    }
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


def _write_planning_artifacts(tmp_path: Path, *, include_implementation_plan: bool = True) -> list[dict]:
    artifact_dir = tmp_path / "docs" / "dual-agent" / "gate-1"
    artifact_dir.mkdir(parents=True)
    files = {
        "prd": artifact_dir / "prd.md",
        "tdd_plan": artifact_dir / "tdd.md",
        "grill_findings": artifact_dir / "grill-findings.md",
        "issues": artifact_dir / "issues.md",
    }
    if include_implementation_plan:
        files["implementation_plan"] = artifact_dir / "implementation-plan.md"
    artifacts = []
    for kind, path in files.items():
        path.write_text(f"# {kind}\n", encoding="utf-8")
        artifacts.append({"path": str(path), "kind": kind, "mutable_by_worker": False})
    return artifacts


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
        "check_budget",
        "escalate_deadlock",
        "poll_resume_signal",
        "start_codex_session",
    }

    result = await _maybe_await(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="tdd_review",
        instruction="Run the gate.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept plan"],
        expected_objections=[],
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
async def test_codex_supervisor_mcp_accepts_strict_outcome_gate_with_required_artifacts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    planning_artifacts = _write_planning_artifacts(tmp_path)

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
    screenshot = tmp_path / "final-state.png"
    screenshot.write_bytes(b"fake-png")

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
        }],
    ))

    assert result["status"] == "accepted"
    assert result["artifact_rigor"]["status"] == "ok"
    assert "docs/dual-agent/gate-1/screenshots.md" in result["artifact_export"]["files"]
    assert "docs/dual-agent/gate-1/screenshots/01-final-state.png" in result["artifact_export"]["files"]


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
