"""Model-first hook critique via Claude Agent SDK.

This is intentionally a tiny adapter around the optional SDK. The hook server
depends only on the `.critique(...)` method so tests can pass fakes, and so the
daemon can degrade to deterministic guardrails when the SDK is unavailable.
"""
from __future__ import annotations
import json
import logging
from typing import Any

from .config import Config

log = logging.getLogger(__name__)


HOOK_CRITIQUE_PROMPT = """You are the supervisor critic for a local coding agent.

You receive one hook event before or around an agent action. Decide whether the
supervisor should allow or deny the action.

Be conservative about destructive, off-task, or credential-touching actions.
Prefer "allow" for ordinary reads, searches, tests, formatting, and safe edits.

Return JSON only:
{
  "action": "allow" | "deny",
  "reason": "short snake_case or sentence",
  "confidence": 0.0
}
"""


class ClaudeAgentSDKHookCritic:
    """Use Claude Agent SDK for every hook decision."""

    def __init__(self, cfg: Config, *, timeout_s: float = 20.0):
        self.cfg = cfg
        self.timeout_s = timeout_s

    async def critique(self, hook_event, *, raw_payload: dict[str, Any]) -> dict[str, Any]:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        options = ClaudeAgentOptions(
            system_prompt=HOOK_CRITIQUE_PROMPT,
            model=self.cfg.models.realtime_critique_model,
            max_turns=2,
            allowed_tools=[],
        )
        user_message = json.dumps({
            "target": hook_event.source_target,
            "hook_kind": hook_event.hook_kind,
            "session_id": hook_event.session_id,
            "tool_name": hook_event.tool_name,
            "tool_args": hook_event.tool_args,
            "raw_payload": raw_payload,
        }, indent=2, default=str)[:12000]

        outputs: list[str] = []
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            async for msg in client.receive_response():
                if hasattr(msg, "content"):
                    for block in getattr(msg, "content", []) or []:
                        text = getattr(block, "text", None)
                        if text:
                            outputs.append(text)

        return _parse_verdict("\n".join(outputs))


def _parse_verdict(text: str) -> dict[str, Any]:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
    except Exception as e:
        log.warning("hook critic verdict parse failed: %s; text=%r", e, text[:200])
        return {
            "action": "allow",
            "reason": "model_parse_failed",
            "confidence": 0.0,
        }

    action = str(data.get("action", "allow")).lower()
    if action not in {"allow", "deny"}:
        action = "allow"
    reason = str(data.get("reason") or "no_reason")[:500]
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "action": action,
        "reason": reason,
        "confidence": max(0.0, min(1.0, confidence)),
    }
