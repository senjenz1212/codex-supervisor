from __future__ import annotations

import json
import asyncio

import pytest

from supervisor.rollout_watcher import RolloutWatcher
from supervisor.state import State
from supervisor.target.types import ScopeContract


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, **kwargs):
        self.messages.append(text)
        return {"ok": True}


@pytest.mark.asyncio
async def test_watched_run_streams_task_complete_from_rollout_ingestion(tmp_path):
    from supervisor.telegram_progress import TelegramProgressStreamer

    sessions_root = tmp_path / "sessions"
    registry_dir = tmp_path / "runs"
    rollout_dir = sessions_root / "2026" / "05" / "19"
    rollout_dir.mkdir(parents=True)
    registry_dir.mkdir()

    session_id = "11111111-1111-1111-1111-111111111111"
    run_id = f"run_{session_id}"
    rollout = rollout_dir / f"rollout-2026-05-19T10-00-00-{session_id}.jsonl"
    rollout.write_text(json.dumps({
        "timestamp": "2026-05-19T22:22:24.048Z",
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "turn-1",
            "last_agent_message": (
                "PR #55 is green now. Head SHA: "
                "61a32a3bffea638735406c1f72a3ff84f2d202cd. "
                "Bearer secret-token"
            ),
        },
    }) + "\n")

    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id=run_id,
        session_id=session_id,
        rollout_path=str(rollout),
        task="Monitor Vela 18c.4",
        scope=ScopeContract(),
        target_kind="codex",
    )
    state.create_run_watch(chat_id="42", run_id=run_id)
    notifier = _FakeNotifier()
    streamer = TelegramProgressStreamer(state=state, notifier=notifier)
    watcher = RolloutWatcher(
        sessions_root=str(sessions_root),
        registry_dir=str(registry_dir),
        state=state,
        on_event=streamer.handle_event,
    )

    await watcher._drain_file(rollout)

    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert "Run complete" in message
    assert "PR #55 is green now" in message
    assert "61a32a3bffea638735406c1f72a3ff84f2d202cd" in message
    assert "secret-token" not in message
    assert "[REDACTED" in message


@pytest.mark.asyncio
async def test_progress_streamer_ignores_noise_and_duplicate_event_ids(tmp_path):
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Monitor Vela",
        scope=ScopeContract(),
        target_kind="codex",
    )
    state.create_run_watch(chat_id="42", run_id="run-vela", last_event_id=10)
    notifier = _FakeNotifier()
    streamer = TelegramProgressStreamer(state=state, notifier=notifier)

    await streamer.handle_event("run-vela", {
        "id": 9,
        "kind": "event_msg",
        "payload": {"type": "task_complete", "last_agent_message": "old"},
    })
    await streamer.handle_event("run-vela", {
        "id": 11,
        "kind": "event_msg",
        "payload": {"type": "token_count", "info": {"total_tokens": 123}},
    })
    await streamer.handle_event("run-vela", {
        "id": 12,
        "kind": "response_item",
        "payload": {"type": "reasoning", "encrypted_content": "opaque"},
    })
    assert notifier.messages == []

    high_signal = {
        "id": 13,
        "kind": "event_msg",
        "payload": {"type": "task_started", "turn_id": "turn-2"},
    }
    await streamer.handle_event("run-vela", high_signal)
    await streamer.handle_event("run-vela", high_signal)

    assert len(notifier.messages) == 1
    assert "Run started" in notifier.messages[0]
    watch = state.active_run_watches("run-vela")[0]
    assert watch["last_event_id"] == 13


@pytest.mark.asyncio
async def test_task_complete_progress_enqueues_grounded_review_once(tmp_path):
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Monitor Vela",
        scope=ScopeContract(),
        target_kind="codex",
    )
    state.create_run_watch(chat_id="42", run_id="run-vela", last_event_id=20)
    notifier = _FakeNotifier()
    streamer = TelegramProgressStreamer(state=state, notifier=notifier)

    complete = {
        "id": 21,
        "kind": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "turn-3",
            "last_agent_message": "Implemented update.",
        },
    }
    await streamer.handle_event("run-vela", complete)
    await streamer.handle_event("run-vela", complete)
    decision = await asyncio.wait_for(state.next_decision(), timeout=0.2)

    assert decision.kind == "review_updates"
    assert decision.run_id == "run-vela"
    assert decision.payload["chat_id"] == "42"
    assert decision.payload["event_id"] == 21
    assert state.decisions.empty()
