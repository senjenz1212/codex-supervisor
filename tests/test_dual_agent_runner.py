from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from supervisor.dual_agent import GateRound, ProbeResult
from supervisor.dual_agent_runner import (
    DualAgentGateSpec,
    build_lead_replay_stdout,
    claim_resume_signal,
    make_replay_runner,
    request_deadlock_escalation,
    request_validation_escalation,
    resume_pending_gates,
    run_dual_agent_gate,
    run_dual_agent_gate_with_escalation,
    run_dual_agent_gates,
    send_stale_paused_digests,
    write_replay_fixture_family,
)
from supervisor.dual_agent_lead import OutcomeValidationPolicy, PlanningArtifact
from supervisor.state import State


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


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


def _planning_artifact_fixture(kind: str, fixture_name: str = "good") -> PlanningArtifact:
    return PlanningArtifact(
        path=FIXTURE_ROOT / kind / f"{fixture_name}.md",
        kind=kind,  # type: ignore[arg-type]
        mutable_by_worker=False,
    )


def _good_planning_artifacts() -> tuple[PlanningArtifact, ...]:
    return (
        _planning_artifact_fixture("prd"),
        _planning_artifact_fixture("issues"),
        _planning_artifact_fixture("tdd_plan"),
        _planning_artifact_fixture("grill_findings"),
        _planning_artifact_fixture("implementation_plan"),
    )


def _write_good_planning_artifacts(tmp_path: Path) -> tuple[PlanningArtifact, ...]:
    artifact_dir = tmp_path / "docs" / "dual-agent" / "gate-fixture" / "source"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "prd": "prd.md",
        "issues": "issues.md",
        "tdd_plan": "tdd.md",
        "grill_findings": "grill-findings.md",
        "implementation_plan": "implementation-plan.md",
    }
    artifacts = []
    for kind, filename in mapping.items():
        target = artifact_dir / filename
        target.write_text(
            (FIXTURE_ROOT / kind / "good.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        artifacts.append(PlanningArtifact(path=target, kind=kind))  # type: ignore[arg-type]
    return tuple(artifacts)


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


def test_run_dual_agent_gate_blocks_stub_prd_before_claude_invocation(tmp_path):
    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Should not run.\n" + _outcome_block()),
            stderr="",
        )

    spec = DualAgentGateSpec(
        task_id="gate-stub-prd",
        run_id="run-planning",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
        planning_artifacts=(_planning_artifact_fixture("prd", "stub"),),
    )

    result = run_dual_agent_gate(spec, runner=fake_runner)

    assert result.status == "blocked"
    assert result.attempts == 0
    assert result.probes["P_planning"].reason == "planning_validation_failed"
    assert calls == 0


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
    planning_artifacts = _write_good_planning_artifacts(tmp_path)
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
        planning_artifacts=planning_artifacts,
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
    assert packet["planning_artifacts"][0]["kind"] == "prd"


def test_cs24_gate_runner_refuses_existing_handoff_lock_without_invoking_lead(tmp_path):
    handoff_dir = tmp_path / ".handoff"
    handoff_dir.mkdir()
    lock_path = handoff_dir / ".dual-agent.lock"
    lock_path.write_text(json.dumps({"run_id": "other-run", "task_id": "other-task"}))
    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Should not run.\n" + _outcome_block("gate-locked")),
            stderr="",
        )

    spec = DualAgentGateSpec(
        task_id="gate-locked",
        run_id="run-cs24",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
    )

    result = run_dual_agent_gate(spec, runner=fake_runner)

    assert result.status == "blocked"
    assert result.attempts == 0
    assert result.handoff_packet_path == tmp_path / ".handoff" / "gate-locked.json"
    assert result.probes["P1"].reason == "handoff_lock_held"
    assert calls == 0
    assert lock_path.exists()


def test_cs24_gate_runner_removes_handoff_lock_after_success(tmp_path):
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"

    def fake_runner(argv, **kwargs):
        assert lock_path.exists()
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Accepted.\n" + _outcome_block("gate-cleanup")),
            stderr="",
        )

    spec = DualAgentGateSpec(
        task_id="gate-cleanup",
        run_id="run-cs24",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
        planning_artifacts=_write_good_planning_artifacts(tmp_path),
        expected_specialists=("Planner",),
        expected_decisions=("accept plan",),
        expected_objections=(),
    )

    result = run_dual_agent_gate(spec, runner=fake_runner)

    assert result.status == "accepted"
    assert not lock_path.exists()


