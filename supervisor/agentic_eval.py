"""Evaluation reports for making agentic lead policy changes empirical."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


_NUMERIC_FIELDS = (
    "wall_clock_s",
    "cost_usd",
    "retries",
    "rejected_gates",
    "missed_issues",
    "operator_interventions",
    "graceful_degradation",
    "score",
)
LATENCY_FIELDS = (
    "time_to_first_useful_finding",
    "time_to_accepted_outcome",
    "orchestration_overhead_s",
    "reviewer_time_s",
    "worker_idle_wait_s",
)
ACCELERATION_WIN_THRESHOLD = 1.2

REQUIRED_MODES = ("lead_direct", "agentic_allowed", "agentic_required")
DATASET_SCHEMA_VERSION = "agentic-lead-eval-dataset/v1"
RUNNER_SCHEMA_VERSION = "agentic-lead-eval-runner/v1"
SCORE_SCHEMA_VERSION = "agentic-lead-eval-score/v1"
EVIDENCE_SCHEMA_VERSION = "agentic-lead-eval-evidence/v1"
PASSING_EVIDENCE_STATUSES = {"pass", "passed", "accept", "accepted", "ok", "green"}
CONCRETE_EVIDENCE_KINDS = {"probe_receipt", "artifact_path", "diff_hunk"}
REQUIRED_WORKFLOW_GATES = (
    "prd_review",
    "issues_review",
    "tdd_review",
    "implementation_plan",
    "execution",
    "outcome_review",
)
REQUIRED_BASE_PROBES = ("P1", "P2", "P3")
REQUIRED_AGENTIC_PROBES = ("P13", "P14")


def build_agentic_eval_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate historical lead-direct versus agentic policy runs.

    The report is deliberately conservative: it never authorizes a default
    policy change by itself. Operators need explicit review over the summary and
    the raw rows before moving agentic modes beyond opt-in.
    """
    normalized_rows = [_normalise_row(row) for row in rows]
    _attach_acceleration_and_qualification(normalized_rows)
    summary: dict[str, dict[str, Any]] = {}
    for row in normalized_rows:
        mode = str(row.get("mode") or "unknown")
        bucket = summary.setdefault(mode, {
            "task_count": 0,
            "wall_clock_s": 0.0,
            "cost_usd": 0.0,
            "retries": 0,
            "rejected_gates": 0,
            "missed_issues": 0,
            "operator_interventions": 0,
            "graceful_degradation": 0.0,
            "score": 0.0,
            "qualifying_task_count": 0,
            "non_qualifying_task_count": 0,
            "qualified_task_ids": [],
            "non_qualifying_task_ids": [],
            "qualification_failing_predicates": [],
            "_acceleration_ratios": [],
            "_latency_values": {field: [] for field in LATENCY_FIELDS},
            "_latency_unavailable": {field: 0 for field in LATENCY_FIELDS},
        })
        bucket["task_count"] += 1
        for field in _NUMERIC_FIELDS:
            bucket[field] += row[field]
        if row.get("qualifies"):
            bucket["qualifying_task_count"] += 1
            bucket["qualified_task_ids"].append(row["task_id"])
        else:
            bucket["non_qualifying_task_count"] += 1
            bucket["non_qualifying_task_ids"].append(row["task_id"])
            for predicate in row.get("qualification_failing_predicates") or []:
                if predicate not in bucket["qualification_failing_predicates"]:
                    bucket["qualification_failing_predicates"].append(predicate)
        ratio = row.get("acceleration_ratio")
        if isinstance(ratio, (int, float)):
            bucket["_acceleration_ratios"].append(float(ratio))
        for field in LATENCY_FIELDS:
            value = row.get(field)
            if isinstance(value, (int, float)):
                bucket["_latency_values"][field].append(float(value))
            else:
                bucket["_latency_unavailable"][field] += 1

    for bucket in summary.values():
        task_count = max(1, int(bucket["task_count"]))
        bucket["avg_wall_clock_s"] = bucket["wall_clock_s"] / task_count
        bucket["avg_cost_usd"] = bucket["cost_usd"] / task_count
        bucket["avg_score"] = bucket["score"] / task_count
        bucket["avg_graceful_degradation"] = bucket["graceful_degradation"] / task_count
        ratios = bucket.pop("_acceleration_ratios")
        bucket["acceleration_ratio_p50"] = _percentile(ratios, 50)
        bucket["acceleration_ratio_p95"] = _percentile(ratios, 95)
        latency_values = bucket.pop("_latency_values")
        latency_unavailable = bucket.pop("_latency_unavailable")
        bucket["latency"] = {
            field: {
                "avg": _mean_or_none(values),
                "p50": _percentile(values, 50),
                "p95": _percentile(values, 95),
                "unavailable_count": latency_unavailable[field],
            }
            for field, values in latency_values.items()
        }
        bucket["qualifies"] = (
            bucket["task_count"] > 0
            and bucket["qualifying_task_count"] == bucket["task_count"]
            and not bucket["qualification_failing_predicates"]
        )

    return {
        "schema_version": "agentic-lead-eval/v1",
        "rows": normalized_rows,
        "summary": summary,
        "recommendation": _build_report_only_recommendation(summary),
        "default_change_allowed": False,
        "default_change_gate": {
            "required_modes": list(REQUIRED_MODES),
            "required_review": "operator_accepts_eval_report",
        },
    }


