"""Read-only observability sink for auto-evolve benchmark artifacts."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .auto_evolve_benchmark_ledger import (
    AUTHORITY_FLAGS_FALSE,
    append_auto_evolve_benchmark_event,
)


AUTO_EVOLVE_OBSERVABILITY_SINK_SCHEMA_VERSION = (
    "supervisor-auto-evolve-observability-sink/v1"
)

_AUTHORITY_FIELDS = frozenset(AUTHORITY_FLAGS_FALSE)
_EVALUATOR_PROVENANCE_FIELDS = frozenset({
    "evaluator_run_ref",
    "evaluator_run_hash",
    "quality_controls",
    "evaluator_quality",
    "empty_floor_comparison",
    "candidate_overlay_ref",
    "policy_overlay_candidate_ref",
})
_ROSTER_AUTHORITY_FIELDS = frozenset({
    "effective_vote_estimate",
    "roster_selection",
    "selected_reviewers",
    "pairwise_oracle_error_overlap",
})


def ingest_auto_evolve_observability_sink(
    *,
    aeb0_artifact_path: str | Path | None,
    output_dir: str | Path,
    sink_kind: str = "langfuse",
    sink_payload: Mapping[str, Any] | None = None,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a local read-only sink report from an existing AEB-0 artifact.

    This adapter does not call Langfuse, Opik, or any remote API. Slice 1 is an
    ingest contract: normalize source artifacts and prove sink-origin metadata
    cannot enter the benchmark trust path.
    """
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    payload = dict(sink_payload or {})
    blocked_reasons: list[str] = []
    source_aeb0: dict[str, Any] | None = None
    aeb0_path: Path | None = None

    if not aeb0_artifact_path:
        blocked_reasons.append("aeb0_artifact_ref_required")
    else:
        aeb0_path = Path(aeb0_artifact_path).expanduser()
        if not aeb0_path.exists() or not aeb0_path.is_file():
            blocked_reasons.append("aeb0_artifact_missing")
        else:
            source_aeb0 = json.loads(aeb0_path.read_text(encoding="utf-8"))

    source_flags = _source_authority_flags(source_aeb0)
    rejected_fields = _rejected_sink_fields(payload)
    warnings = _sink_payload_warnings(payload)
    report: dict[str, Any] = {
        "schema_version": AUTO_EVOLVE_OBSERVABILITY_SINK_SCHEMA_VERSION,
        "status": "blocked" if blocked_reasons else "reported",
        "sink_kind": str(sink_kind),
        "sink_recommendation": _sink_recommendation(str(sink_kind)),
        "blocked_reasons": list(blocked_reasons),
        "warnings": warnings,
        "source_artifacts": {
            "aeb0": _aeb0_source_artifact(aeb0_path, source_aeb0),
        },
        "source_authority_flags": source_flags,
        "sink_authority_flags": dict(AUTHORITY_FLAGS_FALSE),
        "rejected_sink_fields": rejected_fields,
        "accepted_sink_fields": sorted(
            str(key)
            for key in payload
            if key not in {item["field"] for item in rejected_fields}
        ),
        "trust_path": _trust_path_boundary(),
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "powered_improvement_claim_allowed": False,
        "human_mergeability_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
            "operator_review_required": True,
        },
    }
    report_path = output_path / "observability_sink_report.json"
    report["report_path"] = str(report_path)
    report["report_sha256"] = _sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    if ledger_path:
        dataset = source_aeb0.get("dataset") if isinstance(source_aeb0, Mapping) else ""
        split = source_aeb0.get("dataset_split") if isinstance(source_aeb0, Mapping) else ""
        attempt_stage = (
            source_aeb0.get("attempt_stage") if isinstance(source_aeb0, Mapping) else ""
        )
        append_auto_evolve_benchmark_event(
            ledger_path,
            stage="observability_sink",
            promise_ids=("P0", "P1", "P10"),
            status=report["status"],
            artifact_path=report_path,
            aeb0_artifact_ref=aeb0_path,
            blocked_reasons=blocked_reasons,
            dataset=str(dataset or "unknown"),
            split=str(split or "unknown"),
            dataset_revision="source-artifact",
            attempt_stage=str(attempt_stage or "recon"),
            metric_source="read_only_observability_sink",
            sink_status=report["status"],
            annotation_queue_status="deferred",
        )
    return report


def _aeb0_source_artifact(
    path: Path | None,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    exists = path is not None and path.exists() and path.is_file()
    gate = payload.get("aeb0_artifact_gate") if isinstance(payload, Mapping) else {}
    if not isinstance(gate, Mapping):
        gate = {}
    return {
        "path": str(path or ""),
        "exists": bool(exists),
        "sha256": sha256(path.read_bytes()).hexdigest() if exists and path else "",
        "status": str(gate.get("status") or payload.get("status") or "")
        if isinstance(payload, Mapping)
        else "",
        "blocked_reasons": list(gate.get("blocked_reasons") or [])
        if isinstance(gate.get("blocked_reasons"), list)
        else [],
    }


def _source_authority_flags(payload: Mapping[str, Any] | None) -> dict[str, bool]:
    flags = dict(AUTHORITY_FLAGS_FALSE)
    if not isinstance(payload, Mapping):
        return flags
    gate = payload.get("aeb0_artifact_gate")
    gate_flags = gate.get("authority_flags") if isinstance(gate, Mapping) else {}
    for key in flags:
        raw = gate_flags.get(key) if isinstance(gate_flags, Mapping) else payload.get(key)
        flags[key] = bool(raw) if raw is not None else False
    return flags


def _rejected_sink_fields(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    rejected: list[dict[str, str]] = []
    for key in sorted(payload):
        field = str(key)
        if field in _AUTHORITY_FIELDS:
            rejected.append({
                "field": field,
                "reason": "sink_payload_cannot_set_authority_flags",
            })
        elif field in _EVALUATOR_PROVENANCE_FIELDS:
            rejected.append({
                "field": field,
                "reason": "sink_payload_cannot_satisfy_evaluator_provenance",
            })
        elif field in _ROSTER_AUTHORITY_FIELDS:
            rejected.append({
                "field": field,
                "reason": "sink_payload_cannot_change_reviewer_authority",
            })
    return rejected


def _sink_payload_warnings(payload: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if any(key in payload for key in _EVALUATOR_PROVENANCE_FIELDS):
        warnings.append("sink_payload_cannot_satisfy_evaluator_provenance")
    if any(key in payload for key in _ROSTER_AUTHORITY_FIELDS):
        warnings.append("sink_payload_cannot_change_reviewer_authority")
    if any(key in payload for key in _AUTHORITY_FIELDS):
        warnings.append("sink_payload_cannot_set_authority_flags")
    return warnings


def _trust_path_boundary() -> dict[str, bool]:
    return {
        "sink_never_in_trust_path": True,
        "can_satisfy_evaluator_run_ref": False,
        "can_satisfy_quality_controls": False,
        "can_satisfy_empty_floor": False,
        "can_satisfy_candidate_overlay": False,
        "can_satisfy_policy_derivation": False,
        "can_change_effective_vote": False,
        "can_change_roster_selection": False,
    }


def _sink_recommendation(sink_kind: str) -> dict[str, Any]:
    return {
        "chosen": sink_kind or "langfuse",
        "scope": "read_only_artifact_ingest",
        "reason": (
            "Langfuse is preferred for LiteLLM-native tracing; Opik remains a "
            "candidate if the annotation/eval workflow becomes the primary "
            "product surface."
        ),
        "trust_path_role": "none",
    }


def _sha256_json(payload: Any) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
