"""Bridge benchmark evidence into report-only AutoResearch records."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from ..auto_evolve_benchmark_ledger import append_auto_evolve_benchmark_event
from .schema import sha256_json


def promote_benchmark_report_to_autoresearch_report(
    *,
    benchmark_report: Mapping[str, Any],
    output_dir: str | Path,
    candidate_overlay_ref: str = "",
    evaluator_run_ref: str = "",
    evaluator_run_hash: str = "",
    evaluator_quality: Mapping[str, Any] | None = None,
    empty_floor_comparison: Mapping[str, Any] | None = None,
    aeb0_artifact_ref: str | Path | None = None,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """Convert benchmark evidence into an AutoResearch-shaped report.

    The first implemented slice is intentionally conservative: incomplete
    bridge inputs become a blocked AutoResearch record and optional ledger row.
    """
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    blocked_reasons = _blocked_reasons(
        aeb0_artifact_ref=aeb0_artifact_ref,
        evaluator_run_ref=evaluator_run_ref,
        evaluator_run_hash=evaluator_run_hash,
        candidate_overlay_ref=candidate_overlay_ref,
        evaluator_quality=evaluator_quality,
        empty_floor_comparison=empty_floor_comparison,
        benchmark_report=benchmark_report,
    )
    status = "blocked" if blocked_reasons else "accepted"
    record = _autoresearch_record(
        benchmark_report=benchmark_report,
        status=status,
        blocked_reasons=blocked_reasons,
        candidate_overlay_ref=candidate_overlay_ref,
        evaluator_run_ref=evaluator_run_ref,
        evaluator_run_hash=evaluator_run_hash,
        evaluator_quality=evaluator_quality or {},
        empty_floor_comparison=empty_floor_comparison or {},
        aeb0_artifact_ref=str(aeb0_artifact_ref or ""),
    )
    report: dict[str, Any] = {
        "schema_version": "supervisor-autoresearch-summary/v1",
        "records": [record],
        "summary": {
            "attempt_count": 1,
            "accepted_attempt_count": 1 if status == "accepted" else 0,
            "rejected_attempt_count": 0 if status == "accepted" else 1,
            "report_only": True,
        },
        "recommendation": {
            "decision": "candidate_evidence_ready" if status == "accepted" else "blocked",
            "reason": "all_bridge_prerequisites_present"
            if status == "accepted"
            else "benchmark_evidence_conversion_blocked",
            "operator_review_required": True,
        },
        "default_change_allowed": False,
        "report_only": {
            "default_change_allowed": False,
            "config_mutated": False,
            "policy_mutated": False,
            "operator_review_required": True,
        },
    }
    if status != "accepted":
        report["metric_applyable"] = False
        report["improvement_claim_allowed"] = False
    report["report_sha256"] = sha256_json({
        key: value for key, value in report.items() if key != "report_sha256"
    })
    report_path = output_path / "autoresearch_benchmark_promotion_report.json"
    report["report_ref"] = str(report_path)
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    if ledger_path:
        dataset = str(benchmark_report.get("dataset") or "unknown")
        split = str(
            benchmark_report.get("dataset_split")
            or benchmark_report.get("split")
            or "unknown"
        )
        append_auto_evolve_benchmark_event(
            ledger_path,
            stage="benchmark_to_autoresearch_evidence_conversion",
            promise_ids=("P5", "P8", "P10"),
            status=status,
            artifact_path=report_path,
            aeb0_artifact_ref=aeb0_artifact_ref,
            blocked_reasons=blocked_reasons,
            dataset=dataset,
            split=split,
            dataset_revision=str(
                benchmark_report.get("dataset_revision")
                or benchmark_report.get("dataset_sha")
                or "source-artifact"
            ),
            attempt_stage=str(benchmark_report.get("attempt_stage") or "recon"),
            metric_source="evaluator_execution",
            sink_status="not_applicable",
            annotation_queue_status="deferred",
        )
    return report


def _blocked_reasons(
    *,
    aeb0_artifact_ref: str | Path | None,
    evaluator_run_ref: str,
    evaluator_run_hash: str,
    candidate_overlay_ref: str,
    evaluator_quality: Mapping[str, Any] | None,
    empty_floor_comparison: Mapping[str, Any] | None,
    benchmark_report: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if not aeb0_artifact_ref:
        reasons.append("missing_aeb0_artifact_ref")
    else:
        path = Path(aeb0_artifact_ref).expanduser()
        if not path.exists() or not path.is_file():
            reasons.append("missing_aeb0_artifact")
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            gate = payload.get("aeb0_artifact_gate") if isinstance(payload, Mapping) else {}
            if not isinstance(gate, Mapping) or gate.get("status") != "completed":
                reasons.append("aeb0_artifact_not_completed")
    if not evaluator_run_ref:
        reasons.append("missing_evaluator_run_ref")
    if not evaluator_run_hash:
        reasons.append("missing_evaluator_run_hash")
    if not candidate_overlay_ref:
        reasons.append("missing_candidate_overlay_ref")
    if not evaluator_quality:
        reasons.append("missing_evaluator_quality_controls")
    if not empty_floor_comparison:
        reasons.append("missing_empty_floor_comparison")
    contract = benchmark_report.get("evidence_conversion_power_contract")
    if isinstance(contract, Mapping) and contract.get("status") not in {None, "qualified"}:
        reasons.append("power_contract_not_qualified")
    elif not isinstance(contract, Mapping):
        reasons.append("missing_power_contract")
    return reasons


def _autoresearch_record(
    *,
    benchmark_report: Mapping[str, Any],
    status: str,
    blocked_reasons: list[str],
    candidate_overlay_ref: str,
    evaluator_run_ref: str,
    evaluator_run_hash: str,
    evaluator_quality: Mapping[str, Any],
    empty_floor_comparison: Mapping[str, Any],
    aeb0_artifact_ref: str,
) -> dict[str, Any]:
    benchmark_hash = str(benchmark_report.get("report_sha256") or "")
    seed = {
        "benchmark_hash": benchmark_hash,
        "candidate_overlay_ref": candidate_overlay_ref,
        "evaluator_run_ref": evaluator_run_ref,
        "aeb0_artifact_ref": aeb0_artifact_ref,
    }
    attempt_id = "benchmark-promotion-" + sha256_json(seed)[:16]
    return {
        "schema_version": "supervisor-autoresearch-report/v1",
        "experiment_id": "auto-evolve-benchmark-promotion",
        "task_id": "benchmark-to-autoresearch-evidence-conversion",
        "attempt_id": attempt_id,
        "validation_status": status,
        "recommendation": "operator review required"
        if status == "accepted"
        else "blocked benchmark evidence conversion",
        "metric_name": "benchmark_evidence_conversion",
        "metric_trials": [],
        "metric_source": "evaluator_execution",
        "evaluator_run_ref": evaluator_run_ref,
        "evaluator_run_hash": evaluator_run_hash,
        "metric_before": None,
        "metric_after": None,
        "metric_delta": None,
        "empty_floor_comparison": dict(empty_floor_comparison),
        "policy_overlay_candidate_ref": candidate_overlay_ref,
        "policy_candidate_changes": {},
        "metric_median": None,
        "metric_iqr": None,
        "quality_unstable_across_trials": False,
        "changed_files": [candidate_overlay_ref] if candidate_overlay_ref else [],
        "artifact_hashes": {},
        "evidence_refs": [
            ref for ref in (f"aeb0:{aeb0_artifact_ref}" if aeb0_artifact_ref else "",)
            if ref
        ],
        "evaluator_quality": dict(evaluator_quality),
        "gaming_flags": [],
        "validation_errors": list(blocked_reasons),
        "blocked_reasons": list(blocked_reasons),
        "cost_usd": 0.0,
        "wall_clock_s": 0.0,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
        "benchmark_report_sha256": benchmark_hash,
    }
