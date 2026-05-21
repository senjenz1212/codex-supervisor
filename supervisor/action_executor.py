"""Action executor: records proposed_actions as durable rows and dispatches
them via the target adapter (ticket 06, PRD promises P4, P8).

Public boundary:
    execute_actions(proposed, *, state, adapter, telegram_sender) -> list[dict]

Design constraints:
  - Pure record-and-route: no I/O beyond State + adapter + telegram_sender.
  - No subprocess, httpx, Anthropic, or OpenAI calls from here.
  - Destructive actions (ask_user, deny_next_write) never execute without
    pending-approval state; the adapter is not called until approval arrives.
  - One pending ask_user per run (dedup guard against racing injections).
  - telegram_sender is an optional callable; if None, asks are recorded but
    no notification is sent (Telegram outage fails closed for destructive actions).
"""
from __future__ import annotations
import json
import secrets
import time
from typing import Any, Callable

from .state import State
from .target.types import TargetAction


DESTRUCTIVE_ACTIONS = {"kill", "restart"}
APPROVAL_REQUIRED_ACTIONS = {"ask_user", "inject_steering", *DESTRUCTIVE_ACTIONS}
AUTO_EXECUTABLE_ACTIONS = {"inject_steering"}


def _has_pending_action(
    state: State,
    run_id: str,
    kind: str,
    session_id: str | None = None,
) -> bool:
    row = state._conn.execute(
        """SELECT payload_json FROM actions
           WHERE run_id=? AND action_type=? AND status='pending_approval'""",
        (run_id, kind),
    ).fetchall()
    if session_id is None:
        return bool(row)
    for r in row:
        payload = json.loads(r["payload_json"])
        if payload.get("session_id") == session_id:
            return True
    return False


def _pending_action_for_ask(state: State, ask_id: int):
    return state._conn.execute(
        """SELECT * FROM actions
           WHERE status='pending_approval'
             AND json_extract(payload_json, '$.ask_id')=?
           ORDER BY id DESC LIMIT 1""",
        (ask_id,),
    ).fetchone()


def _expire_pending_action_for_ask(state: State, ask_id: int) -> None:
    row = _pending_action_for_ask(state, ask_id)
    if row is None:
        return
    state.complete_action(row["id"], "approval_expired", {
        "ask_id": ask_id,
        "requires_approval": True,
    })


