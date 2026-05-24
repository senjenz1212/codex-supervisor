"""Deterministic failure taxonomy for dual-agent workflow events."""
from __future__ import annotations

from typing import Any


FAILURE_TAXONOMY_VERSION = "dual-agent-failure-taxonomy/v1"
BLOCKING_PROBE_IDS = frozenset({"P2", "P3", "P11", "P12", "P_planning", "CURSOR"})


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

    if probe_id == "P_planning" or "planning" in normalized or "artifact" in normalized:
        category, subcategory = "system_design", "artifact_quality"
    elif probe_id == "P12" or "skill" in normalized:
        category, subcategory = "governance", "missing_skill_provenance"
    elif probe_id == "P11" or "receipt" in normalized or "claim" in normalized:
        category, subcategory = "task_verification", "missing_or_stale_receipt"
    elif probe_id == "P2" or "lead_invocation" in normalized or "timeout" in normalized:
        category, subcategory = "tool_execution", "worker_invocation"
    elif probe_id == "P3" or "outcome" in normalized or "schema" in normalized:
        category, subcategory = "inter_agent_misalignment", "message_protocol"
    elif "cursor" in normalized:
        category, subcategory = "inter_agent_misalignment", "reviewer_disagreement"
    elif "agents_not_converged" in normalized or "max_rounds" in normalized:
        category, subcategory = "inter_agent_misalignment", "decision_deadlock"
    elif "visual" in normalized or "screenshot" in normalized:
        category, subcategory = "task_verification", "visual_evidence"
    elif "lock" in normalized:
        category, subcategory = "system_design", "resource_contention"

    payload = {
        "schema_version": FAILURE_TAXONOMY_VERSION,
        "framework": "MAST-inspired",
        "category": category,
        "subcategory": subcategory,
        "code": normalized or "unknown_failure",
        "probe_id": probe_id,
        "source": source,
    }
    if details not in (None, "", [], {}):
        payload["details"] = details
    return payload


def _text(value: Any) -> str:
    return str(value or "").strip()
