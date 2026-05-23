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
class _RoundEvent:
    event_id: int
    ts: Any
    gate: str
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
    escalation: Any
    handoff_packet_path: str | None


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
        escalation=payload.get("escalation"),
        handoff_packet_path=_string_or_none(payload.get("handoff_packet_path")),
    )


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
