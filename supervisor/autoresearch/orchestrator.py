"""Fixture orchestration for report-only supervisor AutoResearch runs."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from .durable_jobs import resolve_evaluator_defaults, run_durable_evaluator_trials
from .evaluator import EvaluatorContractError
from .policy_evolution import (
    derive_policy_evolution_proposals_from_report,
    report_contains_derivable_policy_record,
)
from .report import build_autoresearch_report
from .schema import AutoresearchAttempt, AutoresearchExperiment, sha256_json
from .validation import validate_attempt


class EventWriter(Protocol):
    def write_event(self, *, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> int:
        ...


AUTORESEARCH_EVENT_KINDS = (
    "autoresearch_experiment_started",
    "autoresearch_attempt_started",
    "autoresearch_attempt_execution_job_submitted",
    "autoresearch_attempt_execution_job_started",
    "autoresearch_attempt_execution_job_completed",
    "autoresearch_attempt_execution_job_failed",
    "autoresearch_attempt_completed",
    "autoresearch_evaluator_quality_started",
    "autoresearch_evaluator_quality_control_started",
    "autoresearch_evaluator_quality_control_completed",
    "autoresearch_evaluator_quality_completed",
    "autoresearch_validation_started",
    "autoresearch_validation_completed",
    "autoresearch_policy_proposal_derivation_skipped",
    "autoresearch_policy_proposal_created",
    "autoresearch_policy_proposal_approved",
    "autoresearch_policy_proposal_denied",
    "autoresearch_policy_proposal_rolled_back",
    "autoresearch_report_emitted",
)


def run_autoresearch_fixture(
    *,
    fixture_path: str | Path,
    state: EventWriter,
    run_id: str,
    repo_root: str | Path = ".",
    output_dir: str | Path | None = None,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    """Run a deterministic fixture and write ledger-backed evidence events."""
    fixture = _load_fixture(fixture_path)
    experiment = resolve_evaluator_defaults(
        AutoresearchExperiment.from_mapping(fixture["experiment"]),
        repo_root=repo_root,
    )
    if execution_mode is not None:
        experiment = replace(experiment, execution_mode=execution_mode)
    attempts = [
        AutoresearchAttempt.from_mapping(raw, experiment=experiment)
        for raw in fixture.get("attempts", [])
    ]
    if not attempts:
        raise ValueError("autoresearch fixture requires at least one attempt")

    event_ids: list[int] = []
    emitted_event_kinds: list[str] = []

    def emit(kind: str, payload: dict[str, Any]) -> None:
        event_ids.append(_write_event(
            state,
            run_id=run_id,
            kind=kind,
            payload=payload,
        ))
        emitted_event_kinds.append(kind)

    emit(
        "autoresearch_experiment_started",
        {**experiment.to_payload(), "status": "started"},
    )

    validation_reports = []
    for attempt in attempts:
        emit(
            "autoresearch_attempt_started",
            {**attempt.to_payload(), "status": "started"},
        )
        if experiment.execution_mode == "live":
            emit(
                "autoresearch_attempt_execution_job_submitted",
                {
                    "schema_version": "supervisor-autoresearch/v1",
                    "experiment_id": experiment.experiment_id,
                    "task_id": experiment.task_id,
                    "attempt_id": attempt.attempt_id,
                    "status": "submitted",
                    "budget_usd": experiment.budget_usd,
                    "timeout_s": experiment.timeout_s,
                },
            )
            emit(
                "autoresearch_attempt_execution_job_started",
                {
                    "schema_version": "supervisor-autoresearch/v1",
                    "experiment_id": experiment.experiment_id,
                    "task_id": experiment.task_id,
                    "attempt_id": attempt.attempt_id,
                    "status": "started",
                    "budget_usd": experiment.budget_usd,
                    "timeout_s": experiment.timeout_s,
                },
            )
            try:
                execution = run_durable_evaluator_trials(
                    state=state,
                    run_id=run_id,
                    experiment=experiment,
                    attempt=attempt,
                    repo_root=repo_root,
                    output_dir=_execution_output_dir(
                        output_dir=output_dir,
                        repo_root=repo_root,
                        run_id=run_id,
                    ),
                )
                attempt = replace(
                    attempt,
                    metric_trials=execution.metric_trials,
                    metric_before=_execution_or_attempt_metric(
                        execution.metric_before,
                        attempt.metric_before,
                        attempt=attempt,
                    ),
                    metric_after=_execution_or_attempt_metric(
                        execution.metric_after,
                        attempt.metric_after,
                        attempt=attempt,
                    ),
                    metric_delta=_execution_or_attempt_metric(
                        execution.metric_delta,
                        attempt.metric_delta,
                        attempt=attempt,
                    ),
                    metric_source=execution.metric_source,
                    evaluator_run_ref=execution.evaluator_run_ref,
                    evaluator_run_hash=execution.evaluator_run_hash,
                    attempt_worktree_ref=execution.attempt_worktree_ref,
                    evidence_refs=execution.evidence_refs,
                    evaluator_quality=execution.evaluator_quality or attempt.evaluator_quality,
                    execution_errors=execution.execution_errors,
                    cost_usd=round(attempt.cost_usd + execution.cost_usd, 6),
                    wall_clock_s=execution.wall_clock_s,
                )
                emit(
                    "autoresearch_attempt_execution_job_completed",
                    {
                        **attempt.to_payload(),
                        "status": "completed",
                        "job_id": execution.job_id,
                        "resumed_from_trial_count": execution.resumed_from_trial_count,
                        "budget_usd": experiment.budget_usd,
                        "timeout_s": experiment.timeout_s,
                    },
                )
            except (EvaluatorContractError, TimeoutError) as exc:  # type: ignore[name-defined]
                attempt = replace(
                    attempt,
                    execution_errors=(str(exc),),
                    status="failed",
                )
                emit(
                    "autoresearch_attempt_execution_job_failed",
                    {
                        **attempt.to_payload(),
                        "status": "failed",
                        "budget_usd": experiment.budget_usd,
                        "timeout_s": experiment.timeout_s,
                        "error": str(exc),
                    },
                )
        emit(
            "autoresearch_attempt_completed",
            {
                **attempt.to_payload(),
                "status": attempt.status if attempt.status != "pending" else "completed",
            },
        )
        if attempt.evaluator_quality:
            emit(
                "autoresearch_evaluator_quality_started",
                {
                    "schema_version": "supervisor-autoresearch/v1",
                    "experiment_id": experiment.experiment_id,
                    "task_id": experiment.task_id,
                    "attempt_id": attempt.attempt_id,
                    "status": "started",
                    "control_refs": _quality_control_refs(attempt.evaluator_quality),
                    "source": _quality_source(attempt.evaluator_quality),
                    "evidence_grade": _quality_evidence_grade(attempt.evaluator_quality),
                    "default_change_allowed": False,
                },
            )
        emit(
            "autoresearch_validation_started",
            {
                "schema_version": "supervisor-autoresearch/v1",
                "experiment_id": experiment.experiment_id,
                "task_id": experiment.task_id,
                "attempt_id": attempt.attempt_id,
                "status": "started",
            },
        )
        validation_reports.append(validate_attempt(
            experiment=experiment,
            attempt=attempt,
            repo_root=repo_root,
        ))
        validation_payload = validation_reports[-1].to_payload()
        if attempt.evaluator_quality:
            evaluator_quality = validation_payload["evaluator_quality"]
            for control_kind, control in _quality_controls(evaluator_quality):
                emit(
                    "autoresearch_evaluator_quality_control_started",
                    {
                        "schema_version": "supervisor-autoresearch/v1",
                        "experiment_id": experiment.experiment_id,
                        "task_id": experiment.task_id,
                        "attempt_id": attempt.attempt_id,
                        "control_kind": control_kind,
                        "status": "started",
                        "source": _quality_source(control),
                        "evidence_grade": _quality_evidence_grade(control),
                        "default_change_allowed": False,
                    },
                )
                emit(
                    "autoresearch_evaluator_quality_control_completed",
                    {
                        "schema_version": "supervisor-autoresearch/v1",
                        "experiment_id": experiment.experiment_id,
                        "task_id": experiment.task_id,
                        "attempt_id": attempt.attempt_id,
                        "control_kind": control_kind,
                        "status": control.get("verdict") or evaluator_quality["verdict"],
                        "control": control,
                        "receipt": {
                            "receipt_id": f"autoresearch-quality:{attempt.attempt_id}:{control_kind}",
                            "source": _quality_source(control),
                            "evidence_grade": _quality_evidence_grade(control),
                            "control_kind": control_kind,
                            "candidate_ref": control.get("candidate_ref"),
                            "candidate_hash": control.get("candidate_hash"),
                            "metric_delta": control.get("metric_delta"),
                            "control_run_ref": control.get("control_run_ref"),
                            "control_run_hash": control.get("control_run_hash"),
                            "verdict": control.get("verdict"),
                        },
                        "default_change_allowed": False,
                        "policy_mutated": False,
                        "gate_advanced": False,
                    },
                )
            emit(
                "autoresearch_evaluator_quality_completed",
                {
                    "schema_version": "supervisor-autoresearch/v1",
                    "experiment_id": experiment.experiment_id,
                    "task_id": experiment.task_id,
                    "attempt_id": attempt.attempt_id,
                    "status": evaluator_quality["verdict"],
                    "evaluator_quality": evaluator_quality,
                    "gaming_flags": validation_payload["gaming_flags"],
                    "validation_errors": validation_payload["validation_errors"],
                    "receipt": {
                        "receipt_id": f"autoresearch-quality:{attempt.attempt_id}",
                        "source": _quality_source(evaluator_quality),
                        "evidence_grade": _quality_evidence_grade(evaluator_quality),
                        "supervisor_runtime_origin": evaluator_quality.get("supervisor_runtime_origin") or "",
                        "control_refs": evaluator_quality["control_refs"],
                        "quality_manifest_ref": evaluator_quality.get("quality_manifest_ref"),
                        "quality_manifest_hash": evaluator_quality.get("quality_manifest_hash"),
                        "verdict": evaluator_quality["verdict"],
                    },
                    "default_change_allowed": False,
                    "policy_mutated": False,
                    "gate_advanced": False,
                },
            )
        emit("autoresearch_validation_completed", validation_payload)

    report = build_autoresearch_report(validation_reports)
    report["experiment"] = experiment.to_payload()
    report["event_kinds"] = emitted_event_kinds
    report["supported_event_kinds"] = list(AUTORESEARCH_EVENT_KINDS)
    report["event_ids"] = event_ids
    if output_dir is not None:
        report["report_ref"] = (Path(output_dir) / "report.json").as_posix()
    report["report_sha256"] = sha256_json(_without_report_sha(report))
    report_event_id = _write_event(
        state,
        run_id=run_id,
        kind="autoresearch_report_emitted",
        payload={
            "schema_version": "supervisor-autoresearch/v1",
            "experiment_id": experiment.experiment_id,
            "task_id": experiment.task_id,
            "attempt_count": len(attempts),
            "report_sha256": report["report_sha256"],
            "default_change_allowed": False,
        },
    )
    report["event_ids"].append(report_event_id)
    report["event_kinds"].append("autoresearch_report_emitted")
    report["report_sha256"] = sha256_json(_without_report_sha(report))
    derived = []
    if report_contains_derivable_policy_record(report, repo_root=repo_root):
        derived = derive_policy_evolution_proposals_from_report(
            report,
            repo_root=repo_root,
            affected_gates=_affected_gates(report),
            state=state,
            run_id=run_id,
        )
    report["derived_policy_proposals"] = [
        {
            "proposal_id": proposal.get("proposal_id"),
            "status": proposal.get("status"),
            "source": proposal.get("source"),
        }
        for proposal in derived
    ]
    if output_dir is not None:
        _export_report(report, Path(output_dir))
    return report


def _load_fixture(path: str | Path) -> dict[str, Any]:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("experiment"), dict):
        raise ValueError("autoresearch fixture must contain an experiment object")
    return raw


def _write_event(
    state: EventWriter,
    *,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
) -> int:
    return int(state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind=kind,
        payload=payload,
    ))


def _export_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _without_report_sha(report: dict[str, Any]) -> dict[str, Any]:
    clone = dict(report)
    clone.pop("report_sha256", None)
    clone.pop("event_ids", None)
    clone.pop("derived_policy_proposals", None)
    return clone


def _affected_gates(report: dict[str, Any]) -> tuple[str, ...]:
    gates: set[str] = set()
    for record in report.get("records") or []:
        if not isinstance(record, dict):
            continue
        for key in ("affected_gates", "gates"):
            raw = record.get(key)
            if isinstance(raw, (list, tuple)):
                gates.update(str(item) for item in raw if str(item).strip())
        gate = str(record.get("gate") or "").strip()
        if gate:
            gates.add(gate)
    return tuple(sorted(gates or {"outcome_review"}))


def _execution_output_dir(
    *,
    output_dir: str | Path | None,
    repo_root: str | Path,
    run_id: str,
) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    return Path(repo_root).expanduser().resolve() / ".scratch" / "autoresearch" / run_id


def _execution_or_attempt_metric(
    execution_value: float | None,
    attempt_value: float | None,
    *,
    attempt: AutoresearchAttempt,
) -> float | None:
    if execution_value is not None:
        return execution_value
    if attempt.metric_source == "pending":
        return None
    return attempt_value


def _quality_control_refs(evaluator_quality: dict[str, Any]) -> list[str]:
    controls = evaluator_quality.get("controls")
    if not isinstance(controls, dict):
        return []
    return [kind for kind in ("noop", "harmful", "known_good") if kind in controls]


def _quality_controls(evaluator_quality: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    controls = evaluator_quality.get("controls")
    if not isinstance(controls, dict):
        return []
    return [
        (kind, dict(controls[kind]))
        for kind in ("noop", "harmful", "known_good")
        if isinstance(controls.get(kind), dict)
    ]


def _quality_source(evaluator_quality: dict[str, Any]) -> str:
    source = str(evaluator_quality.get("source") or "")
    if source == "supervisor_control_execution":
        return source
    return "caller_supplied_metadata"


def _quality_evidence_grade(evaluator_quality: dict[str, Any]) -> str:
    if (
        str(evaluator_quality.get("source") or "") == "supervisor_control_execution"
        and str(evaluator_quality.get("evidence_grade") or "") == "runtime_native"
        and str(evaluator_quality.get("supervisor_runtime_origin") or "") == "run_evaluator_quality_controls"
    ):
        return "runtime_native"
    return "self_reported"
