"""Typed mailbox-style interaction events for agent collaboration traces."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


AGENT_INTERACTION_SCHEMA_VERSION = "dual-agent-interaction/v1"
CRITICAL_REVIEW_SCHEMA_VERSION = "critical-review/v1"


def critical_review_prompt(subject: str = "handoff") -> str:
    return (
        "Critical review: before accepting, try to disprove the "
        f"{subject}. Identify the strongest reason this should not advance, "
        "missing evidence, contradictions checked, assumptions to verify, and "
        "what would change your decision. Keep this concise and auditable; do "
        "not include private chain-of-thought."
    )


def normalize_critical_review(
    value: Any,
    *,
    decision: str = "",
    objections: tuple[str, ...] = (),
    evidence_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    would_change_if: str = "",
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    strongest = _text(source.get("strongest_objection"))
    if not strongest:
        strongest = next((item for item in objections if item), "none")
    return {
        "schema_version": str(source.get("schema_version") or CRITICAL_REVIEW_SCHEMA_VERSION),
        "strongest_objection": strongest,
        "missing_evidence": _text_list(source.get("missing_evidence")),
        "contradictions_checked": _text_list(source.get("contradictions_checked")),
        "assumptions_to_verify": _text_list(source.get("assumptions_to_verify")),
        "what_would_change_my_mind": _text(
            source.get("what_would_change_my_mind") or would_change_if
        ),
        "decision": _text(source.get("decision") or decision),
        "severity": _text(source.get("severity") or ("important" if objections else "none")),
        "evidence_refs": list(source.get("evidence_refs") or evidence_refs or []),
    }


def critical_review_from_outcome(
    outcome: dict[str, Any] | None,
    *,
    decision: str = "",
    evidence_refs: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    would_change_if: str = "",
) -> dict[str, Any]:
    if not isinstance(outcome, dict):
        return normalize_critical_review(
            {},
            decision=decision,
            evidence_refs=evidence_refs,
            would_change_if=would_change_if,
        )
    return normalize_critical_review(
        outcome.get("critical_review"),
        decision=decision or _first_text(outcome.get("decisions")),
        objections=tuple(str(item) for item in outcome.get("objections") or ()),
        evidence_refs=evidence_refs,
        would_change_if=would_change_if,
    )


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
    critical_review: dict[str, Any] | None = None
    review_packet: dict[str, Any] | None = None
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
        classification = _cursor_review_failure_classification(cursor_review)
        if classification:
            criteria.append("cursor_reviewer_infrastructure_failure")
            evidence.append(classification)
            value = 0.82
            rationale = (
                "Codex blocked advancement because Cursor review produced a "
                "recoverable infrastructure failure, not a valid review verdict."
            )
        else:
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


def codex_review_packet(
    *,
    task_id: str,
    gate: str,
    decision: str,
    confidence: ConfidenceReport,
    probe_statuses: dict[str, str],
    claim_verification: dict[str, Any] | None,
    cursor_review: dict[str, Any] | None,
    objection: str,
    evidence_refs: tuple[dict[str, Any], ...] = (),
    tool_receipts: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build Codex's structured reviewer packet for audit and replay."""
    requirements: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    def add_finding(
        *,
        severity: str,
        code: str,
        title: str,
        requirement_id: str,
        evidence: list[str],
        receipt_replay: dict[str, Any] | None = None,
    ) -> None:
        findings.append({
            "finding_id": f"finding-{len(findings) + 1:03d}",
            "severity": severity,
            "code": code,
            "title": title,
            "requirement_id": requirement_id,
            "ref": requirement_id,
            "fix": title,
            "evidence": evidence,
            "receipt_replay": receipt_replay or {
                "failures": [],
                "observed_receipt_ids": _receipt_ids(tool_receipts),
            },
        })

    for probe_id, status in sorted(probe_statuses.items()):
        requirement_id = f"probe.{probe_id}"
        evidence = [f"{probe_id}:{status}"]
        requirements.append({
            "requirement_id": requirement_id,
            "status": "pass" if status == "green" else "fail",
            "evidence": evidence,
        })
        claim_verification_failed = (
            claim_verification is not None
            and claim_verification.get("status") != "green"
        )
        if status != "green" and not (probe_id == "P11" and claim_verification_failed):
            add_finding(
                severity="CRITICAL" if probe_id in {"P3", "P11", "P12"} else "IMPORTANT",
                code=probe_id,
                title=f"probe {probe_id} failed",
                requirement_id=requirement_id,
                evidence=evidence,
            )
    if claim_verification is not None:
        details = claim_verification.get("details") if isinstance(claim_verification, dict) else {}
        details = details if isinstance(details, dict) else {}
        failures = [
            str(item)
            for item in details.get("failures", [])
            if item is not None
        ]
        observed_receipt_ids = _receipt_ids(details.get("receipts", [])) or _receipt_ids(tool_receipts)
        evidence = [str(claim_verification.get("reason") or "")]
        requirements.append({
            "requirement_id": "claim_verification.P11",
            "status": "pass" if claim_verification.get("status") == "green" else "fail",
            "evidence": evidence,
        })
        if claim_verification.get("status") != "green":
            add_finding(
                severity="CRITICAL",
                code="P11",
                title="claim verification failed",
                requirement_id="claim_verification.P11",
                evidence=evidence,
                receipt_replay={
                    "failures": failures,
                    "observed_receipt_ids": observed_receipt_ids,
                },
            )
    if cursor_review is not None:
        classification = _cursor_review_failure_classification(cursor_review)
        panel_decision = (
            cursor_review.get("independent_reviewer_panel_decision")
            if isinstance(cursor_review.get("independent_reviewer_panel_decision"), dict)
            else {}
        )
        panel_status = str(panel_decision.get("decision") or "")
        panel_reason = str(panel_decision.get("reason") or "")
        recovery = (
            cursor_review.get("reviewer_unavailable_recovery")
            if isinstance(cursor_review.get("reviewer_unavailable_recovery"), dict)
            else {}
        )
        degraded_reviewer_unavailable = (
            recovery.get("decision") == "proceed_degraded"
            and recovery.get("reviewer_verdict_counted_as_accept") is False
        )
        evidence = [classification or str((cursor_review.get("probe") or {}).get("reason") or "")]
        if panel_status:
            evidence.append(f"panel_decision={panel_status}:{panel_reason}")
        reviewer_status = (
            "pass"
            if bool(cursor_review.get("accepted")) and panel_status in {"", "accept"}
            else "degraded"
            if degraded_reviewer_unavailable
            else "fail"
        )
        requirements.append({
            "requirement_id": "independent_reviewer",
            "legacy_requirement_id": "cursor_review",
            "status": reviewer_status,
            "evidence": evidence,
        })
        if (
            bool(cursor_review.get("accepted"))
            and panel_status
            and panel_status != "accept"
            and not degraded_reviewer_unavailable
        ):
            add_finding(
                severity="IMPORTANT",
                code="REVIEWER_PANEL",
                title=f"independent reviewer panel did not accept: {panel_reason}",
                requirement_id="independent_reviewer",
                evidence=evidence,
            )
        elif not bool(cursor_review.get("accepted")) and not degraded_reviewer_unavailable:
            add_finding(
                severity="IMPORTANT",
                code="CURSOR_INFRA" if classification else "CURSOR",
                title=(
                    f"independent reviewer infrastructure failure: {classification}"
                    if classification else "independent reviewer rejected the gate"
                ),
                requirement_id="independent_reviewer",
                evidence=evidence,
            )
    blocking_findings = [
        finding["finding_id"]
        for finding in findings
        if str(finding.get("severity") or "").upper() in {"CRITICAL", "IMPORTANT"}
    ]
    strongest_objection = objection if decision != "accept" else "none"
    if findings:
        strongest_objection = str(findings[0].get("title") or strongest_objection)
    what_would_change = "Every requirement is pass and both reviewers accept."
    return {
        "schema_version": "codex-review-packet/v1",
        "task_id": task_id,
        "gate": gate,
        "reviewer": "codex",
        "decision": decision,
        "confidence": asdict(confidence),
        "requirements": requirements,
        "findings": findings,
        "round_policy": {
            "force_next_round": bool(blocking_findings),
            "blocking_findings": blocking_findings,
            "close_allowed": not blocking_findings,
        },
        "objections": [] if decision == "accept" else [objection],
        "evidence_refs": list(evidence_refs),
        "independent_reviewer_panel_decision": (
            cursor_review.get("independent_reviewer_panel_decision")
            if isinstance(cursor_review, dict)
            else None
        ),
        "independent_reviewer_results": (
            cursor_review.get("independent_reviewer_results")
            if isinstance(cursor_review, dict)
            else None
        ),
        "would_change_if": what_would_change,
        "critical_review": normalize_critical_review(
            {
                "strongest_objection": strongest_objection,
                "missing_evidence": [
                    str(finding.get("title") or "")
                    for finding in findings
                    if str(finding.get("title") or "").strip()
                ],
                "contradictions_checked": [
                    "supervisor probes",
                    "claim verification",
                    "cursor review",
                ],
                "assumptions_to_verify": [],
                "what_would_change_my_mind": what_would_change,
                "decision": decision,
                "severity": (
                    str(findings[0].get("severity") or "none").lower()
                    if findings else "none"
                ),
                "evidence_refs": list(evidence_refs),
            },
            decision=decision,
            evidence_refs=evidence_refs,
            would_change_if=what_would_change,
        ),
    }


def _receipt_ids(receipts: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(receipts, (list, tuple)):
        return ids
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        receipt_id = str(receipt.get("receipt_id") or receipt.get("id") or "").strip()
        if receipt_id:
            ids.append(receipt_id)
    return ids


def _cursor_review_failure_classification(cursor_review: dict[str, Any] | None) -> str:
    if not isinstance(cursor_review, dict):
        return ""
    classification = str(cursor_review.get("failure_classification") or "").strip()
    if classification:
        return classification
    probe = cursor_review.get("probe") if isinstance(cursor_review.get("probe"), dict) else {}
    reason = str(probe.get("reason") or "").strip()
    if reason in {"reviewer_contract_unmet", "reviewer_infrastructure_unavailable"}:
        return reason
    return ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _first_text(value: Any) -> str:
    items = _text_list(value)
    return items[0] if items else ""


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
