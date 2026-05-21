from __future__ import annotations

import json

import pytest

from supervisor.config import Config
from supervisor.state import State


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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeTelegramClient:
    def __init__(self, updates):
        self.updates = updates
        self.posts: list[dict] = []

    async def get(self, url, params=None):
        return _FakeResponse({"ok": True, "result": self.updates})

    async def post(self, url, data=None, **kwargs):
        self.posts.append({"url": url, "data": data or {}, "kwargs": kwargs})
        return _FakeResponse({"ok": True})


class _FakeChatHandler:
    def __init__(self):
        self.calls: list[dict] = []

    async def handle_message(self, text: str, *, telegram_message: dict) -> str:
        self.calls.append({"text": text, "telegram_message": telegram_message})
        return "Vela chat bot is running under run-vela."


@pytest.mark.asyncio
async def test_non_slash_telegram_text_routes_to_supervisor_chat_handler(tmp_path):
    from supervisor.telegram import TelegramPoller

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    handler = _FakeChatHandler()
    poller = TelegramPoller(cfg, state, chat_handler=handler)
    client = _FakeTelegramClient([{
        "update_id": 100,
        "message": {
            "message_id": 8,
            "chat": {"id": 42},
            "text": "what is happening in Vela chat bot?",
        },
    }])

    await poller._poll(client)

    assert handler.calls[0]["text"] == "what is happening in Vela chat bot?"
    assert client.posts[0]["data"]["chat_id"] == "42"
    assert "Vela chat bot" in client.posts[0]["data"]["text"]


@pytest.mark.asyncio
async def test_telegram_chat_replies_are_plain_text_to_avoid_markdown_parse_drops(tmp_path):
    from supervisor.telegram import TelegramPoller

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    handler = _FakeChatHandler()
    poller = TelegramPoller(cfg, state, chat_handler=handler)
    client = _FakeTelegramClient([{
        "update_id": 101,
        "message": {
            "message_id": 9,
            "chat": {"id": 42},
            "text": "Can u help me monitor the current process?",
        },
    }])

    await poller._poll(client)

    payload = client.posts[0]["data"]
    assert payload["text"] == "Vela chat bot is running under run-vela."
    assert "parse_mode" not in payload


@pytest.mark.asyncio
async def test_telegram_chat_supervisor_persists_turn_and_model_response(tmp_path):
    from supervisor.supervisor_tools import SupervisorToolAPI
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    class _FakeRuntime:
        model = "fake-claude"

        async def answer(
            self,
            *,
            message: str,
            tool_api: SupervisorToolAPI,
            conversation_context: dict,
        ) -> dict:
            resolved = tool_api.resolve_session("Vela chat bot")
            return {
                "text": f"Resolved status: {resolved['status']}",
                "tool_outputs": [{"name": "resolve_session", "output": resolved}],
                "proposed_actions": [],
            }

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    state.upsert_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Build Vela chat bot",
        scope_hints=None,
    )
    supervisor = TelegramChatSupervisor(cfg, state, runtime=_FakeRuntime())

    response = await supervisor.handle_message(
        "what is happening in Vela chat bot?",
        telegram_message={"chat": {"id": 42}},
    )

    assert response == "Resolved status: resolved"
    row = state.get_supervisor_turn(1)
    assert row["status"] == "completed"
    assert row["model"] == "fake-claude"
    assert "Vela chat bot" in row["message_text"]
    assert "resolve_session" in row["tool_outputs_json"]


@pytest.mark.asyncio
async def test_telegram_steer_request_creates_approval_action_not_direct_injection(tmp_path):
    from supervisor.supervisor_tools import SupervisorToolAPI
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    sent: list[dict] = []

    def telegram_sender(**kwargs):
        sent.append(kwargs)

    class _SteeringRuntime:
        model = "fake-claude"

        async def answer(
            self,
            *,
            message: str,
            tool_api: SupervisorToolAPI,
            conversation_context: dict,
        ) -> dict:
            result = tool_api.request_steering(
                session_name="Vela chat bot",
                message="Please re-anchor on Vela 18c.5.",
            )
            return {
                "text": "I queued that steer for approval.",
                "tool_outputs": [{"name": "request_steering", "output": result}],
                "proposed_actions": result.get("action_results", []),
            }

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    state.upsert_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Build Vela chat bot",
        scope_hints=None,
    )
    supervisor = TelegramChatSupervisor(
        cfg,
        state,
        runtime=_SteeringRuntime(),
        target_adapter=object(),
        telegram_sender=telegram_sender,
    )

    response = await supervisor.handle_message(
        "steer Vela chat bot: please re-anchor",
        telegram_message={"chat": {"id": 42}},
    )

    assert response == "I queued that steer for approval."
    assert len(sent) == 1
    assert sent[0]["options"] == ["Approve", "Reject"]
    action = state._conn.execute(
        "SELECT action_type, status, payload_json FROM actions"
    ).fetchone()
    assert action["action_type"] == "inject_steering"
    assert action["status"] == "pending_approval"
    payload = json.loads(action["payload_json"])
    assert payload["requires_approval"] is True
    assert payload["message"] == "Please re-anchor on Vela 18c.5."
    ask = state._conn.execute("SELECT status, nonce FROM telegram_asks").fetchone()
    assert ask["status"] == "pending"
    assert ask["nonce"]


