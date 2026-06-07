"""Fixture orchestration for report-only supervisor AutoResearch runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .report import build_autoresearch_report
from .schema import AutoresearchAttempt, AutoresearchExperiment, sha256_json
from .validation import validate_attempt


class EventWriter(Protocol):
    def write_event(self, *, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> int:
        ...


AUTORESEARCH_EVENT_KINDS = (
    "autoresearch_experiment_started",
    "autoresearch_attempt_started",
    "autoresearch_attempt_completed",
    "autoresearch_validation_started",
    "autoresearch_validation_completed",
    "autoresearch_report_emitted",
)


def run_autoresearch_fixture(
    *,
    fixture_path: str | Path,
    state: EventWriter,
    run_id: str,
    repo_root: str | Path = ".",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run a deterministic fixture and write ledger-backed evidence events."""
    fixture = _load_fixture(fixture_path)
    experiment = AutoresearchExperiment.from_mapping(fixture["experiment"])
    attempts = [
        AutoresearchAttempt.from_mapping(raw, experiment=experiment)
        for raw in fixture.get("attempts", [])
    ]
    if not attempts:
        raise ValueError("autoresearch fixture requires at least one attempt")

    event_ids: list[int] = []
    event_ids.append(_write_event(
        state,
        run_id=run_id,
        kind="autoresearch_experiment_started",
        payload={**experiment.to_payload(), "status": "started"},
    ))

    validation_reports = []
    for attempt in attempts:
        event_ids.append(_write_event(
            state,
            run_id=run_id,
            kind="autoresearch_attempt_started",
            payload={**attempt.to_payload(), "status": "started"},
        ))
        event_ids.append(_write_event(
            state,
            run_id=run_id,
            kind="autoresearch_attempt_completed",
            payload={**attempt.to_payload(), "status": "completed"},
        ))
        event_ids.append(_write_event(
            state,
            run_id=run_id,
            kind="autoresearch_validation_started",
            payload={
                "schema_version": "supervisor-autoresearch/v1",
                "experiment_id": experiment.experiment_id,
                "task_id": experiment.task_id,
                "attempt_id": attempt.attempt_id,
                "status": "started",
            },
        ))
        validation_reports.append(validate_attempt(
            experiment=experiment,
            attempt=attempt,
            repo_root=repo_root,
        ))
        validation_payload = validation_reports[-1].to_payload()
        event_ids.append(_write_event(
            state,
            run_id=run_id,
            kind="autoresearch_validation_completed",
            payload=validation_payload,
        ))

    report = build_autoresearch_report(validation_reports)
    report["experiment"] = experiment.to_payload()
    report["event_kinds"] = list(AUTORESEARCH_EVENT_KINDS)
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
