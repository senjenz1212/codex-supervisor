"""Telegram progress streaming for watched Codex rollout events."""
from __future__ import annotations

import logging
from typing import Any

from .redaction import redact_for_telegram
from .state import Decision, State
from .target.types import TargetAction
from .desktop_visibility import classify_desktop_status_visibility

log = logging.getLogger(__name__)


class TelegramProgressStreamer:
    """Routes high-signal persisted rollout events to durable run watches."""

    def __init__(
        self,
        *,
        state: State,
        notifier: Any,
        target_adapter: Any | None = None,
        desktop_status_mode: str = "off",
        telegram_fyi_mode: str = "advise",
    ):
        self.state = state
        self.notifier = notifier
        self.target_adapter = target_adapter
        self.desktop_status_mode = desktop_status_mode
        self.telegram_fyi_mode = telegram_fyi_mode

    async def handle_event(self, run_id: str, event: dict[str, Any]) -> None:
        event_id = int(event.get("id") or 0)
        if event_id <= 0:
            return
        message = progress_message(run_id, event)
        if not message:
            return
        urgency = progress_urgency(event, message)

        for watch in self.state.active_run_watches(run_id):
            if event_id <= int(watch["last_event_id"] or 0):
                continue
            suppressed = self.telegram_fyi_mode == "off" and urgency != "alert"
            if not suppressed:
                try:
                    await self.notifier.send_message(message)
                except Exception as e:
                    log.warning("telegram progress send failed for run %s: %s", run_id, e)
                    continue
            request = {
                "origin": _notification_origin(suppressed=suppressed, urgency=urgency),
                "kind": "watched_run_progress",
                "run_id": run_id,
                "event_id": event_id,
                "urgency": urgency,
            }
            if suppressed:
                request["suppressed_by"] = "telegram_fyis_off"
            self.state.record_supervisor_notification(
                chat_id=str(watch["chat_id"]),
                message_text=_notification_turn_label(
                    suppressed=suppressed,
                    urgency=urgency,
                ),
                response_text=message,
                request=request,
                tool_outputs=[{
                    "name": "progress_message",
                    "run_id": run_id,
                    "event_id": event_id,
                    "suppressed": suppressed,
                    "urgency": urgency,
                }],
                proposed_actions=[],
            )
            self.state.upsert_supervisor_conversation(
                chat_id=str(watch["chat_id"]),
                active_run_id=run_id,
            )
            await self._append_desktop_status(
                run_id=run_id,
                event_id=event_id,
                message=message,
            )
            self.state.mark_run_watch_notified(
                watch_id=int(watch["id"]),
                event_id=event_id,
            )
            if _should_review(event):
                await self.state.enqueue_decision(Decision(
                    kind="review_updates",
                    run_id=run_id,
                    payload={
                        "chat_id": watch["chat_id"],
                        "event_id": event_id,
                        "trigger": "watched_task_complete",
                    },
                ))

    async def _append_desktop_status(
        self,
        *,
        run_id: str,
        event_id: int,
        message: str,
    ) -> None:
        if self.desktop_status_mode == "off" or self.target_adapter is None:
            return
        run = self.state.get_run(run_id)
        if run is None:
            return
        session_id = str(run["session_id"] or "").strip()
        if not session_id:
            return
        payload = {
            "run_id": run_id,
            "event_id": event_id,
            "session_id": session_id,
            "source": "watched_run_progress",
            "message": message,
            "mode": self.desktop_status_mode,
        }
        action_id = self.state.record_action(
            run_id=run_id,
            action_type="append_status_item",
            requested_by="telegram_progress",
            payload=payload,
            status="pending",
        )
        try:
            result = await self.target_adapter.execute_action(TargetAction(
                kind="append_status_item",
                session_id=session_id,
                payload={
                    "message": message,
                    "source": "watched_run_progress",
                    "event_id": event_id,
                    "run_id": run_id,
                },
            ))
        except Exception as e:
            self.state.complete_action(action_id, "failed", {
                "adapter_result": {
                    "delivered": False,
                    "reason": "adapter_exception",
                    "error": str(e),
                },
            })
            log.warning(
                "desktop status sync failed for run %s event %s: %s",
                run_id, event_id, e,
            )
            return
        visibility = classify_desktop_status_visibility(result)
        status = "delivered" if result.get("delivered") else "failed"
        self.state.complete_action(action_id, status, {
            "adapter_result": result,
            "visibility": visibility,
        })


def progress_message(run_id: str, event: dict[str, Any]) -> str | None:
    kind = event.get("kind")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    payload_type = payload.get("type")
    event_id = event.get("id")

    if kind == "event_msg" and payload_type == "task_started":
        return _format(
            title="Run started",
            run_id=run_id,
            event_id=event_id,
            body=f"turn={payload.get('turn_id') or 'unknown'}",
        )

    if kind == "event_msg" and payload_type == "agent_message":
        phase = payload.get("phase")
        if phase not in {"commentary", "final_answer", None}:
            return None
        text = str(payload.get("message") or "").strip()
        if not text:
            return None
        return _format(
            title="Run update",
            run_id=run_id,
            event_id=event_id,
            body=text,
        )

    if kind == "event_msg" and payload_type == "task_complete":
        text = str(payload.get("last_agent_message") or "").strip()
        return _format(
            title="Run complete",
            run_id=run_id,
            event_id=event_id,
            body=text or "Task completed.",
        )

    return None


def progress_urgency(event: dict[str, Any], message: str) -> str:
    """Classify watched-run progress for Telegram quiet-mode routing.

    Quiet mode suppresses routine FYIs only. Explicit halts, blockers, failed
    checks, and approval-needed states are operator alerts.
    """
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    payload_type = str(payload.get("type") or "").lower()
    text = " ".join([
        str(payload.get("message") or ""),
        str(payload.get("last_agent_message") or ""),
        message,
    ]).lower()

    if "halted" in text or " halt " in f" {text} ":
        return "alert"
    if "blocker" in text or "blocked" in text:
        return "alert"
    if "sandbox" in text and (
        "writable roots" in text
        or "approval policy" in text
        or "blocked" in text
        or "cannot create" in text
    ):
        return "alert"
    if (
        "ci failed" in text
        or "checks failed" in text
        or "check failed" in text
        or "failing check" in text
    ):
        return "alert"
    if (
        "needs approval" in text
        or "requires approval" in text
        or "pending approval" in text
        or "waiting for approval" in text
        or "tier-b approval" in text
    ):
        return "alert"
    if payload_type == "task_complete" and "failed" in text:
        return "alert"
    return "fyi"


def _notification_origin(*, suppressed: bool, urgency: str) -> str:
    if suppressed:
        return "progress_notification_suppressed"
    if urgency == "alert":
        return "progress_alert"
    return "progress_notification"


def _notification_turn_label(*, suppressed: bool, urgency: str) -> str:
    if suppressed:
        return "[watched run progress suppressed]"
    if urgency == "alert":
        return "[watched run alert]"
    return "[watched run progress]"


def _should_review(event: dict[str, Any]) -> bool:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return event.get("kind") == "event_msg" and payload.get("type") == "task_complete"


def _format(*, title: str, run_id: str, event_id: Any, body: str) -> str:
    clipped = _clip(body)
    return redact_for_telegram(
        f"{title}\nrun={run_id}\nevent={event_id}\n\n{clipped}"
    )


def _clip(text: str, limit: int = 1400) -> str:
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 20].rstrip() + "\n...[truncated]"
