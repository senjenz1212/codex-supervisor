"""Deterministic failure taxonomy for dual-agent workflow events."""
from __future__ import annotations

from typing import Any


FAILURE_TAXONOMY_VERSION = "dual-agent-failure-taxonomy/v1"
BLOCKING_PROBE_IDS = frozenset({"P2", "P3", "P11", "P12", "P_planning", "CURSOR"})
MAST_FAILURE_MODES: dict[str, dict[str, str]] = {
    "FM-1.1": {"category": "Specification Issues", "mode": "Disobey task specification"},
    "FM-1.2": {"category": "Specification Issues", "mode": "Disobey role specification"},
    "FM-1.3": {"category": "Specification Issues", "mode": "Step repetition"},
    "FM-1.4": {"category": "Specification Issues", "mode": "Loss of conversation history"},
    "FM-1.5": {"category": "Specification Issues", "mode": "Unaware of termination conditions"},
    "FM-2.1": {"category": "Inter-Agent Misalignment", "mode": "Conversation reset"},
    "FM-2.2": {"category": "Inter-Agent Misalignment", "mode": "Fail to ask for clarification"},
    "FM-2.3": {"category": "Inter-Agent Misalignment", "mode": "Task derailment"},
    "FM-2.4": {"category": "Inter-Agent Misalignment", "mode": "Information withholding"},
    "FM-2.5": {"category": "Inter-Agent Misalignment", "mode": "Ignored other agent input"},
    "FM-2.6": {"category": "Inter-Agent Misalignment", "mode": "Reasoning-action mismatch"},
    "FM-3.1": {"category": "Task Verification", "mode": "Premature termination"},
    "FM-3.2": {"category": "Task Verification", "mode": "No or incomplete verification"},
    "FM-3.3": {"category": "Task Verification", "mode": "Incorrect verification"},
}


