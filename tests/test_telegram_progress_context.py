from __future__ import annotations

import json

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
                "desktop_process_names": ["Codex"],
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-opus-4-7",
            "drift_l3_model": "claude-opus-4-7",
            "drift_l4_model": "claude-opus-4-7",
            "post_run_eval_model": "claude-opus-4-7",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "123456:test-token", "chat_id": "42"},
    })


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, text: str, **kwargs):
        self.messages.append(text)
        return {"ok": True}


@pytest.mark.asyncio
async def test_followup_chat_context_includes_latest_watched_progress_notification(tmp_path):
    """CS11 RED: Telegram sent "Run complete"; the next conversational turn
    must see that outbound notification before answering "what's your suggestion?"
    """
    from supervisor.supervisor_tools import SupervisorToolAPI
    from supervisor.telegram_progress import TelegramProgressStreamer
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    class _SuggestionRuntime:
        model = "fake-claude"

        def __init__(self) -> None:
            self.contexts: list[dict] = []

        async def answer(
            self,
            *,
            message: str,
            tool_api: SupervisorToolAPI,
            conversation_context: dict,
        ) -> dict:
            self.contexts.append(conversation_context)
            assert message == "What's ure suggestion?"
            assert conversation_context["active_run_id"] == "run-vela"
            prior = conversation_context["recent_turns"]
            prior_json = json.dumps(prior)
            assert "What's ure suggestion?" not in prior_json
            assert "Run complete" in prior_json
            assert "PR #57 is now merged" in prior_json
            assert "18c.5 is now shipped" in prior_json
            return {
                "text": "18c.5 is shipped. My suggestion: archive the watch and move to the next planned slice only after you confirm the next gate.",
                "tool_outputs": [],
                "proposed_actions": [],
            }

    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Ship Vela 18c.5",
        scope=ScopeContract(),
        target_kind="codex",
    )
    state.create_run_watch(chat_id="42", run_id="run-vela", last_event_id=30)
    notifier = _FakeNotifier()
    streamer = TelegramProgressStreamer(state=state, notifier=notifier)

    await streamer.handle_event("run-vela", {
        "id": 31,
        "kind": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "turn-ship",
            "last_agent_message": (
                "PR #57 is now merged. Merge commit: "
                "47c85cabb44135bd58df87cb2421fc34cb3f0b51. "
                "18c.5 is now shipped."
            ),
        },
    })

    assert len(notifier.messages) == 1
    assert "Run complete" in notifier.messages[0]

    runtime = _SuggestionRuntime()
    supervisor = TelegramChatSupervisor(_cfg(tmp_path), state, runtime=runtime)
    response = await supervisor.handle_message(
        "What's ure suggestion?",
        telegram_message={"chat": {"id": 42}},
    )

    assert response.startswith("18c.5 is shipped")
    assert len(runtime.contexts) == 1


@pytest.mark.asyncio
async def test_progress_notification_context_is_redacted_and_records_active_run(tmp_path):
    from supervisor.telegram_progress import TelegramProgressStreamer

    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Ship Vela 18c.5",
        scope=ScopeContract(),
        target_kind="codex",
    )
    state.create_run_watch(chat_id="42", run_id="run-vela", last_event_id=40)
    notifier = _FakeNotifier()
    streamer = TelegramProgressStreamer(state=state, notifier=notifier)

    await streamer.handle_event("run-vela", {
        "id": 41,
        "kind": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": "turn-secret",
            "last_agent_message": (
                "Run shipped with ANTHROPIC_API_KEY=sk-ant-supersecret12345"
            ),
        },
    })

    turns = state.recent_supervisor_turns(chat_id="42", n=5)
    turns_json = json.dumps(turns)
    assert "sk-ant-supersecret12345" not in turns_json
    assert "[REDACTED" in turns_json
    assert "progress_notification" in turns[0]["request"].get("origin", "")
    conversation = state.get_supervisor_conversation("42")
    assert conversation is not None
    assert conversation["active_run_id"] == "run-vela"
