"""Fixture orchestration for report-only supervisor AutoResearch runs."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from .evaluator import EvaluatorContractError, run_evaluator_trials
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
    "autoresearch_validation_started",
    "autoresearch_validation_completed",
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
    experiment = AutoresearchExperiment.from_mapping(fixture["experiment"])
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
                execution = run_evaluator_trials(
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
                    metric_source=execution.metric_source,
                    evaluator_run_ref=execution.evaluator_run_ref,
                    evaluator_run_hash=execution.evaluator_run_hash,
                    attempt_worktree_ref=execution.attempt_worktree_ref,
                    evidence_refs=execution.evidence_refs,
                    execution_errors=execution.execution_errors,
                    cost_usd=round(attempt.cost_usd + execution.cost_usd, 6),
                    wall_clock_s=execution.wall_clock_s,
                )
                emit(
                    "autoresearch_attempt_execution_job_completed",
                    {
                        **attempt.to_payload(),
                        "status": "completed",
                        "budget_usd": experiment.budget_usd,
                        "timeout_s": experiment.timeout_s,
                    },
                )
            except (EvaluatorContractError, TimeoutError, subprocess.TimeoutExpired) as exc:  # type: ignore[name-defined]
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
        emit("autoresearch_validation_completed", validation_payload)

    report = build_autoresearch_report(validation_reports)
    report["experiment"] = experiment.to_payload()
    report["event_kinds"] = emitted_event_kinds
    report["supported_event_kinds"] = list(AUTORESEARCH_EVENT_KINDS)
    report["event_ids"] = event_ids
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
    return clone


def _execution_output_dir(
    *,
    output_dir: str | Path | None,
    repo_root: str | Path,
    run_id: str,
) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    return Path(repo_root).expanduser().resolve() / ".scratch" / "autoresearch" / run_id