def test_cs24_gate_runner_removes_handoff_lock_after_blocked_worker_result(tmp_path):
    lock_path = tmp_path / ".handoff" / ".dual-agent.lock"

    def fake_runner(argv, **kwargs):
        assert lock_path.exists()
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")

    spec = DualAgentGateSpec(
        task_id="gate-blocked-cleanup",
        run_id="run-cs24",
        gate="outcome_review",
        instruction="Review outcome.",
        cwd=tmp_path,
        planning_artifacts=_write_good_planning_artifacts(tmp_path),
    )

    result = run_dual_agent_gate(spec, runner=fake_runner)

    assert result.status == "blocked"
    assert result.probes["P2"].reason == "lead_invocation_failed"
    assert not lock_path.exists()


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
        planning_artifacts=_write_good_planning_artifacts(tmp_path),
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
            planning_artifacts=_write_good_planning_artifacts(tmp_path),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "expected_policy", "expected_reason"),
    [
        (
            json.dumps({"result": _outcome_block(decision="wrong decision")}),
            "fidelity_failure",
            "outcome_signal_loss",
        ),
        (
            "",
            "subprocess_failure",
            "lead_invocation_failed",
        ),
        (
            None,
            "timeout",
            "lead_invocation_timeout",
        ),
        (
            json.dumps({"message": _outcome_block()}),
            "subprocess_failure",
            "claude_json_schema_drift",
        ),
    ],
)
async def test_blocked_gate_abort_policies_escalate_validation_failures(
    tmp_path,
    stdout,
    expected_policy,
    expected_reason,
):
    state = State(str(tmp_path / "state.db"))
    sent: list[dict] = []

    class FakeNotifier:
        async def send_approval_prompt(self, **kwargs):
            sent.append(kwargs)
            return {"ok": True}

    def fake_runner(argv, **kwargs):
        if stdout is None:
            raise subprocess.TimeoutExpired(argv, timeout=3)
        if expected_policy == "subprocess_failure" and stdout == "":
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    spec = DualAgentGateSpec(
        task_id="gate-validation",
        run_id="run-validation",
        gate="outcome_review",
        instruction="Review outcome.",
        cwd=tmp_path,
        planning_artifacts=_write_good_planning_artifacts(tmp_path),
        expected_specialists=("Planner",),
        expected_decisions=("accept plan",),
        expected_objections=(),
        timeout_s=3,
    )

    result = await run_dual_agent_gate_with_escalation(
        spec,
        state=state,
        notifier=FakeNotifier(),
        runner=fake_runner,
    )

    assert result.status == "blocked"
    assert result.escalation is not None
    assert result.escalation.status == "validation_failure"
    assert sent, "validation failure should ask the operator"
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["action_type"] == "dual_agent_validation_failure"
    assert action["status"] == "paused_for_human"
    payload = json.loads(action["payload_json"])
    assert payload["escalation_type"] == "validation_failure"
    assert payload["policy_field"] == expected_policy
    assert payload["probe_reason"] == expected_reason


@pytest.mark.asyncio
async def test_validation_failure_callback_resolves_retry_request(tmp_path):
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
    state = State(str(tmp_path / "state.db"))
    sent: list[dict] = []

    class FakeNotifier:
        async def send_approval_prompt(self, **kwargs):
            sent.append(kwargs)
            return {"ok": True}

    escalation = await request_validation_escalation(
        state=state,
        notifier=FakeNotifier(),
        run_id="run-validation",
        task_id="gate-validation",
        gate="outcome_review",
        policy_field="fidelity_failure",
        probe=ProbeResult(
            "P3",
            "red",
            "outcome_signal_loss",
            {"decisions": ["accept plan"]},
        ),
    )

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
        {"id": "validation-callback", "data": f"ask:{escalation.ask_id}:{escalation.nonce}:1"},
        client=client,
    )

    ask = state.get_ask(escalation.ask_id)
    assert ask["status"] == "answered"
    assert ask["answer"] == "Retry"
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["status"] == "retry_requested"
    assert client.posts[-1]["data"]["text"] == "Recorded: Retry"

    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Retried.\n" + _outcome_block("gate-validation")),
            stderr="",
        )

    results = resume_pending_gates([
        DualAgentGateSpec(
            task_id="gate-validation",
            run_id="run-validation",
            gate="outcome_review",
            instruction="Retry validation.",
            cwd=tmp_path,
            planning_artifacts=_write_good_planning_artifacts(tmp_path),
            expected_specialists=("Planner",),
            expected_decisions=("accept plan",),
            expected_objections=(),
        )
    ], state=state, runner=fake_runner)

    assert len(results) == 1
    assert results[0].status == "accepted"
    assert calls == 1
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["status"] == "retried"


