from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from supervisor.dual_agent import GateRound
from supervisor.dual_agent_runner import (
    DualAgentGateSpec,
    build_lead_replay_stdout,
    make_replay_runner,
    request_deadlock_escalation,
    run_dual_agent_gate,
    run_dual_agent_gates,
    write_replay_fixture_family,
)
from supervisor.dual_agent_lead import PlanningArtifact
from supervisor.state import State


def _outcome_block(task_id: str = "gate-1", *, decision: str = "accept plan") -> str:
    payload = {
        "task_id": task_id,
        "summary": "Gate accepted.",
        "specialists": [
            {"name": "Planner", "decision": decision, "objection": None},
        ],
        "decisions": [decision],
        "objections": [],
        "changed_files": ["docs/prd.md", "tests/test_dual_agent_runner.py"],
        "tests": ["pytest tests/test_dual_agent_runner.py"],
        "test_status": "passed",
        "confidence": 0.96,
        "claims": [],
    }
    return f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"


def test_p2_replay_fixture_family_exercises_large_stdout_without_truncation(tmp_path):
    seed_transcript = (
        "Real lead transcript seed.\n"
        "Specialist Planner decided accept plan.\n"
        "{body}\n"
        + _outcome_block()
    )
    fixtures = write_replay_fixture_family(
        tmp_path / "fixtures",
        seed_transcript=seed_transcript,
        token_sizes=(2_000, 10_000, 50_000, 200_000),
        model="claude-haiku-4-5-20251001",
    )

    for fixture in fixtures:
        runner = make_replay_runner(fixture.stdout_path)
        spec = DualAgentGateSpec(
            task_id=f"gate-{fixture.token_size}",
            run_id="run-replay",
            gate="intent",
            instruction="Replay a captured lead fixture.",
            cwd=tmp_path,
            expected_specialists=("Planner",),
            expected_decisions=("accept plan",),
            expected_objections=(),
        )

        result = run_dual_agent_gate(spec, runner=runner)

        assert result.status == "accepted"
        assert result.lead_result is not None
        assert result.lead_result.stdout_bytes == fixture.stdout_path.stat().st_size
        assert result.probes["P2"].ok
        assert result.probes["P3"].ok
        assert result.probes["P2"].details["captured_bytes"] == fixture.stdout_path.stat().st_size


@pytest.mark.asyncio
async def test_p4_deadlock_escalation_sends_telegram_prompt_and_callback_resolves(tmp_path):
    from supervisor.config import Config
    from supervisor.telegram import TelegramPoller

    cfg = Config(**{
        "target": {"kind": "codex", "codex": {"sessions_root": str(tmp_path / "sessions")}},
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
    })
    state = State(str(tmp_path / "telegram.db"))
    sent: list[dict] = []

    class FakeNotifier:
        async def send_approval_prompt(self, **kwargs):
            sent.append(kwargs)
            return {"ok": True}

    rounds = [
        GateRound(
            round_index=i,
            codex_decision="deny",
            claude_decision="accept",
            codex_confidence=0.96,
            claude_confidence=0.95,
            objection="Codex requires tests; Claude wants to proceed.",
        )
        for i in range(1, 4)
    ]

    escalation = await request_deadlock_escalation(
        state=state,
        notifier=FakeNotifier(),
        run_id="run-deadlock",
        task_id="task-deadlock",
        gate="tdd_review",
        rounds=rounds,
        per_gate_cap=3,
        task_budget=10,
        now=lambda: int(time.time()),
    )

    assert escalation.status == "paused_for_human"
    assert sent[0]["ask_id"] == escalation.ask_id
    assert sent[0]["options"] == ["Pause", "Kill", "Continue"]
    assert "task-deadlock" in sent[0]["question"]
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["action_type"] == "dual_agent_gate_deadlock"
    assert action["status"] == "paused_for_human"
    payload = json.loads(action["payload_json"])
    assert payload["ask_id"] == escalation.ask_id
    assert payload["escalation_type"] == "kill_or_pause"

    class FakeClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, data=None, **kwargs):
            self.posts.append({"url": url, "data": data or {}})
            return FakeResponse()

    class FakeResponse:
        def json(self):
            return {"ok": True}

    poller = TelegramPoller(cfg, state)
    client = FakeClient()
    await poller._handle_callback(
        {"id": "deadlock-callback", "data": f"ask:{escalation.ask_id}:{escalation.nonce}:2"},
        client=client,
    )

    ask = state.get_ask(escalation.ask_id)
    assert ask["status"] == "answered"
    assert ask["answer"] == "Continue"
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["status"] == "continue_requested"
    payload = json.loads(action["payload_json"])
    assert payload["answer"] == "Continue"
    assert client.posts[-1]["data"]["text"] == "Recorded: Continue"


def test_cs24_gate_runner_writes_handoff_invokes_lead_and_verifies_planning_boundaries(tmp_path):
    prd = tmp_path / "docs" / "prd.md"
    prd.parent.mkdir()
    prd.write_text("# PRD\nOriginal.\n")
    stdout = build_lead_replay_stdout(
        "Lead reviewed packet.\n" + _outcome_block(decision="accept plan"),
        model="claude-opus-4-7",
    )

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    spec = DualAgentGateSpec(
        task_id="gate-1",
        run_id="run-cs24",
        gate="prd_review",
        instruction="Review the PRD packet.",
        cwd=tmp_path,
        planning_artifacts=(PlanningArtifact(path=prd, kind="prd"),),
        expected_specialists=("Planner",),
        expected_decisions=("accept plan",),
        expected_objections=(),
    )

    result = run_dual_agent_gate(spec, runner=fake_runner)

    assert result.status == "accepted"
    assert result.handoff_packet_path == tmp_path / ".handoff" / "gate-1.json"
    assert result.probes["P1"].reason == "planning_artifact_boundaries_ok"
    assert result.probes["P2"].ok
    assert result.probes["P3"].ok
    packet = json.loads(result.handoff_packet_path.read_text())
    assert packet["planning_artifacts"][0]["path"] == "docs/prd.md"


def test_cs24_gate_runner_retries_malformed_outcome_once_then_accepts(tmp_path):
    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps({"result": "<dual_agent_outcome>{bad}</dual_agent_outcome>"}),
                stderr="",
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Corrected.\n" + _outcome_block()),
            stderr="",
        )

    spec = DualAgentGateSpec(
        task_id="gate-retry",
        run_id="run-cs24",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        expected_specialists=("Planner",),
        expected_decisions=("accept plan",),
        expected_objections=(),
    )

    result = run_dual_agent_gate(spec, runner=fake_runner)

    assert result.status == "accepted"
    assert calls == 2
    assert result.attempts == 2


def test_cs24_gate_runner_stops_sequence_on_blocked_gate(tmp_path):
    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"message": _outcome_block()}),
            stderr="",
        )

    specs = [
        DualAgentGateSpec(
            task_id="gate-blocked",
            run_id="run-cs24",
            gate="prd_review",
            instruction="Review PRD.",
            cwd=tmp_path,
        ),
        DualAgentGateSpec(
            task_id="gate-never-runs",
            run_id="run-cs24",
            gate="tdd_review",
            instruction="Review TDD.",
            cwd=tmp_path,
        ),
    ]

    results = run_dual_agent_gates(specs, runner=fake_runner)

    assert len(results) == 1
    assert results[0].status == "blocked"
    assert results[0].probes["P2"].reason == "claude_json_schema_drift"
    assert calls == 1
