"""Mode policy: maps drift findings → proposed actions per operating mode.

Pure function. No I/O, no DB, no Telegram, no target adapter. Ticket 06 will
consume the same action schema and turn it into durable rows + Telegram /
adapter execution.

Output schema (action-executor-shaped but not yet coupled to State.record_action):

    {
        "kind":               str,    # see ACTION_KINDS below
        "status":             str,    # "would_*" verb mirroring `kind`
        "run_id":             str,
        "session_id":         str,
        "evidence_event_ids": list[int],  # cites supporting findings
        "severity":           str,    # max severity across cited findings
        "summary":            str,    # short human-readable
        "options":            list[str] | None,  # only present for ask_user
    }

Mode mapping (drift_l1_l3 only in this cycle; cycle 3 will add l4 verdicts):

    off / shadow → []
    advise       → one "recommend_scope_violation" if any findings
    enforce      → one of:
                     - "ask_user"        when any finding is severe
                                         (protected / never_touch). Destructive
                                         recovery ALWAYS asks the user — the
                                         mode policy never emits immediate
                                         kill/restart.
                     - "deny_next_write" when all findings are warn-only
                                         (out_of_scope).

This module is shaped so ticket 06's `action_executor` can take this list and
turn it into durable `actions` rows + Telegram delivery + adapter calls
without changing the contract above.
"""
from __future__ import annotations
from typing import Any, Iterable


# Kinds the policy may emit. Kept here so ticket 06 has one source of truth.
ACTION_KINDS = ("recommend_scope_violation", "ask_user", "deny_next_write")

# Kinds the policy MUST NOT emit even in enforce — they're destructive and
# can only be initiated after explicit user approval (PRD §11 P8).
FORBIDDEN_KINDS_EVEN_IN_ENFORCE = ("kill", "restart", "execute_deny")


_SEVERITY_RANK = {"info": 0, "warn": 1, "severe": 2}


def _max_severity(findings: list[dict[str, Any]]) -> str:
    return max(
        (f.get("severity", "info") for f in findings),
        key=lambda s: _SEVERITY_RANK.get(s, 0),
        default="info",
    )


def _has_severe(findings: list[dict[str, Any]]) -> bool:
    return any(f.get("severity") == "severe" for f in findings)


def _summary(findings: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for f in findings:
        c = f.get("classification", "unknown")
        counts[c] = counts.get(c, 0) + 1
    parts = [f"{n} {c.replace('_', '-')}" for c, n in counts.items()]
    return ", ".join(parts) + " write(s)"


def _evidence_event_ids(findings: list[dict[str, Any]]) -> list[int]:
    return [f["event_id"] for f in findings if f.get("event_id") is not None]


def propose_actions(
    findings: Iterable[dict[str, Any]],
    modes: dict[str, Any],
    *,
    run_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    findings_list = [f for f in findings]
    if not findings_list:
        return []

    mode = modes.get("drift_l1_l3", "off")
    if mode in ("off", "shadow"):
        return []

    evidence = _evidence_event_ids(findings_list)
    severity = _max_severity(findings_list)
    summary  = _summary(findings_list)

    if mode == "advise":
        return [{
            "kind":               "recommend_scope_violation",
            "status":             "would_recommend",
            "run_id":             run_id,
            "session_id":         session_id,
            "evidence_event_ids": evidence,
            "severity":           severity,
            "summary":            summary,
        }]

    if mode == "enforce":
        if _has_severe(findings_list):
            return [{
                "kind":               "ask_user",
                "status":             "would_ask",
                "run_id":             run_id,
                "session_id":         session_id,
                "evidence_event_ids": evidence,
                "severity":           severity,
                "summary":            summary,
                "options": ["Re-anchor", "Allow scope expansion", "Kill run"],
            }]
        return [{
            "kind":               "deny_next_write",
            "status":             "would_deny_next_write",
            "run_id":             run_id,
            "session_id":         session_id,
            "evidence_event_ids": evidence,
            "severity":           severity,
            "summary":            summary,
        }]

    return []
