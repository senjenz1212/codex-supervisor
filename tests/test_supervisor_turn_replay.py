from __future__ import annotations

import httpx
import subprocess

from supervisor.supervisor_turn_replay import replay_supervisor_turn


class _Tripwire:
    def __init__(self, name: str):
        self.name = name

    def __call__(self, *args, **kwargs):
        raise AssertionError(f"replay must not call {self.name}")


def test_supervisor_turn_replay_uses_frozen_tool_and_model_outputs(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _Tripwire("httpx.AsyncClient"))
    monkeypatch.setattr(httpx, "Client", _Tripwire("httpx.Client"))
    monkeypatch.setattr(subprocess, "run", _Tripwire("subprocess.run"))

    result = replay_supervisor_turn({
        "message": "what is happening in Vela chat bot?",
        "tool_outputs": [
            {
                "name": "resolve_session",
                "output": {"status": "resolved", "run": {"run_id": "run-vela"}},
            }
        ],
        "model_output": {
            "text": "Vela chat bot is active.",
            "proposed_actions": [],
        },
    })

    assert result["response_text"] == "Vela chat bot is active."
    assert result["tool_calls"][0]["name"] == "resolve_session"
    assert result["proposed_actions"] == []
