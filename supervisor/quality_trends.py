"""Ledger-derived quality trend metrics for supervised workflows."""
from __future__ import annotations

import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .runtime_evidence import RuntimeEvidenceResult, capture_runtime_baseline, collect_runtime_evidence

ACCEPTED_STATUSES = {"accepted", "accept"}
AUDIT_GATES = ("execution", "outcome_review")


def record_quality_trends_for_run(
    state: Any,
    *,
    run_id: str,
    task_id: str | None = None,
    task_class: str | None = None,
    computed_at: int | None = None,
) -> list[dict[str, Any]]:
    """Compute and persist per-gate trend rows from existing ledger events."""
    events = _decode_events(state.read_dual_agent_gate_events(run_id))
    if not events:
        return []

    route = _latest_payload(events, "dual_agent_workflow_route")
    resolved_task_id = _resolve_task_id(events, explicit=task_id, route=route)
    resolved_task_class = _resolve_task_class(route, explicit=task_class)
    now = int(time.time()) if computed_at is None else int(computed_at)

    by_gate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["kind"] != "dual_agent_gate_result":
            continue
        gate = str(event["payload"].get("gate") or "unknown")
        by_gate[gate].append(event)

    rows: list[dict[str, Any]] = []
    for gate, gate_events in sorted(by_gate.items()):
        if not gate_events:
            continue
        latest_accepted_event = next(
            (event for event in reversed(gate_events) if _payload_accepted(event["payload"])),
            None,
        )
        first_event = gate_events[0]
        final_event = gate_events[-1]
        accepted_event = (
            latest_accepted_event
            if latest_accepted_event is not None
            and latest_accepted_event["event_id"] == final_event["event_id"]
            else None
        )
        accepted = accepted_event is not None
        accepted_attempts = _attempts(accepted_event["payload"]) if accepted_event else 0
        prior_non_accepts = [
            event for event in gate_events
            if accepted_event is not None
            and event["event_id"] < accepted_event["event_id"]
            and not _payload_accepted(event["payload"])
        ]
        first_pass_accepted = bool(
            accepted
            and not prior_non_accepts
            and accepted_attempts <= 1
        )
        revision_rounds = _revision_rounds(gate_events, accepted_event)
        time_to_accept_s = (
            max(0, int(accepted_event["ts"]) - int(first_event["ts"]))
            if accepted_event is not None else None
        )
        rows.append(state.upsert_quality_trend_row(
            run_id=run_id,
            task_id=resolved_task_id,
            task_class=resolved_task_class,
            gate=gate,
            accepted=accepted,
            first_pass_accepted=first_pass_accepted,
            revision_rounds=revision_rounds,
            time_to_accepted_outcome_s=(
                float(time_to_accept_s) if time_to_accept_s is not None else None
            ),
            details={
                "source": "ledger_events",
                "metric_semantics": "observational_only",
                "gate_result_event_ids": [event["event_id"] for event in gate_events],
                "first_gate_result_event_id": first_event["event_id"],
                "accepted_gate_result_event_id": (
                    accepted_event["event_id"] if accepted_event is not None else None
                ),
                "accepted_attempts": accepted_attempts,
                "time_to_accepted_outcome": time_to_accept_s,
            },
            computed_at=now,
        ))
    return rows


