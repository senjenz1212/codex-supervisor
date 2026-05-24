"""Typed mailbox-style interaction events for agent collaboration traces."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


AGENT_INTERACTION_SCHEMA_VERSION = "dual-agent-interaction/v1"


@dataclass(frozen=True)
class ConfidenceReport:
    value: float | None
    source: str
    criteria: tuple[str, ...] = ()
    rationale: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentMailboxMessage:
    task_id: str
    gate: str
    sender: str
    recipient: str
    message_type: str
    content: str
    round_index: int | None = None
    persona_id: str = ""
    addresses: tuple[str, ...] = ()
    confidence: ConfidenceReport | None = None
    claims: tuple[str, ...] = ()
    objections: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    tool_receipts: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    raw_transcript_refs: tuple[dict[str, Any], ...] = ()
    would_change_if: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_event_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = AGENT_INTERACTION_SCHEMA_VERSION
        return payload


def outcome_confidence_report(
    outcome: dict[str, Any] | None,
    *,
    source: str,
) -> ConfidenceReport:
    if not isinstance(outcome, dict):
        return ConfidenceReport(
            value=None,
            source=f"{source}_missing_outcome",
            criteria=("typed_outcome_missing",),
            rationale="No typed outcome was available to explain confidence.",
        )
    value = _coerce_confidence(outcome.get("confidence"))
    criteria = tuple(
        str(item)
        for item in outcome.get("confidence_criteria") or ()
        if str(item).strip()
    )
    rationale = str(outcome.get("confidence_rationale") or "").strip()
    evidence = tuple(
        str(item)
        for item in [
            *(outcome.get("tests") or []),
            *(outcome.get("changed_files") or []),
            *(outcome.get("decisions") or []),
        ]
        if str(item).strip()
    )
    return ConfidenceReport(
        value=value,
        source=f"{source}_self_reported",
        criteria=criteria or ("self_reported_without_explicit_criteria",),
        rationale=rationale or "Agent supplied a numeric confidence without an explicit rationale.",
        evidence=evidence,
    )


def codex_confidence_report(
    *,
    decision: str,
    gate_status: str,
    probe_statuses: dict[str, str],
    claim_verification: dict[str, Any] | None,
    cursor_review: dict[str, Any] | None,
) -> ConfidenceReport:
    failed_probes = sorted(
        probe_id
        for probe_id, status in probe_statuses.items()
        if status != "green"
    )
    criteria: list[str] = [
        f"gate_status={gate_status}",
        f"decision={decision}",
    ]
    evidence = [f"{probe_id}:{status}" for probe_id, status in sorted(probe_statuses.items())]
    value = 0.7
    rationale = "Codex requested revision because acceptance criteria were not all satisfied."

    if failed_probes:
        criteria.append("blocked_or_failed_probes=" + ",".join(failed_probes))
        value = 0.75
        rationale = "Codex denied advancement because one or more supervisor probes failed."
    elif claim_verification is not None and claim_verification.get("status") != "green":
        criteria.append("claim_verification_failed")
        evidence.append(str(claim_verification.get("reason") or "claim_verification_failed"))
        value = 0.8
        rationale = "Codex denied advancement because final claims lacked matching evidence."
    elif cursor_review is not None and not bool(cursor_review.get("accepted")):
        criteria.append("cursor_reviewer_rejected")
        evidence.append(str(cursor_review.get("reason") or "cursor_review_failed"))
        value = 0.8
        rationale = "Codex denied advancement because Cursor raised an unresolved review objection."
    elif decision == "accept" and gate_status == "accepted":
        criteria.extend([
            "all_supervisor_probes_green",
            "claude_outcome_accepted",
            "claim_verification_ok_or_not_required",
            "cursor_accepted_or_not_requested",
        ])
        value = 0.95
        rationale = "Codex accepted because the gate result, probes, claim checks, and optional Cursor review all satisfied the gate criteria."

    return ConfidenceReport(
        value=value,
        source="codex_supervisor_deterministic_policy",
        criteria=tuple(criteria),
        rationale=rationale,
        evidence=tuple(evidence),
    )


def planning_artifact_refs(planning_artifacts: list[dict[str, Any]] | tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for artifact in planning_artifacts:
        if isinstance(artifact, dict):
            refs.append({
                "kind": str(artifact.get("kind") or ""),
                "path": str(artifact.get("path") or ""),
                "mutable_by_worker": bool(artifact.get("mutable_by_worker", False)),
            })
            continue
        refs.append({
            "kind": str(getattr(artifact, "kind", "") or ""),
            "path": str(getattr(artifact, "path", "") or ""),
            "mutable_by_worker": bool(getattr(artifact, "mutable_by_worker", False)),
        })
    return tuple(refs)


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))
