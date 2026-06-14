"""Supervisor-built review packets.

Pure builder/validator for ReviewPacket. The supervisor builds the packet
deterministically from supervisor-owned inputs (planning artifacts, git
name-status/diff refs, runtime-native receipts, changed-file hashes,
lesson/policy hashes, reviewer ids).

The implementer transcript is excluded by default; only an explicit
supervisor-owned dependency/context ref may carry an implementer reference.

Hashing is stable across identical inputs by canonical-JSON serialisation
of an explicit ordered subset of fields.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable


_REVIEW_PACKET_EVENT_KIND = "supervisor_review_packet_created"
_REVIEW_CONTEXT_RECEIPT_INCOMPLETE_KIND = "review_context_incomplete"


@dataclass(frozen=True)
class PlanningRef:
    """Reference to a supervisor-owned planning artifact."""

    kind: str
    path: str
    sha256: str


@dataclass(frozen=True)
class ChangedFile:
    """A git-changed file with supervisor-computed content hash."""

    path: str
    status: str  # e.g. "M", "A", "D", "R"
    sha256: str | None = None  # None when deleted


@dataclass(frozen=True)
class ReceiptRef:
    """Pointer to a supervisor-owned runtime-native evidence receipt."""

    receipt_id: str
    kind: str


@dataclass
class ReviewPacket:
    """A supervisor-built packet passed to an independent reviewer.

    Fields are populated only from supervisor state (planning artifacts,
    git, runtime evidence, policy/lesson hashes, reviewer registry).
    """

    task_id: str
    run_id: str
    gate: str
    packet_id: str
    base_head: str
    candidate_head: str | None
    patch_hash: str | None
    planning_refs: list[PlanningRef] = field(default_factory=list)
    acceptance_items: list[str] = field(default_factory=list)
    diff_refs: list[str] = field(default_factory=list)
    name_status_refs: list[str] = field(default_factory=list)
    changed_files: list[ChangedFile] = field(default_factory=list)
    runtime_receipt_ids: list[ReceiptRef] = field(default_factory=list)
    declared_tests: list[str] = field(default_factory=list)
    executed_test_receipt_ids: list[ReceiptRef] = field(default_factory=list)
    dependency_refs: list[str] = field(default_factory=list)
    policy_overlay_hash: str = ""
    lesson_hashes: list[str] = field(default_factory=list)
    reviewer_ids: list[str] = field(default_factory=list)
    # By default the supervisor excludes the implementer transcript. Callers
    # may opt-in by setting this to a supervisor-owned ref, never to raw
    # implementer narrative.
    implementer_transcript_ref: str | None = None
    packet_sha256: str = ""

    def to_event_payload(self) -> dict[str, Any]:
        """Render the packet as a ledger event payload."""
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "gate": self.gate,
            "packet_id": self.packet_id,
            "base_head": self.base_head,
            "candidate_head": self.candidate_head,
            "patch_hash": self.patch_hash,
            "planning_refs": [_planning_ref_dict(ref) for ref in self.planning_refs],
            "acceptance_items": list(self.acceptance_items),
            "diff_refs": list(self.diff_refs),
            "name_status_refs": list(self.name_status_refs),
            "changed_files": [_changed_file_dict(cf) for cf in self.changed_files],
            "runtime_receipt_ids": [_receipt_dict(r) for r in self.runtime_receipt_ids],
            "declared_tests": list(self.declared_tests),
            "executed_test_receipt_ids": [
                _receipt_dict(r) for r in self.executed_test_receipt_ids
            ],
            "dependency_refs": list(self.dependency_refs),
            "policy_overlay_hash": self.policy_overlay_hash,
            "lesson_hashes": list(self.lesson_hashes),
            "reviewer_ids": list(self.reviewer_ids),
            "implementer_transcript_ref": self.implementer_transcript_ref,
            "packet_sha256": self.packet_sha256,
        }


def _planning_ref_dict(ref: PlanningRef) -> dict[str, Any]:
    return {"kind": ref.kind, "path": ref.path, "sha256": ref.sha256}


def _changed_file_dict(cf: ChangedFile) -> dict[str, Any]:
    return {"path": cf.path, "status": cf.status, "sha256": cf.sha256}


def _receipt_dict(r: ReceiptRef) -> dict[str, Any]:
    return {"receipt_id": r.receipt_id, "kind": r.kind}


def _coerce_planning_refs(refs: Iterable[Any]) -> list[PlanningRef]:
    out: list[PlanningRef] = []
    for r in refs:
        if isinstance(r, PlanningRef):
            out.append(r)
            continue
        out.append(
            PlanningRef(
                kind=str(r.get("kind", "")),
                path=str(r.get("path", "")),
                sha256=str(r.get("sha256", "")),
            )
        )
    return out


def _coerce_changed_files(refs: Iterable[Any]) -> list[ChangedFile]:
    out: list[ChangedFile] = []
    for r in refs:
        if isinstance(r, ChangedFile):
            out.append(r)
            continue
        sha = r.get("sha256")
        out.append(
            ChangedFile(
                path=str(r.get("path", "")),
                status=str(r.get("status", "M")),
                sha256=None if sha is None else str(sha),
            )
        )
    return out


def _coerce_receipts(refs: Iterable[Any]) -> list[ReceiptRef]:
    out: list[ReceiptRef] = []
    for r in refs:
        if isinstance(r, ReceiptRef):
            out.append(r)
            continue
        out.append(
            ReceiptRef(
                receipt_id=str(r.get("receipt_id", "")),
                kind=str(r.get("kind", "")),
            )
        )
    return out


def _canonical_packet_input(packet: ReviewPacket) -> dict[str, Any]:
    """Return the canonical hash input for a packet.

    Excludes packet_sha256 itself. Sorts list fields where ordering would
    otherwise be presentation noise (changed_files, acceptance_items,
    declared_tests, lesson_hashes, reviewer_ids, receipt ids). Includes
    implementer_transcript_ref because its presence/absence is meaningful.
    """
    return {
        "task_id": packet.task_id,
        "run_id": packet.run_id,
        "gate": packet.gate,
        "packet_id": packet.packet_id,
        "base_head": packet.base_head,
        "candidate_head": packet.candidate_head,
        "patch_hash": packet.patch_hash,
        "planning_refs": sorted(
            [_planning_ref_dict(ref) for ref in packet.planning_refs],
            key=lambda d: (d["kind"], d["path"]),
        ),
        "acceptance_items": sorted(set(packet.acceptance_items)),
        "diff_refs": sorted(set(packet.diff_refs)),
        "name_status_refs": sorted(set(packet.name_status_refs)),
        "changed_files": sorted(
            [_changed_file_dict(cf) for cf in packet.changed_files],
            key=lambda d: d["path"],
        ),
        "runtime_receipt_ids": sorted(
            [_receipt_dict(r) for r in packet.runtime_receipt_ids],
            key=lambda d: d["receipt_id"],
        ),
        "declared_tests": sorted(set(packet.declared_tests)),
        "executed_test_receipt_ids": sorted(
            [_receipt_dict(r) for r in packet.executed_test_receipt_ids],
            key=lambda d: d["receipt_id"],
        ),
        "dependency_refs": sorted(set(packet.dependency_refs)),
        "policy_overlay_hash": packet.policy_overlay_hash,
        "lesson_hashes": sorted(set(packet.lesson_hashes)),
        "reviewer_ids": sorted(set(packet.reviewer_ids)),
        "implementer_transcript_ref": packet.implementer_transcript_ref,
    }


def compute_packet_sha256(packet: ReviewPacket) -> str:
    """Compute a stable SHA256 over the canonical packet inputs."""
    canonical = json.dumps(
        _canonical_packet_input(packet),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def build_review_packet(
    *,
    task_id: str,
    run_id: str,
    gate: str,
    packet_id: str,
    base_head: str,
    candidate_head: str | None = None,
    patch_hash: str | None = None,
    planning_refs: Iterable[Any] = (),
    acceptance_items: Iterable[str] = (),
    diff_refs: Iterable[str] = (),
    name_status_refs: Iterable[str] = (),
    changed_files: Iterable[Any] = (),
    runtime_receipt_ids: Iterable[Any] = (),
    declared_tests: Iterable[str] = (),
    executed_test_receipt_ids: Iterable[Any] = (),
    dependency_refs: Iterable[str] = (),
    policy_overlay_hash: str = "",
    lesson_hashes: Iterable[str] = (),
    reviewer_ids: Iterable[str] = (),
    implementer_transcript_ref: str | None = None,
) -> ReviewPacket:
    """Build a ReviewPacket and stamp a stable packet_sha256."""
    packet = ReviewPacket(
        task_id=task_id,
        run_id=run_id,
        gate=gate,
        packet_id=packet_id,
        base_head=base_head,
        candidate_head=candidate_head,
        patch_hash=patch_hash,
        planning_refs=_coerce_planning_refs(planning_refs),
        acceptance_items=list(acceptance_items),
        diff_refs=list(diff_refs),
        name_status_refs=list(name_status_refs),
        changed_files=_coerce_changed_files(changed_files),
        runtime_receipt_ids=_coerce_receipts(runtime_receipt_ids),
        declared_tests=list(declared_tests),
        executed_test_receipt_ids=_coerce_receipts(executed_test_receipt_ids),
        dependency_refs=list(dependency_refs),
        policy_overlay_hash=policy_overlay_hash,
        lesson_hashes=list(lesson_hashes),
        reviewer_ids=list(reviewer_ids),
        implementer_transcript_ref=implementer_transcript_ref,
    )
    packet.packet_sha256 = compute_packet_sha256(packet)
    return packet


@dataclass
class PacketValidationFailure:
    """A single packet validation failure reason."""

    reason: str
    detail: str


def validate_review_packet(
    packet: ReviewPacket,
    *,
    expected_changed_files: Iterable[str],
    expected_acceptance_items: Iterable[str],
    expected_declared_tests: Iterable[str] = (),
    supervisor_runtime_receipt_ids: Iterable[str] = (),
) -> list[PacketValidationFailure]:
    """Validate that the packet covers every supervisor-owned expectation.

    `expected_changed_files` should come from supervisor-computed git
    name-status, not from implementer claims. `expected_acceptance_items`
    should come from the supervisor-loaded TDD acceptance plan.

    Returns an empty list when valid.
    """
    failures: list[PacketValidationFailure] = []

    packet_files = {cf.path for cf in packet.changed_files}
    for path in expected_changed_files:
        if path not in packet_files:
            failures.append(
                PacketValidationFailure(
                    reason="review_packet_changed_file_missing",
                    detail=path,
                )
            )

    packet_items = set(packet.acceptance_items)
    for item in expected_acceptance_items:
        if item not in packet_items:
            failures.append(
                PacketValidationFailure(
                    reason="review_packet_acceptance_item_missing",
                    detail=item,
                )
            )

    packet_tests = set(packet.declared_tests)
    for test_name in expected_declared_tests:
        if test_name not in packet_tests:
            failures.append(
                PacketValidationFailure(
                    reason="review_packet_declared_test_missing",
                    detail=test_name,
                )
            )

    trusted_runtime_receipts = set(supervisor_runtime_receipt_ids)
    for receipt in packet.runtime_receipt_ids:
        if trusted_runtime_receipts and receipt.receipt_id not in trusted_runtime_receipts:
            failures.append(
                PacketValidationFailure(
                    reason="review_packet_runtime_receipt_not_supervisor_originated",
                    detail=receipt.receipt_id,
                )
            )

    return failures


@dataclass
class ReviewerContextReceipt:
    """A reviewer's self-report of which packet context it inspected."""

    reviewer_id: str
    files_reviewed: list[str] = field(default_factory=list)
    criteria_checked: list[str] = field(default_factory=list)
    receipts_considered: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "files_reviewed": list(self.files_reviewed),
            "criteria_checked": list(self.criteria_checked),
            "receipts_considered": list(self.receipts_considered),
            "assumptions": list(self.assumptions),
            "missing_context": list(self.missing_context),
        }


