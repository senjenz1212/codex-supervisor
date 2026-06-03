"""Deterministic reviewer-panel eval runner.

This module is intentionally separate from ``supervisor.agentic_eval``. The
agentic eval report compares lead execution modes; this runner measures
reviewer-panel behavior from labeled fixtures so future weighting work has raw
dependency data without changing gate policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, is_dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from .reviewer_registry import ReviewerSpec, configured_reviewers


SCHEMA_VERSION = "reviewer-panel-eval/v1"
CALIBRATION_SCHEMA_VERSION = "reviewer-panel-calibration/v1"
LABELED_SET_SCHEMA_VERSION = "reviewer-panel-labeled-set/v1"
ROW_SCHEMA_VERSION = "reviewer-panel-eval-row/v1"
ALLOWED_LABELS = {"accept_allowed", "block_required"}
DECISIONS = ("accept", "revise", "deny", "missing")
REAL_REVIEWER_SOURCE_KINDS = {
    "workflow_transcript",
    "workflow_transcript_event",
    "real_reviewer_result",
    "live_reviewer_result",
}


def reviewer_panel_eval_runner(
    *,
    labeled_tasks: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    reviewers: list[Any] | tuple[Any, ...] | None = None,
    cassettes: list[dict[str, Any]] | tuple[dict[str, Any], ...] | dict[Any, dict[str, Any]] | None = None,
    output_dir: str | Path | None = None,
    state: Any | None = None,
    run_id: str = "reviewer-panel-eval",
    execution_mode: str = "fixture_replay",
    emit_calibration: bool = False,
) -> dict[str, Any]:
    """Replay labeled gate fixtures through the configured reviewer roster.

    The default mode is fixture replay. Live reviewer calls are deliberately not
    implemented at this boundary because this report must be deterministic in
    CI and must not mutate live gate policy.
    """
    if execution_mode != "fixture_replay":
        raise ValueError("reviewer_panel_eval_runner only supports execution_mode=fixture_replay")

    normalized_tasks = _normalize_labeled_tasks(labeled_tasks)
    roster = _normalize_reviewer_roster(reviewers)
    cassette_index = _normalize_cassettes(cassettes or [])

    rows = _materialize_rows(
        labeled_tasks=normalized_tasks,
        reviewer_roster=roster,
        cassettes=cassette_index,
    )
    per_reviewer = _per_reviewer_metrics(rows, roster)
    pairwise = _pairwise_metrics(rows, roster)
    provenance = _provenance_summary(rows)
    provenance["row_roster_consistency"] = _row_roster_consistency(rows, roster)
    cassette_ids = sorted({
        row["cassette_id"]
        for row in rows
        if row.get("cassette_id") and not str(row.get("cassette_id")).startswith("missing-cassette:")
    })

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runner": {
            "public_boundary": "reviewer_panel_eval_runner",
            "execution_mode": execution_mode,
            "live_calls_allowed": False,
        },
        "labeled_set": {
            "schema_version": LABELED_SET_SCHEMA_VERSION,
            "task_count": len(normalized_tasks),
            "labels": sorted({task["label"] for task in normalized_tasks}),
            "sha256": _sha256_json(normalized_tasks),
        },
        "reviewer_roster": roster,
        "cassette_ids": cassette_ids,
        "rows": rows,
        "per_reviewer": per_reviewer,
        "pairwise": pairwise,
        "provenance": provenance,
        "policy_change_allowed": False,
        "active_weight_changes": [],
        "non_goals": [
            "does_not_change_gate_aggregation",
            "does_not_change_reviewer_roster_defaults",
            "does_not_flip_agentic_or_reviewer_policy",
        ],
        "calibration": None,
        "ledger_event_ids": [],
        "exports": {},
        "report_sha256": "",
    }
    if not emit_calibration:
        report["non_goals"].insert(2, "does_not_emit_active_calibrated_weights")

    if state is not None:
        event_id = state.write_event(
            run_id=run_id,
            source="reviewer_panel_eval",
            kind="reviewer_panel_eval_observation",
            payload={
                "schema_version": "reviewer-panel-eval-observation/v1",
                "report_schema_version": SCHEMA_VERSION,
                "task_count": len(normalized_tasks),
                "reviewer_count": len(roster),
                "labeled_set_sha256": report["labeled_set"]["sha256"],
                "cassette_ids": cassette_ids,
                "policy_change_allowed": False,
                "observation_only": True,
            },
        )
        report["ledger_event_ids"] = [event_id]

    if output_dir is not None:
        out = Path(output_dir)
        report["exports"] = {
            "report_json": str(out / "report.json"),
            "report_markdown": str(out / "report.md"),
            "rows_jsonl": str(out / "rows.jsonl"),
            "replay_manifest": str(out / "replay-manifest.json"),
        }

    report["report_sha256"] = _report_sha256(report)

    if emit_calibration:
        calibration_path = (
            Path(output_dir) / "reviewer-panel-calibration.json"
            if output_dir is not None else None
        )
        calibration = build_reviewer_panel_calibration(
            report,
            output_path=calibration_path,
        )
        report["calibration"] = calibration
        if output_dir is not None:
            report["exports"]["calibration_json"] = str(calibration_path)
        report["active_weight_changes"] = [{
            "schema_version": "reviewer-panel-active-weight-change/v1",
            "status": "calibration_artifact_emitted",
            "calibration_sha256": calibration["calibration_sha256"],
            "calibration_path": str(calibration_path) if calibration_path else None,
            "source_report_sha256": calibration["source_report_sha256"],
        }]
        report["report_sha256"] = _report_sha256(report)
        if calibration_path is not None:
            # Re-write after the report hash is final so the artifact can point
            # back at the exact report digest used by deterministic replay.
            calibration = build_reviewer_panel_calibration(
                report,
                output_path=calibration_path,
            )
            report["calibration"] = calibration
            report["active_weight_changes"][0]["calibration_sha256"] = calibration["calibration_sha256"]
            report["active_weight_changes"][0]["source_report_sha256"] = calibration["source_report_sha256"]

    if output_dir is not None:
        _export_report(report, Path(output_dir))

    return report


def build_reviewer_panel_calibration(
    report: dict[str, Any],
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build deterministic reviewer weights from measured eval dependencies."""
    roster = list(report.get("reviewer_roster") or [])
    pairwise = report.get("pairwise") if isinstance(report.get("pairwise"), dict) else {}
    rows = list(report.get("rows") or [])
    row_roster_consistency = _row_roster_consistency(rows, roster)
    if not row_roster_consistency["all_rows_match_reviewer_roster"]:
        raise ValueError("calibration rows must match reviewer roster metadata")
    pair_dependencies = {
        pair_id: _pair_dependency(pair_metrics, roster=roster)
        for pair_id, pair_metrics in sorted(pairwise.items())
        if isinstance(pair_metrics, dict)
    }
    reviewer_dependencies: dict[str, float] = {
        str(reviewer.get("reviewer_id")): 0.0
        for reviewer in roster
        if reviewer.get("reviewer_id")
    }
    reviewer_dependency_sources: dict[str, list[dict[str, Any]]] = {
        reviewer_id: [] for reviewer_id in reviewer_dependencies
    }
    for pair_id, dependency in pair_dependencies.items():
        reviewer_ids = dependency["reviewer_ids"]
        for reviewer_id in reviewer_ids:
            current = reviewer_dependencies.get(reviewer_id, 0.0)
            score = float(dependency["dependency_score"])
            if score > current:
                reviewer_dependencies[reviewer_id] = score
            reviewer_dependency_sources.setdefault(reviewer_id, []).append({
                "pair_id": pair_id,
                "dependency_score": score,
                "signals": dependency["signals"],
            })

    reviewer_weights: dict[str, dict[str, Any]] = {}
    for reviewer in roster:
        reviewer_id = str(reviewer.get("reviewer_id") or "")
        if not reviewer_id:
            continue
        dependency_score = _clamp_unit(reviewer_dependencies.get(reviewer_id, 0.0))
        reviewer_weights[reviewer_id] = {
            "reviewer_id": reviewer_id,
            "weight": round(max(0.05, 1.0 - dependency_score), 6),
            "dependency_score": dependency_score,
            "provider_family": reviewer.get("provider_family") or "unknown",
            "runtime": reviewer.get("runtime") or "unknown",
            "model": reviewer.get("model"),
            "lineage": list(reviewer.get("lineage") or []),
            "tool_access": reviewer.get("tool_access") or "unknown",
            "assurance_grade": reviewer.get("assurance_grade") or "self_reported",
            "derived_from_pairwise": sorted(
                reviewer_dependency_sources.get(reviewer_id, []),
                key=lambda item: str(item.get("pair_id") or ""),
            ),
        }

    source_provenance = copy.deepcopy(report.get("provenance") or {})
    source_provenance["row_roster_consistency"] = row_roster_consistency

    calibration = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "source_report_schema_version": report.get("schema_version"),
        "source_report_sha256": report.get("report_sha256") or _report_sha256(report),
        "labeled_set_sha256": (report.get("labeled_set") or {}).get("sha256"),
        "reviewer_roster_sha256": _sha256_json(roster),
        "pairwise_dependency": pair_dependencies,
        "reviewer_weights": reviewer_weights,
        "source_provenance": source_provenance,
        "accept_confidence_threshold": _accept_confidence_threshold(rows),
        "weight_formula": {
            "description": "weight = max(0.05, 1 - max_measured_pair_dependency)",
            "dependency_signals": [
                "same_provider_family",
                "combined_failure_jaccard",
                "false_accept_overlap.jaccard",
                "false_block_overlap.jaccard",
                "positive(block_decision_correlation.value)",
                "positive(wrong_decision_correlation.value)",
            ],
            "threshold_formula": "0.8 * average observed accept confidence, clamped to [0.5, 0.95]",
            "accept_aggregation_formula": (
                "aggregate = sum(weight * confidence * severity_multiplier) / reviewer_count"
            ),
            "severity_multipliers": {
                "none": 1.0,
                "low": 0.95,
                "medium": 0.85,
                "important": 0.65,
                "critical": 0.35,
            },
        },
        "recalibratable": True,
        "calibration_sha256": "",
    }
    calibration["calibration_sha256"] = _sha256_json({
        **calibration,
        "calibration_sha256": "",
    })
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(calibration, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return calibration


def _pair_dependency(pair_metrics: dict[str, Any], *, roster: list[dict[str, Any]]) -> dict[str, Any]:
    reviewer_ids = [str(item) for item in pair_metrics.get("reviewer_ids") or []]
    roster_by_id = {str(item.get("reviewer_id")): item for item in roster if item.get("reviewer_id")}
    same_provider = False
    if len(reviewer_ids) == 2:
        families = [
            str(roster_by_id.get(reviewer_id, {}).get("provider_family") or "unknown")
            for reviewer_id in reviewer_ids
        ]
        same_provider = bool(families[0] != "unknown" and families[0] == families[1])
    signals = {
        "same_provider_family": 1.0 if same_provider else 0.0,
        "combined_failure_jaccard": _clamp_unit(pair_metrics.get("combined_failure_jaccard")),
        "false_accept_overlap_jaccard": _clamp_unit(
            (pair_metrics.get("false_accept_overlap") or {}).get("jaccard")
            if isinstance(pair_metrics.get("false_accept_overlap"), dict) else 0.0
        ),
        "false_block_overlap_jaccard": _clamp_unit(
            (pair_metrics.get("false_block_overlap") or {}).get("jaccard")
            if isinstance(pair_metrics.get("false_block_overlap"), dict) else 0.0
        ),
        "block_decision_positive_correlation": _positive_correlation(
            pair_metrics.get("block_decision_correlation")
        ),
        "wrong_decision_positive_correlation": _positive_correlation(
            pair_metrics.get("wrong_decision_correlation")
        ),
    }
    score = max(signals.values()) if signals else 0.0
    return {
        "schema_version": "reviewer-panel-pair-dependency/v1",
        "reviewer_ids": reviewer_ids,
        "dependency_score": round(score, 6),
        "signals": signals,
    }


def _accept_confidence_threshold(rows: list[dict[str, Any]]) -> float:
    confidences = [
        float(row["confidence"])
        for row in rows
        if row.get("decision") == "accept" and isinstance(row.get("confidence"), (int, float))
    ]
    if not confidences:
        return 0.7
    return round(max(0.5, min(0.95, (sum(confidences) / len(confidences)) * 0.8)), 6)


def _positive_correlation(value: Any) -> float:
    if not isinstance(value, dict) or value.get("status") != "ok":
        return 0.0
    return _clamp_unit(float(value.get("value") or 0.0))


def _clamp_unit(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _normalize_labeled_tasks(tasks: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id = str(task.get("task_id") or "").strip()
        gate = str(task.get("gate") or "").strip()
        label = str(task.get("label") or "").strip()
        if not task_id:
            raise ValueError(f"labeled task at index {index} is missing task_id")
        if not gate:
            raise ValueError(f"labeled task {task_id} is missing gate")
        if label not in ALLOWED_LABELS:
            raise ValueError(
                f"invalid label for task {task_id}: {label!r}; expected one of {sorted(ALLOWED_LABELS)}"
            )
        normalized.append({
            "schema_version": LABELED_SET_SCHEMA_VERSION,
            "task_id": task_id,
            "gate": gate,
            "label": label,
            "input_sha256": task.get("input_sha256"),
            "evidence_ref": task.get("evidence_ref"),
        })
    return sorted(normalized, key=lambda item: (item["task_id"], item["gate"]))


def _normalize_reviewer_roster(reviewers: list[Any] | tuple[Any, ...] | None) -> list[dict[str, Any]]:
    if reviewers is None:
        reviewers = tuple(
            reviewer.spec
            for reviewer in configured_reviewers(
                reviewer_output_mode="litellm_structured",
                reviewer_model="gemini-3.1-pro-preview",
            )
        )
    roster = [_reviewer_to_dict(item) for item in reviewers]
    if not roster:
        raise ValueError("reviewer_panel_eval_runner requires at least one reviewer")
    return sorted(roster, key=lambda item: item["reviewer_id"])


def _reviewer_to_dict(item: Any) -> dict[str, Any]:
    spec = getattr(item, "spec", item)
    if isinstance(spec, ReviewerSpec):
        payload = asdict(spec)
    elif is_dataclass(spec):
        payload = asdict(spec)
    elif isinstance(spec, dict):
        payload = dict(spec)
    else:
        payload = {
            "reviewer_id": getattr(spec, "reviewer_id", None),
            "runtime": getattr(spec, "runtime", None),
            "model": getattr(spec, "model", None),
            "provider_family": getattr(spec, "provider_family", "unknown"),
            "lineage": getattr(spec, "lineage", ()),
            "tool_access": getattr(spec, "tool_access", "unknown"),
            "assurance_grade": getattr(spec, "assurance_grade", "self_reported"),
        }
    reviewer_id = str(payload.get("reviewer_id") or "").strip()
    if not reviewer_id:
        raise ValueError("reviewer roster item is missing reviewer_id")
    return {
        "reviewer_id": reviewer_id,
        "runtime": payload.get("runtime") or "unknown",
        "model": payload.get("model"),
        "provider_family": payload.get("provider_family") or "unknown",
        "lineage": list(payload.get("lineage") or []),
        "tool_access": payload.get("tool_access") or "unknown",
        "assurance_grade": payload.get("assurance_grade") or "self_reported",
    }


def _normalize_cassettes(
    cassettes: list[dict[str, Any]] | tuple[dict[str, Any], ...] | dict[Any, dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    if isinstance(cassettes, dict):
        items = list(cassettes.values())
    else:
        items = list(cassettes)
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        task_id = str(item.get("task_id") or "").strip()
        gate = str(item.get("gate") or "").strip()
        reviewer_id = str(item.get("reviewer_id") or "").strip()
        if not task_id or not gate or not reviewer_id:
            raise ValueError("reviewer cassette requires task_id, gate, and reviewer_id")
        index[(task_id, gate, reviewer_id)] = dict(item)
    return index


def _materialize_rows(
    *,
    labeled_tasks: list[dict[str, Any]],
    reviewer_roster: list[dict[str, Any]],
    cassettes: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in labeled_tasks:
        for reviewer in reviewer_roster:
            cassette = cassettes.get((task["task_id"], task["gate"], reviewer["reviewer_id"]))
            rows.append(_row_from_fixture(task=task, reviewer=reviewer, cassette=cassette))
    return sorted(rows, key=lambda row: (row["task_id"], row["reviewer_id"]))


def _row_from_fixture(
    *,
    task: dict[str, Any],
    reviewer: dict[str, Any],
    cassette: dict[str, Any] | None,
) -> dict[str, Any]:
    cassette = dict(cassette or {})
    raw_decision = str(cassette.get("decision") or "").strip().lower()
    explicit_present = cassette.get("verdict_present")
    verdict_present = bool(explicit_present) if explicit_present is not None else raw_decision in {"accept", "revise", "deny"}
    decision = raw_decision if verdict_present and raw_decision in {"accept", "revise", "deny"} else "missing"
    accepted = verdict_present and decision == "accept"
    label = task["label"]
    false_accept = label == "block_required" and accepted
    false_block = label == "accept_allowed" and not accepted
    false_block_cause = _false_block_cause(decision, verdict_present) if false_block else None
    latency_ms = _number(cassette.get("latency_ms"), default=0)
    if isinstance(latency_ms, float):
        latency_ms = int(latency_ms)

    row = {
        "schema_version": ROW_SCHEMA_VERSION,
        "task_id": task["task_id"],
        "gate": task["gate"],
        "label": label,
        "reviewer_id": reviewer["reviewer_id"],
        "cassette_id": cassette.get("cassette_id")
        or f"missing-cassette:{task['task_id']}:{task['gate']}:{reviewer['reviewer_id']}",
        "verdict_present": verdict_present,
        "accepted": accepted,
        "decision": decision,
        "severity": cassette.get("severity") or ("none" if accepted else "important"),
        "confidence": _optional_float(cassette.get("confidence")),
        "runtime": cassette.get("runtime") or reviewer["runtime"],
        "model": cassette.get("model") or reviewer.get("model"),
        "provider_family": cassette.get("provider_family") or reviewer["provider_family"],
        "lineage": list(cassette.get("lineage") or reviewer.get("lineage") or []),
        "tool_access": cassette.get("tool_access") or reviewer["tool_access"],
        "assurance_grade": cassette.get("assurance_grade") or reviewer["assurance_grade"],
        "transcript_refs": list(cassette.get("transcript_refs") or []),
        "input_sha256": cassette.get("input_sha256") or task.get("input_sha256"),
        "output_sha256": cassette.get("output_sha256") if verdict_present else None,
        "failure_classification": cassette.get("failure_classification"),
        "source_kind": cassette.get("source_kind") or "fixture",
        "source_trace_path": cassette.get("source_trace_path"),
        "source_event_id": cassette.get("source_event_id"),
        "source_payload_sha256": cassette.get("source_payload_sha256"),
        "transcript_sha256": cassette.get("transcript_sha256"),
        "cost_usd": float(_number(cassette.get("cost_usd"), default=0.0)),
        "latency_ms": int(latency_ms),
        "false_accept": false_accept,
        "false_block": false_block,
        "false_block_cause": false_block_cause,
        "blocks_gate": not accepted,
    }
    if not verdict_present and row["failure_classification"] is None:
        row["failure_classification"] = "reviewer_verdict_missing"
    return row


def _provenance_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_kind_counts: dict[str, int] = {}
    source_trace_paths: set[str] = set()
    source_event_ids: set[str] = set()
    transcript_ref_count = 0
    real_reviewer_output_count = 0
    auditable_real_output_count = 0
    fixture_row_count = 0

    for row in rows:
        source_kind = str(row.get("source_kind") or "fixture")
        source_kind_counts[source_kind] = source_kind_counts.get(source_kind, 0) + 1
        if source_kind == "fixture":
            fixture_row_count += 1
        if row.get("source_trace_path"):
            source_trace_paths.add(str(row["source_trace_path"]))
        if row.get("source_event_id") is not None:
            source_event_ids.add(str(row["source_event_id"]))
        refs = row.get("transcript_refs") if isinstance(row.get("transcript_refs"), list) else []
        transcript_ref_count += len(refs)
        if source_kind in REAL_REVIEWER_SOURCE_KINDS and row.get("verdict_present"):
            real_reviewer_output_count += 1
            if (
                refs
                and row.get("source_trace_path")
                and row.get("source_event_id") is not None
                and _looks_like_sha256(row.get("output_sha256"))
                and _looks_like_sha256(row.get("transcript_sha256"))
            ):
                auditable_real_output_count += 1

    return {
        "schema_version": "reviewer-panel-eval-provenance/v1",
        "source_kind_counts": dict(sorted(source_kind_counts.items())),
        "source_trace_paths": sorted(source_trace_paths),
        "source_event_ids": sorted(source_event_ids),
        "transcript_ref_count": transcript_ref_count,
        "real_reviewer_output_count": real_reviewer_output_count,
        "auditable_real_output_count": auditable_real_output_count,
        "fixture_row_count": fixture_row_count,
        "all_rows_auditable_real_reviewer_outputs": bool(
            rows and auditable_real_output_count == len(rows)
        ),
    }


def _row_roster_consistency(
    rows: list[dict[str, Any]],
    roster: list[dict[str, Any]],
) -> dict[str, Any]:
    roster_by_id = {
        str(item.get("reviewer_id") or ""): item
        for item in roster
        if str(item.get("reviewer_id") or "")
    }
    compared_fields = (
        "runtime",
        "model",
        "provider_family",
        "lineage",
        "tool_access",
        "assurance_grade",
    )
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        reviewer_id = str(row.get("reviewer_id") or "")
        reviewer = roster_by_id.get(reviewer_id)
        if reviewer is None:
            mismatches.append({
                "task_id": row.get("task_id"),
                "gate": row.get("gate"),
                "reviewer_id": reviewer_id,
                "reason": "reviewer_not_in_roster",
            })
            continue
        field_mismatches = []
        for field in compared_fields:
            row_value = _normalise_roster_value(row.get(field))
            roster_value = _normalise_roster_value(reviewer.get(field))
            if row_value != roster_value:
                field_mismatches.append({
                    "field": field,
                    "row": row_value,
                    "roster": roster_value,
                })
        if field_mismatches:
            mismatches.append({
                "task_id": row.get("task_id"),
                "gate": row.get("gate"),
                "reviewer_id": reviewer_id,
                "mismatches": field_mismatches,
            })
    return {
        "schema_version": "reviewer-panel-row-roster-consistency/v1",
        "row_count": len(rows),
        "reviewer_count": len(roster_by_id),
        "compared_fields": list(compared_fields),
        "mismatched_row_count": len(mismatches),
        "all_rows_match_reviewer_roster": not mismatches,
        "mismatches": mismatches[:20],
    }


def _normalise_roster_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value is None:
        return None
    return str(value)


def _false_block_cause(decision: str, verdict_present: bool) -> str:
    if not verdict_present or decision == "missing":
        return "missing_unavailable"
    if decision == "revise":
        return "explicit_revise"
    if decision == "deny":
        return "explicit_deny"
    return "other_non_accept"


def _per_reviewer_metrics(rows: list[dict[str, Any]], roster: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for reviewer in roster:
        reviewer_id = reviewer["reviewer_id"]
        reviewer_rows = [row for row in rows if row["reviewer_id"] == reviewer_id]
        task_count = len(reviewer_rows)
        decision_counts = {decision: 0 for decision in DECISIONS}
        false_block_causes: dict[str, int] = {
            "explicit_revise": 0,
            "explicit_deny": 0,
            "missing_unavailable": 0,
            "other_non_accept": 0,
        }
        for row in reviewer_rows:
            decision_counts[row["decision"]] = decision_counts.get(row["decision"], 0) + 1
            if row["false_block"] and row.get("false_block_cause"):
                false_block_causes[row["false_block_cause"]] = (
                    false_block_causes.get(row["false_block_cause"], 0) + 1
                )
        block_required_count = sum(1 for row in reviewer_rows if row["label"] == "block_required")
        accept_allowed_count = sum(1 for row in reviewer_rows if row["label"] == "accept_allowed")
        false_accept_count = sum(1 for row in reviewer_rows if row["false_accept"])
        false_block_count = sum(1 for row in reviewer_rows if row["false_block"])
        missing_count = decision_counts.get("missing", 0)
        total_cost = sum(float(row["cost_usd"]) for row in reviewer_rows)
        total_latency = sum(int(row["latency_ms"]) for row in reviewer_rows)
        result[reviewer_id] = {
            "reviewer_id": reviewer_id,
            "task_count": task_count,
            "verdict_present_count": sum(1 for row in reviewer_rows if row["verdict_present"]),
            "decision_counts": decision_counts,
            "accept_count": decision_counts.get("accept", 0),
            "revise_count": decision_counts.get("revise", 0),
            "deny_count": decision_counts.get("deny", 0),
            "missing_count": missing_count,
            "false_accept_count": false_accept_count,
            "false_accept_denominator": block_required_count,
            "false_accept_rate": _rate(false_accept_count, block_required_count),
            "false_block_count": false_block_count,
            "false_block_denominator": accept_allowed_count,
            "false_block_rate": _rate(false_block_count, accept_allowed_count),
            "false_block_cause_counts": false_block_causes,
            "unavailable_rate": _rate(missing_count, task_count),
            "total_cost_usd": total_cost,
            "avg_cost_usd": _rate(total_cost, task_count),
            "total_latency_ms": total_latency,
            "avg_latency_ms": _rate(total_latency, task_count),
        }
    return result


def _pairwise_metrics(rows: list[dict[str, Any]], roster: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_reviewer = {
        reviewer["reviewer_id"]: {
            (row["task_id"], row["gate"]): row
            for row in rows
            if row["reviewer_id"] == reviewer["reviewer_id"]
        }
        for reviewer in roster
    }
    result: dict[str, dict[str, Any]] = {}
    for left, right in combinations([reviewer["reviewer_id"] for reviewer in roster], 2):
        left_rows = by_reviewer[left]
        right_rows = by_reviewer[right]
        keys = sorted(set(left_rows) & set(right_rows))
        paired = [(left_rows[key], right_rows[key]) for key in keys]
        agreement_count = sum(1 for a, b in paired if a["decision"] == b["decision"])
        disagreement_count = len(paired) - agreement_count
        false_accept_left = {a["task_id"] for a, _b in paired if a["false_accept"]}
        false_accept_right = {b["task_id"] for _a, b in paired if b["false_accept"]}
        false_block_left = {a["task_id"] for a, _b in paired if a["false_block"]}
        false_block_right = {b["task_id"] for _a, b in paired if b["false_block"]}
        failure_left = false_accept_left | false_block_left
        failure_right = false_accept_right | false_block_right
        block_a = [bool(a["blocks_gate"]) for a, _b in paired]
        block_b = [bool(b["blocks_gate"]) for _a, b in paired]
        wrong_a = [bool(a["false_accept"] or a["false_block"]) for a, _b in paired]
        wrong_b = [bool(b["false_accept"] or b["false_block"]) for _a, b in paired]
        key = f"{left}__{right}"
        result[key] = {
            "reviewer_ids": [left, right],
            "comparable_task_count": len(paired),
            "agreement_count": agreement_count,
            "disagreement_count": disagreement_count,
            "agreement_rate": _rate(agreement_count, len(paired)),
            "disagreement_counts": _disagreement_counts(paired),
            "false_accept_overlap": _overlap(false_accept_left, false_accept_right),
            "false_block_overlap": _overlap(false_block_left, false_block_right),
            "combined_failure_jaccard": _jaccard(failure_left, failure_right),
            "contingency": {
                "block_decision": _contingency(block_a, block_b),
                "wrong_decision": _contingency(wrong_a, wrong_b),
            },
            "block_decision_correlation": _phi_correlation(block_a, block_b),
            "wrong_decision_correlation": _phi_correlation(wrong_a, wrong_b),
            "avg_cost_usd_delta": _avg_delta(paired, "cost_usd"),
            "avg_latency_ms_delta": _avg_delta(paired, "latency_ms"),
        }
    return result


def _disagreement_counts(paired: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for left, right in paired:
        if left["decision"] == right["decision"]:
            continue
        key = f"{left['decision']}_vs_{right['decision']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _overlap(left: set[str], right: set[str]) -> dict[str, Any]:
    intersection = left & right
    union = left | right
    return {
        "count": len(intersection),
        "left_count": len(left),
        "right_count": len(right),
        "union_count": len(union),
        "jaccard": _jaccard(left, right),
        "task_ids": sorted(intersection),
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _contingency(left: list[bool], right: list[bool]) -> dict[str, int]:
    counts = {
        "true_true": 0,
        "true_false": 0,
        "false_true": 0,
        "false_false": 0,
    }
    for a, b in zip(left, right):
        if a and b:
            counts["true_true"] += 1
        elif a and not b:
            counts["true_false"] += 1
        elif not a and b:
            counts["false_true"] += 1
        else:
            counts["false_false"] += 1
    return counts


def _phi_correlation(left: list[bool], right: list[bool]) -> dict[str, Any]:
    if not left or not right:
        return {"status": "not_applicable", "value": None, "reason": "no comparable tasks"}
    if len(set(left)) < 2 or len(set(right)) < 2:
        return {
            "status": "not_applicable",
            "value": None,
            "reason": "zero variance in one or both reviewer vectors",
        }
    table = _contingency(left, right)
    tt = table["true_true"]
    tf = table["true_false"]
    ft = table["false_true"]
    ff = table["false_false"]
    denominator = math.sqrt((tt + tf) * (tt + ft) * (ff + tf) * (ff + ft))
    if denominator == 0:
        return {
            "status": "not_applicable",
            "value": None,
            "reason": "zero variance in one or both reviewer vectors",
        }
    return {"status": "ok", "value": ((tt * ff) - (tf * ft)) / denominator, "reason": None}


def _avg_delta(paired: list[tuple[dict[str, Any], dict[str, Any]]], field: str) -> float:
    if not paired:
        return 0.0
    left_avg = sum(float(left[field]) for left, _right in paired) / len(paired)
    right_avg = sum(float(right[field]) for _left, right in paired) / len(paired)
    return right_avg - left_avg


def _export_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "rows.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in report["rows"]),
        encoding="utf-8",
    )
    (output_dir / "replay-manifest.json").write_text(
        json.dumps(_replay_manifest(report), sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(_markdown_report(report), encoding="utf-8")


def _replay_manifest(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "reviewer-panel-eval-replay-manifest/v1",
        "report_schema_version": report["schema_version"],
        "labeled_set_sha256": report["labeled_set"]["sha256"],
        "cassette_ids": report["cassette_ids"],
        "reviewer_roster": report["reviewer_roster"],
        "provenance": report.get("provenance") or {},
        "rows_sha256": _sha256_json(report["rows"]),
        "report_sha256": report["report_sha256"],
        "ledger_event_ids": report["ledger_event_ids"],
        "policy_change_allowed": False,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Reviewer Panel Eval Report",
        "",
        f"- schema_version: `{report['schema_version']}`",
        f"- execution_mode: `{report['runner']['execution_mode']}`",
        f"- task_count: {report['labeled_set']['task_count']}",
        f"- reviewer_count: {len(report['reviewer_roster'])}",
        f"- policy_change_allowed: `{str(report['policy_change_allowed']).lower()}`",
        "",
        "## Per Reviewer",
    ]
    for reviewer_id, metrics in report["per_reviewer"].items():
        lines.extend([
            "",
            f"### {reviewer_id}",
            f"- false_accept_rate: {metrics['false_accept_count']}/{metrics['false_accept_denominator']} = {metrics['false_accept_rate']:.3f}",
            f"- false_block_rate: {metrics['false_block_count']}/{metrics['false_block_denominator']} = {metrics['false_block_rate']:.3f}",
            f"- unavailable_rate: {metrics['missing_count']}/{metrics['task_count']} = {metrics['unavailable_rate']:.3f}",
            f"- total_cost_usd: {metrics['total_cost_usd']:.6f}",
            f"- avg_latency_ms: {metrics['avg_latency_ms']:.3f}",
        ])
    lines.extend(["", "## Pairwise"])
    for pair_id, metrics in report["pairwise"].items():
        lines.extend([
            "",
            f"### {pair_id}",
            f"- agreement_rate: {metrics['agreement_count']}/{metrics['comparable_task_count']} = {metrics['agreement_rate']:.3f}",
            f"- combined_failure_jaccard: {metrics['combined_failure_jaccard']:.3f}",
            f"- block_decision_correlation: {metrics['block_decision_correlation']['status']}",
            f"- wrong_decision_correlation: {metrics['wrong_decision_correlation']['status']}",
        ])
    lines.append("")
    return "\n".join(lines)


def _report_sha256(report: dict[str, Any]) -> str:
    stable = copy.deepcopy(report)
    stable["report_sha256"] = ""
    # Calibration points back to the eval report. Exclude it from the report
    # digest to avoid a circular hash while keeping the labeled-set, row,
    # metric, and roster bytes reproducible.
    stable["calibration"] = None
    stable["active_weight_changes"] = []
    return _sha256_json(stable)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()


def _looks_like_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def _number(value: Any, *, default: float | int = 0) -> float | int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(numerator: float | int, denominator: float | int) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)
