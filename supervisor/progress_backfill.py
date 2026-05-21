"""Repair watched-run progress context without resending Telegram messages."""
from __future__ import annotations

import argparse
import json
from typing import Any

from .config import Config
from .state import State
from .telegram_progress import progress_message


def backfill_progress_context(
    *,
    state: State,
    chat_id: str,
    run_id: str,
    event_id: int,
) -> dict[str, Any]:
    """Persist an already-sent progress notification as read-only context.

    This is an operational repair path for events that were streamed to
    Telegram before progress notifications were recorded in `supervisor_turns`.
    It never sends Telegram and never creates action authority.
    """
    clean_chat_id = str(chat_id or "").strip()
    if not clean_chat_id:
        raise ValueError("chat_id is required for progress context backfill")

    existing = state.find_supervisor_notification(
        chat_id=clean_chat_id,
        run_id=run_id,
        event_id=int(event_id),
    )
    if existing is not None:
        return {
            "status": "already_present",
            "turn_id": int(existing["id"]),
            "sent_telegram": False,
        }

    row = state.get_event(run_id=run_id, event_id=int(event_id))
    if row is None:
        raise LookupError(f"event not found: run_id={run_id} event_id={event_id}")

    payload = json.loads(row["payload_json"])
    event = {"id": int(event_id), "kind": row["kind"], **payload}
    message = progress_message(run_id, event)
    if not message:
        return {
            "status": "not_progress_event",
            "turn_id": None,
            "sent_telegram": False,
        }

    turn_id = state.record_supervisor_notification(
        chat_id=clean_chat_id,
        message_text="[watched run progress]",
        response_text=message,
        request={
            "origin": "progress_notification_backfill",
            "kind": "watched_run_progress",
            "run_id": run_id,
            "event_id": int(event_id),
        },
        tool_outputs=[{
            "name": "progress_context_backfill",
            "run_id": run_id,
            "event_id": int(event_id),
        }],
        proposed_actions=[],
    )
    state.upsert_supervisor_conversation(
        chat_id=clean_chat_id,
        active_run_id=run_id,
    )
    return {
        "status": "recorded",
        "turn_id": int(turn_id),
        "sent_telegram": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("event_id", type=int)
    parser.add_argument(
        "--config",
        default="~/.codex-supervisor/config.yaml",
        help="Supervisor config path.",
    )
    parser.add_argument(
        "--chat-id",
        default=None,
        help="Telegram chat id. Defaults to telegram.chat_id from config.",
    )
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    chat_id = args.chat_id if args.chat_id is not None else cfg.telegram.chat_id
    state = State(cfg.supervisor.state_db)
    result = backfill_progress_context(
        state=state,
        chat_id=chat_id,
        run_id=args.run_id,
        event_id=args.event_id,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