@dataclass
class ContextReceiptValidation:
    """Outcome of validating a reviewer context receipt against a packet."""

    complete: bool
    incomplete_reason: str | None
    missing_changed_files: list[str]
    missing_acceptance_items: list[str]
    missing_runtime_receipts: list[str]


def validate_reviewer_context_receipt(
    packet: ReviewPacket,
    receipt: ReviewerContextReceipt,
) -> ContextReceiptValidation:
    """Confirm the receipt covers every changed file, acceptance item, and
    runtime receipt from the packet."""
    packet_files = {cf.path for cf in packet.changed_files}
    receipt_files = set(receipt.files_reviewed)
    missing_files = sorted(packet_files - receipt_files)

    packet_items = set(packet.acceptance_items)
    receipt_items = set(receipt.criteria_checked)
    missing_items = sorted(packet_items - receipt_items)

    packet_receipts = {r.receipt_id for r in packet.runtime_receipt_ids}
    receipt_receipts = set(receipt.receipts_considered)
    missing_receipts = sorted(packet_receipts - receipt_receipts)

    complete = not (missing_files or missing_items or missing_receipts)
    reason = None if complete else _REVIEW_CONTEXT_RECEIPT_INCOMPLETE_KIND
    return ContextReceiptValidation(
        complete=complete,
        incomplete_reason=reason,
        missing_changed_files=missing_files,
        missing_acceptance_items=missing_items,
        missing_runtime_receipts=missing_receipts,
    )


