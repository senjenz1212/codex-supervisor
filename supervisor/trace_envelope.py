"""Universal trace envelope helpers for supervisor ledger events."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import time
from typing import Any, Callable, Iterator

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
        envelope = stamped.get("trace_envelope")
        if isinstance(envelope, dict):
            tool_calls = envelope.get("tool_calls")
            if isinstance(tool_calls, list):
                envelope["tool_calls"] = [
                    ensure_tool_call_timing(item)
                    for item in tool_calls
                    if isinstance(item, dict)
                ]
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
    if failure_taxonomy is not None:
        return "blocked"
    if status in {"accepted", "blocked", "failed", "rejected"}:
        return status
    milestone = _text(payload.get("milestone")).lower()
    if milestone:
        return f"milestone:{milestone}"
    return "observed"


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct = payload.get("tool_calls")
    if isinstance(direct, list):
        return [ensure_tool_call_timing(item) for item in direct if isinstance(item, dict)]
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    calls = metadata.get("tool_calls") if isinstance(metadata, dict) else None
    return [ensure_tool_call_timing(item) for item in calls if isinstance(item, dict)] if isinstance(calls, list) else []


@contextmanager
def timed_tool_call(
    name: str,
    *,
    wall_clock_ms: Callable[[], int] | None = None,
    monotonic_ns: Callable[[], int] | None = None,
    **extra: Any,
) -> Iterator[dict[str, Any]]:
    """Yield a trace tool-call record and stamp timing on exit."""
    wall = wall_clock_ms or _current_time_ms
    monotonic = monotonic_ns or time.monotonic_ns
    started_at_ms = int(wall())
    started_ns = int(monotonic())
    record: dict[str, Any] = {
        "name": name,
        "started_at_ms": started_at_ms,
        **extra,
    }
    try:
        yield record
    except BaseException as exc:
        record["status"] = "error"
        record["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        duration_ms = max(0, (int(monotonic()) - started_ns) // 1_000_000)
        record["duration_ms"] = duration_ms
        record["ended_at_ms"] = started_at_ms + duration_ms


def ensure_tool_call_timing(call: dict[str, Any]) -> dict[str, Any]:
    """Return a tool-call record with the standard timing fields present."""
    record = dict(call)
    started = _int_or_none(record.get("started_at_ms"))
    duration = _int_or_none(record.get("duration_ms"))
    ended = _int_or_none(record.get("ended_at_ms"))
    if started is None and ended is not None and duration is not None:
        started = max(0, ended - duration)
    if started is None:
        started = _current_time_ms()
    if duration is None and ended is not None:
        duration = max(0, ended - started)
    if duration is None:
        duration = 0
    if ended is None:
        ended = started + duration
    record["started_at_ms"] = int(started)
    record["duration_ms"] = int(duration)
    record["ended_at_ms"] = int(ended)
    return record


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


def _current_time_ms() -> int:
    return int(time.time() * 1000)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