def agentic_eval_runner(
    *,
    dataset_path: str | Path,
    output_dir: str | Path | None = None,
    execution_mode: str = "fixture_replay",
    workflow_runner: Any | None = None,
    allow_live_calls: bool = False,
) -> dict[str, Any]:
    """Run the agentic lead-mode eval from deterministic workflow cassettes.

    The default path intentionally does not execute live agents or providers.
    It consumes replayed workflow outcomes that preserve the gated workflow
    shape, then applies deterministic scoring and report aggregation.
    """
    dataset = load_agentic_eval_dataset(dataset_path)
    if execution_mode != "fixture_replay" and not allow_live_calls:
        raise RuntimeError("agentic eval live workflow execution is disabled by default")

    rows: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    for task in dataset["tasks"]:
        arms = task["arms"]
        _assert_required_modes(task["task_id"], arms)
        budget = _assert_equal_task_budget(task["task_id"], arms)
        for mode in REQUIRED_MODES:
            arm = arms[mode]
            workflow_result = _workflow_result_for_arm(
                task=task,
                mode=mode,
                arm=arm,
                execution_mode=execution_mode,
                workflow_runner=workflow_runner,
                allow_live_calls=allow_live_calls,
            )
            score = score_agentic_eval_arm(task=task, mode=mode, arm=arm, workflow_result=workflow_result)
            metrics = arm.get("metrics") if isinstance(arm.get("metrics"), dict) else {}
            rejected_gates = _rejected_gate_count(workflow_result)
            retries = _retry_count(workflow_result)
            missed_issues = int(score["failed_verdict_count"])
            quality_divergence = _quality_metric_divergence(
                metrics=metrics,
                authoritative={
                    "missed_issues": missed_issues,
                    "rejected_gates": rejected_gates,
                },
            )
            probe_statuses = _probe_statuses(workflow_result)
            reviewer_panel_decisions = _reviewer_panel_decisions(workflow_result)
            row = {
                "task_id": task["task_id"],
                "mode": mode,
                "token_budget": budget["token_budget"],
                "budget_usd_limit": budget["budget_usd_limit"],
                "wall_clock_s": _number(metrics.get("wall_clock_s")),
                "cost_usd": _number(metrics.get("cost_usd")),
                "retries": _number(metrics.get("retries"), default=retries),
                "rejected_gates": rejected_gates,
                "missed_issues": missed_issues,
                "metrics_divergence": bool(quality_divergence["fields"]),
                "metrics_divergence_fields": quality_divergence["fields"],
                "operator_interventions": _number(metrics.get("operator_interventions")),
                "graceful_degradation": _number(
                    metrics.get("graceful_degradation"),
                    default=_graceful_degradation(score=score, rejected_gates=rejected_gates),
                ),
                "score": score["score"],
                "score_max": score["score_max"],
                "workflow_status": str(workflow_result.get("status") or "unknown"),
                "gate_statuses": _gate_statuses(workflow_result),
                "probe_statuses": probe_statuses,
                "reviewer_panel_decisions": reviewer_panel_decisions,
                "required_verdicts": [item["verdict_id"] for item in score["verdicts"]],
            }
            row.update(_latency_fields_from_metrics(metrics))
            row.update(quality_divergence["reported"])
            rows.append(row)
            evidence_records.append({
                "task_id": task["task_id"],
                "mode": mode,
                "score": score,
                "workflow_status": row["workflow_status"],
                "gate_statuses": row["gate_statuses"],
                "probe_statuses": probe_statuses,
                "reviewer_panel_decisions": reviewer_panel_decisions,
            })

    report = build_agentic_eval_report(rows)
    report["runner"] = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "public_boundary": "agentic_eval_runner",
        "execution_mode": execution_mode,
        "live_calls_allowed": bool(allow_live_calls),
        "workflow_runner_used": bool(execution_mode != "fixture_replay" and workflow_runner is not None),
    }
    report["dataset"] = {
        "schema_version": dataset["schema_version"],
        "path": str(Path(dataset_path)),
        "task_count": len(dataset["tasks"]),
        "sha256": dataset["sha256"],
    }
    report["agentic_lead_policy_snapshot"] = {
        "policy": "off",
        "mutated": False,
        "source": "report_only_runner_invariant",
    }
    report["report_only"] = {
        "default_change_allowed": False,
        "config_mutated": False,
        "policy_mutated": False,
    }
    report["evidence"] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "records": evidence_records,
        "sha256": _sha256_json(evidence_records),
    }
    report["exports"] = {}
    report["report_sha256"] = _sha256_json(_report_without_sha(report))

    if output_dir is not None:
        _export_agentic_eval(report, Path(output_dir))

    return report


