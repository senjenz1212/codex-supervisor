"""Operator-facing visibility policy for passive Codex Desktop status sync.

Transport delivery and user-visible GUI repaint are separate outcomes. The
Codex app-server can accept a `thread/inject_items` write even when an
already-open Desktop renderer does not repaint live. This module keeps that
distinction explicit everywhere the supervisor reports status.
"""
from __future__ import annotations

from typing import Any


def classify_desktop_status_visibility(result: dict[str, Any]) -> dict[str, Any]:
    """Classify an append_status_item adapter result for operators.

    `delivered=true` means the target adapter completed its write. It does not
    prove the Desktop GUI rendered the item. Only an explicit
    `desktop_gui_repaint="verified"` result may become `gui_live`.
    """
    delivered = bool(result.get("delivered"))
    repaint = str(result.get("desktop_gui_repaint") or "unverified").strip().lower()
    method = str(result.get("method") or "").strip()
    reason = str(result.get("reason") or "").strip()

    if not delivered:
        return {
            "effective_state": "failed",
            "transport_delivered": False,
            "history_appended": False,
            "gui_repaint": repaint,
            "gui_live": False,
            "method": method or None,
            "reason": reason or None,
            "user_visible_summary": (
                "Desktop status append failed; Telegram remains the reliable "
                "live progress stream."
            ),
        }

    if repaint == "verified":
        return {
            "effective_state": "gui_live",
            "transport_delivered": True,
            "history_appended": True,
            "gui_repaint": "verified",
            "gui_live": True,
            "method": method or None,
            "reason": reason or None,
            "user_visible_summary": (
                "Status appended to Codex thread history and Desktop GUI "
                "repaint was verified."
            ),
        }

    return {
        "effective_state": "history_only",
        "transport_delivered": True,
        "history_appended": True,
        "gui_repaint": repaint or "unverified",
        "gui_live": False,
        "method": method or None,
        "reason": reason or None,
        "user_visible_summary": (
            "Status appended to Codex thread history; live Desktop GUI repaint "
            "is unverified. Telegram remains the reliable live progress stream."
        ),
    }


def desktop_status_health(mode: str) -> dict[str, Any]:
    """Return health fields that do not overclaim GUI visibility."""
    if mode == "off":
        return {
            "desktop_status_sync_effective": "off",
            "desktop_gui_live_stream": False,
        }
    return {
        "desktop_status_sync_effective": "history_only",
        "desktop_gui_live_stream": False,
    }