def _expire_stale_pending_approvals(state: State) -> None:
    """Close pending action rows whose approval ask has expired.

    This keeps old inline Telegram buttons from blocking future steering
    requests through the dedup guard.
    """
    now = int(time.time())
    rows = state._conn.execute(
        "SELECT id, payload_json FROM actions WHERE status='pending_approval'"
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        ask_id = payload.get("ask_id")
        if not ask_id:
            continue
        ask = state.get_ask(int(ask_id))
        if ask is None:
            continue
        expires_at = ask["expires_at"]
        if ask["status"] == "expired" or (
            ask["status"] == "pending"
            and expires_at is not None
            and now > int(expires_at)
        ):
            if ask["status"] == "pending":
                state._conn.execute(
                    "UPDATE telegram_asks SET status='expired' WHERE ask_id=?",
                    (int(ask_id),),
                )
                state._conn.commit()
            state.complete_action(row["id"], "approval_expired", {
                "ask_id": int(ask_id),
                "requires_approval": True,
            })


def _create_approval(
    *,
    state: State,
    action: dict[str, Any],
    action_id: int,
    telegram_sender: Callable[..., None] | None,
) -> tuple[str, int | None]:
    """Create the approval ask and notify Telegram.

    Returns (status, ask_id). Telegram failures are fail-closed by marking the
    action approval_unavailable and returning no ask id.
    """
    run_id = action.get("run_id", "")
    nonce = secrets.token_hex(8)
    expires_at = int(time.time()) + int(action.get("timeout_s", 300))
    options = action.get("options", ["Approve", "Reject"])
    question = action.get("summary", "Approve supervisor action?")
    ask_id = state.create_ask(
        run_id=run_id,
        question=question,
        options=options,
        nonce=nonce,
        expires_at=expires_at,
    )
    state.complete_action(action_id, "pending_approval", {
        "ask_id": ask_id,
        "nonce": nonce,
        "expires_at": expires_at,
        "requires_approval": True,
    })
    if telegram_sender is None:
        return "pending_approval", ask_id
    try:
        telegram_sender(
            ask_id=ask_id,
            run_id=run_id,
            question=question,
            options=options,
            nonce=nonce,
            expires_at=expires_at,
        )
    except Exception as e:
        state.complete_action(action_id, "approval_unavailable", {
            "approval_error": str(e),
        })
        return "approval_unavailable", None
    return "pending_approval", ask_id


def execute_actions(
    proposed_actions: list[dict[str, Any]],
    *,
    state: State,
    adapter: Any,
    telegram_sender: Callable[..., None] | None = None,
) -> list[dict[str, Any]]:
    """Process each proposed action from mode_policy.

    Returns one result dict per proposed action:
        {"action_id": int | None, "status": str, "kind": str}

    Statuses:
        "recommended"      - advise: row written, no adapter, no telegram
        "pending_approval" - ask_user: row written, telegram asked
        "skipped_dedup"    - ask_user blocked (another pending exists for run)
        "recorded"         - deny_next_write: row written as flag
    """
    results: list[dict[str, Any]] = []
    _expire_stale_pending_approvals(state)

    for action in proposed_actions:
        kind    = action.get("kind", "")
        run_id  = action.get("run_id", "")

        if kind == "recommend_scope_violation":
            action_id = state.record_action(
                run_id=run_id,
                action_type=kind,
                requested_by="mode_policy",
                payload=action,
                status="recommended",
            )
            results.append({"action_id": action_id, "status": "recommended", "kind": kind})

        elif kind in APPROVAL_REQUIRED_ACTIONS:
            session_id = action.get("session_id")
            if _has_pending_action(state, run_id, kind, session_id=session_id):
                results.append({"action_id": None, "status": "skipped_dedup", "kind": kind})
                continue

            action_payload = dict(action)
            action_payload["requires_approval"] = True
            action_id = state.record_action(
                run_id=run_id,
                action_type=kind,
                requested_by="mode_policy",
                payload=action_payload,
                status="pending_approval",
            )
            if kind in DESTRUCTIVE_ACTIONS and telegram_sender is None:
                state.complete_action(action_id, "approval_unavailable")
                results.append({
                    "action_id": action_id,
                    "ask_id": None,
                    "status": "approval_unavailable",
                    "kind": kind,
                })
                continue

            status, ask_id = _create_approval(
                state=state,
                action=action_payload,
                action_id=action_id,
                telegram_sender=telegram_sender,
            )
            results.append({
                "action_id": action_id,
                "ask_id": ask_id,
                "status": status,
                "kind": kind,
            })

        elif kind == "deny_next_write":
            action_id = state.record_action(
                run_id=run_id,
                action_type=kind,
                requested_by="mode_policy",
                payload=action,
                status="pending",
            )
            results.append({"action_id": action_id, "status": "recorded", "kind": kind})

        else:
            results.append({"action_id": None, "status": "unknown_kind", "kind": kind})

    return results


async def execute_actions_async(
    proposed_actions: list[dict[str, Any]],
    *,
    state: State,
    adapter: Any,
    telegram_sender: Callable[..., None] | None = None,
    auto_execute_actions: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Async extension for auto-executing explicitly allowlisted actions.

    The synchronous `execute_actions` path remains the approval-first default.
    This wrapper is used by aggressive/enforce steering mode for
    non-destructive actions only. Destructive actions always fall back to the
    approval path, even if a caller mistakenly includes them in
    `auto_execute_actions`.
    """
    auto_allowed = (auto_execute_actions or set()) & AUTO_EXECUTABLE_ACTIONS
    if not auto_allowed:
        return execute_actions(
            proposed_actions,
            state=state,
            adapter=adapter,
            telegram_sender=telegram_sender,
        )

    results: list[dict[str, Any]] = []
    seen_auto_keys: set[tuple[str, str, str]] = set()
    _expire_stale_pending_approvals(state)

    for action in proposed_actions:
        kind = str(action.get("kind") or "")
        run_id = str(action.get("run_id") or "")
        session_id = str(action.get("session_id") or "")

        if kind not in auto_allowed or kind in DESTRUCTIVE_ACTIONS:
            results.extend(execute_actions(
                [action],
                state=state,
                adapter=adapter,
                telegram_sender=telegram_sender,
            ))
            continue

        dedup_key = (run_id, kind, session_id)
        if dedup_key in seen_auto_keys or _has_pending_action(
            state,
            run_id,
            kind,
            session_id=session_id or None,
        ):
            results.append({"action_id": None, "status": "skipped_dedup", "kind": kind})
            continue
        seen_auto_keys.add(dedup_key)

        action_payload = dict(action)
        action_payload["auto_executed"] = True
        action_payload["requires_approval"] = False
        action_id = state.record_action(
            run_id=run_id,
            action_type=kind,
            requested_by="mode_policy",
            payload=action_payload,
            status="pending",
        )

        if adapter is None:
            state.complete_action(action_id, "failed", {
                "adapter_result": {
                    "delivered": False,
                    "reason": "target_adapter_unavailable",
                },
            })
            results.append({"action_id": action_id, "status": "failed", "kind": kind})
            continue

        target_action = TargetAction(
            kind=kind,
            session_id=session_id,
            payload=action_payload,
        )
        try:
            adapter_result = await adapter.execute_action(target_action)
        except Exception as e:
            adapter_result = {
                "delivered": False,
                "reason": "adapter_exception",
                "error": str(e),
            }
        status = "delivered" if adapter_result.get("delivered") else "failed"
        state.complete_action(action_id, status, {"adapter_result": adapter_result})
        results.append({"action_id": action_id, "status": status, "kind": kind})

    return results


async def resolve_approval(
    *,
    ask_id: int,
    answer: str,
    nonce: str | None,
    state: State,
    adapter: Any,
) -> dict[str, Any]:
    """Resolve a Telegram approval and dispatch the approved target action.

    Stale, spoofed, expired, or rejected approvals fail closed and never call
    the target adapter. Only non-destructive target actions are dispatched here;
    kill/restart remain approval records for a later explicit recovery runner.
    """
    ask = state.get_ask(ask_id)
    if not ask:
        return {"status": "rejected", "reason": "ask_not_found"}

    ok = state.answer_ask(ask_id, answer, nonce=nonce)
    if not ok:
        refreshed = state.get_ask(ask_id)
        if refreshed is not None and refreshed["status"] == "expired":
            _expire_pending_action_for_ask(state, ask_id)
        return {"status": "rejected", "reason": "invalid_or_expired_approval"}

    row = _pending_action_for_ask(state, ask_id)
    if row is None:
        return {"status": "rejected", "reason": "action_not_found"}

    payload = json.loads(row["payload_json"])
    action_type = row["action_type"]
    if answer.lower() not in ("approve", "re-anchor", "reanchor"):
        state.complete_action(row["id"], "cancelled", {"answer": answer})
        return {"status": "cancelled", "action_id": row["id"]}

    if action_type in DESTRUCTIVE_ACTIONS:
        state.complete_action(row["id"], "approval_recorded", {"answer": answer})
        return {"status": "approval_recorded", "action_id": row["id"]}

    target_action = TargetAction(
        kind=action_type,
        session_id=payload.get("session_id", ""),
        payload=payload,
    )
    result = await adapter.execute_action(target_action)
    delivered = bool(result.get("delivered"))
    status = "delivered" if delivered else "failed"
    state.complete_action(row["id"], status, {
        "answer": answer,
        "adapter_result": result,
    })
    return {"status": status, "action_id": row["id"], "adapter_result": result}