def load_agentic_eval_dataset(path: str | Path) -> dict[str, Any]:
    dataset_path = Path(path)
    text = dataset_path.read_text(encoding="utf-8")
    if dataset_path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text) or {}
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("agentic eval dataset must be an object")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("agentic eval dataset requires non-empty tasks")
    normalized_tasks = [_normalise_task(task, index=index) for index, task in enumerate(tasks)]
    dataset = {
        "schema_version": raw.get("schema_version") or DATASET_SCHEMA_VERSION,
        "tasks": normalized_tasks,
    }
    dataset["sha256"] = _sha256_json(dataset)
    return dataset


def score_agentic_eval_arm(
    *,
    task: dict[str, Any],
    mode: str,
    arm: dict[str, Any],
    workflow_result: dict[str, Any] | None = None,
    evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    """Score one arm by a deterministic evidence decision tree."""
    required = _normalise_required_verdicts(task)
    evidence_map = arm.get("verdict_evidence") if isinstance(arm.get("verdict_evidence"), dict) else {}
    root = Path(evidence_root) if evidence_root is not None else Path.cwd()
    verdicts: list[dict[str, Any]] = []
    for verdict in required:
        verdict_id = verdict["verdict_id"]
        evidence = _normalise_evidence_list(evidence_map.get(verdict_id))
        if not evidence:
            passed = False
            reason = "missing_evidence"
        elif any(item["status"] not in PASSING_EVIDENCE_STATUSES for item in evidence):
            passed = False
            reason = "evidence_status_not_passing"
        elif any(not _evidence_ref_resolves(item, root=root) for item in evidence):
            passed = False
            reason = "evidence_ref_not_resolvable"
        else:
            passed = True
            reason = "evidence_present"
        verdicts.append({
            "verdict_id": verdict_id,
            "description": verdict.get("description") or verdict_id,
            "status": "passed" if passed else "failed",
            "reason": reason,
            "evidence": evidence,
        })

    passed_count = sum(1 for verdict in verdicts if verdict["status"] == "passed")
    total = max(1, len(verdicts))
    score = round(5.0 * passed_count / total, 3)
    workflow_status = str((workflow_result or {}).get("status") or "unknown")
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "task_id": str(task.get("task_id") or ""),
        "mode": mode,
        "score": score,
        "score_max": 5,
        "passed_verdict_count": passed_count,
        "failed_verdict_count": len(verdicts) - passed_count,
        "workflow_status": workflow_status,
        "verdicts": verdicts,
    }


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["task_id"] = str(row.get("task_id") or "")
    result["mode"] = str(row.get("mode") or "unknown")
    result["workflow_status"] = str(row.get("workflow_status") or "unknown").strip().lower()
    for field in _NUMERIC_FIELDS:
        result[field] = _number(row.get(field))
    result["token_budget"] = _number(row.get("token_budget"))
    result["budget_usd_limit"] = _number(row.get("budget_usd_limit"))
    for field in LATENCY_FIELDS:
        value = _optional_number(row.get(field))
        result[field] = value
        result[f"{field}_unavailable_reason"] = (
            None
            if value is not None
            else str(row.get(f"{field}_unavailable_reason") or "not_recorded")
        )
    return result


