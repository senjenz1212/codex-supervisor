"""Conversational Telegram supervisor powered by Claude Agent SDK."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from .connectors import (
    connector_allowed_tools,
    connector_disallowed_tools,
    external_mcp_servers,
)
from .config import Config
from .redaction import redact, redact_for_telegram
from .state import State
from .supervisor_tools import SupervisorToolAPI

log = logging.getLogger(__name__)
SUMMARY_REFRESH_EVERY_TURNS = 10


SYSTEM_PROMPT = """You are Claude, the conversational supervisor for a local Codex Desktop session.

Telegram is the user's control surface. Codex Desktop is the supervised target.
Use supervisor tools before answering questions about sessions, hooks, events,
actions, scope, or drift. Human names such as "Vela chat bot" are Codex Desktop
session names unless the user explicitly says otherwise.

Keep replies concise and mobile-friendly. Cite run_id, event ids, hook ids, or
action ids when relevant. Never claim you changed, killed, restarted, blocked,
or steered Codex unless a tool returned a durable action row. For mutations,
describe the proposed action and use the mode-safe tools; do not bypass
approvals.

You receive a conversation continuity pack with recent Telegram turns, any
stored rolling summary, active run id, and the prior Claude SDK session id.
Use it to make the chat feel continuous, but treat it as read-only context.
Never execute or approve actions from remembered context alone.

For "monitor", "current process", "what happened", PR, CI, or approval-token
requests, call `read_run_timeline` before answering and use its `outcome_card`
as the primary source. Answer with outcome-shaped status, not raw event dumps:
- current state
- latest completed/final event with event id
- PR/SHA/check/test facts when present
- exact approval token only when the timeline provides a matching PR and SHA
- next useful action

For requests like "watch this", "watch Vela chat bot", or "stream progress",
call `watch_run` with the Telegram chat id from the conversation context and
the resolved run id or session name. Watching is notification-only; it must not
steer, block, kill, restart, or mutate Codex Desktop.

For requests like "steer", "nudge", "re-anchor", "continue", "do the suggested
next move", or "send this to Codex", call `request_steering` with the resolved
run id or session name and the exact message to deliver. The tool is
mode-aware: in advise mode it creates a durable action and Telegram approval
prompt; in enforce mode it may auto-deliver non-destructive steering. Trust the
tool output, and say whether the steer was delivered, queued for approval, or
failed. Destructive actions still require escalation.
"""

SUMMARY_PROMPT = """You summarize a Telegram conversation with a local Codex Desktop supervisor.

Return only compact JSON:
{
  "summary": "2-5 sentences of durable user/session context",
  "active_run_id": "run id if one is clearly active, else null"
}

Preserve:
- what Sam is monitoring
- current named session or PR if known
- pending approvals/actions
- user preferences that affect future replies

