"""Read-only dual-agent interaction snapshots derived from the event ledger."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from .state import State


SnapshotStatus = Literal["ok", "not_found", "partial"]
InteractionLiveness = Literal["unknown", "active", "stale", "complete", "blocked"]
GateDecision = Literal["accept", "revise", "deny", "unknown"]
BlockerSource = Literal["result_escalation", "validation_probe", "latest_round_objection"]
InboxItemKind = Literal["round", "result", "blocker", "warning", "handoff"]
InboxActor = Literal["codex", "claude", "supervisor", "system"]
InboxSeverity = Literal["info", "warning", "blocked", "success"]


@dataclass(frozen=True)
class ConfidencePair:
    codex: float | None
    claude: float | None


@dataclass(frozen=True)
class Blocker:
    source: BlockerSource
    code: str | None
    message: str


@dataclass(frozen=True)
class SnapshotWarning:
    code: str
    message: str
    event_id: int | None


@dataclass(frozen=True)
class AgentInteractionSnapshot:
    run_id: str
    task_id: str
    status: SnapshotStatus
    latest_event_id: int | None
    latest_result_event_id: int | None
    rounds_count: int
    latest_gate: str | None
    codex_decision: GateDecision | None
    claude_decision: GateDecision | None
    confidence_pair: ConfidencePair
    blocker: Blocker | None
    liveness: InteractionLiveness
    handoff_packet_path: str | None
    generated_at_s: int
    warnings: list[SnapshotWarning]


@dataclass(frozen=True)
class AgentInteractionInboxItem:
    item_id: str
    event_id: int | None
    ts: Any
    gate: str | None
    kind: InboxItemKind
    attributed_to: InboxActor
    title: str
    body: str
    decision: str | None
    severity: InboxSeverity
    action_required: bool


@dataclass(frozen=True)
class AgentInteractionInbox:
    run_id: str
    task_id: str
    liveness: InteractionLiveness
    status: SnapshotStatus
    generated_at_s: int
    items: tuple[AgentInteractionInboxItem, ...]
    warnings: tuple[SnapshotWarning, ...]


@dataclass(frozen=True)
class _RoundEvent:
    event_id: int
    ts: Any
    gate: str
    round_index: int | None
    codex_decision: GateDecision | None
    claude_decision: GateDecision | None
    codex_confidence: float | None
    claude_confidence: float | None
    objection: str | None


@dataclass(frozen=True)
class _ResultEvent:
    event_id: int
    ts: Any
    gate: str
    status: str
    probes: dict[str, Any]
    outcome: Any
    escalation: Any
    handoff_packet_path: str | None


@dataclass(frozen=True)
class _InboxItemDraft:
    event_id: int | None
    ts: Any
    gate: str | None
    kind: InboxItemKind
    attributed_to: InboxActor
    title: str
    body: str
    decision: str | None
    severity: InboxSeverity
    action_required: bool


def get_agent_interaction_snapshot(
    state: State,
    *,
    run_id: str,
    task_id: str,
    now_s: int | None = None,
) -> AgentInteractionSnapshot:
    generated_at_s = int(time.time()) if now_s is None else int(now_s)
    warnings: list[SnapshotWarning] = []
    rounds: list[_RoundEvent] = []
    results: list[_ResultEvent] = []
    matched_rows = 0

    for row in state.read_dual_agent_gate_events(run_id):
        event_id = int(row["event_id"])
        kind = str(row["kind"])
        ts = row["ts"]
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            matched_rows += 1
            warnings.append(SnapshotWarning(
                code="corrupt_event_skipped",
                message="Dual-agent event payload JSON could not be parsed.",
                event_id=event_id,
            ))
            continue

        if not isinstance(payload, dict):
            matched_rows += 1
            warnings.append(_unexpected(event_id, "Dual-agent event payload was not an object."))
            continue
        if payload.get("task_id") != task_id:
            continue
        matched_rows += 1

        if kind == "dual_agent_gate_round":
            parsed_round = _parse_round(payload, event_id, ts, warnings)
            if parsed_round is not None:
                rounds.append(parsed_round)
        elif kind == "dual_agent_gate_result":
            parsed_result = _parse_result(payload, event_id, ts, warnings)
            if parsed_result is not None:
                results.append(parsed_result)

    usable_events: list[_RoundEvent | _ResultEvent] = [*rounds, *results]
    if not usable_events:
        if matched_rows and warnings:
            return AgentInteractionSnapshot(
                run_id=run_id,
                task_id=task_id,
                status="partial",
                latest_event_id=None,
                latest_result_event_id=None,
                rounds_count=0,
                latest_gate=None,
                codex_decision=None,
                claude_decision=None,
                confidence_pair=ConfidencePair(codex=None, claude=None),
                blocker=None,
                liveness="unknown",
                handoff_packet_path=None,
                generated_at_s=generated_at_s,
                warnings=warnings,
            )
        return AgentInteractionSnapshot(
            run_id=run_id,
            task_id=task_id,
            status="not_found",
            latest_event_id=None,
            latest_result_event_id=None,
            rounds_count=0,
            latest_gate=None,
            codex_decision=None,
            claude_decision=None,
            confidence_pair=ConfidencePair(codex=None, claude=None),
            blocker=None,
            liveness="unknown",
            handoff_packet_path=None,
            generated_at_s=generated_at_s,
            warnings=[
                SnapshotWarning(
                    code="no_dual_agent_events",
                    message="No dual-agent gate events found for run_id/task_id.",
                    event_id=None,
                )
            ],
        )

    latest_event = max(usable_events, key=lambda event: event.event_id)
    latest_round = max(rounds, key=lambda event: event.event_id) if rounds else None
    latest_result = max(results, key=lambda event: event.event_id) if results else None
    latest_event_id = latest_event.event_id
    latest_result_event_id = latest_result.event_id if latest_result else None
    handoff_packet_path = latest_result.handoff_packet_path if latest_result else None

    if latest_result is not None and handoff_packet_path is None:
        warnings.append(SnapshotWarning(
            code="missing_handoff_packet_path",
            message="Latest gate result did not include top-level handoff_packet_path.",
            event_id=latest_result.event_id,
        ))

    blocker, liveness = _derive_blocker_and_liveness(
        latest_result=latest_result,
        latest_round=latest_round,
        latest_event=latest_event,
        now_s=generated_at_s,
        warnings=warnings,
    )
    status: SnapshotStatus = "partial" if warnings else "ok"

    return AgentInteractionSnapshot(
        run_id=run_id,
        task_id=task_id,
        status=status,
        latest_event_id=latest_event_id,
        latest_result_event_id=latest_result_event_id,
        rounds_count=len(rounds),
        latest_gate=latest_event.gate,
        codex_decision=latest_round.codex_decision if latest_round else None,
        claude_decision=latest_round.claude_decision if latest_round else None,
        confidence_pair=ConfidencePair(
            codex=latest_round.codex_confidence if latest_round else None,
            claude=latest_round.claude_confidence if latest_round else None,
        ),
        blocker=blocker,
        liveness=liveness,
        handoff_packet_path=handoff_packet_path,
        generated_at_s=generated_at_s,
        warnings=warnings,
    )


def get_agent_interaction_inbox(
    state: State,
    *,
    run_id: str,
    task_id: str,
    now_s: int | None = None,
    limit: int | None = None,
) -> AgentInteractionInbox:
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")

    snapshot = get_agent_interaction_snapshot(
        state,
        run_id=run_id,
        task_id=task_id,
        now_s=now_s,
    )
    event_items = _read_event_inbox_item_drafts(state, run_id=run_id, task_id=task_id)
    limited_event_items = _apply_event_item_limit(event_items, limit)
    synthetic_items = _synthetic_inbox_item_drafts(snapshot)
    return AgentInteractionInbox(
        run_id=run_id,
        task_id=task_id,
        liveness=snapshot.liveness,
        status=snapshot.status,
        generated_at_s=snapshot.generated_at_s,
        items=_materialize_inbox_items([*limited_event_items, *synthetic_items]),
        warnings=tuple(snapshot.warnings),
    )


def _parse_round(
    payload: dict[str, Any],
    event_id: int,
    ts: Any,
    warnings: list[SnapshotWarning],
) -> _RoundEvent | None:
    gate = payload.get("gate")
    round_payload = payload.get("round")
    if not isinstance(gate, str) or not isinstance(round_payload, dict):
        warnings.append(_unexpected(event_id, "Dual-agent round event had an unexpected shape."))
        return None

    codex_decision = _normalize_decision(
        round_payload.get("codex_decision"),
        event_id=event_id,
        field="codex_decision",
        warnings=warnings,
    )
    claude_decision = _normalize_decision(
        round_payload.get("claude_decision"),
        event_id=event_id,
        field="claude_decision",
        warnings=warnings,
    )
    return _RoundEvent(
        event_id=event_id,
        ts=ts,
        gate=gate,
        round_index=_normalize_round_index(round_payload.get("round_index")),
        codex_decision=codex_decision,
        claude_decision=claude_decision,
        codex_confidence=_normalize_confidence(
            round_payload.get("codex_confidence"),
            event_id=event_id,
            field="codex_confidence",
            warnings=warnings,
        ),
        claude_confidence=_normalize_confidence(
            round_payload.get("claude_confidence"),
            event_id=event_id,
            field="claude_confidence",
            warnings=warnings,
        ),
        objection=_string_or_none(round_payload.get("objection")),
    )


def _parse_result(
    payload: dict[str, Any],
    event_id: int,
    ts: Any,
    warnings: list[SnapshotWarning],
) -> _ResultEvent | None:
    gate = payload.get("gate")
    status = payload.get("status")
    if not isinstance(gate, str) or not isinstance(status, str):
        warnings.append(_unexpected(event_id, "Dual-agent result event had an unexpected shape."))
        return None
    probes = payload.get("probes") if isinstance(payload.get("probes"), dict) else {}
    return _ResultEvent(
        event_id=event_id,
        ts=ts,
        gate=gate,
        status=status,
        probes=probes,
        outcome=payload.get("outcome"),
        escalation=payload.get("escalation"),
        handoff_packet_path=_string_or_none(payload.get("handoff_packet_path")),
    )


def _read_event_inbox_item_drafts(
    state: State,
    *,
    run_id: str,
    task_id: str,
) -> list[_InboxItemDraft]:
    items: list[_InboxItemDraft] = []
    for row in state.read_dual_agent_gate_events(run_id):
        event_id = int(row["event_id"])
        kind = str(row["kind"])
        ts = row["ts"]
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("task_id") != task_id:
            continue
        parse_warnings: list[SnapshotWarning] = []
        if kind == "dual_agent_gate_round":
            parsed_round = _parse_round(payload, event_id, ts, parse_warnings)
            if parsed_round is not None:
                items.append(_round_inbox_item_draft(parsed_round))
        elif kind == "dual_agent_gate_result":
            parsed_result = _parse_result(payload, event_id, ts, parse_warnings)
            if parsed_result is not None:
                items.append(_result_inbox_item_draft(parsed_result))
    return items


def _round_inbox_item_draft(round_event: _RoundEvent) -> _InboxItemDraft:
    codex_decision = _decision_label(round_event.codex_decision)
    claude_decision = _decision_label(round_event.claude_decision)
    decision = f"codex={codex_decision};claude={claude_decision}"
    return _InboxItemDraft(
        event_id=round_event.event_id,
        ts=round_event.ts,
        gate=round_event.gate,
        kind="round",
        attributed_to=_round_actor(codex_decision, claude_decision),
        title=f"Round {_round_index_label(round_event.round_index)}: {codex_decision}/{claude_decision}",
        body=round_event.objection or "No objection recorded.",
        decision=decision,
        severity=_round_severity(codex_decision, claude_decision),
        action_required=(
            codex_decision == "deny"
            or claude_decision == "deny"
            or bool(round_event.objection)
        ),
    )


def _result_inbox_item_draft(result: _ResultEvent) -> _InboxItemDraft:
    validation_issue = _result_has_validation_issue(result)
    status = result.status
    return _InboxItemDraft(
        event_id=result.event_id,
        ts=result.ts,
        gate=result.gate,
        kind="result",
        attributed_to="supervisor",
        title=f"Gate {result.gate}: {status}",
        body=_result_body(result),
        decision=status,
        severity=_result_severity(result, validation_issue=validation_issue),
        action_required=validation_issue or status == "blocked" or result.escalation is not None,
    )


def _synthetic_inbox_item_drafts(snapshot: AgentInteractionSnapshot) -> list[_InboxItemDraft]:
    items: list[_InboxItemDraft] = []
    if snapshot.blocker is not None:
        blocker_title = snapshot.blocker.code or snapshot.blocker.source or "unknown"
        items.append(_InboxItemDraft(
            event_id=None,
            ts=None,
            gate=snapshot.latest_gate,
            kind="blocker",
            attributed_to="supervisor",
            title=f"Blocked: {blocker_title}",
            body=snapshot.blocker.message,
            decision=None,
            severity="blocked",
            action_required=True,
        ))
    for warning in snapshot.warnings:
        items.append(_InboxItemDraft(
            event_id=warning.event_id,
            ts=None,
            gate=snapshot.latest_gate,
            kind="warning",
            attributed_to="system",
            title=f"Warning: {warning.code}",
            body=warning.message,
            decision=None,
            severity="warning",
            action_required=warning.code in {"corrupt_event_skipped", "unexpected_event_shape"},
        ))
    if snapshot.handoff_packet_path is not None:
        items.append(_InboxItemDraft(
            event_id=snapshot.latest_result_event_id,
            ts=None,
            gate=snapshot.latest_gate,
            kind="handoff",
            attributed_to="system",
            title="Handoff packet",
            body=snapshot.handoff_packet_path,
            decision=None,
            severity="info",
            action_required=False,
        ))
    return items


def _apply_event_item_limit(
    items: list[_InboxItemDraft],
    limit: int | None,
) -> list[_InboxItemDraft]:
    if limit is None or limit >= len(items):
        return list(items)
    if limit == 0:
        return []
    if limit == 1:
        return [items[-1]]
    return [items[0], *items[-(limit - 1):]]


def _materialize_inbox_items(drafts: list[_InboxItemDraft]) -> tuple[AgentInteractionInboxItem, ...]:
    counts: dict[tuple[InboxItemKind, int | None], int] = {}
    items: list[AgentInteractionInboxItem] = []
    for draft in drafts:
        key = (draft.kind, draft.event_id)
        ordinal = counts.get(key, 0)
        counts[key] = ordinal + 1
        event_id_label = draft.event_id if draft.event_id is not None else "none"
        items.append(AgentInteractionInboxItem(
            item_id=f"{draft.kind}:{event_id_label}:{ordinal}",
            event_id=draft.event_id,
            ts=draft.ts,
            gate=draft.gate,
            kind=draft.kind,
            attributed_to=draft.attributed_to,
            title=draft.title,
            body=draft.body,
            decision=draft.decision,
            severity=draft.severity,
            action_required=draft.action_required,
        ))
    return tuple(items)


def _round_actor(codex_decision: str, claude_decision: str) -> InboxActor:
    if codex_decision in {"deny", "revise"}:
        return "codex"
    if claude_decision in {"deny", "revise"}:
        return "claude"
    return "supervisor"


def _round_severity(codex_decision: str, claude_decision: str) -> InboxSeverity:
    if "deny" in {codex_decision, claude_decision}:
        return "blocked"
    if {codex_decision, claude_decision} == {"accept"}:
        return "success"
    if "revise" in {codex_decision, claude_decision} or "unknown" in {codex_decision, claude_decision}:
        return "warning"
    return "info"


def _result_has_validation_issue(result: _ResultEvent) -> bool:
    for probe_id in ("P2", "P3"):
        probe = result.probes.get(probe_id)
        if not isinstance(probe, dict):
            continue
        status = probe.get("status")
        if status is not None and status != "green":
            return True
    return False


def _result_severity(result: _ResultEvent, *, validation_issue: bool) -> InboxSeverity:
    if validation_issue or result.status == "blocked" or result.escalation is not None:
        return "blocked"
    if result.status == "accepted":
        return "success"
    if result.status in {"failed", "rejected"}:
        return "warning"
    return "info"


def _result_body(result: _ResultEvent) -> str:
    if isinstance(result.outcome, dict):
        summary = _string_or_none(result.outcome.get("summary"))
        if summary:
            return summary
    if isinstance(result.escalation, dict):
        reason = _string_or_none(result.escalation.get("reason"))
        if reason:
            return reason
        message = _string_or_none(result.escalation.get("message"))
        if message:
            return message
        return json.dumps(result.escalation, sort_keys=True, default=str)
    if result.escalation is not None:
        return json.dumps(result.escalation, sort_keys=True, default=str)
    return "No outcome summary recorded."


def _decision_label(decision: GateDecision | None) -> str:
    return decision or "unknown"


def _round_index_label(round_index: int | None) -> str:
    return str(round_index) if round_index is not None else "unknown"


def _derive_blocker_and_liveness(
    *,
    latest_result: _ResultEvent | None,
    latest_round: _RoundEvent | None,
    latest_event: _RoundEvent | _ResultEvent,
    now_s: int,
    warnings: list[SnapshotWarning],
) -> tuple[Blocker | None, InteractionLiveness]:
    escalation_blocker = _result_escalation_blocker(latest_result)
    if escalation_blocker is not None:
        return escalation_blocker, "blocked"

    probe_blocker = _red_probe_blocker(latest_result, warnings)
    if probe_blocker is not None:
        return probe_blocker, "blocked"

    round_objection_blocker = _round_objection_blocker(latest_round)
    if latest_result is not None and latest_result.status == "blocked":
        if round_objection_blocker is not None:
            return round_objection_blocker, "blocked"
        return Blocker(
            source="validation_probe",
            code="blocked_without_probe_reason",
            message="Gate result status blocked without escalation, red probe, or round objection.",
        ), "blocked"

    if (
        latest_result is not None
        and latest_result.status == "accepted"
        and latest_round is not None
        and latest_round.codex_decision == "accept"
        and latest_round.claude_decision == "accept"
    ):
        return None, "complete"

    if latest_round is not None and (
        latest_round.codex_decision == "deny"
        or latest_round.claude_decision == "deny"
    ):
        return _deny_blocker(latest_round), "blocked"

    if (
        round_objection_blocker is not None
        and latest_round is not None
        and not (
            latest_result is not None
            and latest_result.status == "accepted"
            and latest_result.event_id > latest_round.event_id
        )
    ):
        return round_objection_blocker, "blocked"

    if not isinstance(latest_event.ts, int):
        warnings.append(SnapshotWarning(
            code="missing_timestamp",
            message="Latest usable event did not include an integer epoch timestamp.",
            event_id=latest_event.event_id,
        ))
        return None, "unknown"

    if now_s - int(latest_event.ts) <= 900:
        return None, "active"
    return None, "stale"


def _result_escalation_blocker(result: _ResultEvent | None) -> Blocker | None:
    if result is None or result.escalation is None:
        return None
    if not isinstance(result.escalation, dict):
        return Blocker(
            source="result_escalation",
            code=None,
            message=json.dumps(result.escalation, sort_keys=True, default=str),
        )
    code = _string_or_none(result.escalation.get("type"))
    code = code or _string_or_none(result.escalation.get("status"))
    code = code or _string_or_none(result.escalation.get("policy_field"))
    message = _string_or_none(result.escalation.get("reason"))
    message = message or _string_or_none(result.escalation.get("message"))
    message = message or json.dumps(result.escalation, sort_keys=True, default=str)
    return Blocker(source="result_escalation", code=code, message=message)


def _red_probe_blocker(
    result: _ResultEvent | None,
    warnings: list[SnapshotWarning],
) -> Blocker | None:
    if result is None:
        return None
    for probe_id in ("P2", "P3"):
        probe = result.probes.get(probe_id)
        if not isinstance(probe, dict):
            continue
        status = probe.get("status")
        if status is not None and status != "green":
            reason = _string_or_none(probe.get("reason"))
            if reason is None:
                warnings.append(SnapshotWarning(
                    code="missing_probe_reason",
                    message=f"{probe_id} red probe did not include a reason.",
                    event_id=result.event_id,
                ))
            code = reason or "unknown_probe_reason"
            details = probe.get("details")
            if details:
                message = f"{code}: {json.dumps(details, sort_keys=True, default=str)}"
            else:
                message = code
            return Blocker(source="validation_probe", code=code, message=message)
    return None


def _round_objection_blocker(round_event: _RoundEvent | None) -> Blocker | None:
    if round_event is None or not round_event.objection:
        return None
    if round_event.codex_decision not in {"revise", "deny"} and round_event.claude_decision not in {"revise", "deny"}:
        return None
    return Blocker(
        source="latest_round_objection",
        code=f"codex={round_event.codex_decision};claude={round_event.claude_decision}",
        message=round_event.objection,
    )


def _deny_blocker(round_event: _RoundEvent) -> Blocker:
    if round_event.objection:
        return Blocker(
            source="latest_round_objection",
            code=f"codex={round_event.codex_decision};claude={round_event.claude_decision}",
            message=round_event.objection,
        )
    return Blocker(
        source="latest_round_objection",
        code="deny_without_objection",
        message="Latest round denied without an objection message.",
    )


def _normalize_decision(
    value: Any,
    *,
    event_id: int,
    field: str,
    warnings: list[SnapshotWarning],
) -> GateDecision | None:
    if value is None:
        warnings.append(_unexpected(event_id, f"Round event missing {field}."))
        return None
    if value == "needs_revision":
        return "revise"
    if value in {"accept", "revise", "deny"}:
        return value
    warnings.append(SnapshotWarning(
        code="unknown_decision_value",
        message=f"Unknown {field} value: {value!r}.",
        event_id=event_id,
    ))
    return "unknown"


def _normalize_confidence(
    value: Any,
    *,
    event_id: int,
    field: str,
    warnings: list[SnapshotWarning],
) -> float | None:
    if value is None:
        warnings.append(_unexpected(event_id, f"Round event missing {field}."))
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        warnings.append(_invalid_confidence(event_id, field, value))
        return None
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        warnings.append(_invalid_confidence(event_id, field, value))
        return None
    return confidence


def _normalize_round_index(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _invalid_confidence(event_id: int, field: str, value: Any) -> SnapshotWarning:
    return SnapshotWarning(
        code="invalid_confidence",
        message=f"Invalid {field} value: {value!r}.",
        event_id=event_id,
    )


def _unexpected(event_id: int, message: str) -> SnapshotWarning:
    return SnapshotWarning(
        code="unexpected_event_shape",
        message=message,
        event_id=event_id,
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