def run_sampled_p11_false_accept_audit(
    state: Any,
    *,
    run_id: str,
    task_id: str | None = None,
    task_class: str | None = None,
    sample_size: int = 10,
    gates: tuple[str, ...] = AUDIT_GATES,
    test_timeout_s: int = 120,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Re-verify sampled accepted execution/outcome receipts against current git state."""
    trend_rows = record_quality_trends_for_run(
        state,
        run_id=run_id,
        task_id=task_id,
        task_class=task_class,
    )
    events = _decode_events(state.read_dual_agent_gate_events(run_id))
    route = _latest_payload(events, "dual_agent_workflow_route")
    resolved_task_id = _resolve_task_id(events, explicit=task_id, route=route)
    cwd = _workflow_cwd(state, run_id=run_id, task_id=resolved_task_id, route=route)

    accepted_gate_events = [
        event for event in events
        if event["kind"] == "dual_agent_gate_result"
        and str(event["payload"].get("gate") or "") in gates
        and _payload_accepted(event["payload"])
    ][:max(0, int(sample_size))]

    audited: list[dict[str, Any]] = []
    by_gate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in accepted_gate_events:
        payload = event["payload"]
        gate = str(payload.get("gate") or "unknown")
        baseline = _runtime_baseline_for_gate(events, gate=gate) or capture_runtime_baseline(
            cwd,
            runner=runner,
        )
        outcome_payload = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
        result = collect_runtime_evidence(
            cwd=cwd,
            task_id=resolved_task_id,
            run_id=run_id,
            gate=gate,
            round_index=_attempts(payload) or 1,
            baseline=baseline,
            outcome_payload=outcome_payload,
            test_timeout_s=test_timeout_s,
            runner=runner,
        )
        item = _audit_item(event, result)
        audited.append(item)
        by_gate[gate].append(item)

    updated_rows: list[dict[str, Any]] = []
    for gate, items in sorted(by_gate.items()):
        false_accepts = [item for item in items if item["false_accept"]]
        updated = state.update_quality_trend_audit(
            run_id=run_id,
            gate=gate,
            sample_size=len(items),
            false_accept_count=len(false_accepts),
            false_accept_denominator=len(items),
            audit_details={
                "source": "sampled_p11_audit",
                "observational_only": True,
                "sample_event_ids": [item["event_id"] for item in items],
                "false_accept_event_ids": [item["event_id"] for item in false_accepts],
                "items": items,
            },
        )
        if updated is not None:
            updated_rows.append(updated)

    denominator = len(audited)
    false_accept_count = sum(1 for item in audited if item["false_accept"])
    return {
        "run_id": run_id,
        "task_id": resolved_task_id,
        "cwd": str(cwd),
        "sample_size": denominator,
        "false_accept_count": false_accept_count,
        "false_accept_denominator": denominator,
        "false_accept_rate": false_accept_count / denominator if denominator else 0.0,
        "audited": audited,
        "updated_trend_rows": updated_rows,
        "trend_rows_recorded": trend_rows,
        "observational_only": True,
        "gate_authority": "unchanged",
    }


def query_quality_trends(
    state: Any,
    *,
    task_class: str | None = None,
    gate: str | None = None,
) -> list[dict[str, Any]]:
    """Read-only trend query wrapper."""
    return state.query_quality_trends(task_class=task_class, gate=gate)


def _decode_events(rows: list[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        payload_json = row["payload_json"] if isinstance(row, dict) else row["payload_json"]
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        events.append({
            "event_id": int(row["event_id"]),
            "ts": int(row["ts"]),
            "kind": str(row["kind"]),
            "payload": payload if isinstance(payload, dict) else {},
        })
    return events


def _latest_payload(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    for event in reversed(events):
        if event["kind"] == kind:
            return event["payload"]
    return {}


def _resolve_task_id(
    events: list[dict[str, Any]],
    *,
    explicit: str | None,
    route: dict[str, Any],
) -> str:
    if explicit:
        return explicit
    if route.get("task_id"):
        return str(route["task_id"])
    for event in events:
        task_id = event["payload"].get("task_id")
        if task_id:
            return str(task_id)
    return "unknown-task"


def _resolve_task_class(route: dict[str, Any], *, explicit: str | None) -> str:
    if explicit:
        return explicit
    for key in ("lesson_task_class", "task_class", "dynamic_workflow_task_class", "task_complexity"):
        value = route.get(key)
        if value:
            return str(value)
    return "unclassified"


def _payload_accepted(payload: dict[str, Any]) -> bool:
    for key in ("supervisor_final_status", "status", "claude_gate_status"):
        value = str(payload.get(key) or "").strip().lower()
        if value:
            return value in ACCEPTED_STATUSES
    return False


def _attempts(payload: dict[str, Any]) -> int:
    try:
        return max(0, int(payload.get("attempts") or 0))
    except (TypeError, ValueError):
        return 0


def _revision_rounds(
    gate_events: list[dict[str, Any]],
    accepted_event: dict[str, Any] | None,
) -> int:
    if accepted_event is None:
        return len(gate_events)
    prior_non_accepts = [
        event for event in gate_events
        if event["event_id"] < accepted_event["event_id"]
        and not _payload_accepted(event["payload"])
    ]
    return len(prior_non_accepts) + max(0, _attempts(accepted_event["payload"]) - 1)


def _workflow_cwd(
    state: Any,
    *,
    run_id: str,
    task_id: str,
    route: dict[str, Any],
) -> Path:
    workflow = None
    get_workflow = getattr(state, "get_dual_agent_workflow", None)
    if get_workflow is not None:
        workflow = get_workflow(run_id=run_id, task_id=task_id)
    workflow_cwd = _row_get(workflow, "cwd") if workflow is not None else None
    if workflow_cwd:
        return Path(str(workflow_cwd)).expanduser().resolve()
    if route.get("cwd"):
        return Path(str(route["cwd"])).expanduser().resolve()
    return Path.cwd().resolve()


def _row_get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        get = getattr(row, "get", None)
        return get(key) if get is not None else None


def _runtime_baseline_for_gate(
    events: list[dict[str, Any]],
    *,
    gate: str,
) -> dict[str, Any] | None:
    for event in reversed(events):
        payload = event["payload"]
        if event["kind"] != "dual_agent_runtime_evidence":
            continue
        if str(payload.get("gate") or "") != gate:
            continue
        probe = payload.get("probe") if isinstance(payload.get("probe"), dict) else {}
        details = probe.get("details") if isinstance(probe.get("details"), dict) else {}
        baseline = details.get("baseline") if isinstance(details.get("baseline"), dict) else None
        if baseline:
            return baseline
    return None


def _audit_item(event: dict[str, Any], result: RuntimeEvidenceResult) -> dict[str, Any]:
    probe = result.probe
    false_accept = probe.status != "green"
    details = probe.details if isinstance(probe.details, dict) else {}
    return {
        "event_id": event["event_id"],
        "gate": str(event["payload"].get("gate") or "unknown"),
        "false_accept": false_accept,
        "probe_id": probe.probe_id,
        "probe_status": probe.status,
        "probe_reason": probe.reason,
        "failures": list(details.get("failures") or []),
        "declared_changed_files": list(details.get("declared_changed_files") or []),
        "actual_changed_files": list(details.get("actual_changed_files") or []),
    }
