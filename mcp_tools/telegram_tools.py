"""Telegram MCP toolpack.

The live Telegram poller/notifier live in supervisor.telegram and do not depend
on the optional Claude Agent SDK. This module only builds MCP wrappers when the
SDK is installed.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any

from supervisor.config import Config
from supervisor.state import State
from supervisor.telegram import TelegramNotifier, TelegramPoller


def build_telegram_mcp_server(cfg: Config, state: State):
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ModuleNotFoundError as e:
        if e.name == "claude_agent_sdk":
            raise
        raise

    notifier = TelegramNotifier(cfg)

    @tool(
        "send_message",
        "Send a Telegram message to the supervisor's user.",
        {"text": str, "urgency": str},
    )
    async def send_message(args: dict) -> dict[str, Any]:
        text = args["text"]
        urg = args.get("urgency", "normal")
        if cfg.modes.telegram_fyis == "off" and urg != "alert":
            return {"content": [{"type": "text", "text": json.dumps({
                "sent": False,
                "suppressed": True,
                "reason": "telegram_fyis_off",
                "urgency": urg,
            })}]}
        prefix = {"normal": "", "alert": "[alert] ", "fyi": "[fyi] "}.get(urg, "")
        await notifier.send_message(prefix + text)
        return {"content": [{"type": "text", "text": json.dumps({
            "sent": True,
            "suppressed": False,
            "urgency": urg,
        })}]}

    @tool(
        "ask_user",
        "Ask the user a question with inline buttons; block until reply or timeout.",
        {"run_id": str, "question": str, "options": list, "timeout_s": int},
    )
    async def ask_user(args: dict) -> dict[str, Any]:
        run_id = args["run_id"]
        question = args["question"]
        options = args["options"]
        timeout_s = int(args.get("timeout_s") or cfg.telegram.ask_timeout_s)

        nonce = f"mcp-{int(time.time())}"
        expires_at = int(time.time()) + timeout_s
        ask_id = state.create_ask(
            run_id,
            question,
            options,
            nonce=nonce,
            expires_at=expires_at,
        )
        await notifier.send_approval_prompt(
            ask_id=ask_id,
            question=question,
            options=options,
            nonce=nonce,
        )

        start = time.time()
        while time.time() - start < timeout_s:
            row = state.get_ask(ask_id)
            if row and row["status"] == "answered":
                return {"content": [{"type": "text",
                                     "text": json.dumps({"answer": row["answer"]})}]}
            await asyncio.sleep(1)

        return {"content": [{"type": "text",
                             "text": json.dumps({"answer": None, "timed_out": True})}]}

    return create_sdk_mcp_server(
        name="telegram",
        version="0.3.0",
        tools=[send_message, ask_user],
    )
