"""Typed worker/reviewer dispatch lifecycle ledger helpers.

Pure builders/validators that produce payloads suitable for the supervisor
ledger via ``State.write_event(kind=..., payload=...)``. The supervisor
ledger is the single source of truth; scratch files are not.

Includes roster preflight payloads and an ``EvidenceAttempt`` linkage
helper for read-only worker findings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable


WORKER_ROSTER_CHECKED = "supervisor_worker_roster_checked"
WORKER_SESSION_CREATED = "supervisor_worker_session_created"
WORKER_DISPATCHED = "supervisor_worker_dispatched"
WORKER_COMPLETED = "supervisor_worker_completed"
WORKER_FAILED = "supervisor_worker_failed"
WORKER_BLOCKED = "supervisor_worker_blocked"
WORKER_CANCELLED = "supervisor_worker_cancelled"
CROSS_VENDOR_REVIEW_SELECTED = "supervisor_cross_vendor_review_selected"
DEGRADED_REVIEW_UNAVAILABLE = "supervisor_degraded_review_unavailable"
EVIDENCE_ATTEMPT_RECORDED = "supervisor_evidence_attempt_recorded"


# Status values used in lifecycle payloads. The ledger remains the truth,
# so these constants exist only to keep callers consistent.
WORKER_STATUS_DISPATCHED = "dispatched"
WORKER_STATUS_SESSION_CREATED = "session_created"
WORKER_STATUS_COMPLETED = "completed"
WORKER_STATUS_FAILED = "failed"
WORKER_STATUS_BLOCKED = "blocked"
WORKER_STATUS_CANCELLED = "cancelled"


_REQUIRED_LIFECYCLE_FIELDS = (
    "task_id",
    "run_id",
    "gate",
    "worker_id",
    "purpose",
    "provider_family",
    "runtime",
    "model",
    "status",
)


@dataclass
class WorkerDispatch:
    """A single worker/reviewer lifecycle record.

    Required fields cover the supervisor-owned context (task_id, run_id,
    gate, worker_id, purpose, provider_family, runtime, model, status).
    Optional fields describe timing/cost and any artifacts/refs.
    """

    task_id: str
    run_id: str
    gate: str
    worker_id: str
    purpose: str
    provider_family: str
    runtime: str
    model: str
    status: str
    reviewer_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    wall_clock_s: float | None = None
    cost_usd: float | None = None
    evidence_attempt_id: str | None = None
    receipt_ids: list[str] = field(default_factory=list)
    output_ref: str | None = None
    transcript_ref: str | None = None
    output_hash: str | None = None
    transcript_hash: str | None = None
    worktree_ref: str | None = None
    branch: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    session_ref: str | None = None
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def build_dispatch_payload(record: WorkerDispatch) -> dict[str, Any]:
    """Return the canonical payload dict for a worker dispatch event."""
    payload: dict[str, Any] = {
        "task_id": record.task_id,
        "run_id": record.run_id,
        "gate": record.gate,
        "worker_id": record.worker_id,
        "reviewer_id": record.reviewer_id,
        "purpose": record.purpose,
        "provider_family": record.provider_family,
        "runtime": record.runtime,
        "model": record.model,
        "status": record.status,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "duration_ms": record.duration_ms,
        "wall_clock_s": record.wall_clock_s,
        "cost_usd": record.cost_usd,
        "evidence_attempt_id": record.evidence_attempt_id,
        "receipt_ids": list(record.receipt_ids),
        "output_ref": record.output_ref,
        "transcript_ref": record.transcript_ref,
        "output_hash": record.output_hash,
        "transcript_hash": record.transcript_hash,
        "worktree_ref": record.worktree_ref,
        "branch": record.branch,
        "session_id": record.session_id,
        "conversation_id": record.conversation_id,
        "session_ref": record.session_ref,
        "reason": record.reason,
    }
    if record.extra:
        payload["extra"] = dict(record.extra)
    return payload


def validate_lifecycle_payload(payload: dict[str, Any]) -> list[str]:
    """Return a list of missing required field names for a lifecycle payload.

    Empty list means the payload is well-formed for the supervisor ledger.
    """
    missing: list[str] = []
    for key in _REQUIRED_LIFECYCLE_FIELDS:
        value = payload.get(key)
        if value is None or value == "":
            missing.append(key)
    return missing


def event_kind_for_status(status: str) -> str:
    """Map a worker status to the canonical lifecycle event kind."""
    mapping = {
        WORKER_STATUS_SESSION_CREATED: WORKER_SESSION_CREATED,
        WORKER_STATUS_DISPATCHED: WORKER_DISPATCHED,
        WORKER_STATUS_COMPLETED: WORKER_COMPLETED,
        WORKER_STATUS_FAILED: WORKER_FAILED,
        WORKER_STATUS_BLOCKED: WORKER_BLOCKED,
        WORKER_STATUS_CANCELLED: WORKER_CANCELLED,
    }
    if status not in mapping:
        raise ValueError(f"unknown worker status: {status!r}")
    return mapping[status]


@dataclass
class RosterEntry:
    """One probed worker/reviewer in a roster preflight."""

    worker_id: str
    purpose: str
    provider_family: str
    runtime: str
    model: str
    available: bool
    boot_status: str = "ok"
    failure_reason: str | None = None


@dataclass
class RosterPreflight:
    """Result of a roster preflight probe before fan-out/routing."""

    task_id: str
    run_id: str
    gate: str
    entries: list[RosterEntry] = field(default_factory=list)

    def available_worker_ids(self) -> list[str]:
        return [e.worker_id for e in self.entries if e.available]

    def unavailable_worker_ids(self) -> list[str]:
        return [e.worker_id for e in self.entries if not e.available]

    def boot_failed_worker_ids(self) -> list[str]:
        return [
            e.worker_id
            for e in self.entries
            if not e.available and e.boot_status not in {"", "ok", "available"}
        ]


def build_roster_payload(preflight: RosterPreflight) -> dict[str, Any]:
    """Render a roster preflight as a ledger payload."""
    return {
        "task_id": preflight.task_id,
        "run_id": preflight.run_id,
        "gate": preflight.gate,
        "available": [
            _roster_entry_dict(e) for e in preflight.entries if e.available
        ],
        "unavailable": [
            _roster_entry_dict(e) for e in preflight.entries if not e.available
        ],
        "available_worker_ids": preflight.available_worker_ids(),
        "unavailable_worker_ids": preflight.unavailable_worker_ids(),
        "boot_failed_worker_ids": preflight.boot_failed_worker_ids(),
    }


def _roster_entry_dict(entry: RosterEntry) -> dict[str, Any]:
    return {
        "worker_id": entry.worker_id,
        "purpose": entry.purpose,
        "provider_family": entry.provider_family,
        "runtime": entry.runtime,
        "model": entry.model,
        "available": entry.available,
        "boot_status": entry.boot_status,
        "failure_reason": entry.failure_reason,
    }


@dataclass
class EvidenceAttempt:
    """A ledger-backed evidence attempt linked to a worker dispatch.

    Used so that read-only worker findings (probes, lints, lookups) are
    surfaced as supervisor-owned evidence and not as bespoke scratch files.
    """

    attempt_id: str
    task_id: str
    run_id: str
    gate: str
    worker_id: str
    purpose: str
    finding_kind: str
    receipt_ids: list[str] = field(default_factory=list)
    output_hash: str | None = None
    transcript_hash: str | None = None
    finding_summary: str | None = None


def link_evidence_attempt(
    dispatch: WorkerDispatch,
    attempt: EvidenceAttempt,
) -> WorkerDispatch:
    """Attach an EvidenceAttempt id to a worker dispatch record in place."""
    if dispatch.task_id != attempt.task_id or dispatch.run_id != attempt.run_id:
        raise ValueError(
            "evidence attempt task_id/run_id must match dispatch record"
        )
    dispatch.evidence_attempt_id = attempt.attempt_id
    merged = list(dispatch.receipt_ids)
    for rid in attempt.receipt_ids:
        if rid not in merged:
            merged.append(rid)
    dispatch.receipt_ids = merged
    return dispatch


def build_evidence_attempt_payload(attempt: EvidenceAttempt) -> dict[str, Any]:
    """Return a ledger payload for an EvidenceAttempt record."""
    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "run_id": attempt.run_id,
        "gate": attempt.gate,
        "worker_id": attempt.worker_id,
        "purpose": attempt.purpose,
        "finding_kind": attempt.finding_kind,
        "receipt_ids": list(attempt.receipt_ids),
        "output_hash": attempt.output_hash,
        "transcript_hash": attempt.transcript_hash,
        "finding_summary": attempt.finding_summary,
    }


def compute_attempt_id(
    *,
    task_id: str,
    run_id: str,
    gate: str,
    worker_id: str,
    purpose: str,
    finding_kind: str,
    receipt_ids: Iterable[str] = (),
) -> str:
    """Compute a stable attempt_id from supervisor-owned inputs."""
    payload = {
        "task_id": task_id,
        "run_id": run_id,
        "gate": gate,
        "worker_id": worker_id,
        "purpose": purpose,
        "finding_kind": finding_kind,
        "receipt_ids": sorted(set(receipt_ids)),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()[:32]
    return f"attempt-{digest}"


@dataclass
class CrossVendorSelection:
    """Cross-vendor reviewer selection outcome."""

    task_id: str
    run_id: str
    gate: str
    implementation_provider_family: str
    selected_reviewer_id: str | None
    selected_provider_family: str | None
    selection_status: str  # "selected", "block", "degraded"
    reason: str | None = None


def build_cross_vendor_payload(sel: CrossVendorSelection) -> dict[str, Any]:
    return {
        "task_id": sel.task_id,
        "run_id": sel.run_id,
        "gate": sel.gate,
        "implementation_provider_family": sel.implementation_provider_family,
        "selected_reviewer_id": sel.selected_reviewer_id,
        "selected_provider_family": sel.selected_provider_family,
        "selection_status": sel.selection_status,
        "reason": sel.reason,
    }


def select_cross_vendor_reviewer(
    *,
    implementation_provider_family: str,
    roster: RosterPreflight,
    policy: str,
    task_id: str,
    run_id: str,
    gate: str,
) -> CrossVendorSelection:
    """Pick an available reviewer whose provider_family differs from the
    implementation provider. Honours roster availability first.

    policy values: ``block``, ``degraded``, ``escalate``.
    """
    candidates = [
        e
        for e in roster.entries
        if e.available
        and e.purpose == "reviewer"
        and e.provider_family != implementation_provider_family
        and e.provider_family not in ("", "unknown")
    ]
    if candidates:
        chosen = candidates[0]
        return CrossVendorSelection(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            implementation_provider_family=implementation_provider_family,
            selected_reviewer_id=chosen.worker_id,
            selected_provider_family=chosen.provider_family,
            selection_status="selected",
            reason=None,
        )
    if policy == "block":
        return CrossVendorSelection(
            task_id=task_id,
            run_id=run_id,
            gate=gate,
            implementation_provider_family=implementation_provider_family,
            selected_reviewer_id=None,
            selected_provider_family=None,
            selection_status="block",
            reason="cross_vendor_review_unavailable",
        )
    return CrossVendorSelection(
        task_id=task_id,
        run_id=run_id,
        gate=gate,
        implementation_provider_family=implementation_provider_family,
        selected_reviewer_id=None,
        selected_provider_family=None,
        selection_status="degraded",
        reason="degraded_review_unavailable",
    )


def provider_family_for(runtime: str | None, model: str | None) -> str:
    """Public wrapper around the reviewer registry's family classifier.

    Roster preflight needs the same classification logic the reviewer
    registry uses for adjudication; this keeps both callers consistent.
    """
    from supervisor.reviewer_registry import _provider_family

    return _provider_family(runtime, model)