def reviewer_context_receipt_from_payload(
    *,
    reviewer_id: str,
    payload: dict[str, Any] | None,
) -> ReviewerContextReceipt | None:
    """Extract a reviewer context receipt from a reviewer result payload.

    The current typed `Outcome` schema has no top-level context-receipt field,
    so reviewers place it under `critical_review.reviewer_context_receipt`.
    Returning None lets legacy reviewer outputs remain readable; callers can
    decide whether absence should block for a specific gate.
    """
    if not isinstance(payload, dict):
        return None
    source = payload.get("reviewer_context_receipt")
    if source is None and isinstance(payload.get("critical_review"), dict):
        source = payload["critical_review"].get("reviewer_context_receipt")
    if not isinstance(source, dict):
        return None
    return ReviewerContextReceipt(
        reviewer_id=str(source.get("reviewer_id") or reviewer_id),
        files_reviewed=_text_list(source.get("files_reviewed")),
        criteria_checked=_text_list(source.get("criteria_checked")),
        receipts_considered=_text_list(source.get("receipts_considered")),
        assumptions=_text_list(source.get("assumptions")),
        missing_context=_text_list(source.get("missing_context")),
    )


def context_validation_payload(
    *,
    packet: ReviewPacket,
    reviewer_id: str,
    receipt: ReviewerContextReceipt,
    validation: ContextReceiptValidation,
) -> dict[str, Any]:
    """Render a replayable context validation result for the ledger."""
    return {
        "schema_version": "supervisor-review-context-validation/v1",
        "task_id": packet.task_id,
        "run_id": packet.run_id,
        "gate": packet.gate,
        "packet_id": packet.packet_id,
        "packet_sha256": packet.packet_sha256,
        "reviewer_id": reviewer_id,
        "receipt": receipt.to_payload(),
        "complete": validation.complete,
        "reason": validation.incomplete_reason,
        "missing_changed_files": list(validation.missing_changed_files),
        "missing_acceptance_items": list(validation.missing_acceptance_items),
        "missing_runtime_receipts": list(validation.missing_runtime_receipts),
    }