def _attach_acceleration_and_qualification(rows: list[dict[str, Any]]) -> None:
    rows_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_task.setdefault(row["task_id"], []).append(row)

    for task_rows in rows_by_task.values():
        lead = next((row for row in task_rows if row["mode"] == "lead_direct"), None)
        for row in task_rows:
            ratio, reason = _acceleration_ratio(lead=lead, row=row)
            row["acceleration_ratio"] = ratio
            row["acceleration_ratio_unavailable_reason"] = reason
            qualifies, failing = _qualification_result(lead=lead, row=row)
            row["qualifies"] = qualifies
            row["qualification_failing_predicates"] = failing


def _acceleration_ratio(*, lead: dict[str, Any] | None, row: dict[str, Any]) -> tuple[float | None, str | None]:
    if lead is None:
        return None, "lead_direct_missing"
    lead_wall = _optional_number(lead.get("wall_clock_s"))
    row_wall = _optional_number(row.get("wall_clock_s"))
    if lead_wall is None or lead_wall <= 0:
        return None, "lead_direct_wall_clock_unavailable"
    if row_wall is None or row_wall <= 0:
        return None, "mode_wall_clock_unavailable"
    return round(float(lead_wall) / float(row_wall), 3), None


def _qualification_result(*, lead: dict[str, Any] | None, row: dict[str, Any]) -> tuple[bool, list[str]]:
    failing: list[str] = []
    if _status(row.get("workflow_status")) != "accepted":
        failing.append("workflow_status_not_accepted")
    if lead is None:
        failing.append("lead_direct_missing")
        return False, failing
    if row["mode"] == "lead_direct":
        return not failing, failing

    if row.get("score", 0) < lead.get("score", 0):
        failing.append("score_below_lead_direct")
    if row.get("missed_issues", 0) > lead.get("missed_issues", 0):
        failing.append("missed_issues_above_lead_direct")
    if row.get("rejected_gates", 0) > lead.get("rejected_gates", 0):
        failing.append("rejected_gates_above_lead_direct")
    ratio = row.get("acceleration_ratio")
    if ratio is None:
        failing.append("acceleration_ratio_unavailable")
    elif float(ratio) < ACCELERATION_WIN_THRESHOLD:
        failing.append("acceleration_ratio_below_1_2")
    return not failing, failing


