"""Ledger-derived quality trend metrics for supervised workflows."""
from __future__ import annotations

import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .policy_overlay import draft_policy_regression_rollbacks_for_trend_rows
from .runtime_evidence import RuntimeEvidenceResult, capture_runtime_baseline, collect_runtime_evidence

ACCEPTED_STATUSES = {"accepted", "accept"}
AUDIT_GATES = ("execution", "outcome_review")
AXI_CUTOVER_TS = 1781049600


def record_transport_incident(
    state: Any,
    *,
    run_id: str,
    incident_type: str,
    interface: str,
    task_id: str | None = None,
    job_id: str | None = None,
    client_token: str | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Write an observational transport incident event.

    These events are metrics only. They do not advance or block gates.
    """
    import hashlib

    token_hash = (
        hashlib.sha256(str(client_token).encode("utf-8")).hexdigest()
        if client_token else ""
    )
    return state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="transport_incident_observed",
        payload={
            "schema_version": "supervisor-transport-incident/v1",
            "incident_type": str(incident_type),
            "interface": str(interface),
            "task_id": str(task_id or ""),
            "job_id": str(job_id or ""),
            "run_id": str(run_id),
            "client_token_hash": token_hash,
            "details": details or {},
            "observational_only": True,
            "gate_authority": "unchanged",
        },
    )


def record_quality_trends_for_run(
    state: Any,
    *,
    run_id: str,
    task_id: str | None = None,
    task_class: str | None = None,
    computed_at: int | None = None,
) -> list[dict[str, Any]]:
    """Compute and persist per-gate trend rows from existing ledger events."""
    events = _decode_events(state.read_events_since(run_id, after_event_id=0, limit=10_000))
    if not events:
        return []

    route = _latest_payload(events, "dual_agent_workflow_route")
    resolved_task_id = _resolve_task_id(events, explicit=task_id, route=route)
    resolved_task_class = _resolve_task_class(route, explicit=task_class)
    overlays_by_gate = _overlay_snapshots_by_gate(events)
    transport_metrics = _transport_incident_metrics(events)
    format_metrics = _format_ab_metrics(events)
    visual_override_count = _visual_override_count(events)
    run_era = _run_era(events)
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
            policy_overlay_hash=str(overlays_by_gate.get(gate, {}).get("policy_overlay_hash") or ""),
            policy_proposal_id=str(overlays_by_gate.get(gate, {}).get("policy_proposal_id") or ""),
            details={
                "source": "ledger_events",
                "metric_semantics": "observational_only",
                "transport_incidents": {**transport_metrics, "run_era": run_era},
                "format_ab": format_metrics,
                "visual_evidence_override_count": visual_override_count,
                "policy_overlay": overlays_by_gate.get(gate, {}),
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
        item = _audit_item(
            event,
            result,
            runtime_floor_present=_runtime_floor_present_for_gate_event(events, event),
        )
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
    summaries = state.query_quality_trends(task_class=task_class, gate=gate)
    rows = state.list_quality_trend_rows(task_class=task_class, gate=gate)
    by_key: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_trend_rollup)
    for row in rows:
        key = (str(row["task_class"]), str(row["gate"]))
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        incidents = details.get("transport_incidents") if isinstance(details.get("transport_incidents"), dict) else {}
        by_key[key]["transport_incident_count"] += int(incidents.get("total_count") or 0)
        by_era = incidents.get("by_era") if isinstance(incidents.get("by_era"), dict) else {}
        for run_era in _row_run_eras(row, incidents):
            by_key[key]["transport_run_count_by_era"][run_era] += 1
        for era, count in by_era.items():
            by_key[key]["transport_incident_by_era"][str(era)] += int(count or 0)
        by_key[key]["visual_evidence_override_count"] += int(details.get("visual_evidence_override_count") or 0)
        fmt = details.get("format_ab") if isinstance(details.get("format_ab"), dict) else {}
        for format_name, prefix in (("toon", "format_toon"), ("json", "format_json")):
            item = fmt.get(format_name) if isinstance(fmt.get(format_name), dict) else {}
            by_key[key][f"{prefix}_turns"] += int(item.get("turns") or 0)
            by_key[key][f"{prefix}_bytes"] += int(item.get("bytes") or 0)
        _accumulate_false_accept_segments(state, row=row, details=details, rollup=by_key[key])
    enriched: list[dict[str, Any]] = []
    for summary in summaries:
        key = (str(summary["task_class"]), str(summary["gate"]))
        rollup = dict(by_key[key])
        era_counts = dict(sorted(rollup.pop("transport_incident_by_era").items()))
        total = int(rollup.get("transport_incident_count") or 0)
        rollup["transport_incident_by_era"] = era_counts
        rollup["transport_incident_mcp_count"] = int(era_counts.get("mcp") or 0)
        rollup["transport_incident_axi_count"] = int(era_counts.get("axi") or 0)
        run_counts = dict(sorted(rollup.pop("transport_run_count_by_era").items()))
        rollup["transport_run_count_by_era"] = run_counts
        mcp_runs = int(run_counts.get("mcp") or 0)
        axi_runs = int(run_counts.get("axi") or 0)
        rollup["transport_incident_mcp_rate"] = rollup["transport_incident_mcp_count"] / mcp_runs if mcp_runs else 0.0
        rollup["transport_incident_axi_rate"] = rollup["transport_incident_axi_count"] / axi_runs if axi_runs else 0.0
        rollup["transport_incident_mcp_share"] = (
            rollup["transport_incident_mcp_count"] / total if total else 0.0
        )
        rollup["transport_incident_axi_share"] = (
            rollup["transport_incident_axi_count"] / total if total else 0.0
        )
        _finalize_false_accept_segments(rollup)
        rollup.update(_decision_statuses(rollup))
        enriched.append({**summary, **rollup})
    return enriched


def _empty_trend_rollup() -> dict[str, Any]:
    return {
        "transport_incident_count": 0,
        "transport_incident_by_era": defaultdict(int),
        "transport_run_count_by_era": defaultdict(int),
        "transport_incident_mcp_count": 0,
        "transport_incident_axi_count": 0,
        "transport_incident_mcp_rate": 0.0,
        "transport_incident_axi_rate": 0.0,
        "transport_incident_mcp_share": 0.0,
        "transport_incident_axi_share": 0.0,
        "visual_evidence_override_count": 0,
        "format_toon_turns": 0,
        "format_json_turns": 0,
        "format_toon_bytes": 0,
        "format_json_bytes": 0,
        "false_accept_pre_floor_count": 0,
        "false_accept_pre_floor_denominator": 0,
        "false_accept_pre_floor_rate": 0.0,
        "false_accept_post_floor_count": 0,
        "false_accept_post_floor_denominator": 0,
        "false_accept_post_floor_rate": 0.0,
        "false_accept_unknown_floor_count": 0,
        "false_accept_unknown_floor_denominator": 0,
        "false_accept_unknown_floor_rate": 0.0,
    }


def _accumulate_false_accept_segments(
    state: Any,
    *,
    row: dict[str, Any],
    details: dict[str, Any],
    rollup: dict[str, Any],
) -> None:
    audit = details.get("p11_audit") if isinstance(details.get("p11_audit"), dict) else {}
    items = audit.get("items") if isinstance(audit.get("items"), list) else []
    row_denominator = int(row.get("false_accept_denominator") or 0)
    row_false_accepts = int(row.get("false_accept_count") or 0)
    if not items:
        if row_denominator:
            rollup["false_accept_unknown_floor_denominator"] += row_denominator
            rollup["false_accept_unknown_floor_count"] += row_false_accepts
        return
    events: list[dict[str, Any]] | None = None
    events_by_id: dict[int, dict[str, Any]] = {}
    item_denominator = 0
    item_false_accepts = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_denominator += 1
        item_false_accepts += 1 if bool(item.get("false_accept")) else 0
        segment = _audit_item_floor_segment(item)
        if segment == "unknown_floor" and item.get("event_id") is not None:
            if events is None:
                events = _decode_events(state.read_dual_agent_gate_events(str(row["run_id"])))
                events_by_id = {int(event["event_id"]): event for event in events}
            event = events_by_id.get(int(item.get("event_id") or 0))
            if event is not None:
                segment = (
                    "post_floor"
                    if _runtime_floor_present_for_gate_event(events or [], event)
                    else "pre_floor"
                )
        rollup[f"false_accept_{segment}_denominator"] += 1
        if bool(item.get("false_accept")):
            rollup[f"false_accept_{segment}_count"] += 1
    if row_denominator > item_denominator:
        rollup["false_accept_unknown_floor_denominator"] += row_denominator - item_denominator
        rollup["false_accept_unknown_floor_count"] += max(0, row_false_accepts - item_false_accepts)


def _audit_item_floor_segment(item: dict[str, Any]) -> str:
    explicit = str(item.get("floor_segment") or "").strip()
    if explicit in {"pre_floor", "post_floor", "unknown_floor"}:
        return explicit
    if "runtime_floor_present" in item:
        return "post_floor" if bool(item.get("runtime_floor_present")) else "pre_floor"
    return "unknown_floor"


def _finalize_false_accept_segments(rollup: dict[str, Any]) -> None:
    for segment in ("pre_floor", "post_floor", "unknown_floor"):
        denominator = int(rollup.get(f"false_accept_{segment}_denominator") or 0)
        count = int(rollup.get(f"false_accept_{segment}_count") or 0)
        rollup[f"false_accept_{segment}_rate"] = count / denominator if denominator else 0.0


def _decision_statuses(rollup: dict[str, Any]) -> dict[str, str]:
    mcp_runs = int((rollup.get("transport_run_count_by_era") or {}).get("mcp") or 0)
    axi_runs = int((rollup.get("transport_run_count_by_era") or {}).get("axi") or 0)
    toon_turns = int(rollup.get("format_toon_turns") or 0)
    json_turns = int(rollup.get("format_json_turns") or 0)
    min_transport = 5
    min_format = 5
    transport_status = "ready"
    transport_reason = "sufficient mcp and axi run denominators"
    if mcp_runs < min_transport or axi_runs < min_transport:
        transport_status = "insufficient_data"
        transport_reason = f"need at least {min_transport} mcp and {min_transport} axi runs"
    format_status = "ready"
    format_reason = "sufficient toon and json samples"
    if toon_turns < min_format or json_turns < min_format:
        format_status = "insufficient_data"
        format_reason = f"need at least {min_format} toon and {min_format} json samples"
    return {
        "transport_decision_status": transport_status,
        "transport_decision_reason": transport_reason,
        "format_decision_status": format_status,
        "format_decision_reason": format_reason,
    }


def run_weekly_p11_audit_if_due(
    state: Any,
    *,
    run_id: str,
    task_id: str | None = None,
    task_class: str | None = None,
    now: int | None = None,
    cadence_s: int = 7 * 24 * 60 * 60,
    policy_regression_kwargs: dict[str, Any] | None = None,
    **audit_kwargs: Any,
) -> dict[str, Any]:
    """Run the sampled P11 audit at most once per cadence window."""
    timestamp = int(time.time()) if now is None else int(now)
    window_start = timestamp - max(1, int(cadence_s))
    existing = [
        event for event in state.read_events_since(run_id, after_event_id=0, limit=10_000)
        if event["kind"] == "supervisor_p11_audit_scheduled"
        and int(event["payload"].get("scheduled_at") or 0) >= window_start
    ]
    if existing:
        return {
            "status": "not_due",
            "last_event_id": existing[-1]["event_id"],
            "observational_only": True,
            "gate_authority": "unchanged",
        }
    audit = run_sampled_p11_false_accept_audit(
        state,
        run_id=run_id,
        task_id=task_id,
        task_class=task_class,
        **audit_kwargs,
    )
    try:
        audit = {
            **audit,
            "policy_regression_rollbacks": draft_policy_regression_rollbacks_for_trend_rows(
                state,
                run_id=run_id,
                trend_rows=list(audit.get("updated_trend_rows") or []),
                **(policy_regression_kwargs or {}),
            ),
        }
    except Exception as exc:
        audit = {
            **audit,
            "policy_regression_rollbacks": [{
                "status": "failed",
                "reason": type(exc).__name__,
                "message": str(exc),
                "observational_only": True,
                "gate_authority": "unchanged",
            }],
        }
    event_id = state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="supervisor_p11_audit_scheduled",
        payload={
            "schema_version": "supervisor-p11-audit-schedule/v1",
            "scheduled_at": timestamp,
            "audit": audit,
            "observational_only": True,
            "gate_authority": "unchanged",
        },
    )
    return {**audit, "status": "audited", "schedule_event_id": event_id}


def _decode_events(rows: list[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and "payload" in row:
            payload = row.get("payload")
        else:
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


def _overlay_snapshots_by_gate(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "supervisor_policy_overlay_snapshot":
            continue
        payload = event["payload"]
        gate = str(payload.get("gate") or "unknown")
        snapshots[gate] = {
            "schema_version": payload.get("schema_version"),
            "overlay_hash": str(payload.get("overlay_hash") or payload.get("policy_overlay_hash") or ""),
            "policy_overlay_hash": str(payload.get("policy_overlay_hash") or payload.get("overlay_hash") or ""),
            "proposal_id": str(payload.get("proposal_id") or payload.get("policy_proposal_id") or ""),
            "policy_proposal_id": str(payload.get("policy_proposal_id") or payload.get("proposal_id") or ""),
            "block_sha256": str(payload.get("block_sha256") or ""),
        }
    return snapshots


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


def _audit_item(
    event: dict[str, Any],
    result: RuntimeEvidenceResult,
    *,
    runtime_floor_present: bool,
) -> dict[str, Any]:
    probe = result.probe
    false_accept = probe.status != "green"
    details = probe.details if isinstance(probe.details, dict) else {}
    return {
        "event_id": event["event_id"],
        "gate": str(event["payload"].get("gate") or "unknown"),
        "false_accept": false_accept,
        "runtime_floor_present": runtime_floor_present,
        "floor_segment": "post_floor" if runtime_floor_present else "pre_floor",
        "probe_id": probe.probe_id,
        "probe_status": probe.status,
        "probe_reason": probe.reason,
        "failures": list(details.get("failures") or []),
        "declared_changed_files": list(details.get("declared_changed_files") or []),
        "actual_changed_files": list(details.get("actual_changed_files") or []),
    }


def _runtime_floor_present_for_gate_event(
    events: list[dict[str, Any]],
    gate_event: dict[str, Any],
) -> bool:
    gate_event_id = int(gate_event.get("event_id") or 0)
    payload = gate_event.get("payload") if isinstance(gate_event.get("payload"), dict) else {}
    gate = str(payload.get("gate") or "")
    round_index = _attempts(payload) or _int_or_zero(payload.get("round_index"))
    for event in reversed(events):
        if int(event.get("event_id") or 0) >= gate_event_id:
            continue
        if event.get("kind") != "dual_agent_runtime_evidence":
            continue
        runtime_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(runtime_payload.get("gate") or "") != gate:
            continue
        runtime_round = _int_or_zero(runtime_payload.get("round_index"))
        if round_index and runtime_round and runtime_round != round_index:
            continue
        receipts = runtime_payload.get("receipts") if isinstance(runtime_payload.get("receipts"), list) else []
        if any(_is_runtime_native_supervisor_receipt(receipt) for receipt in receipts):
            return True
    return False


def _is_runtime_native_supervisor_receipt(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    return (
        str(receipt.get("source") or "") == "supervisor"
        and str(receipt.get("evidence_grade") or "") == "runtime_native"
    )


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _transport_incident_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    by_interface: dict[str, int] = defaultdict(int)
    era_counts: dict[str, int] = defaultdict(int)
    for event in events:
        if event["kind"] == "transport_incident_observed":
            payload = event["payload"]
            incident_type = str(payload.get("incident_type") or "unknown")
            interface = str(payload.get("interface") or "unknown")
            by_type[incident_type] += 1
            by_interface[interface] += 1
            era_counts[_transport_era(event)] += 1
        elif event["kind"] == "dual_agent_workflow_job":
            payload = event["payload"]
            error = str(payload.get("error") or payload.get("reason") or "")
            if error == "worker_lease_stale_or_dead":
                by_type["dispatcher_lease_reap"] += 1
                by_interface[str(payload.get("interface") or "dispatcher")] += 1
                era_counts[_transport_era(event)] += 1
    total = sum(by_type.values())
    return {
        "total_count": total,
        "by_type": dict(sorted(by_type.items())),
        "by_interface": dict(sorted(by_interface.items())),
        "by_era": dict(sorted(era_counts.items())),
    }


def _format_ab_metrics(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"turns": 0, "bytes": 0, "poll_loops": 0})
    for event in events:
        if event["kind"] != "supervisor_axi_format_metric":
            continue
        payload = event["payload"]
        fmt = str(payload.get("format") or "").lower()
        if fmt not in {"toon", "json"}:
            continue
        totals[fmt]["turns"] += int(payload.get("turns") or 0)
        totals[fmt]["bytes"] += int(payload.get("bytes") or payload.get("bytes_per_poll_loop") or 0)
        totals[fmt]["poll_loops"] += int(payload.get("poll_loops") or 1)
    return {key: dict(value) for key, value in sorted(totals.items())}


def _visual_override_count(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event["kind"] == "visual_evidence_override_asserted")


def _transport_era(event: dict[str, Any]) -> str:
    payload = event["payload"]
    interface = str(payload.get("interface") or "").lower()
    if interface in {"axi", "cli"}:
        return "axi"
    if interface == "mcp":
        return "mcp"
    return "axi" if int(event["ts"]) >= AXI_CUTOVER_TS else "mcp"


def _run_era(events: list[dict[str, Any]]) -> str:
    if not events:
        return "unknown"
    first_ts = min(int(event["ts"]) for event in events)
    return "axi" if first_ts >= AXI_CUTOVER_TS else "mcp"


def _row_run_eras(row: dict[str, Any], incidents: dict[str, Any]) -> list[str]:
    explicit = str(incidents.get("run_era") or "").strip().lower()
    if explicit in {"axi", "mcp"}:
        return [explicit]
    by_era = incidents.get("by_era") if isinstance(incidents.get("by_era"), dict) else {}
    nonzero = [str(era).lower() for era, count in by_era.items() if int(count or 0) > 0]
    legacy_eras = sorted({era for era in nonzero if era in {"axi", "mcp"}})
    if legacy_eras:
        return legacy_eras
    try:
        computed_at = int(row.get("computed_at") or 0)
    except (TypeError, ValueError):
        computed_at = 0
    if computed_at:
        return ["axi" if computed_at >= AXI_CUTOVER_TS else "mcp"]
    return ["unknown"]
