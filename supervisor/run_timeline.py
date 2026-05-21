"""Build user-facing run timeline cards from normalized rollout events."""
from __future__ import annotations

import json
import re
from typing import Any

from .redaction import redact


SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
PR_RE = re.compile(r"\bPR\s*#?(\d+)\b|\bpull/(\d+)\b", re.IGNORECASE)
CHECK_RE = re.compile(
    r"`?([A-Za-z0-9_.-]*(?:smoke|freshness|review|check|ci)[A-Za-z0-9_.-]*)`?"
    r"\s+(passed|failed|queued|skipped|in[_ -]progress|success|green)",
    re.IGNORECASE,
)
PASSED_TEST_RE = re.compile(r"\b(\d+)\s+passed\b", re.IGNORECASE)
APPROVED_SHA_RE = re.compile(
    r"\b(?:approved|approval|head)\s+(?:sha|ref|oid)\s*[:=]?\s*`?([0-9a-f]{40})`?",
    re.IGNORECASE,
)
MERGE_COMMIT_RE = re.compile(
    r"\bmerge\s+commit\s*[:=]?\s*`?([0-9a-f]{40})`?",
    re.IGNORECASE,
)
PR_STATE_RE = re.compile(r"\bPR\s+state\s*[:=]\s*`?([A-Z_]+)`?", re.IGNORECASE)


NOISE_EVENT_KINDS = {"state.stalled", "state.healthy", "state.hooks_broken"}


def build_run_timeline(
    *,
    run: dict[str, Any],
    events: list[dict[str, Any]],
    max_items: int = 8,
) -> dict[str, Any]:
    """Return compact, high-signal event cards for Telegram monitoring."""
    cards: list[dict[str, Any]] = []
    monitor_state: dict[str, Any] | None = None
    facts: dict[str, Any] = {
        "prs": [],
        "shas": [],
        "approved_shas": [],
        "head_shas": [],
        "merge_commit_shas": [],
        "checks": [],
        "check_statuses": [],
        "test_counts": [],
        "pr_states": [],
        "mergeable": [],
    }

    for ev in events:
        if str(ev.get("kind", "")).startswith("state."):
            monitor_state = {
                "event_id": ev.get("id"),
                "state": ev.get("kind", "").removeprefix("state."),
                "previous": ev.get("prev"),
            }
            continue

        card = _event_card(ev)
        if card is None:
            continue
        _merge_facts(facts, card.get("signals", {}))
        cards.append(card)

    cards = cards[-max_items:]
    final_facts = _finalize_facts(facts)
    return redact({
        "run": _public_run(run),
        "monitor_state": monitor_state,
        "facts": final_facts,
        "outcome_card": _build_outcome_card(
            run=_public_run(run),
            facts=final_facts,
            monitor_state=monitor_state,
            cards=cards,
        ),
        "events": cards,
        "format_hint": (
            "Use these cards to answer with outcome-shaped bullets: current "
            "state, latest completion/final answer, PR/SHA/check status when "
            "present, tests, changed files, and exact approval token if present."
        ),
    })