def review_packet_event_kind() -> str:
    """Event kind for the supervisor-built ReviewPacket creation event."""
    return _REVIEW_PACKET_EVENT_KIND


def review_context_incomplete_reason() -> str:
    """Failure reason emitted when a reviewer's context receipt is incomplete."""
    return _REVIEW_CONTEXT_RECEIPT_INCOMPLETE_KIND


def packet_includes_implementer_transcript(packet: ReviewPacket) -> bool:
    """Default-state check: a packet must not silently include implementer
    transcript unless an explicit supervisor-owned dependency/context ref is
    set on `implementer_transcript_ref`.
    """
    return packet.implementer_transcript_ref is not None


def read_only_changed_file_contents(
    packet: ReviewPacket,
    changed_file_path: str,
    *,
    contents_reader,
) -> str:
    """Return read-only contents for a packet-listed changed file.

    The `contents_reader` is a callable provided by the supervisor that
    returns content bytes for a given path; the reviewer never receives
    write access. The returned content's hash must match the packet's
    changed-file hash, otherwise a ValueError is raised.
    """
    target: ChangedFile | None = None
    for cf in packet.changed_files:
        if cf.path == changed_file_path:
            target = cf
            break
    if target is None:
        raise KeyError(f"changed file {changed_file_path!r} not in packet")
    raw = contents_reader(changed_file_path)
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
        text = raw
    else:
        raw_bytes = bytes(raw)
        text = raw_bytes.decode("utf-8", errors="replace")
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if target.sha256 and target.sha256 != actual_sha:
        raise ValueError(
            "changed file content hash mismatch: "
            f"packet={target.sha256!r} actual={actual_sha!r}"
        )
    return text


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return [str(value)] if str(value).strip() else []
    return [str(item).strip() for item in value if str(item).strip()]