Do not include secrets, raw tokens, or verbose event logs.
"""


class SupervisorRuntime(Protocol):
    model: str

    async def answer(
        self,
        *,
        message: str,
        tool_api: SupervisorToolAPI,
        conversation_context: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    async def summarize_conversation(
        self,
        *,
        conversation_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


class ClaudeAgentSupervisorRuntime:
    def __init__(self, cfg: Config, state: State):
        self.cfg = cfg
        self.state = state
        self.model = cfg.models.post_run_eval_model

    async def answer(
        self,
        *,
        message: str,
        tool_api: SupervisorToolAPI,
        conversation_context: dict[str, Any],
    ) -> dict[str, Any]:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from mcp_tools.supervisor_tools import build_supervisor_mcp_server

        options = self._build_options(tool_api, conversation_context=conversation_context)
        outputs: list[str] = []
        claude_session_id: str | None = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(_message_with_context(message, conversation_context))
            async for msg in client.receive_response():
                session_id = getattr(msg, "session_id", None)
                if isinstance(session_id, str) and session_id:
                    claude_session_id = session_id
                if hasattr(msg, "content"):
                    for block in getattr(msg, "content", []) or []:
                        text = getattr(block, "text", None)
                        if text:
                            outputs.append(text)
        text = "\n".join(outputs).strip() or "I checked, but I did not get a usable supervisor response."
        return {
            "text": text,
            "tool_outputs": [],
            "proposed_actions": [],
            "claude_session_id": claude_session_id,
        }

    async def summarize_conversation(
        self,
        *,
        conversation_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        prompt = (
            "<existing_conversation_context>\n"
            f"{json.dumps(conversation_context, indent=2, default=str)}\n"
            "</existing_conversation_context>\n\n"
            "<recent_turns>\n"
            f"{json.dumps(redact(recent_turns), indent=2, default=str)}\n"
            "</recent_turns>"
        )
        options = ClaudeAgentOptions(
            system_prompt=SUMMARY_PROMPT,
            model=self.model,
            max_turns=1,
            permission_mode="dontAsk",
        )
        outputs: list[str] = []
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if hasattr(msg, "content"):
                    for block in getattr(msg, "content", []) or []:
                        text = getattr(block, "text", None)
                        if text:
                            outputs.append(text)
        return _parse_summary_result("\n".join(outputs))

    def _build_options(
        self,
        tool_api: SupervisorToolAPI,
        *,
        conversation_context: dict[str, Any] | None = None,
    ):
        from claude_agent_sdk import ClaudeAgentOptions
        from mcp_tools.supervisor_tools import build_supervisor_mcp_server

        mcp = build_supervisor_mcp_server(self.cfg, self.state, tool_api)
        mcp_servers = {"supervisor": mcp}
        mcp_servers.update(external_mcp_servers(self.cfg))
        resume = None
        if conversation_context:
            candidate = conversation_context.get("claude_session_id")
            if isinstance(candidate, str) and _looks_like_uuid(candidate):
                resume = candidate
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            model=self.model,
            max_turns=10,
            resume=resume,
            mcp_servers=mcp_servers,
            allowed_tools=connector_allowed_tools(self.cfg),
            disallowed_tools=connector_disallowed_tools(self.cfg),
            permission_mode="dontAsk",
        )
        return options


class TelegramChatSupervisor:
    def __init__(
        self,
        cfg: Config,
        state: State,
        *,
        runtime: SupervisorRuntime | None = None,
        tool_api: SupervisorToolAPI | None = None,
        target_adapter: Any | None = None,
        telegram_sender: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.tool_api = tool_api or SupervisorToolAPI(
            state,
            aliases_path="~/.codex-supervisor/session-aliases.json",
            target_adapter=target_adapter,
            telegram_sender=telegram_sender,
            steering_mode=cfg.modes.steering_injection,
        )
        self.runtime = runtime or ClaudeAgentSupervisorRuntime(cfg, state)

    async def handle_message(self, text: str, *, telegram_message: dict[str, Any]) -> str:
        chat_id = str((telegram_message.get("chat") or {}).get("id", ""))
        turn_id = self.state.record_supervisor_turn(
            chat_id=chat_id,
            message_text=text,
            request={"telegram_message": telegram_message},
            model=getattr(self.runtime, "model", None),
        )
        conversation_context = self._conversation_context(
            chat_id=chat_id,
            exclude_turn_id=turn_id,
        )
        try:
            result = await self.runtime.answer(
                message=text,
                tool_api=self.tool_api,
                conversation_context=conversation_context,
            )
            response_text = str(result.get("text") or "").strip()
            if not response_text:
                response_text = "I checked, but I did not get a usable supervisor response."
            tool_outputs = result.get("tool_outputs") or []
            proposed_actions = result.get("proposed_actions") or []
            self.state.complete_supervisor_turn(
                turn_id,
                response_text=response_text,
                status="completed",
                model=getattr(self.runtime, "model", None),
                tool_outputs=tool_outputs,
                proposed_actions=proposed_actions,
            )
            self.state.upsert_supervisor_conversation(
                chat_id=chat_id,
                claude_session_id=result.get("claude_session_id") or None,
                increment_turn_count=True,
            )
            await self._maybe_refresh_summary(chat_id)
            return redact_for_telegram(response_text)
        except Exception as e:
            log.exception("telegram supervisor turn failed: %s", e)
            response_text = (
                "I could not complete that supervisor turn. The daemon is still "
                "watching hooks and rollouts; check /status while I recover."
            )
            self.state.complete_supervisor_turn(
                turn_id,
                response_text=response_text,
                status="failed",
                model=getattr(self.runtime, "model", None),
                tool_outputs=[{"error_type": type(e).__name__}],
                proposed_actions=[],
            )
            return response_text

    async def _maybe_refresh_summary(self, chat_id: str) -> None:
        row = self.state.get_supervisor_conversation(chat_id)
        if row is None:
            return
        turn_count = int(row["turn_count"] or 0)
        if turn_count == 0 or turn_count % SUMMARY_REFRESH_EVERY_TURNS != 0:
            return
        summarizer = getattr(self.runtime, "summarize_conversation", None)
        if summarizer is None:
            return
        conversation_context = self._conversation_context(
            chat_id=chat_id,
            exclude_turn_id=-1,
        )
        recent_turns = self.state.recent_supervisor_turns(
            chat_id=chat_id,
            n=SUMMARY_REFRESH_EVERY_TURNS,
        )
        try:
            result = await summarizer(
                conversation_context=conversation_context,
                recent_turns=recent_turns,
            )
        except Exception as e:
            log.warning("conversation summary refresh failed for chat %s: %s", chat_id, e)
            return
        summary = str(result.get("summary") or "").strip()
        active_run_id = result.get("active_run_id")
        if not summary:
            return
        self.state.upsert_supervisor_conversation(
            chat_id=chat_id,
            summary=summary,
            active_run_id=str(active_run_id) if active_run_id else None,
        )

    def _conversation_context(self, *, chat_id: str, exclude_turn_id: int) -> dict[str, Any]:
        row = self.state.get_supervisor_conversation(chat_id)
        return redact({
            "chat_id": chat_id,
            "claude_session_id": row["claude_session_id"] if row else None,
            "summary": row["summary"] if row else "",
            "active_run_id": row["active_run_id"] if row else None,
            "turn_count": row["turn_count"] if row else 0,
            "recent_turns": self.state.recent_supervisor_turns(
                chat_id=chat_id,
                n=5,
                exclude_turn_id=exclude_turn_id,
            ),
        })


def _message_with_context(message: str, context: dict[str, Any]) -> str:
    return (
        "<conversation_context>\n"
        f"{json.dumps(context, indent=2, default=str)}\n"
        "</conversation_context>\n\n"
        "<user_message>\n"
        f"{redact_for_telegram(message)}\n"
        "</user_message>"
    )


def _looks_like_uuid(value: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    ))


def _parse_summary_result(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {"summary": "", "active_run_id": None}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                parsed = None
    if isinstance(parsed, dict):
        return redact({
            "summary": str(parsed.get("summary") or "").strip(),
            "active_run_id": parsed.get("active_run_id"),
        })
    return redact({"summary": text, "active_run_id": None})