def _event_card(ev: dict[str, Any]) -> dict[str, Any] | None:
    kind = ev.get("kind")
    if kind in NOISE_EVENT_KINDS:
        return None
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    payload_type = payload.get("type")

    if kind == "event_msg" and payload_type == "task_complete":
        text = str(payload.get("last_agent_message") or "")
        return {
            "event_id": ev.get("id"),
            "timestamp": ev.get("timestamp"),
            "type": "task_complete",
            "turn_id": payload.get("turn_id"),
            "duration_ms": payload.get("duration_ms"),
            "text": _clip(text, 1800),
            "signals": _signals(text),
        }

    if kind == "event_msg" and payload_type == "agent_message":
        text = str(payload.get("message") or "")
        phase = payload.get("phase")
        if phase not in {"final_answer", "commentary", None}:
            return None
        return {
            "event_id": ev.get("id"),
            "timestamp": ev.get("timestamp"),
            "type": "agent_message",
            "phase": phase,
            "text": _clip(text, 1200),
            "signals": _signals(text),
        }

    if kind == "response_item":
        response_payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        rtype = response_payload.get("type")
        if rtype == "message":
            text = _message_text(response_payload)
            if not text:
                return None
            return {
                "event_id": ev.get("id"),
                "timestamp": ev.get("timestamp"),
                "type": "assistant_message",
                "phase": response_payload.get("phase"),
                "text": _clip(text, 1200),
                "signals": _signals(text),
            }
        if rtype == "function_call_output":
            output = str(response_payload.get("output") or "")
            signals = _signals(output)
            if not _is_useful_output(output, signals):
                return None
            return {
                "event_id": ev.get("id"),
                "timestamp": ev.get("timestamp"),
                "type": "tool_output",
                "call_id": response_payload.get("call_id"),
                "text": _clip(_clean_tool_output(output), 1200),
                "signals": signals,
            }

    return None


def _message_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts).strip()


def _signals(text: str) -> dict[str, Any]:
    prs = []
    for m in PR_RE.finditer(text):
        value = m.group(1) or m.group(2)
        if value and value not in prs:
            prs.append(value)
    json_signals = _json_signals(_load_jsonish(text))
    for value in json_signals["prs"]:
        if value not in prs:
            prs.append(value)
    shas = _dedupe(SHA_RE.findall(text))
    checks: list[str] = []
    check_statuses: list[dict[str, str]] = []
    for m in CHECK_RE.finditer(text):
        name = _normalize_check_name(m.group(1))
        if _is_generic_check_name(name):
            continue
        status = _normalize_check_status(m.group(2))
        check = f"{name} {_human_check_status(status)}"
        if check not in checks:
            checks.append(check)
        entry = {"name": name, "status": status}
        if entry not in check_statuses:
            check_statuses.append(entry)
    test_counts = _dedupe(m.group(0).strip() for m in PASSED_TEST_RE.finditer(text))
    approved_shas = _dedupe(APPROVED_SHA_RE.findall(text))
    merge_commit_shas = _dedupe(MERGE_COMMIT_RE.findall(text))
    head_shas = list(approved_shas)
    pr_states = _dedupe(m.group(1).upper() for m in PR_STATE_RE.finditer(text))
    for key in ("shas", "head_shas", "approved_shas", "merge_commit_shas",
                "checks", "check_statuses", "pr_states", "mergeable"):
        for value in json_signals[key]:
            target = locals().get(key)
            if isinstance(target, list) and value not in target:
                target.append(value)
    token = None
    token_sha = _choose_approval_sha({
        "approved_shas": approved_shas,
        "head_shas": head_shas,
        "shas": shas,
        "merge_commit_shas": merge_commit_shas,
    })
    if prs and token_sha and re.search(r"\b(?:green|passed|mergeable|open|ready)\b", text, re.I):
        token = f"[approved PR#{prs[0]} {token_sha}]"
    return {
        "prs": prs,
        "shas": shas,
        "approved_shas": approved_shas,
        "head_shas": head_shas,
        "merge_commit_shas": merge_commit_shas,
        "checks": checks,
        "check_statuses": check_statuses,
        "test_counts": test_counts,
        "pr_states": pr_states,
        "mergeable": json_signals["mergeable"],
        "approval_token": token,
    }


def _merge_facts(facts: dict[str, list], signals: dict[str, Any]) -> None:
    for key in ("prs", "shas", "approved_shas", "head_shas",
                "merge_commit_shas", "checks", "check_statuses",
                "test_counts", "pr_states", "mergeable"):
        facts[key].extend(signals[key])


