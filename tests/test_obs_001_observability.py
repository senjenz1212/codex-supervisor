"""OBS-001 public-boundary tests for rollout, joins, and semantic drift."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
from supervisor.agent_runtime import AgentRunHandle, AgentRunResult, AgentTask
from supervisor.config import Config
from supervisor.drift_detector import DriftDetector
from supervisor.model_client import ModelResponse
from supervisor.rollout_watcher import RolloutWatcher
from supervisor.run_registry import register_submitted_workflow
from supervisor.runtime_execution import RuntimeExecution
from supervisor.state import State
from supervisor.target.types import ScopeContract


FIXTURES = Path(__file__).parent / "fixtures" / "rollout_watcher"


def _config(tmp_path: Path) -> Config:
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


def _captured_rollout(
    *,
    tmp_path: Path,
    fixture_name: str,
    session_id: str,
    captured_cwd: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "07" / "11"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir(exist_ok=True)
    rollout = rollout_dir / f"rollout-2026-07-11T19-14-22-{session_id}.jsonl"
    fixture_text = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    if captured_cwd is not None:
        fixture_text = fixture_text.replace(
            "/captured/workspace",
            str(Path(captured_cwd).expanduser().resolve()),
        )
    rollout.write_text(
        fixture_text,
        encoding="utf-8",
    )
    return sessions_root, registry_dir, rollout


@pytest.mark.asyncio
async def test_captured_nested_codex_rollout_reaches_turn_terminal_only(tmp_path):
    session_id = "019f52a1-1111-7222-8333-444444444444"
    sessions_root, registry_dir, rollout = _captured_rollout(
        tmp_path=tmp_path,
        fixture_name="codex_nested_terminal.jsonl",
        session_id=session_id,
    )
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="workflow-captured-codex",
        session_id=session_id,
        rollout_path=str(rollout),
        task="Inspect the requested files.",
        scope=ScopeContract(),
        target_kind="codex",
    )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    run = state.get_run_by_session(session_id)
    assert run is not None
    assert run["status"] == "running"
    assert [
        row["kind"]
        for row in state._conn.execute(
            "SELECT kind FROM events WHERE run_id=? ORDER BY event_id",
            (run["run_id"],),
        )
    ] == [
        "run.started",
        "turn.started",
        "agent.message",
        "tool.started",
        "tool.completed",
        "turn.completed",
    ]
    assert state.decisions.empty()


@pytest.mark.asyncio
async def test_captured_nested_claude_rollout_reaches_turn_terminal_only(tmp_path):
    session_id = "bc5fde4c-1a70-4286-94fc-82e3be648008"
    sessions_root, registry_dir, rollout = _captured_rollout(
        tmp_path=tmp_path,
        fixture_name="claude_nested_terminal.jsonl",
        session_id=session_id,
    )
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="workflow-captured-claude",
        session_id=session_id,
        rollout_path=str(rollout),
        task="Inspect the repository.",
        scope=ScopeContract(),
        target_kind="claude_code",
    )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    run = state.get_run_by_session(session_id)
    assert run is not None
    assert run["status"] == "running"
    assert [
        row["kind"]
        for row in state._conn.execute(
            "SELECT kind FROM events WHERE run_id=? ORDER BY event_id",
            (run["run_id"],),
        )
    ] == [
        "turn.started",
        "agent.message",
        "tool.started",
        "tool.completed",
        "agent.message",
        "turn.completed",
    ]
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE run_id=? AND kind='user'",
        (run["run_id"],),
    ).fetchone()[0] == 0
    message_rows = state._conn.execute(
        """SELECT payload_json
             FROM events
            WHERE run_id=? AND kind='agent.message'
            ORDER BY event_id""",
        (run["run_id"],),
    ).fetchall()
    assert [
        block["text"]
        for row in message_rows
        for block in json.loads(row["payload_json"])["message"]["content"]
        if block.get("type") == "text"
    ] == [
        "I will inspect the repository.",
        "The repository inspection completed.",
    ]
    assert state.decisions.empty()


@pytest.mark.asyncio
async def test_submission_registers_target_session_and_joins_rollout_to_workflow(
    tmp_path,
):
    target_session_id = "019f52a1-aaaa-7bbb-8ccc-dddddddddddd"
    workflow_run_id = "d30b3005-83f8-4bba-9479-e8706a98ccce"
    task_id = "obs-001-observability-20260711"
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    api = CodexSupervisorMcpAPI(cfg, state)

    submitted = api.submit_dual_agent_workflow_job(
        cwd=str(tmp_path),
        task_id=task_id,
        run_id=workflow_run_id,
        intent="Normalize rollout events and repair workflow joins.",
        target_session_id=target_session_id,
        client_token="obs-001-registration-test",
    )

    assert submitted["status"] == "submitted"
    run = state.get_run(workflow_run_id)
    assert run is not None
    assert run["session_id"] == target_session_id
    assert run["task"] == "Normalize rollout events and repair workflow joins."
    assert [row["run_id"] for row in state.active_runs()] == [workflow_run_id]

    registry_path = Path(cfg.orchestrator.run_registry_dir) / f"{target_session_id}.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["workflow_run_id"] == workflow_run_id
    assert registry["target_session_id"] == target_session_id
    assert registry["task_id"] == task_id

    sessions_root, registry_dir, rollout = _captured_rollout(
        tmp_path=tmp_path,
        fixture_name="codex_nested_terminal.jsonl",
        session_id=target_session_id,
        captured_cwd=tmp_path,
    )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    event_rows = state._conn.execute(
        "SELECT run_id, payload_json FROM events WHERE source='rollout' ORDER BY event_id"
    ).fetchall()
    assert event_rows
    assert {row["run_id"] for row in event_rows} == {workflow_run_id}
    assert {
        json.loads(row["payload_json"])["workflow_run_id"]
        for row in event_rows
    } == {workflow_run_id}
    assert {
        json.loads(row["payload_json"])["task_id"]
        for row in event_rows
    } == {task_id}

    joined = state._conn.execute(
        """SELECT COUNT(DISTINCT jobs.task_id) AS task_count,
                  COUNT(DISTINCT jobs.run_id) AS run_count,
                  COUNT(DISTINCT jobs.job_id) AS workflow_count
             FROM events
             JOIN dual_agent_workflow_jobs AS jobs
               ON jobs.run_id = events.run_id
            WHERE events.source='rollout' AND events.run_id=?""",
        (workflow_run_id,),
    ).fetchone()
    assert dict(joined) == {
        "task_count": 1,
        "run_count": 1,
        "workflow_count": 1,
    }


@pytest.mark.asyncio
async def test_foreign_same_cwd_rollout_stays_quarantined_and_does_not_bind(
    tmp_path,
    monkeypatch,
):
    for key in (
        "SUPERVISOR_TARGET_SESSION_ID",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    workflow_run_id = "workflow-pending-session"
    target_session_id = "019f52a1-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    api = CodexSupervisorMcpAPI(cfg, state)

    submitted = api.submit_dual_agent_workflow_job(
        cwd=str(tmp_path),
        task_id="obs-pending-session",
        run_id=workflow_run_id,
        intent="Bind the first real rollout session.",
        client_token="obs-pending-session-token",
    )

    assert submitted["target_session_id"] == ""
    assert state.get_run(workflow_run_id)["session_id"] == (
        f"pending:{workflow_run_id}"
    )

    sessions_root, registry_dir, rollout = _captured_rollout(
        tmp_path=tmp_path,
        fixture_name="codex_nested_terminal.jsonl",
        session_id=target_session_id,
        captured_cwd=tmp_path,
    )
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )
    await watcher._drain_file(rollout)

    assert state.get_run(workflow_run_id)["session_id"] == (
        f"pending:{workflow_run_id}"
    )
    assert state.get_run_by_session(target_session_id) is None
    assert state.get_run(f"run_{target_session_id}") is None
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == 0
    assert len(
        list(
            (
                Path(cfg.orchestrator.run_registry_dir)
                / ".rollout-quarantine"
            ).glob("*.json")
        )
    ) == 1


def test_start_codex_session_reserves_before_spawn_and_binds_runtime_session(
    tmp_path,
    monkeypatch,
):
    for key in (
        "SUPERVISOR_TARGET_SESSION_ID",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    workflow_run_id = "workflow-launch-receipt"
    task_id = "obs-launch-receipt"
    target_session_id = "019f52a1-dddd-7eee-8fff-aaaaaaaaaaaa"
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    observed: dict[str, str] = {}

    def fake_codex_runtime_runner(task: AgentTask) -> RuntimeExecution:
        observed["launch_id"] = task.env["SUPERVISOR_LAUNCH_ID"]
        observed["nonce"] = task.env["SUPERVISOR_LAUNCH_NONCE"]
        pending_receipts = list(
            (
                Path(cfg.orchestrator.run_registry_dir)
                / ".launch-receipts"
                / "pending"
            ).glob("*.json")
        )
        assert len(pending_receipts) == 1
        assert observed["nonce"] not in pending_receipts[0].read_text(
            encoding="utf-8"
        )
        assert state.get_run(workflow_run_id)["session_id"] == (
            f"pending:{workflow_run_id}"
        )
        handle = AgentRunHandle(
            run_id="runtime-run",
            task_id=task.task_id,
            runtime="codex",
            session_id=target_session_id,
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
            result_hash="runtime-result-hash",
            token_usage={"tokens_in": 3, "tokens_out": 1},
            model_provenance="fake.model",
            token_provenance="fake.usage",
            metadata={"returncode": 0, "stderr": ""},
        )
        return RuntimeExecution(handle=handle, events=(), result=result)

    api = CodexSupervisorMcpAPI(
        cfg,
        state,
        codex_runtime_runner=fake_codex_runtime_runner,
    )
    api.submit_dual_agent_workflow_job(
        cwd=str(tmp_path),
        task_id=task_id,
        run_id=workflow_run_id,
        intent="Launch and bind the target runtime cryptographically.",
        client_token="obs-launch-receipt-token",
    )

    result = api.start_codex_session(
        prompt="Implement the bounded target task.",
        cwd=str(tmp_path),
        execute=True,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
    )

    assert result["status"] == "completed"
    assert result["launch_id"] == observed["launch_id"]
    assert result["session_id"] == target_session_id
    assert state.get_run(workflow_run_id)["session_id"] == target_session_id
    registration = json.loads(
        (
            Path(cfg.orchestrator.run_registry_dir)
            / f"{target_session_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert registration["launch_id"] == observed["launch_id"]
    assert registration["workflow_run_id"] == workflow_run_id
    assert registration["task_id"] == task_id
    assert registration["target_kind"] == "codex"
    assert not list(
        (
            Path(cfg.orchestrator.run_registry_dir)
            / ".launch-receipts"
            / "pending"
        ).glob("*.json")
    )
    assert len(
        list(
            (
                Path(cfg.orchestrator.run_registry_dir)
                / ".launch-receipts"
                / "consumed"
            ).glob("*.json")
        )
    ) == 1


@pytest.mark.asyncio
async def test_unjoined_rollout_is_quarantined_then_replayed_after_registration(
    tmp_path,
):
    workflow_run_id = "workflow-late-registration"
    target_session_id = "019f52a1-cccc-7ddd-8eee-ffffffffffff"
    sessions_root, registry_dir, rollout = _captured_rollout(
        tmp_path=tmp_path,
        fixture_name="codex_nested_terminal.jsonl",
        session_id=target_session_id,
        captured_cwd=tmp_path,
    )
    rollout_size = rollout.stat().st_size
    state = State(str(tmp_path / "state.db"))
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )

    await watcher._drain_file(rollout)

    assert state.get_run_by_session(target_session_id) is None
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == 0
    assert state.decisions.empty()

    register_submitted_workflow(
        state=state,
        registry_dir=registry_dir,
        workflow_run_id=workflow_run_id,
        target_session_id=target_session_id,
        task_id="obs-late-registration",
        task="Replay the quarantined rollout.",
        target_kind="codex",
        cwd=tmp_path,
        session_id_source="runtime_receipt",
    )
    rollout.unlink()

    restarted_watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
    )
    await restarted_watcher.sweep_once()

    run = state.get_run(workflow_run_id)
    assert run is not None
    assert run["session_id"] == target_session_id
    assert run["status"] == "running"
    rollout_events = state._conn.execute(
        """SELECT run_id, kind, payload_json
             FROM events
            WHERE source='rollout'
            ORDER BY event_id"""
    ).fetchall()
    assert [row["kind"] for row in rollout_events] == [
        "run.started",
        "turn.started",
        "agent.message",
        "tool.started",
        "tool.completed",
        "turn.completed",
    ]
    assert {row["run_id"] for row in rollout_events} == {workflow_run_id}
    assert not state._conn.execute(
        "SELECT 1 FROM events WHERE run_id LIKE 'unjoined:%'"
    ).fetchall()
    assert json.loads(rollout_events[0]["payload_json"])["type"] == "session_meta"
    assert (
        json.loads(rollout_events[-1]["payload_json"])["payload"]["type"]
        == "task_complete"
    )
    assert state.get_tail_offset(str(rollout)) == rollout_size
    await restarted_watcher.sweep_once()
    assert state._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source='rollout'"
    ).fetchone()[0] == len(rollout_events)
    assert state.decisions.empty()


def test_submission_explicit_target_session_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "environment-session")
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    api = CodexSupervisorMcpAPI(cfg, state)

    submitted = api.submit_dual_agent_workflow_job(
        cwd=str(tmp_path),
        task_id="obs-explicit-session",
        run_id="workflow-explicit-session",
        target_session_id="explicit-target-session",
        intent="Use the explicitly supplied target session.",
        client_token="obs-explicit-session-token",
    )

    assert submitted["target_session_id"] == "explicit-target-session"
    assert state.get_run("workflow-explicit-session")["session_id"] == "explicit-target-session"


def test_same_token_reattach_preserves_original_target_session(tmp_path):
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    api = CodexSupervisorMcpAPI(cfg, state)
    common = {
        "cwd": str(tmp_path),
        "task_id": "obs-stable-session",
        "run_id": "workflow-stable-session",
        "intent": "Keep the target-session join stable across retries.",
        "client_token": "obs-stable-session-token",
    }

    first = api.submit_dual_agent_workflow_job(
        **common,
        target_session_id="target-session-a",
    )
    second = api.submit_dual_agent_workflow_job(
        **common,
        target_session_id="target-session-b",
    )

    assert second["job_id"] == first["job_id"]
    assert second["reattached"] is True
    assert second["target_session_id"] == "target-session-a"
    assert state.get_run("workflow-stable-session")["session_id"] == "target-session-a"
    assert not (
        Path(cfg.orchestrator.run_registry_dir) / "target-session-b.json"
    ).exists()


def test_same_token_reattach_without_target_session_remains_pending(
    tmp_path,
    monkeypatch,
):
    for key in (
        "SUPERVISOR_TARGET_SESSION_ID",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    api = CodexSupervisorMcpAPI(cfg, state)
    common = {
        "cwd": str(tmp_path),
        "task_id": "obs-pending-reattach",
        "run_id": "workflow-pending-reattach",
        "intent": "Remain pending until a runtime session is observed.",
        "client_token": "obs-pending-reattach-token",
    }

    first = api.submit_dual_agent_workflow_job(**common)
    second = api.submit_dual_agent_workflow_job(
        **common,
        target_session_id="late-session-must-not-rebind",
    )

    assert second["job_id"] == first["job_id"]
    assert second["reattached"] is True
    assert second["target_session_id"] == ""
    run = state.get_run("workflow-pending-reattach")
    assert run is not None
    assert run["session_id"] == "pending:workflow-pending-reattach"
    pending_registrations = list(
        Path(cfg.orchestrator.run_registry_dir).glob(".pending-*.json")
    )
    assert len(pending_registrations) == 1
    assert json.loads(
        pending_registrations[0].read_text(encoding="utf-8")
    )["pending"] is True
    assert not (
        Path(cfg.orchestrator.run_registry_dir)
        / "late-session-must-not-rebind.json"
    ).exists()


class _LowSimilarityOpenAI:
    async def embed(self, *, model, texts):
        return [
            [1.0, 0.0],
            [0.0, 1.0],
        ]


class _AbandonedAnthropic:
    async def complete(self, request):
        return ModelResponse(
            text=json.dumps({
                "current_step": "unrelated redesign",
                "plan_status": "abandoned",
                "rationale": "The original goal is no longer being pursued.",
            }),
            resolved_model=request.model,
            provider="test",
        )

    async def structured_complete(self, request, schema):
        raise AssertionError("drift detector should use complete")


@pytest.mark.asyncio
async def test_goal_abandonment_inside_allowed_files_opens_semantic_adjudication(tmp_path):
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    state.register_run(
        run_id="workflow-semantic-drift",
        session_id="session-semantic-drift",
        rollout_path="/captured/semantic-drift.jsonl",
        task="Repair authentication token refresh.",
        scope=ScopeContract(allowed_paths=("src/auth/",)),
        target_kind="codex",
    )
    state.write_event(
        run_id="workflow-semantic-drift",
        source="rollout",
        kind="agent.message",
        payload={"text": "Plan: repair authentication token refresh."},
    )
    state.write_event(
        run_id="workflow-semantic-drift",
        source="rollout",
        kind="file_change",
        payload={"path": "src/auth/token.py"},
    )
    state.write_event(
        run_id="workflow-semantic-drift",
        source="rollout",
        kind="agent.message",
        payload={
            "text": (
                "I abandoned the authentication task and am redesigning "
                "an unrelated subsystem."
            )
        },
    )
    detector = DriftDetector(
        cfg,
        state,
        anthropic=_AbandonedAnthropic(),
        oai=_LowSimilarityOpenAI(),
    )

    await detector._tick()

    verdicts = state._conn.execute(
        """SELECT layer, output_json
             FROM verdicts
            WHERE run_id='workflow-semantic-drift'
            ORDER BY verdict_id"""
    ).fetchall()
    assert [row["layer"] for row in verdicts] == ["L1", "L2", "L3"]
    assert json.loads(verdicts[0]["output_json"])["scope_violations"] == 0
    decision = state.decisions.get_nowait()
    assert decision.kind == "adjudicate_drift"
    assert decision.run_id == "workflow-semantic-drift"
    assert decision.payload["evidence"]["scope_violations"] == 0
    assert set(decision.payload["evidence"]["signals"]) >= {
        "goal_similarity",
        "plan_progress",
    }


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"type": "session.created", "payload": {"info": {"id": "ses_1"}}}, "run.started"),
        (
            {
                "type": "message.part.updated",
                "payload": {
                    "part": {
                        "type": "tool",
                        "state": {"status": "running"},
                    }
                },
            },
            "tool.started",
        ),
        (
            {
                "type": "message.part.updated",
                "payload": {
                    "part": {
                        "type": "tool",
                        "state": {"status": "completed"},
                    }
                },
            },
            "tool.completed",
        ),
        (
            {
                "type": "message.updated",
                "payload": {
                    "info": {
                        "role": "assistant",
                        "time": {"completed": 1783810000000},
                    }
                },
            },
            "turn.completed",
        ),
        ({"type": "session.idle", "payload": {"sessionID": "ses_1"}}, "turn.completed"),
        ({"type": "session.error", "payload": {"sessionID": "ses_1"}}, "run.failed"),
    ],
)
def test_opencode_events_normalize_to_shared_taxonomy(event, expected):
    assert RolloutWatcher._extract_kind(event) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signal", "events"),
    [
        (
            "loop_repetition",
            [
                ("agent.message", {"text": "Retrying the same unrelated step."}),
                ("agent.message", {"text": "Retrying the same unrelated step."}),
                ("agent.message", {"text": "Retrying the same unrelated step."}),
            ],
        ),
        (
            "tool_error",
            [
                ("turn.started", {"turn_id": "turn-1"}),
                ("tool.completed", {"status": "failed", "error": "exit 1"}),
                ("tool.completed", {"status": "failed", "error": "exit 1"}),
            ],
        ),
    ],
)
async def test_deterministic_semantic_signals_open_adjudication(
    tmp_path,
    signal,
    events,
):
    cfg = _config(tmp_path)
    state = State(cfg.supervisor.state_db)
    state.register_run(
        run_id=f"workflow-{signal}",
        session_id=f"session-{signal}",
        rollout_path=f"/captured/{signal}.jsonl",
        task="Complete the original task.",
        scope=ScopeContract(allowed_paths=("src/",)),
        target_kind="codex",
    )
    for kind, payload in events:
        state.write_event(
            run_id=f"workflow-{signal}",
            source="rollout",
            kind=kind,
            payload=payload,
        )
    detector = DriftDetector(cfg, state, anthropic=None, oai=None)

    await detector._tick()

    decision = state.decisions.get_nowait()
    assert signal in decision.payload["evidence"]["signals"]
    assert decision.payload["evidence"]["scope_violations"] == 0


@pytest.mark.asyncio
async def test_time_without_progress_opens_adjudication(tmp_path):
    cfg = _config(tmp_path)
    cfg.supervisor.stall_threshold_s = 10
    state = State(cfg.supervisor.state_db)
    state.register_run(
        run_id="workflow-stalled",
        session_id="session-stalled",
        rollout_path="/captured/stalled.jsonl",
        task="Complete the original task.",
        scope=ScopeContract(allowed_paths=("src/",)),
        target_kind="codex",
    )
    for index in range(3):
        state.write_event(
            run_id="workflow-stalled",
            source="rollout",
            kind="agent.message",
            payload={"text": f"Progress update {index}."},
            ts=int(time.time()) - 60,
        )
    detector = DriftDetector(cfg, state, anthropic=None, oai=None)

    await detector._tick()

    decision = state.decisions.get_nowait()
    assert "time_without_progress" in decision.payload["evidence"]["signals"]
    assert decision.payload["evidence"]["seconds_without_progress"] >= 60