@pytest.mark.asyncio
async def test_telegram_steer_request_auto_delivers_in_enforce_mode(tmp_path):
    from supervisor.supervisor_tools import SupervisorToolAPI
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    sent: list[dict] = []

    def telegram_sender(**kwargs):
        sent.append(kwargs)

    class _FakeAdapter:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def execute_action(self, action) -> dict:
            self.calls.append({
                "kind": action.kind,
                "session_id": action.session_id,
                "payload": dict(action.payload),
            })
            return {"delivered": True, "reason": "fake"}

    class _AutoSteeringRuntime:
        model = "fake-claude"

        async def answer(
            self,
            *,
            message: str,
            tool_api: SupervisorToolAPI,
            conversation_context: dict,
        ) -> dict:
            result = await tool_api.request_steering_async(
                session_name="Vela chat bot",
                message="Proceed with the next low-risk issue-prep step.",
            )
            return {
                "text": "Delivered the low-risk steer.",
                "tool_outputs": [{"name": "request_steering", "output": result}],
                "proposed_actions": result.get("action_results", []),
            }

    cfg = _cfg(tmp_path)
    cfg.modes.steering_injection = "enforce"
    state = State(str(tmp_path / "state.db"))
    state.upsert_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Build Vela chat bot",
        scope_hints=None,
    )
    adapter = _FakeAdapter()
    supervisor = TelegramChatSupervisor(
        cfg,
        state,
        runtime=_AutoSteeringRuntime(),
        target_adapter=adapter,
        telegram_sender=telegram_sender,
    )

    response = await supervisor.handle_message(
        "please continue with the suggested move",
        telegram_message={"chat": {"id": 42}},
    )

    assert response == "Delivered the low-risk steer."
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["kind"] == "inject_steering"
    assert sent == []
    assert state._conn.execute("SELECT COUNT(*) AS n FROM telegram_asks").fetchone()["n"] == 0
    action = state._conn.execute(
        "SELECT action_type, status, payload_json FROM actions"
    ).fetchone()
    assert action["action_type"] == "inject_steering"
    assert action["status"] == "delivered"
    payload = json.loads(action["payload_json"])
    assert payload["auto_executed"] is True
    assert payload["requires_approval"] is False


@pytest.mark.asyncio
async def test_telegram_chat_supervisor_passes_durable_conversation_context(tmp_path):
    from supervisor.supervisor_tools import SupervisorToolAPI
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    class _MemoryRuntime:
        model = "fake-claude"

        def __init__(self):
            self.contexts: list[dict] = []

        async def answer(
            self,
            *,
            message: str,
            tool_api: SupervisorToolAPI,
            conversation_context: dict,
        ) -> dict:
            self.contexts.append(conversation_context)
            assert message == "what changed?"
            assert conversation_context["chat_id"] == "42"
            assert conversation_context["claude_session_id"] == "claude-session-old"
            prior = conversation_context["recent_turns"]
            assert len(prior) == 1
            assert prior[0]["message_text"] == "monitor Vela chat bot"
            assert prior[0]["response_text"] == "PR #54 is green now."
            assert "what changed?" not in json.dumps(prior)
            return {
                "text": "Still tracking Vela; PR #54 is merged.",
                "tool_outputs": [],
                "proposed_actions": [],
                "claude_session_id": "claude-session-new",
            }

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    previous_turn = state.record_supervisor_turn(
        chat_id="42",
        message_text="monitor Vela chat bot",
        request={"telegram_message": {"chat": {"id": 42}}},
        model="fake-claude",
    )
    state.complete_supervisor_turn(
        previous_turn,
        response_text="PR #54 is green now.",
        status="completed",
        model="fake-claude",
        tool_outputs=[],
        proposed_actions=[],
    )
    state.upsert_supervisor_conversation(
        chat_id="42",
        claude_session_id="claude-session-old",
        summary="Sam is monitoring Vela chat bot and PR #54.",
        active_run_id="run-vela",
    )
    runtime = _MemoryRuntime()
    supervisor = TelegramChatSupervisor(cfg, state, runtime=runtime)

    response = await supervisor.handle_message(
        "what changed?",
        telegram_message={"chat": {"id": 42}},
    )

    assert response == "Still tracking Vela; PR #54 is merged."
    conversation = state.get_supervisor_conversation("42")
    assert conversation["claude_session_id"] == "claude-session-new"
    assert conversation["turn_count"] == 2
    assert runtime.contexts[0]["summary"] == "Sam is monitoring Vela chat bot and PR #54."