def _finalize_facts(facts: dict[str, list]) -> dict[str, Any]:
    out = {k: _dedupe(v) for k, v in facts.items()}
    out["check_statuses"] = _latest_check_statuses(out["check_statuses"])
    out["checks"] = [
        f"{c['name']} {_human_check_status(c['status'])}"
        for c in out["check_statuses"]
    ]
    out["primary_pr"] = out["prs"][0] if out["prs"] else None
    out["approval_sha"] = _choose_approval_sha(out)
    out["merge_commit_sha"] = (
        out["merge_commit_shas"][0] if out["merge_commit_shas"] else None
    )
    out["pr_state"] = out["pr_states"][-1] if out["pr_states"] else None
    out["all_checks_green"] = _all_checks_green(out["check_statuses"])
    approval_token = None
    if out["primary_pr"] and out["approval_sha"]:
        approval_token = f"[approved PR#{out['primary_pr']} {out['approval_sha']}]"
    out["approval_token_candidate"] = approval_token
    return out


def _build_outcome_card(
    *,
    run: dict[str, Any],
    facts: dict[str, Any],
    monitor_state: dict[str, Any] | None,
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    primary_pr = facts.get("primary_pr")
    pr_state = (facts.get("pr_state") or "").lower()
    if primary_pr and pr_state == "merged":
        headline = f"PR #{primary_pr} is merged."
    elif primary_pr and facts.get("all_checks_green"):
        headline = f"PR #{primary_pr} is green now."
    elif primary_pr:
        headline = f"PR #{primary_pr} has recent activity."
    elif cards:
        headline = "Latest supervised run activity is available."
    else:
        headline = "No high-signal run activity found."
    return {
        "headline": headline,
        "current_state": (
            (monitor_state or {}).get("state")
            or run.get("status")
            or "unknown"
        ),
        "latest_event_id": cards[-1]["event_id"] if cards else None,
        "pr": primary_pr,
        "head_or_approved_sha": facts.get("approval_sha"),
        "merge_commit_sha": facts.get("merge_commit_sha"),
        "checks": facts.get("checks") or [],
        "tests": facts.get("test_counts") or [],
        "approval_token": facts.get("approval_token_candidate"),
    }


def _is_useful_output(output: str, signals: dict[str, Any]) -> bool:
    if any(signals.get(k) for k in (
        "prs", "shas", "checks", "check_statuses", "test_counts", "pr_states"
    )):
        return True
    lower = output.lower()
    return any(term in lower for term in ("merged", "mergeable", "passed", "failed", "queued"))


def _clean_tool_output(output: str) -> str:
    lines = output.splitlines()
    useful: list[str] = []
    for line in lines:
        if line.startswith(("Chunk ID:", "Wall time:", "Process exited", "Original token count:", "Output:")):
            continue
        useful.append(line)
    return "\n".join(useful).strip() or output.strip()


def _load_jsonish(text: str) -> Any:
    cleaned = _clean_tool_output(text)
    for candidate in (cleaned, _substring_json_object(cleaned)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _substring_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def _json_signals(value: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "prs": [],
        "shas": [],
        "approved_shas": [],
        "head_shas": [],
        "merge_commit_shas": [],
        "checks": [],
        "check_statuses": [],
        "pr_states": [],
        "mergeable": [],
    }
    if not isinstance(value, dict):
        return out

    number = value.get("number")
    if number is not None:
        out["prs"].append(str(number))
    url = str(value.get("url") or "")
    for m in PR_RE.finditer(url):
        pr = m.group(1) or m.group(2)
        if pr:
            out["prs"].append(str(pr))

    head_sha = value.get("headRefOid") or value.get("head_sha") or value.get("headSha")
    if isinstance(head_sha, str) and SHA_RE.fullmatch(head_sha):
        out["head_shas"].append(head_sha)
        out["approved_shas"].append(head_sha)
        out["shas"].append(head_sha)

    merge_commit = value.get("mergeCommit")
    if isinstance(merge_commit, dict):
        merge_sha = merge_commit.get("oid")
        if isinstance(merge_sha, str) and SHA_RE.fullmatch(merge_sha):
            out["merge_commit_shas"].append(merge_sha)
            out["shas"].append(merge_sha)

    state = value.get("state")
    if isinstance(state, str):
        out["pr_states"].append(state.upper())
    mergeable = value.get("mergeable")
    if isinstance(mergeable, str):
        out["mergeable"].append(mergeable.upper())

    checks = value.get("statusCheckRollup") or value.get("checks") or []
    if isinstance(checks, dict):
        checks = checks.get("nodes") or checks.get("edges") or []
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, dict) and "node" in item and isinstance(item["node"], dict):
                item = item["node"]
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("context")
            if not isinstance(name, str) or not name:
                continue
            name = _normalize_check_name(name)
            if _is_generic_check_name(name):
                continue
            status = _status_from_check_dict(item)
            entry = {"name": name, "status": status}
            out["check_statuses"].append(entry)
            out["checks"].append(f"{name} {_human_check_status(status)}")
    return out


def _status_from_check_dict(check: dict[str, Any]) -> str:
    conclusion = str(check.get("conclusion") or "").upper()
    status = str(check.get("status") or "").upper()
    if conclusion in {"SUCCESS", "PASSED"}:
        return "passed"
    if conclusion in {"SKIPPED", "NEUTRAL"}:
        return "skipped"
    if conclusion in {"FAILURE", "FAILED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "CANCELLED"}:
        return "failed"
    if status in {"QUEUED", "PENDING", "REQUESTED"}:
        return "queued"
    if status in {"IN_PROGRESS", "RUNNING"}:
        return "in_progress"
    if status == "COMPLETED":
        return "completed"
    return status.lower() or "unknown"


def _normalize_check_status(raw: str) -> str:
    status = raw.lower().replace("-", "_").replace(" ", "_")
    if status in {"success", "green"}:
        return "passed"
    if status == "inprogress":
        return "in_progress"
    return status


def _human_check_status(status: str) -> str:
    return "in progress" if status == "in_progress" else status


def _normalize_check_name(name: str) -> str:
    # GitHub check names sometimes carry policy hints in parens; the status
    # card should surface the stable check identity.
    return re.sub(r"\s+\([^)]*\)$", "", name).strip()


def _is_generic_check_name(name: str) -> bool:
    return name.lower() in {"check", "checks", "ci"}


def _latest_check_statuses(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the latest status per check name in latest-observation order."""
    order: list[str] = []
    latest: dict[str, dict[str, str]] = {}
    for check in checks:
        name = check.get("name")
        status = check.get("status")
        if not name or not status:
            continue
        if name in order:
            order.remove(name)
        order.append(name)
        latest[name] = {"name": name, "status": status}
    rows = [latest[name] for name in order]
    return sorted(rows, key=lambda c: _check_sort_key(c["name"], order))


def _check_sort_key(name: str, observed_order: list[str]) -> tuple[int, int, str]:
    lower = name.lower()
    preferred = (
        ("backend", 0),
        ("frontend", 1),
        ("cassette", 2),
        ("cross-model", 3),
    )
    for token, rank in preferred:
        if token in lower:
            return (0, rank, lower)
    return (1, observed_order.index(name), lower)


def _choose_approval_sha(facts: dict[str, Any]) -> str | None:
    for key in ("approved_shas", "head_shas"):
        values = facts.get(key) or []
        if values:
            return values[0]
    merge_shas = set(facts.get("merge_commit_shas") or [])
    for sha in facts.get("shas") or []:
        if sha not in merge_shas:
            return sha
    shas = facts.get("shas") or []
    return shas[0] if shas else None


def _all_checks_green(checks: list[dict[str, str]]) -> bool:
    if not checks:
        return False
    statuses = {c.get("status") for c in checks}
    return bool(statuses & {"passed"}) and statuses <= {"passed", "skipped"}


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "session_id": run.get("session_id"),
        "task": run.get("task"),
        "status": run.get("status"),
        "rollout_path": run.get("rollout_path"),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
    }


def _clip(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def _dedupe(values) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
