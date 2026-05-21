"""Telegram notification and polling without Agent SDK dependencies.

This module is used by the daemon directly for live monitoring. The MCP
toolpack wraps the same primitives when the optional Claude Agent SDK is
installed, but Telegram itself must work without that optional runtime.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any

import httpx

from .config import Config
from .redaction import redact_for_telegram
from .state import State

log = logging.getLogger(__name__)


def telegram_enabled(cfg: Config) -> bool:
    """True when config contains non-placeholder Telegram credentials."""
    token = (cfg.telegram.bot_token or "").strip()
    chat_id = (cfg.telegram.chat_id or "").strip()
    placeholders = {"", "fake", "todo", "TODO", "changeme", "test"}
    if token in placeholders or chat_id in placeholders:
        return False
    if token.lower().startswith("fake") or chat_id.lower().startswith("fake"):
        return False
    return True


def api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


class TelegramNotifier:
    """Small async Telegram Bot API client for supervisor notifications."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self.cfg.telegram.chat_id,
            "text": redact_for_telegram(text),
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        if client is not None:
            r = await client.post(
                api_url(self.cfg.telegram.bot_token, "sendMessage"),
                data=payload,
            )
            return r.json()

        async with httpx.AsyncClient(timeout=10) as owned:
            r = await owned.post(
                api_url(self.cfg.telegram.bot_token, "sendMessage"),
                data=payload,
            )
            return r.json()

    async def send_startup(self, *, target: str, port: int) -> None:
        await self.send_message(
            f"Codex Supervisor online. target={target}, hook=http://127.0.0.1:{port}"
        )

    async def send_hook_recommendation(
        self,
        *,
        session_id: str,
        run_id: str | None,
        reason: str,
        tool_name: str | None,
    ) -> None:
        run_part = run_id or "unregistered"
        tool_part = tool_name or "unknown tool"
        await self.send_message(
            "Supervisor warning: destructive hook candidate.\n"
            f"run={run_part}\n"
            f"session={session_id or 'unknown'}\n"
            f"tool={tool_part}\n"
            f"reason={reason}"
        )

    async def send_approval_prompt(
        self,
        *,
        ask_id: int,
        question: str,
        options: list[str],
        nonce: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        keyboard = {
            "inline_keyboard": [[
                {
                    "text": opt,
                    "callback_data": f"ask:{ask_id}:{nonce}:{i}",
                }
            ] for i, opt in enumerate(options)]
        }
        return await self.send_message(
            question,
            reply_markup=keyboard,
            client=client,
        )

    def approval_sender(self):
        """Return a sync callback compatible with action_executor.execute_actions."""
        def _send(
            *,
            ask_id: int,
            run_id: str,
            question: str,
            options: list[str],
            nonce: str | None = None,
            expires_at: int | None = None,
        ) -> None:
            if not nonce:
                raise ValueError("approval prompt requires nonce")
            keyboard = {
                "inline_keyboard": [[
                    {
                        "text": opt,
                        "callback_data": f"ask:{ask_id}:{nonce}:{i}",
                    }
                ] for i, opt in enumerate(options)]
            }
            payload = {
                "chat_id": self.cfg.telegram.chat_id,
                "text": redact_for_telegram(question),
                "reply_markup": json.dumps(keyboard),
            }
            with httpx.Client(timeout=10) as client:
                client.post(
                    api_url(self.cfg.telegram.bot_token, "sendMessage"),
                    data=payload,
                ).raise_for_status()

        return _send


class TelegramPoller:
    """Long-poll Telegram and route commands/callbacks into supervisor state."""

    def __init__(
        self,
        cfg: Config,
        state: State,
        target_adapter: Any | None = None,
        chat_handler: Any | None = None,
    ):
        self.cfg = cfg
        self.state = state
        self.target_adapter = target_adapter
        self.chat_handler = chat_handler
        self.offset = 0

    async def run(self) -> None:
        log.info("TelegramPoller: starting")
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                try:
                    await self._poll(client)
                except Exception as e:
                    log.warning("telegram poll error: %s", e)
                    await asyncio.sleep(5)
                await asyncio.sleep(self.cfg.telegram.poll_interval_s)

    async def _poll(self, client: httpx.AsyncClient) -> None:
        r = await client.get(
            api_url(self.cfg.telegram.bot_token, "getUpdates"),
            params={
                "offset": self.offset,
                "timeout": 25,
                "allowed_updates": json.dumps(["callback_query", "message"]),
            },
        )
        data = r.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            self.offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if cb:
                await self._handle_callback(cb, client=client)
                continue
            msg = update.get("message", {})
            text = msg.get("text", "")
            if text.startswith("/"):
                await self._handle_command(msg, client)
            elif text:
                await self._handle_chat_message(msg, client)

    async def _handle_callback(
        self,
        cb: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = self._parse_callback_data(str(cb.get("data", "")))
        if parsed is None:
            return
        ask_id, nonce, idx = parsed
        row = self.state.get_ask(ask_id)
        if not row:
            return
        options = json.loads(row["options_json"])
        if idx >= len(options):
            return
        answer = options[idx]

        delivered = False
        if self.target_adapter is not None:
            from .action_executor import resolve_approval
            result = await resolve_approval(
                ask_id=ask_id,
                answer=answer,
                nonce=nonce,
                state=self.state,
                adapter=self.target_adapter,
            )
            delivered = result.get("status") in {
                "delivered",
                "approval_recorded",
                "cancelled",
            }
        else:
            delivered = self.state.answer_ask(ask_id, answer, nonce=nonce)

        if not delivered:
            await self._answer_callback(
                cb,
                "Approval expired or invalid.",
                client=client,
            )
            return
        await self._answer_callback(cb, f"Recorded: {answer}", client=client)

    @staticmethod
    def _parse_callback_data(data: str) -> tuple[int, str | None, int] | None:
        # Current form: ask:<ask_id>:<nonce>:<option_index>
        parts = data.split(":")
        try:
            if len(parts) == 4 and parts[0] == "ask":
                return int(parts[1]), parts[2], int(parts[3])
            # Back-compat with old ask_id:idx callbacks for non-nonce asks.
            if len(parts) == 2:
                return int(parts[0]), None, int(parts[1])
        except ValueError:
            return None
        return None

    async def _answer_callback(
        self,
        cb: dict[str, Any],
        text: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        payload = {
            "callback_query_id": cb.get("id", ""),
            "text": redact_for_telegram(text),
        }
        if client is not None:
            await client.post(
                api_url(self.cfg.telegram.bot_token, "answerCallbackQuery"),
                data=payload,
            )
            return
        async with httpx.AsyncClient(timeout=5) as owned:
            await owned.post(
                api_url(self.cfg.telegram.bot_token, "answerCallbackQuery"),
                data=payload,
            )

    async def _handle_command(self, msg: dict[str, Any], client: httpx.AsyncClient) -> None:
        chat = msg.get("chat", {})
        if str(chat.get("id", "")) != str(self.cfg.telegram.chat_id):
            return
        cmd = msg["text"].split()[0]
        if cmd == "/status":
            rows = self.state.active_runs()
            if rows:
                text = "*Active runs:*\n" + "\n".join(
                    f"`{r['run_id']}` - {(r['task'] or 'no task')[:60]}"
                    for r in rows
                )
            else:
                text = "No active runs."
            await client.post(
                api_url(self.cfg.telegram.bot_token, "sendMessage"),
                data={
                    "chat_id": self.cfg.telegram.chat_id,
                    "text": redact_for_telegram(text),
                },
            )

    async def _handle_chat_message(self, msg: dict[str, Any], client: httpx.AsyncClient) -> None:
        chat = msg.get("chat", {})
        if str(chat.get("id", "")) != str(self.cfg.telegram.chat_id):
            return
        text = str(msg.get("text") or "")
        if self.chat_handler is None:
            response_text = (
                "Supervisor chat is not enabled in this daemon. /status still works."
            )
        else:
            try:
                response_text = await self.chat_handler.handle_message(
                    text,
                    telegram_message=msg,
                )
            except Exception as e:
                log.exception("telegram chat handler failed: %s", e)
                response_text = (
                    "I could not complete that supervisor turn. The daemon is "
                    "still watching hooks and rollouts; try /status."
                )
        r = await client.post(
            api_url(self.cfg.telegram.bot_token, "sendMessage"),
            data={
                "chat_id": self.cfg.telegram.chat_id,
                "text": redact_for_telegram(response_text),
            },
        )
        try:
            data = r.json()
            if not data.get("ok", True):
                log.warning("telegram chat send failed: %s", data)
        except Exception:
            pass