@pytest.mark.asyncio
async def test_telegram_chat_supervisor_refreshes_rolling_summary_on_threshold(tmp_path):
    from supervisor.supervisor_tools import SupervisorToolAPI
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    class _SummaryRuntime:
        model = "fake-claude"

        def __init__(self):
            self.summary_calls: list[dict] = []

        async def answer(
            self,
            *,
            message: str,
            tool_api: SupervisorToolAPI,
            conversation_context: dict,
        ) -> dict:
            return {
                "text": "Still watching Vela.",
                "tool_outputs": [],
                "proposed_actions": [],
                "claude_session_id": "019e4f33-1a30-7337-9d9b-0f34c5da0001",
            }

        async def summarize_conversation(
            self,
            *,
            conversation_context: dict,
            recent_turns: list[dict],
        ) -> dict:
            self.summary_calls.append({
                "conversation_context": conversation_context,
                "recent_turns": recent_turns,
            })
            assert conversation_context["turn_count"] == 10
            assert len(recent_turns) == 10
            assert recent_turns[-1]["message_text"] == "what changed now?"
            return {
                "summary": (
                    "Sam is monitoring Vela chat bot. PR #54 is merged; "
                    "continue answering follow-up questions against that thread."
                ),
                "active_run_id": "run-vela",
            }

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    for i in range(9):
        turn_id = state.record_supervisor_turn(
            chat_id="42",
            message_text=f"prior question {i}",
            request={"telegram_message": {"chat": {"id": 42}}},
            model="fake-claude",
        )
        state.complete_supervisor_turn(
            turn_id,
            response_text=f"prior answer {i}",
            status="completed",
            model="fake-claude",
            tool_outputs=[],
            proposed_actions=[],
        )
    state.upsert_supervisor_conversation(
        chat_id="42",
        claude_session_id="019e4f33-1a30-7337-9d9b-0f34c5da0000",
        summary="Older summary.",
        active_run_id="run-old",
        increment_turn_count=True,
    )
    runtime = _SummaryRuntime()
    supervisor = TelegramChatSupervisor(cfg, state, runtime=runtime)

    response = await supervisor.handle_message(
        "what changed now?",
        telegram_message={"chat": {"id": 42}},
    )

    assert response == "Still watching Vela."
    conversation = state.get_supervisor_conversation("42")
    assert conversation["turn_count"] == 10
    assert conversation["summary"].startswith("Sam is monitoring Vela chat bot.")
    assert conversation["active_run_id"] == "run-vela"
    assert conversation["claude_session_id"] == "019e4f33-1a30-7337-9d9b-0f34c5da0001"
    assert len(runtime.summary_calls) == 1


@pytest.mark.asyncio
async def test_telegram_chat_supervisor_degrades_cleanly_on_runtime_error(tmp_path):
    from supervisor.telegram_supervisor import TelegramChatSupervisor

    class _FailingRuntime:
        model = "fake-claude"

        async def answer(self, **kwargs):
            raise RuntimeError("boom secret-token")

    cfg = _cfg(tmp_path)
    state = State(str(tmp_path / "state.db"))
    supervisor = TelegramChatSupervisor(cfg, state, runtime=_FailingRuntime())

    response = await supervisor.handle_message(
        "hello",
        telegram_message={"chat": {"id": 42}},
    )

    assert "could not complete" in response.lower()
    assert "secret-token" not in json.dumps(dict(state.get_supervisor_turn(1)))