def _status(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    if status == "accept":
        return "accepted"
    return status


def _latency_fields_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in LATENCY_FIELDS:
        value = _optional_number(metrics.get(field))
        values[field] = value
        if value is None:
            values[f"{field}_unavailable_reason"] = "not_recorded" if field not in metrics else "invalid_metric"
        else:
            values[f"{field}_unavailable_reason"] = None
    return values


def _build_report_only_recommendation(summary: dict[str, dict[str, Any]]) -> dict[str, Any]:
    modes: dict[str, dict[str, Any]] = {}
    qualifying_modes: list[str] = []
    for mode in REQUIRED_MODES:
        bucket = summary.get(mode, {})
        qualifies = bool(bucket.get("qualifies")) if mode != "lead_direct" else False
        if qualifies:
            qualifying_modes.append(mode)
        modes[mode] = {
            "qualifies": qualifies,
            "failing_predicates": list(bucket.get("qualification_failing_predicates") or []),
            "qualifying_task_count": int(bucket.get("qualifying_task_count") or 0),
            "task_count": int(bucket.get("task_count") or 0),
        }
    return {
        "schema_version": "agentic-lead-eval-recommendation/v1",
        "report_only": True,
        "policy_mutated": False,
        "default_change_allowed": False,
        "thresholds": {
            "acceleration_ratio_min": ACCELERATION_WIN_THRESHOLD,
            "requires_workflow_status": "accepted",
            "requires_score_at_least_lead_direct": True,
            "requires_missed_issues_no_more_than_lead_direct": True,
            "requires_rejected_gates_no_more_than_lead_direct": True,
        },
        "modes": modes,
        "recommended_policy": "keep_off" if not qualifying_modes else "operator_review_required",
        "qualifying_modes": qualifying_modes,
    }


def _quality_metric_divergence(
    *,
    metrics: dict[str, Any],
    authoritative: dict[str, int | float],
) -> dict[str, Any]:
    reported: dict[str, int | float] = {}
    fields: list[str] = []
    for field, authoritative_value in authoritative.items():
        if field not in metrics:
            continue
        reported_value = _number(metrics.get(field))
        if reported_value != authoritative_value:
            reported[f"reported_{field}"] = reported_value
            fields.append(field)
    return {
        "fields": fields,
        "reported": reported,
    }


def _number(value: Any, *, default: int | float = 0) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return int(parsed) if parsed.is_integer() else parsed


def _optional_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _percentile(values: list[float], percentile: int | float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (float(percentile) / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(value, 3)


def _normalise_task(task: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError(f"agentic eval task at index {index} must be an object")
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise ValueError(f"agentic eval task at index {index} is missing task_id")
    required_verdicts = _normalise_required_verdicts(task)
    arms = task.get("arms")
    if not isinstance(arms, dict):
        raise ValueError(f"agentic eval task {task_id} requires arms")
    normalized_arms = {
        str(mode): _normalise_arm(task_id=task_id, mode=str(mode), arm=arm)
        for mode, arm in arms.items()
    }
    return {
        "schema_version": "agentic-lead-eval-task/v1",
        "task_id": task_id,
        "intent": str(task.get("intent") or ""),
        "required_verdicts": required_verdicts,
        "arms": normalized_arms,
    }


def _normalise_arm(*, task_id: str, mode: str, arm: Any) -> dict[str, Any]:
    if not isinstance(arm, dict):
        raise ValueError(f"agentic eval arm {task_id}/{mode} must be an object")
    budget = arm.get("budget")
    if not isinstance(budget, dict):
        raise ValueError(f"agentic eval arm {task_id}/{mode} requires budget")
    workflow_result = arm.get("workflow_result")
    if workflow_result is None:
        workflow_result = arm.get("workflow")
    if not isinstance(workflow_result, dict):
        raise ValueError(f"agentic eval arm {task_id}/{mode} requires workflow_result")
    return {
        "budget": {
            "token_budget": _number(budget.get("token_budget")),
            "budget_usd_limit": _number(budget.get("budget_usd_limit")),
        },
        "workflow_result": workflow_result,
        "metrics": dict(arm.get("metrics") or {}),
        "verdict_evidence": dict(arm.get("verdict_evidence") or {}),
    }


def _normalise_required_verdicts(task: dict[str, Any]) -> list[dict[str, str]]:
    raw = task.get("required_verdicts")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"agentic eval task {task.get('task_id') or '<unknown>'} requires required_verdicts")
    verdicts: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            verdict_id = item.strip()
            description = verdict_id
        elif isinstance(item, dict):
            verdict_id = str(item.get("id") or item.get("verdict_id") or "").strip()
            description = str(item.get("description") or verdict_id)
        else:
            raise ValueError(f"required_verdicts[{index}] must be a string or object")
        if not verdict_id:
            raise ValueError(f"required_verdicts[{index}] is missing id")
        verdicts.append({"verdict_id": verdict_id, "description": description})
    return verdicts


def _normalise_evidence_list(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    evidence: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or item.get("path") or item.get("hunk") or "").strip()
        status = str(item.get("status") or "passed").strip().lower()
        if kind not in CONCRETE_EVIDENCE_KINDS or not ref:
            continue
        evidence.append({
            "kind": kind,
            "ref": ref,
            "status": status,
        })
    return evidence


def _evidence_ref_resolves(item: dict[str, str], *, root: Path) -> bool:
    kind = item["kind"]
    ref = item["ref"]
    if kind == "probe_receipt":
        return bool(ref)
    if kind == "artifact_path":
        return (root / ref).exists()
    if kind == "diff_hunk":
        path_ref = ref.split(":", 1)[0]
        return bool(path_ref) and (root / path_ref).exists()
    return False


def _assert_required_modes(task_id: str, arms: dict[str, Any]) -> None:
    observed = set(arms)
    required = set(REQUIRED_MODES)
    if observed != required:
        raise ValueError(
            f"agentic eval task {task_id} must include exactly modes {list(REQUIRED_MODES)}; "
            f"observed {sorted(observed)}"
        )


def _assert_equal_task_budget(task_id: str, arms: dict[str, Any]) -> dict[str, int | float]:
    budgets = {
        mode: {
            "token_budget": arms[mode]["budget"]["token_budget"],
            "budget_usd_limit": arms[mode]["budget"]["budget_usd_limit"],
        }
        for mode in REQUIRED_MODES
    }
    unique = {json.dumps(value, sort_keys=True) for value in budgets.values()}
    if len(unique) != 1:
        raise ValueError(f"agentic eval task {task_id} has unequal arm budgets: {budgets}")
    return dict(budgets[REQUIRED_MODES[0]])


def _workflow_result_for_arm(
    *,
    task: dict[str, Any],
    mode: str,
    arm: dict[str, Any],
    execution_mode: str,
    workflow_runner: Any | None,
    allow_live_calls: bool,
) -> dict[str, Any]:
    if execution_mode == "fixture_replay":
        workflow_result = dict(arm["workflow_result"])
        _validate_replay_workflow_shape(task_id=str(task["task_id"]), mode=mode, workflow_result=workflow_result)
        return workflow_result
    if not allow_live_calls:
        raise RuntimeError("agentic eval live workflow execution is disabled by default")
    if workflow_runner is None:
        raise ValueError("non-fixture agentic eval execution requires workflow_runner")
    result = workflow_runner(task=task, mode=mode, arm=arm)
    if not isinstance(result, dict):
        raise ValueError("workflow_runner must return a workflow result object")
    return result


def _rejected_gate_count(workflow_result: dict[str, Any]) -> int:
    rejected_gates = sum(1 for status in _gate_statuses(workflow_result).values() if status != "accepted")
    rejected_probes = any(status not in PASSING_EVIDENCE_STATUSES for status in _probe_statuses(workflow_result).values())
    return rejected_gates + int(rejected_probes)


def _retry_count(workflow_result: dict[str, Any]) -> int:
    retries = 0
    for step in _workflow_steps(workflow_result):
        attempts = int(_number(step.get("attempt_count") or step.get("attempts"), default=1))
        retries += max(0, attempts - 1)
    return retries


def _gate_statuses(workflow_result: dict[str, Any]) -> dict[str, str]:
    return {
        str(step.get("gate") or "unknown"): str(step.get("status") or "unknown")
        for step in _workflow_steps(workflow_result)
    }


def _workflow_steps(workflow_result: dict[str, Any]) -> list[dict[str, Any]]:
    steps = workflow_result.get("steps")
    if isinstance(steps, list):
        return [step for step in steps if isinstance(step, dict)]
    final = workflow_result.get("final_gate_result")
    if isinstance(final, dict):
        return [{
            "gate": final.get("gate") or "outcome_review",
            "status": final.get("status") or workflow_result.get("status") or "unknown",
            "attempt_count": final.get("attempts") or 1,
        }]
    return []


def _probe_statuses(workflow_result: dict[str, Any]) -> dict[str, str]:
    final = workflow_result.get("final_gate_result")
    if not isinstance(final, dict):
        return {}
    probes = final.get("probes")
    if not isinstance(probes, dict):
        return {}
    statuses: dict[str, str] = {}
    for probe_id, payload in probes.items():
        if isinstance(payload, dict):
            statuses[str(probe_id)] = str(payload.get("status") or "unknown").strip().lower()
        else:
            statuses[str(probe_id)] = str(payload or "unknown").strip().lower()
    return statuses


def _reviewer_panel_decisions(workflow_result: dict[str, Any]) -> list[dict[str, str]]:
    final = workflow_result.get("final_gate_result")
    if not isinstance(final, dict):
        return []
    reviewers = final.get("independent_reviewer_results")
    if not isinstance(reviewers, list):
        return []
    decisions: list[dict[str, str]] = []
    for index, reviewer in enumerate(reviewers):
        if not isinstance(reviewer, dict):
            continue
        decision = str(reviewer.get("decision") or "").strip().lower()
        reviewer_id = str(reviewer.get("reviewer_id") or f"reviewer-{index}")
        if decision:
            decisions.append({"reviewer_id": reviewer_id, "decision": decision})
    return decisions


def _validate_replay_workflow_shape(*, task_id: str, mode: str, workflow_result: dict[str, Any]) -> None:
    gates = _gate_statuses(workflow_result)
    if not gates:
        raise ValueError(f"agentic eval task {task_id}/{mode} replay missing workflow gates")
    final = workflow_result.get("final_gate_result")
    final = final if isinstance(final, dict) else {}
    final_status = str(final.get("status") or workflow_result.get("status") or "").strip().lower()
    reached_outcome_review = "outcome_review" in gates
    terminal_accept = final_status in {"accept", "accepted"} or str(workflow_result.get("status") or "").strip().lower() in {
        "accept",
        "accepted",
    }
    missing_gates = [gate for gate in REQUIRED_WORKFLOW_GATES if gate not in gates]
    if missing_gates and (terminal_accept or reached_outcome_review):
        raise ValueError(f"agentic eval task {task_id}/{mode} replay missing workflow gates: {missing_gates}")

    probe_statuses = _probe_statuses(workflow_result)
    required_probes = list(REQUIRED_BASE_PROBES)
    if mode in {"agentic_allowed", "agentic_required"}:
        required_probes.extend(REQUIRED_AGENTIC_PROBES)
    missing_probes = [probe for probe in required_probes if probe not in probe_statuses]
    if missing_probes and (terminal_accept or reached_outcome_review):
        raise ValueError(f"agentic eval task {task_id}/{mode} replay missing probes: {missing_probes}")

    if not _reviewer_panel_decisions(workflow_result) and (terminal_accept or reached_outcome_review):
        raise ValueError(f"agentic eval task {task_id}/{mode} replay missing reviewer panel decisions")


def _graceful_degradation(*, score: dict[str, Any], rejected_gates: int) -> float:
    base = float(score.get("score") or 0.0) / max(1.0, float(score.get("score_max") or 5))
    if rejected_gates:
        base = base / (1 + rejected_gates)
    return round(base, 3)


def _export_agentic_eval(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = output_dir / "rows.jsonl"
    evidence_path = output_dir / "evidence.json"
    report_path = output_dir / "report.json"
    manifest_path = output_dir / "replay-manifest.json"

    rows_path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in report["rows"]),
        encoding="utf-8",
    )
    evidence_path.write_text(
        json.dumps(report["evidence"], sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    report["exports"] = {
        "report_json": str(report_path),
        "evidence_json": str(evidence_path),
        "rows_jsonl": str(rows_path),
        "replay_manifest": str(manifest_path),
    }
    report["report_sha256"] = _sha256_json(_report_without_sha(report))
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "agentic-lead-eval-replay-manifest/v1",
        "dataset_sha256": report["dataset"]["sha256"],
        "report_sha256": report["report_sha256"],
        "evidence_sha256": report["evidence"]["sha256"],
        "required_modes": list(REQUIRED_MODES),
        "execution_mode": report["runner"]["execution_mode"],
        "live_calls_allowed": report["runner"]["live_calls_allowed"],
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _report_without_sha(report: dict[str, Any]) -> dict[str, Any]:
    result = dict(report)
    result["report_sha256"] = ""
    result["exports"] = {}
    return result


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
