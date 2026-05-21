from __future__ import annotations

import json

import pytest

from supervisor.state import State
from supervisor.supervisor_tools import SupervisorToolAPI
from supervisor.target.types import ScopeContract


class _FakeTargetAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_action(self, action) -> dict:
        self.calls.append({
            "kind": action.kind,
            "session_id": action.session_id,
            "payload": dict(action.payload),
        })
        return {"delivered": True, "reason": "fake"}


def _make_state(tmp_path) -> State:
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Build the Vela chat bot",
        scope=ScopeContract(
            allowed_paths=("src/vela",),
            related_paths=("docs/vela",),
            protected_paths=("src/payments",),
        ),
        target_kind="codex",
        config_snapshot={"modes": {"drift_l1_l3": "advise"}},
    )
    state.write_event(
        run_id="run-vela",
        source="rollout",
        kind="item.file_write",
        payload={"path": "src/payments/refunds.py", "token": "Bearer secret-token"},
    )
    state.write_hook_request(
        run_id="run-vela",
        hook_event="pre_tool_use",
        tool_name="bash",
        payload={"Authorization": "Bearer secret-token"},
        response={"action": "allow"},
        latency_ms=10,
        mode="shadow",
    )
    state.record_action(
        run_id="run-vela",
        action_type="recommend_block",
        requested_by="test",
        payload={"reason": "Bearer secret-token"},
        status="recorded",
    )
    return state


def test_supervisor_tool_api_returns_redacted_bounded_run_context(tmp_path):
    state = _make_state(tmp_path)
    api = SupervisorToolAPI(state)

    resolved = api.resolve_session("Vela chat bot")
    events = api.read_recent_events(run_id="run-vela", n=5)
    hooks = api.read_hooks(run_id="run-vela", n=5)
    actions = api.read_actions(run_id="run-vela", n=5)

    blob = json.dumps({
        "resolved": resolved,
        "events": events,
        "hooks": hooks,
        "actions": actions,
    })
    assert "secret-token" not in blob
    assert "[REDACTED" in blob
    assert resolved["status"] == "resolved"
    assert events["events"][0]["id"] > 0
    assert hooks["hooks"][0]["hook_event"] == "pre_tool_use"
    assert actions["actions"][0]["action_type"] == "recommend_block"


def test_supervisor_tool_api_evaluates_scope_and_proposes_mode_safe_actions(tmp_path):
    state = _make_state(tmp_path)
    api = SupervisorToolAPI(state)

    findings = api.evaluate_scope(run_id="run-vela", n=20)
    actions = api.propose_action(run_id="run-vela", findings=findings["findings"])

    assert findings["findings"][0]["classification"] == "protected"
    assert actions["proposed_actions"][0]["kind"] == "recommend_scope_violation"
    assert actions["proposed_actions"][0]["status"] == "would_recommend"


def test_supervisor_tool_api_read_tools_do_not_expose_sql_or_raw_target_shapes(tmp_path):
    state = _make_state(tmp_path)
    api = SupervisorToolAPI(state)

    runs = api.list_runs(limit=5)

    assert "runs" in runs
    assert set(runs["runs"][0]) >= {"run_id", "session_id", "status", "task", "target"}
    assert "payload_json" not in json.dumps(runs)


def test_read_actions_derives_visibility_for_legacy_append_status_rows(tmp_path):
    """Older action rows predate CS14 and only have adapter_result.

    The supervisor chat should still see the truthful history-only visibility
    state when it calls read_actions.
    """
    state = _make_state(tmp_path)
    state.record_action(
        run_id="run-vela",
        action_type="append_status_item",
        requested_by="telegram_progress",
        payload={
            "adapter_result": {
                "delivered": True,
                "reason": "app_server_injected",
                "method": "thread/inject_items",
                "desktop_gui_repaint": "unverified",
            },
        },
        status="delivered",
    )

    api = SupervisorToolAPI(state)
    actions = api.read_actions(run_id="run-vela", n=5)["actions"]

    append_action = actions[-1]
    assert append_action["action_type"] == "append_status_item"
    visibility = append_action["payload"]["visibility"]
    assert visibility["effective_state"] == "history_only"
    assert visibility["gui_live"] is False


