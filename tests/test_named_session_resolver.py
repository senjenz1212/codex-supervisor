from __future__ import annotations

import json

from supervisor.named_session import NamedSessionResolver
from supervisor.state import State


def _state_with_run(tmp_path, *, run_id: str, session_id: str, text: str, status: str = "running") -> State:
    rollout = tmp_path / f"rollout-2026-05-19T12-00-00-{session_id}.jsonl"
    rollout.write_text(text)
    state = State(str(tmp_path / "state.db"))
    state.upsert_run(
        run_id=run_id,
        session_id=session_id,
        rollout_path=str(rollout),
        task=None,
        scope_hints=None,
    )
    if status != "running":
        state.end_run(run_id, status)
    return state


def test_named_session_resolver_resolves_vela_chat_bot_from_rollout_content(tmp_path):
    """The resolver treats 'Vela chat bot' as a Codex session name, not Slack."""
    state = _state_with_run(
        tmp_path,
        run_id="run-parent",
        session_id="session-parent",
        text=json.dumps({
            "type": "event_msg",
            "payload": {
                "message": "I wanted to systematically develop a slack bot experience for Vela."
            },
        }),
    )

    result = NamedSessionResolver(state).resolve("Vela chat bot")

    assert result["status"] == "resolved"
    assert result["run"]["run_id"] == "run-parent"
    assert result["run"]["session_id"] == "session-parent"
    assert result["run"]["target"] == "codex"
    assert result["source"] in {"rollout_content", "event_content"}


def test_named_session_resolver_prefers_explicit_alias(tmp_path):
    state = _state_with_run(
        tmp_path,
        run_id="run-parent",
        session_id="session-parent",
        text="unrelated",
    )
    resolver = NamedSessionResolver(
        state,
        aliases={"vela chat bot": "session-parent"},
    )

    result = resolver.resolve("Vela chat bot")

    assert result["status"] == "resolved"
    assert result["source"] == "alias"
    assert result["run"]["run_id"] == "run-parent"


def test_named_session_resolver_returns_ambiguous_candidates_without_guessing(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for idx in (1, 2):
        rollout = tmp_path / f"rollout-2026-05-19T12-00-0{idx}-session-{idx}.jsonl"
        rollout.write_text("Vela chat bot")
        state.upsert_run(
            run_id=f"run-{idx}",
            session_id=f"session-{idx}",
            rollout_path=str(rollout),
            task="Vela chat bot",
            scope_hints=None,
        )

    result = NamedSessionResolver(state).resolve("Vela chat bot")

    assert result["status"] == "ambiguous"
    assert [c["run_id"] for c in result["candidates"]] == ["run-2", "run-1"]


def test_named_session_resolver_returns_recent_candidates_on_not_found(tmp_path):
    state = _state_with_run(
        tmp_path,
        run_id="run-other",
        session_id="session-other",
        text="different task",
    )

    result = NamedSessionResolver(state).resolve("does not exist")

    assert result["status"] == "not_found"
    assert result["candidates"][0]["run_id"] == "run-other"