@pytest.mark.asyncio
async def test_deadlock_continue_signal_is_claimed_once_and_redispatches_gate(tmp_path):
    state = State(str(tmp_path / "state.db"))
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
        run_id="run-resume",
        task_id="gate-resume",
        gate="tdd_review",
        rounds=rounds,
        per_gate_cap=3,
        task_budget=10,
    )
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    from supervisor.dual_agent_runner import resolve_deadlock_escalation

    resolve_deadlock_escalation(
        state=state,
        ask_id=escalation.ask_id,
        answer="Continue",
        nonce=escalation.nonce,
        action_row=action,
    )

    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Resumed.\n" + _outcome_block("gate-resume")),
            stderr="",
        )

    specs = [
        DualAgentGateSpec(
            task_id="gate-resume",
            run_id="run-resume",
            gate="tdd_review",
            instruction="Resume the gate.",
            cwd=tmp_path,
            planning_artifacts=_write_good_planning_artifacts(tmp_path),
            expected_specialists=("Planner",),
            expected_decisions=("accept plan",),
            expected_objections=(),
        )
    ]

    results = resume_pending_gates(specs, state=state, runner=fake_runner)

    assert len(results) == 1
    assert results[0].status == "accepted"
    assert calls == 1
    assert claim_resume_signal(state, run_id="run-resume", task_id="gate-resume") is None
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    assert action["status"] == "resumed"


@pytest.mark.asyncio
async def test_paused_dual_agent_actions_send_one_stale_digest(tmp_path):
    state = State(str(tmp_path / "state.db"))

    class FakeNotifier:
        def __init__(self):
            self.messages: list[str] = []

        async def send_approval_prompt(self, **kwargs):
            return {"ok": True}

        async def send_message(self, text, **kwargs):
            self.messages.append(text)
            return {"ok": True}

    notifier = FakeNotifier()
    current = int(time.time())
    rounds = [
        GateRound(
            round_index=1,
            codex_decision="deny",
            claude_decision="accept",
            codex_confidence=0.96,
            claude_confidence=0.95,
            objection="Still missing tests.",
        )
    ]
    escalation = await request_deadlock_escalation(
        state=state,
        notifier=notifier,
        run_id="run-paused",
        task_id="gate-paused",
        gate="tdd_review",
        rounds=rounds,
        per_gate_cap=1,
        task_budget=1,
        now=lambda: current,
    )
    action = state._conn.execute("SELECT * FROM actions").fetchone()
    from supervisor.dual_agent_runner import resolve_deadlock_escalation

    resolve_deadlock_escalation(
        state=state,
        ask_id=escalation.ask_id,
        answer="Pause",
        nonce=escalation.nonce,
        action_row=action,
    )
    state._conn.execute(
        "UPDATE actions SET completed_at=? WHERE id=?",
        (current - 4_000, action["id"]),
    )
    state._conn.commit()

    sent_ids = await send_stale_paused_digests(
        state=state,
        notifier=notifier,
        stale_after_s=3600,
        now=lambda: current,
    )

    assert sent_ids == [action["id"]]
    assert len(notifier.messages) == 1
    assert "[gate-paused] Dual-agent gate still paused." in notifier.messages[0]
    assert "gate=tdd_review" in notifier.messages[0]
    assert "age_min=66" in notifier.messages[0]
    refreshed = state._conn.execute("SELECT * FROM actions").fetchone()
    payload = json.loads(refreshed["payload_json"])
    assert refreshed["status"] == "paused"
    assert payload["paused_digest_sent_at"] == current

    second = await send_stale_paused_digests(
        state=state,
        notifier=notifier,
        stale_after_s=3600,
        now=lambda: current + 3_000,
    )

    assert second == []
    assert len(notifier.messages) == 1