def test_supervisor_tool_api_read_run_timeline_returns_status_card(tmp_path):
    state = _make_state(tmp_path)
    state.write_event(
        run_id="run-vela",
        source="rollout",
        kind="response_item",
        payload={
            "timestamp": "2026-05-19T20:41:16.995Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": json.dumps({
                    "headRefOid": "b07b510bd97787f34f1cd8020845c7c7d2bf5e4e",
                    "number": 54,
                    "state": "OPEN",
                    "statusCheckRollup": [
                        {"name": "backend-smoke", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"name": "frontend-smoke", "status": "COMPLETED", "conclusion": "SUCCESS"},
                    ],
                }),
            },
        },
    )
    api = SupervisorToolAPI(state)

    result = api.read_run_timeline(run_id="run-vela", n=20)

    assert result["status"] == "ok"
    assert result["timeline"]["outcome_card"]["headline"] == "PR #54 is green now."
    assert result["timeline"]["facts"]["approval_token_candidate"] == (
        "[approved PR#54 b07b510bd97787f34f1cd8020845c7c7d2bf5e4e]"
    )


def test_supervisor_tool_api_reads_redacted_supervisor_turn_history(tmp_path):
    state = _make_state(tmp_path)
    turn_id = state.record_supervisor_turn(
        chat_id="42",
        message_text="monitor Vela with Bearer secret-token",
        request={"telegram_message": {"chat": {"id": 42}}},
        model="fake-claude",
    )
    state.complete_supervisor_turn(
        turn_id,
        response_text="Vela is green. API_KEY=secret-token",
        status="completed",
        model="fake-claude",
        tool_outputs=[],
        proposed_actions=[],
    )
    api = SupervisorToolAPI(state)

    result = api.read_supervisor_turns(chat_id="42", n=5)

    blob = json.dumps(result)
    assert result["status"] == "ok"
    assert result["turns"][0]["id"] == turn_id
    assert "secret-token" not in blob
    assert "[REDACTED" in blob


def test_supervisor_tool_api_creates_and_lists_run_watch_at_current_offset(tmp_path):
    state = _make_state(tmp_path)
    existing_event_id = state.latest_event_id("run-vela")
    api = SupervisorToolAPI(state)

    created = api.watch_run(chat_id="42", run_id="run-vela")
    listed = api.list_run_watches(chat_id="42")

    assert created["status"] == "ok"
    assert created["watch"]["run_id"] == "run-vela"
    assert created["watch"]["last_event_id"] == existing_event_id
    assert listed["status"] == "ok"
    assert listed["watches"] == [created["watch"]]


def test_supervisor_tool_api_request_steering_creates_telegram_approval(tmp_path):
    state = _make_state(tmp_path)
    workspace = tmp_path / "vela-worktree"
    workspace.mkdir()
    state.write_event(
        run_id="run-vela",
        source="rollout",
        kind="turn_context",
        payload={"cwd": str(workspace)},
    )
    sent: list[dict] = []

    def telegram_sender(**kwargs):
        sent.append(kwargs)

    api = SupervisorToolAPI(
        state,
        target_adapter=object(),
        telegram_sender=telegram_sender,
        steering_mode="advise",
    )

    result = api.request_steering(
        run_id="run-vela",
        message="Please re-anchor on the approved Vela slice.",
    )

    assert result["status"] == "ok"
    assert result["action_results"][0]["kind"] == "inject_steering"
    assert result["action_results"][0]["status"] == "pending_approval"
    assert len(sent) == 1
    assert sent[0]["options"] == ["Approve", "Reject"]

    action = state._conn.execute(
        "SELECT * FROM actions WHERE action_type='inject_steering'"
    ).fetchone()
    ask = state._conn.execute("SELECT * FROM telegram_asks").fetchone()
    assert action["status"] == "pending_approval"
    payload = json.loads(action["payload_json"])
    assert payload["cwd"] == str(workspace.resolve())
    assert ask["status"] == "pending"
    assert ask["nonce"]


@pytest.mark.asyncio
async def test_supervisor_tool_api_request_steering_enforce_auto_delivers(tmp_path):
    state = _make_state(tmp_path)
    adapter = _FakeTargetAdapter()
    sent: list[dict] = []

    def telegram_sender(**kwargs):
        sent.append(kwargs)

    api = SupervisorToolAPI(
        state,
        target_adapter=adapter,
        telegram_sender=telegram_sender,
        steering_mode="enforce",
    )

    result = await api.request_steering_async(
        run_id="run-vela",
        message="Proceed with the next low-risk issue-prep step.",
    )

    assert result["status"] == "ok"
    assert result["action_results"] == [{
        "action_id": 2,
        "status": "delivered",
        "kind": "inject_steering",
    }]
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["kind"] == "inject_steering"
    assert adapter.calls[0]["session_id"] == "session-vela"
    assert sent == []
    assert state._conn.execute("SELECT COUNT(*) AS n FROM telegram_asks").fetchone()["n"] == 0
    row = state._conn.execute(
        "SELECT status, payload_json FROM actions WHERE action_type='inject_steering'"
    ).fetchone()
    assert row["status"] == "delivered"
    payload = json.loads(row["payload_json"])
    assert payload["auto_executed"] is True
    assert payload["requires_approval"] is False
