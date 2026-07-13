"""Deterministic event-sourced rebuilds for the quality-trend projection."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


QUALITY_TREND_PROJECTION_EVENT = "supervisor_quality_trend_projection"
QUALITY_TREND_PROJECTION_SCHEMA_VERSION = "quality-trend-projection/v1"
QUALITY_TREND_PROJECTION_SOURCES = frozenset(
    {"quality_trends", "schema_migration"}
)

_REQUIRED_FIELDS = frozenset(
    {
        "run_id",
        "task_id",
        "task_class",
        "gate",
        "accepted",
        "first_pass_accepted",
        "revision_rounds",
        "time_to_accepted_outcome_s",
        "p11_audit_sample_size",
        "false_accept_count",
        "false_accept_denominator",
        "false_accept_rate",
        "policy_overlay_hash",
        "policy_proposal_id",
        "details",
        "computed_at",
    }
)


def canonical_quality_trend_projection_row(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {key: row.get(key) for key in _REQUIRED_FIELDS}
    missing = sorted(
        key for key in _REQUIRED_FIELDS if key not in row
    )
    if missing:
        raise ValueError(
            "quality trend projection row is missing fields: "
            + ", ".join(missing)
        )
    payload["run_id"] = str(payload["run_id"])
    payload["task_id"] = str(payload["task_id"])
    payload["task_class"] = str(payload["task_class"])
    payload["gate"] = str(payload["gate"])
    payload["accepted"] = bool(payload["accepted"])
    payload["first_pass_accepted"] = bool(
        payload["first_pass_accepted"]
    )
    payload["revision_rounds"] = int(payload["revision_rounds"] or 0)
    if payload["time_to_accepted_outcome_s"] is not None:
        payload["time_to_accepted_outcome_s"] = float(
            payload["time_to_accepted_outcome_s"]
        )
    payload["p11_audit_sample_size"] = int(
        payload["p11_audit_sample_size"] or 0
    )
    payload["false_accept_count"] = int(
        payload["false_accept_count"] or 0
    )
    payload["false_accept_denominator"] = int(
        payload["false_accept_denominator"] or 0
    )
    payload["false_accept_rate"] = float(
        payload["false_accept_rate"] or 0.0
    )
    payload["policy_overlay_hash"] = str(
        payload["policy_overlay_hash"] or ""
    )
    payload["policy_proposal_id"] = str(
        payload["policy_proposal_id"] or ""
    )
    details = payload["details"]
    payload["details"] = dict(details) if isinstance(details, Mapping) else {}
    payload["computed_at"] = int(payload["computed_at"] or 0)
    return payload


def quality_trend_projection_event_payload(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": QUALITY_TREND_PROJECTION_SCHEMA_VERSION,
        "projection_row": canonical_quality_trend_projection_row(row),
    }


def assert_generic_event_kind_allowed(kind: str) -> None:
    if str(kind) == QUALITY_TREND_PROJECTION_EVENT:
        raise ValueError(
            "reserved projection event kinds require the dedicated "
            "quality-trend writer"
        )


def rebuild_quality_trend_projection(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    projection: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if str(event.get("kind") or "") != QUALITY_TREND_PROJECTION_EVENT:
            continue
        source = str(event.get("source") or "")
        if source not in QUALITY_TREND_PROJECTION_SOURCES:
            raise ValueError(
                "quality trend projection event has an unauthorized source"
            )
        raw_payload = event.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise ValueError("quality trend projection event payload is invalid")
        if (
            raw_payload.get("schema_version")
            != QUALITY_TREND_PROJECTION_SCHEMA_VERSION
        ):
            raise ValueError(
                "unsupported quality trend projection event schema"
            )
        raw_row = raw_payload.get("projection_row")
        if not isinstance(raw_row, Mapping):
            raise ValueError(
                "quality trend projection event row is invalid"
            )
        row = canonical_quality_trend_projection_row(raw_row)
        outer_run_id = str(event.get("run_id") or "")
        if not outer_run_id or outer_run_id != row["run_id"]:
            raise ValueError(
                "quality trend projection event run_id does not match its "
                "projection row"
            )
        projection[(row["run_id"], row["gate"])] = row
    return [
        projection[key]
        for key in sorted(projection)
    ]


__all__ = [
    "QUALITY_TREND_PROJECTION_EVENT",
    "QUALITY_TREND_PROJECTION_SCHEMA_VERSION",
    "QUALITY_TREND_PROJECTION_SOURCES",
    "assert_generic_event_kind_allowed",
    "canonical_quality_trend_projection_row",
    "quality_trend_projection_event_payload",
    "rebuild_quality_trend_projection",
]
