from __future__ import annotations

import json

import pytest

from supervisor.state import State
from supervisor.target.types import ScopeContract


def _register_run(state: State, tmp_path, run_id: str = "run-vela") -> None:
    state.register_run(
        run_id=run_id,
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Ship Vela",
        scope=ScopeContract(),
        target_kind="codex",
    )


def _write_task_complete(state: State, run_id: str, message: str) -> int:
    return state.write_event(
        run_id=run_id,
        source="rollout",
        kind="event_msg",
        payload={
            "timestamp": "2026-05-20T04:23:30.899Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-ship",
                "last_agent_message": message,
            },
        },
    )


def test_progress_context_backfill_records_existing_notification_once(tmp_path):
    """CS12 RED: repair a progress notification that was sent before CS11
    existed, without sending Telegram again or creating duplicate memory rows.
    """
    from supervisor.progress_backfill import backfill_progress_context

    state = State(str(tmp_path / "state.db"))
    _register_run(state, tmp_path)
    event_id = _write_task_complete(
        state,
        "run-vela",
        "PR #57 is now merged. 18c.5 is now shipped.",
    )

    result = backfill_progress_context(
        state=state,
        chat_id="42",
        run_id="run-vela",
        event_id=event_id,
    )
    duplicate = backfill_progress_context(
        state=state,
        chat_id="42",
        run_id="run-vela",
        event_id=event_id,
    )

    assert result["status"] == "recorded"
    assert result["sent_telegram"] is False
    assert duplicate["status"] == "already_present"
    turns = state.recent_supervisor_turns(chat_id="42", n=5)
    assert len(turns) == 1
    assert turns[0]["request"]["origin"] == "progress_notification_backfill"
    assert turns[0]["request"]["event_id"] == event_id
    assert "Run complete" in turns[0]["response_text"]
    assert "18c.5 is now shipped" in turns[0]["response_text"]
    conversation = state.get_supervisor_conversation("42")
    assert conversation is not None
    assert conversation["active_run_id"] == "run-vela"


def test_progress_context_backfill_fails_closed_without_chat_id(tmp_path):
    from supervisor.progress_backfill import backfill_progress_context

    state = State(str(tmp_path / "state.db"))
    _register_run(state, tmp_path)
    event_id = _write_task_complete(state, "run-vela", "18c.5 is now shipped.")

    with pytest.raises(ValueError, match="chat_id"):
        backfill_progress_context(
            state=state,
            chat_id="",
            run_id="run-vela",
            event_id=event_id,
        )

    rows = state._conn.execute("SELECT * FROM supervisor_turns").fetchall()
    assert rows == []


def test_progress_context_backfill_redacts_event_text(tmp_path):
    from supervisor.progress_backfill import backfill_progress_context

    state = State(str(tmp_path / "state.db"))
    _register_run(state, tmp_path)
    event_id = _write_task_complete(
        state,
        "run-vela",
        "Done with ANTHROPIC_API_KEY=sk-ant-supersecret12345.",
    )

    backfill_progress_context(
        state=state,
        chat_id="42",
        run_id="run-vela",
        event_id=event_id,
    )

    turns_json = json.dumps(state.recent_supervisor_turns(chat_id="42", n=5))
    assert "sk-ant-supersecret12345" not in turns_json
    assert "[REDACTED" in turns_json