def failure_taxonomy_for_payload(
    *,
    kind: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Classify a blocking payload with a replayable, MAST-inspired category.

    The labels are intentionally deterministic and local. They are inspired by
    multi-agent failure taxonomies but do not call an LLM or depend on external
    services.
    """
    escalation = payload.get("escalation")
    if isinstance(escalation, dict):
        reason = _text(
            escalation.get("reason")
            or escalation.get("policy_field")
            or escalation.get("type")
            or escalation.get("status")
        )
        if reason:
            return classify_failure(
                reason=reason,
                probe_id=_text(escalation.get("probe_id")),
                source="result_escalation",
            )

    probes = payload.get("probes")
    if isinstance(probes, dict):
        missing_required = _missing_required_probes(payload, probes)
        if missing_required:
            return classify_failure(
                reason="premature_accept_probe_skipped",
                source=kind,
                details={"missing_probes": missing_required},
            )
        blocker = blocking_probe_failure(probes)
        if blocker is not None:
            return blocker

    probe = payload.get("probe")
    if isinstance(probe, dict):
        status = _text(probe.get("status"))
        if status and status != "green":
            return classify_failure(
                reason=_text(probe.get("reason")) or "unknown_probe_reason",
                probe_id=_text(probe.get("probe_id")),
                source=kind,
                details=probe.get("details"),
            )

    if _text(payload.get("status")) == "blocked":
        return classify_failure(
            reason=_text(payload.get("reason")) or "blocked_without_probe_reason",
            source=kind,
        )
    return None


def blocking_probe_failure(probes: dict[str, Any]) -> dict[str, Any] | None:
    for probe_id, probe in sorted(probes.items()):
        if str(probe_id) not in BLOCKING_PROBE_IDS or not isinstance(probe, dict):
            continue
        status = _text(probe.get("status"))
        if status and status != "green":
            return classify_failure(
                reason=_text(probe.get("reason")) or "unknown_probe_reason",
                probe_id=str(probe_id),
                source="validation_probe",
                details=probe.get("details"),
            )
    return None


def classify_failure(
    *,
    reason: str,
    probe_id: str = "",
    source: str = "",
    details: Any = None,
) -> dict[str, Any]:
    normalized = reason.strip().lower().replace("-", "_").replace(" ", "_")
    category = "system_design"
    subcategory = "unknown"
    mast_code: str | None = None

    if _matches(normalized, "role_violation", "covenant", "disobey_role"):
        category, subcategory, mast_code = "specification_issues", "role_violation", "FM-1.2"
    elif _matches(normalized, "repeated_gate", "duplicate_round", "step_repetition"):
        category, subcategory, mast_code = "specification_issues", "step_repetition", "FM-1.3"
    elif _matches(normalized, "loss_history", "lost_conversation_history", "conversation_history_lost"):
        category, subcategory, mast_code = "specification_issues", "loss_of_history", "FM-1.4"
    elif _matches(normalized, "agents_not_converged", "max_rounds", "termination_condition"):
        category, subcategory, mast_code = (
            "inter_agent_misalignment",
            "decision_deadlock",
            "FM-1.5",
        )
    elif _matches(normalized, "conversation_reset", "thread_reset", "context_reset"):
        category, subcategory, mast_code = "inter_agent_misalignment", "conversation_reset", "FM-2.1"
    elif _matches(normalized, "ambiguous_input", "no_clarification", "clarification_missing"):
        category, subcategory, mast_code = (
            "inter_agent_misalignment",
            "fail_to_ask_clarification",
            "FM-2.2",
        )
    elif _matches(normalized, "scope_violation", "off_scope", "task_derailment"):
        category, subcategory, mast_code = "inter_agent_misalignment", "task_derailment", "FM-2.3"
    elif _matches(normalized, "information_withholding", "withheld_information", "reviewer_disagreement"):
        category, subcategory, mast_code = (
            "inter_agent_misalignment",
            "information_withholding",
            "FM-2.4",
        )
    elif _matches(normalized, "objection_unaddressed", "ignored_critique", "ignored_input"):
        category, subcategory, mast_code = "inter_agent_misalignment", "ignored_input", "FM-2.5"
    elif _matches(normalized, "reasoning_action_mismatch", "message_protocol"):
        category, subcategory, mast_code = (
            "inter_agent_misalignment",
            "message_protocol",
            "FM-2.6",
        )
    elif _matches(normalized, "premature_accept", "probe_skipped", "premature_termination"):
        category, subcategory, mast_code = "task_verification", "premature_termination", "FM-3.1"
    elif _matches(normalized, "false_green", "incorrect_verification", "validator_wrong"):
        category, subcategory, mast_code = "task_verification", "incorrect_verification", "FM-3.3"
    elif probe_id == "P_planning" or "planning" in normalized or "artifact" in normalized:
        category, subcategory = "system_design", "artifact_quality"
        mast_code = "FM-1.1"
    elif probe_id == "P12" or "skill" in normalized:
        category, subcategory = "governance", "missing_skill_provenance"
    elif probe_id == "P11" or "receipt" in normalized or "claim" in normalized:
        category, subcategory = "task_verification", "missing_or_stale_receipt"
        mast_code = "FM-3.2"
    elif probe_id == "P2" or "lead_invocation" in normalized or "timeout" in normalized:
        category, subcategory = "tool_execution", "worker_invocation"
    elif probe_id == "P3" or "outcome" in normalized or "schema" in normalized:
        category, subcategory = "inter_agent_misalignment", "message_protocol"
        mast_code = "FM-2.6"
    elif "cursor" in normalized:
        category, subcategory = "inter_agent_misalignment", "reviewer_disagreement"
        mast_code = "FM-2.4"
    elif "visual" in normalized or "screenshot" in normalized:
        category, subcategory = "task_verification", "visual_evidence"
        mast_code = "FM-3.2"
    elif "lock" in normalized:
        category, subcategory = "system_design", "resource_contention"
    elif _matches(normalized, "task_spec_violation", "disobey_task_spec"):
        category, subcategory, mast_code = "specification_issues", "task_specification", "FM-1.1"

    mast = MAST_FAILURE_MODES.get(mast_code or "")

    payload = {
        "schema_version": FAILURE_TAXONOMY_VERSION,
        "framework": "MAST-inspired",
        "category": category,
        "subcategory": subcategory,
        "code": normalized or "unknown_failure",
        "mast_code": mast_code,
        "mast_mode": mast["mode"] if mast is not None else None,
        "mast_category": mast["category"] if mast is not None else None,
        "probe_id": probe_id,
        "source": source,
    }
    if details not in (None, "", [], {}):
        payload["details"] = details
    return payload


def detect_sequence_failures(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect deterministic cross-event MAST failures for replay manifests."""
    failures: list[dict[str, Any]] = []
    seen_gate_inputs: dict[tuple[str, str], int] = {}
    accepted_gate_by_gate: dict[str, int] = {}
    pending_objections: list[dict[str, Any]] = []

    for event in events:
        event_id = int(event.get("event_id") or 0)
        kind = _text(event.get("kind"))
        gate = _text(event.get("gate"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        if kind == "dual_agent_gate_result":
            probes = payload.get("probes") if isinstance(payload.get("probes"), dict) else {}
            missing_required = _missing_required_probes(payload, probes)
            if missing_required:
                failures.append(_sequence_failure(
                    "premature_accept_probe_skipped",
                    [event_id],
                    {"missing_probes": missing_required},
                ))
            if _text(payload.get("status")) == "accepted":
                accepted_gate_by_gate[gate] = event_id
            signature = _gate_input_signature(gate, payload)
            if signature is not None:
                previous = seen_gate_inputs.get(signature)
                if previous is not None:
                    failures.append(_sequence_failure(
                        "repeated_gate_duplicate_round",
                        [previous, event_id],
                        {"signature": list(signature)},
                    ))
                else:
                    seen_gate_inputs[signature] = event_id

        if kind == "dual_agent_gate_round":
            round_payload = payload.get("round") if isinstance(payload.get("round"), dict) else {}
            objection = _text(round_payload.get("objection"))
            if objection:
                pending_objections.append({
                    "event_id": event_id,
                    "gate": gate,
                    "objection": objection,
                })

        if kind == "dual_agent_interaction_message" and _text(payload.get("sender")) == "claude_code":
            pending_objections = _handle_claude_response_for_objections(
                failures=failures,
                pending_objections=pending_objections,
                event_id=event_id,
                gate=gate,
                payload=payload,
            )

        if kind == "tri_agent_cursor_review":
            accepted = payload.get("accepted")
            cursor_review = payload.get("cursor_review") if isinstance(payload.get("cursor_review"), dict) else {}
            if accepted is None and cursor_review:
                accepted = cursor_review.get("accepted")
            if accepted is False and gate in accepted_gate_by_gate:
                failures.append(_sequence_failure(
                    "false_green_incorrect_verification",
                    [event_id],
                    {"accepted_gate_event_id": accepted_gate_by_gate[gate]},
                ))

    return failures


def _missing_required_probes(payload: dict[str, Any], probes: dict[str, Any]) -> list[str]:
    if _text(payload.get("status")) != "accepted":
        return []
    required = payload.get("required_probes")
    if not isinstance(required, (list, tuple)):
        return []
    return sorted(
        str(probe_id)
        for probe_id in required
        if str(probe_id) not in probes
    )


def _sequence_failure(
    reason: str,
    event_ids: list[int],
    details: dict[str, Any],
) -> dict[str, Any]:
    payload = classify_failure(reason=reason, source="event_sequence", details=details)
    payload["event_ids"] = event_ids
    return payload


def _gate_input_signature(gate: str, payload: dict[str, Any]) -> tuple[str, str] | None:
    value = payload.get("handoff_packet_sha256") or payload.get("handoff_packet_path")
    if not value:
        return None
    return (gate, _text(value))


def _handle_claude_response_for_objections(
    *,
    failures: list[dict[str, Any]],
    pending_objections: list[dict[str, Any]],
    event_id: int,
    gate: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    remaining: list[dict[str, Any]] = []
    response_text = _normalized_response_text(payload)
    addresses = {
        _text(item)
        for item in payload.get("addresses") or []
        if _text(item)
    }
    for objection in pending_objections:
        if objection["gate"] != gate:
            remaining.append(objection)
            continue
        event_ref = f"event:{objection['event_id']}"
        objection_text = _text(objection["objection"]).lower()
        if event_ref in addresses or objection_text in response_text:
            continue
        failures.append(_sequence_failure(
            "objection_unaddressed_ignored_critique",
            [int(objection["event_id"]), event_id],
            {"objection": objection["objection"]},
        ))
    return remaining


def _normalized_response_text(payload: dict[str, Any]) -> str:
    parts: list[str] = [_text(payload.get("content"))]
    for key in ("claims", "objections", "questions"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(_text(item) for item in value)
    return "\n".join(parts).lower()


def _matches(normalized: str, *needles: str) -> bool:
    return any(needle in normalized for needle in needles)


def _text(value: Any) -> str:
    return str(value or "").strip()
