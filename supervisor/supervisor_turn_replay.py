"""Replay a Telegram supervisor turn from frozen inputs only."""
from __future__ import annotations

from typing import Any

from .redaction import redact


def replay_supervisor_turn(fixture: dict[str, Any]) -> dict[str, Any]:
    """Return the replayed turn result without live Telegram, target, or model calls."""
    model_output = fixture.get("model_output") or {}
    return redact({
        "message": fixture.get("message", ""),
        "response_text": model_output.get("text", ""),
        "tool_calls": fixture.get("tool_outputs") or [],
        "proposed_actions": model_output.get("proposed_actions") or [],
    })
