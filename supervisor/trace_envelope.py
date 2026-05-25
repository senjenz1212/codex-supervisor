"""Universal trace envelope helpers for supervisor ledger events."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .failure_taxonomy import failure_taxonomy_for_payload


TRACE_ENVELOPE_SCHEMA_VERSION = "dual-agent-trace-envelope/v1"


def stamp_trace_envelope(
    *,
    run_id: str,
    source: str,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a payload copy with a non-breaking trace envelope attached."""
    stamped = deepcopy(payload)
    if "trace_envelope" in stamped:
        return stamped
    if source != "dual_agent" and not kind.startswith(("dual_agent_", "tri_agent_")):
        return stamped

    gate = _text(stamped.get("gate"))
    task_id = _text(stamped.get("task_id"))
    failure_taxonomy = failure_taxonomy_for_payload(kind=kind, payload=stamped)
    stamped["trace_envelope"] = {
        "schema_version": TRACE_ENVELOPE_SCHEMA_VERSION,
        "run_id": run_id,
        "task_id": task_id,
        "gate": gate,
        "source": source,
        "event_kind": kind,
        "policy_verdict": _policy_verdict(stamped, failure_taxonomy),
        "failure_taxonomy": failure_taxonomy,
        "tool_calls": _tool_calls(stamped),
        "artifacts": _artifacts(stamped),
        "claims": _claims(stamped),
        "receipts": _receipts(stamped),
    }
    return stamped


def _policy_verdict(payload: dict[str, Any], failure_taxonomy: dict[str, Any] | None) -> str:
    status = _text(payload.get("status")).lower()
    if status in {"accepted", "blocked", "failed", "rejected"}:
        return status
    if failure_taxonomy is not None:
        return "blocked"
    milestone = _text(payload.get("milestone")).lower()
    if milestone:
        return f"milestone:{milestone}"
    return "observed"


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct = payload.get("tool_calls")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    calls = metadata.get("tool_calls") if isinstance(metadata, dict) else None
    return [item for item in calls if isinstance(item, dict)] if isinstance(calls, list) else []


def _artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        return [item for item in artifacts if isinstance(item, dict)]
    if isinstance(artifacts, tuple):
        return [item for item in artifacts if isinstance(item, dict)]
    return []


def _claims(payload: dict[str, Any]) -> list[str]:
    claims: list[str] = []
    direct = payload.get("claims")
    if isinstance(direct, (list, tuple)):
        claims.extend(str(item) for item in direct if str(item).strip())
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    outcome_claims = outcome.get("claims")
    if isinstance(outcome_claims, list):
        claims.extend(str(item) for item in outcome_claims if str(item).strip())
    return claims


def _receipts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    receipts = payload.get("tool_receipts")
    if isinstance(receipts, list):
        return [item for item in receipts if isinstance(item, dict)]
    if isinstance(receipts, tuple):
        return [item for item in receipts if isinstance(item, dict)]
    return []


def _text(value: Any) -> str:
    return str(value or "").strip()
