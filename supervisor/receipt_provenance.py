"""Receipt provenance helpers for supervisor-owned evidence boundaries."""
from __future__ import annotations

from typing import Any


SUPERVISOR_RUNTIME_EVIDENCE_MARKER = "_supervisor_runtime_evidence"
SUPERVISOR_RUNTIME_EVIDENCE_ORIGIN = "collect_runtime_evidence"


def mark_supervisor_runtime_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Mark a receipt created in-process by runtime evidence collection."""
    item = dict(receipt)
    item[SUPERVISOR_RUNTIME_EVIDENCE_MARKER] = True
    item["supervisor_runtime_origin"] = SUPERVISOR_RUNTIME_EVIDENCE_ORIGIN
    return item


def sanitize_receipt_provenance(
    receipt: dict[str, Any],
    *,
    trusted_runtime_receipt_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Downgrade caller-stamped supervisor/runtime-native provenance.

    The marker in runtime evidence receipts is not a secret. Trust is granted only
    when the caller also supplies the receipt id set produced by the current
    in-process runtime-evidence invocation.
    """
    item = dict(receipt)
    trusted_ids = trusted_runtime_receipt_ids or set()
    receipt_id = _receipt_id(item)
    trusted_runtime_receipt = (
        bool(receipt_id)
        and receipt_id in trusted_ids
        and item.get(SUPERVISOR_RUNTIME_EVIDENCE_MARKER) is True
        and item.get("supervisor_runtime_origin") == SUPERVISOR_RUNTIME_EVIDENCE_ORIGIN
    )

    reasons: list[str] = []
    source = _normalise_receipt_value(item.get("source"))
    evidence_grade = _normalise_receipt_value(item.get("evidence_grade"))

    if source == "supervisor" and not trusted_runtime_receipt:
        item["claimed_source"] = item.get("source")
        item["source"] = "caller_claimed_supervisor"
        reasons.append("caller_claimed_supervisor_source")
    if evidence_grade == "runtime_native" and not trusted_runtime_receipt:
        item["claimed_evidence_grade"] = item.get("evidence_grade")
        item["evidence_grade"] = "self_reported"
        reasons.append("caller_claimed_runtime_native_grade")

    if not trusted_runtime_receipt:
        item.pop(SUPERVISOR_RUNTIME_EVIDENCE_MARKER, None)
        item.pop("supervisor_runtime_origin", None)

    if reasons:
        item["provenance_downgraded"] = True
        item["downgrade_reasons"] = sorted(dict.fromkeys([
            *[str(reason) for reason in item.get("downgrade_reasons") or []],
            *reasons,
        ]))
    return item


def provenance_downgrade_event_payload(
    receipt: dict[str, Any],
    *,
    task_id: str,
    run_id: str,
    scope: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "run_id": run_id,
        "scope": scope,
        "receipt_id": receipt.get("receipt_id") or receipt.get("id"),
        "kind": receipt.get("kind") or receipt.get("type"),
        "claimed_source": receipt.get("claimed_source"),
        "effective_source": receipt.get("source"),
        "claimed_evidence_grade": receipt.get("claimed_evidence_grade"),
        "effective_evidence_grade": receipt.get("evidence_grade"),
        "downgrade_reasons": list(receipt.get("downgrade_reasons") or []),
    }


def _receipt_id(receipt: dict[str, Any]) -> str:
    return str(receipt.get("receipt_id") or receipt.get("id") or "").strip()


def _normalise_receipt_value(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
