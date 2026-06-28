"""Generate operator-activated AutoResearch experiments from ledger signals."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from .durable_jobs import resolve_evaluator_defaults
from .orchestrator import run_autoresearch_fixture
from .schema import AutoresearchAttempt, AutoresearchExperiment, sha256_json
from .validation import DEFAULT_IMMUTABLE_PATHS


AUTORESEARCH_DRAFT_SCHEMA_VERSION = "supervisor-autoresearch-experiment-draft/v1"
AUTORESEARCH_ACTIVATION_SCHEMA_VERSION = "supervisor-autoresearch-experiment-activation/v1"
AUTORESEARCH_RUNNER_SCHEMA_VERSION = "supervisor-autoresearch-auto-runner/v1"
RESERVED_OPERATOR_IDENTITIES = frozenset({
    "codex-supervisor-axi",
    "codex-supervisor",
    "autoresearch",
    "auto",
    "automated",
    "system",
})


@dataclass(frozen=True)
class AutoResearchGeneratorConfig:
    recurrence_threshold: int = 3
    max_open_experiment_drafts: int = 20
    max_runnable_experiments_per_week: int = 2
    default_k_trials: int = 3
    default_budget_usd: float = 0.25
    default_timeout_s: float = 60.0

    @classmethod
    def from_config(cls, config: Any) -> "AutoResearchGeneratorConfig":
        defaults = cls()
        autoresearch = getattr(config, "autoresearch", None)
        return cls(
            recurrence_threshold=int(
                getattr(autoresearch, "signal_recurrence_threshold", defaults.recurrence_threshold)
            ),
            max_open_experiment_drafts=int(
                getattr(autoresearch, "max_open_experiment_drafts", defaults.max_open_experiment_drafts)
            ),
            max_runnable_experiments_per_week=int(
                getattr(
                    autoresearch,
                    "max_runnable_experiments_per_week",
                    defaults.max_runnable_experiments_per_week,
                )
            ),
            default_k_trials=int(getattr(autoresearch, "evaluator_k_trials", defaults.default_k_trials)),
            default_budget_usd=float(getattr(autoresearch, "evaluator_budget_usd", defaults.default_budget_usd)),
            default_timeout_s=float(getattr(autoresearch, "evaluator_timeout_s", defaults.default_timeout_s)),
        )


@dataclass(frozen=True)
class _Signal:
    signal_kind: str
    task_class: str
    gate: str
    taxonomy_code: str
    run_id: str
    event_id: int | None
    lesson_id: str
    implicated_paths: tuple[str, ...]


def generate_autoresearch_experiment_drafts(
    *,
    state: Any,
    repo_root: str | Path,
    config: AutoResearchGeneratorConfig | None = None,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Draft AutoResearch experiments from repeated ledger signals.

    Drafting is idempotent by `signal_key`. The generated rows are inert:
    runnable execution requires `activate_autoresearch_experiment`.
    """
    cfg = config or AutoResearchGeneratorConfig()
    repo_root_path = Path(repo_root).expanduser().resolve()
    timestamp = int(time.time()) if now is None else int(now)
    open_count = len([
        row for row in state.list_autoresearch_experiment_queue(limit=10_000)
        if row["status"] in {"draft", "runnable", "running"}
    ])
    remaining = max(0, int(cfg.max_open_experiment_drafts) - open_count)
    if remaining <= 0:
        return []

    clusters = _cluster_signals(_read_signals(state))
    drafted: list[dict[str, Any]] = []
    for cluster_key, signals in sorted(clusters.items()):
        if len(signals) < max(1, int(cfg.recurrence_threshold)):
            continue
        if len(drafted) >= remaining:
            break
        task_class, gate, taxonomy_code = cluster_key
        signal_key = _signal_key(task_class=task_class, gate=gate, taxonomy_code=taxonomy_code)
        implicated_paths = _cluster_implicated_paths(signals, gate=gate, taxonomy_code=taxonomy_code)
        immutable = _implicates_immutable_surface(implicated_paths)
        status = "report_only" if immutable else "draft"
        mutable_paths = () if immutable else implicated_paths
        experiment = resolve_evaluator_defaults(
            AutoresearchExperiment(
                experiment_id="autoexp-" + sha256_json({
                    "signal_key": signal_key,
                    "schema_version": AUTORESEARCH_DRAFT_SCHEMA_VERSION,
                })[:16],
                task_id=f"autoresearch:{task_class}:{gate}:{taxonomy_code}",
                hypothesis=_hypothesis(
                    task_class=task_class,
                    gate=gate,
                    taxonomy_code=taxonomy_code,
                    implicated_paths=implicated_paths,
                ),
                baseline_ref=f"ledger-signal:{signal_key}",
                mutable_paths=mutable_paths,
                immutable_paths=DEFAULT_IMMUTABLE_PATHS,
                evaluator_ref="",
                evaluator_hash="",
                metric_name="",
                max_attempts=1,
                k_trials=max(1, int(cfg.default_k_trials)),
                budget_usd=max(0.0, float(cfg.default_budget_usd)),
                timeout_s=max(0.001, float(cfg.default_timeout_s)),
                execution_mode="live",
            ),
            repo_root=repo_root_path,
        )
        attempt = _attempt_for_experiment(experiment, mutable_paths=mutable_paths)
        provenance = _provenance_payload(
            signal_key=signal_key,
            signals=signals,
            implicated_paths=implicated_paths,
        )
        proposal_pointer = (
            {
                "proposal_kind": "stability_proposal_required",
                "reason": "immutable_surface_implicated",
                "implicated_paths": list(implicated_paths),
            }
            if immutable else {}
        )
        row, created = state.upsert_autoresearch_experiment_draft(
            experiment_id=experiment.experiment_id,
            signal_key=signal_key,
            status=status,
            task_class=task_class,
            gate=gate,
            taxonomy_code=taxonomy_code,
            experiment=experiment.to_payload(),
            attempt=attempt.to_payload(),
            provenance=provenance,
            report_only_reason="immutable_surface_implicated" if immutable else "",
            proposal_pointer=proposal_pointer,
            created_at=timestamp,
        )
        if not created:
            continue
        _write_event(
            state,
            run_id=_event_run_id(signals),
            kind="autoresearch_experiment_drafted",
            payload={
                "schema_version": AUTORESEARCH_DRAFT_SCHEMA_VERSION,
                "experiment_id": experiment.experiment_id,
                "status": status,
                "signal_key": signal_key,
                "task_class": task_class,
                "gate": gate,
                "taxonomy_code": taxonomy_code,
                "provenance": provenance,
                "proposal_pointer": proposal_pointer,
                "default_change_allowed": False,
                "requires_operator_activation": status == "draft",
                "gate_authority": "unchanged",
            },
        )
        drafted.append(row)
    return drafted


def activate_autoresearch_experiment(
    *,
    state: Any,
    experiment_id: str,
    operator: str,
    approval_channel: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Operator transition from draft to runnable.

    Report-only rows remain report-only and cannot be promoted through this
    transition; they require a separate stability-proposal process.
    """
    _require_named_operator(operator=operator, approval_channel=approval_channel)
    activated_at = int(time.time()) if now is None else int(now)
    row = state.activate_autoresearch_experiment(
        experiment_id=experiment_id,
        operator=operator,
        approval_channel=approval_channel,
        activated_at=activated_at,
    )
    _write_event(
        state,
        run_id=_queue_event_run_id(row),
        kind="autoresearch_experiment_activation_recorded",
        payload={
            "schema_version": AUTORESEARCH_ACTIVATION_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "status": row["status"],
            "operator": operator,
            "approval_channel": approval_channel,
            "activated_at": row.get("activated_at"),
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_advanced": False,
        },
    )
    return row


def park_autoresearch_experiment(
    *,
    state: Any,
    experiment_id: str,
    operator: str,
    approval_channel: str,
    reason: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Operator transition from draft/runnable to parked without running it."""
    _require_named_operator(operator=operator, approval_channel=approval_channel)
    parked_at = int(time.time()) if now is None else int(now)
    row = state.park_autoresearch_experiment(
        experiment_id=experiment_id,
        parked_at=parked_at,
    )
    _write_event(
        state,
        run_id="autoresearch_experiments",
        kind="autoresearch_experiment_parked",
        payload={
            "schema_version": "supervisor-autoresearch-experiment-park/v1",
            "experiment_id": experiment_id,
            "status": row["status"],
            "operator": operator,
            "approval_channel": approval_channel,
            "reason": str(reason or "").strip(),
            "parked_at": parked_at,
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_advanced": False,
            "gate_authority": "unchanged",
        },
    )
    return row


def _require_named_operator(*, operator: str, approval_channel: str) -> None:
    normalized = str(operator or "").strip()
    if not normalized:
        raise ValueError("operator is required")
    if normalized.lower() in RESERVED_OPERATOR_IDENTITIES:
        raise ValueError("named human operator is required")
    if not str(approval_channel or "").strip():
        raise ValueError("approval_channel is required")


def run_runnable_autoresearch_experiments(
    *,
    state: Any,
    repo_root: str | Path,
    output_root: str | Path,
    run_id_prefix: str,
    max_runnable_per_week: int | None = None,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Run activated experiments through the existing durable evaluator lane."""
    timestamp = int(time.time()) if now is None else int(now)
    default_cap = AutoResearchGeneratorConfig().max_runnable_experiments_per_week
    cap = max(0, int(max_runnable_per_week if max_runnable_per_week is not None else default_cap))
    used = state.count_autoresearch_experiments_started_since(
        started_since=_week_start(timestamp),
    )
    remaining = max(0, cap - used)
    if remaining <= 0:
        return []

    output_root_path = Path(output_root).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for row in state.list_autoresearch_experiment_queue(status="runnable", limit=remaining):
        experiment_id = str(row["experiment_id"])
        run_id = f"{run_id_prefix}-{experiment_id}"
        run_dir = output_root_path / experiment_id
        run_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = run_dir / "fixture.json"
        fixture_path.write_text(
            json.dumps(
                {"experiment": row["experiment"], "attempts": [row["attempt"]]},
                sort_keys=True,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        state.mark_autoresearch_experiment_run_started(
            experiment_id=experiment_id,
            run_id=run_id,
            started_at=timestamp,
        )
        try:
            report = run_autoresearch_fixture(
                fixture_path=fixture_path,
                state=state,
                run_id=run_id,
                repo_root=repo_root,
                output_dir=run_dir,
                execution_mode="live",
            )
            report_path = run_dir / "report.json"
            report_hash = sha256(report_path.read_bytes()).hexdigest() if report_path.exists() else ""
            if not _autoresearch_report_accepted(report):
                terminal_row = state.complete_autoresearch_experiment_run(
                    experiment_id=experiment_id,
                    status="failed",
                    report_ref=report_path.as_posix(),
                    report_sha256=report_hash,
                    completed_at=timestamp,
                )
                failure_reason = _autoresearch_report_failure_reason(report)
                _write_event(
                    state,
                    run_id=run_id,
                    kind="autoresearch_experiment_auto_run_failed",
                    payload={
                        "schema_version": AUTORESEARCH_RUNNER_SCHEMA_VERSION,
                        "experiment_id": experiment_id,
                        "status": "failed",
                        "error": failure_reason,
                        "report_ref": report_path.as_posix(),
                        "report_sha256": report_hash,
                        "default_change_allowed": False,
                        "automatic_policy_mutation": False,
                        "gate_advanced": False,
                    },
                )
                results.append({
                    "status": "failed",
                    "queue_row": terminal_row,
                    "report": report,
                    "error": failure_reason,
                })
                continue
            terminal_row = state.complete_autoresearch_experiment_run(
                experiment_id=experiment_id,
                status="completed",
                report_ref=report_path.as_posix(),
                report_sha256=report_hash,
                completed_at=timestamp,
            )
            _write_event(
                state,
                run_id=run_id,
                kind="autoresearch_experiment_auto_run_completed",
                payload={
                    "schema_version": AUTORESEARCH_RUNNER_SCHEMA_VERSION,
                    "experiment_id": experiment_id,
                    "status": "completed",
                    "report_ref": report_path.as_posix(),
                    "report_sha256": report_hash,
                    "default_change_allowed": False,
                    "automatic_policy_mutation": False,
                    "gate_advanced": False,
                },
            )
            results.append({"status": "completed", "queue_row": terminal_row, "report": report})
        except Exception as exc:
            terminal_row = state.complete_autoresearch_experiment_run(
                experiment_id=experiment_id,
                status="failed",
                completed_at=timestamp,
            )
            _write_event(
                state,
                run_id=run_id,
                kind="autoresearch_experiment_auto_run_failed",
                payload={
                    "schema_version": AUTORESEARCH_RUNNER_SCHEMA_VERSION,
                    "experiment_id": experiment_id,
                    "status": "failed",
                    "error": str(exc),
                    "default_change_allowed": False,
                    "automatic_policy_mutation": False,
                    "gate_advanced": False,
                },
            )
            results.append({"status": "failed", "queue_row": terminal_row, "error": str(exc)})
    return results


def _autoresearch_report_accepted(report: Mapping[str, Any]) -> bool:
    records = report.get("records")
    if not isinstance(records, list) or not records:
        return False
    for record in records:
        if not isinstance(record, Mapping):
            return False
        if str(record.get("validation_status") or "") != "accepted":
            return False
        if str(record.get("metric_source") or "") != "evaluator_execution":
            return False
    return True


def _autoresearch_report_failure_reason(report: Mapping[str, Any]) -> str:
    records = report.get("records")
    if not isinstance(records, list) or not records:
        return "autoresearch_report_rejected:no_records"
    failures: list[str] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            failures.append(f"record_{index}:invalid")
            continue
        validation_status = str(record.get("validation_status") or "missing")
        metric_source = str(record.get("metric_source") or "missing")
        if validation_status != "accepted" or metric_source != "evaluator_execution":
            failures.append(
                f"record_{index}:validation_status={validation_status},metric_source={metric_source}"
            )
    return "autoresearch_report_rejected:" + ";".join(failures or ["unknown"])


def _read_signals(state: Any) -> list[_Signal]:
    signals = [_signal_from_event(event) for event in state.list_autoresearch_signal_events(limit=10_000)]
    lesson_signals: list[_Signal | None] = []
    for lesson in state.list_supervisor_lessons(limit=10_000):
        signal = _signal_from_lesson(lesson)
        observed_count = max(1, int(lesson.get("observed_count") or 1))
        lesson_signals.extend([signal] * observed_count)
    return [signal for signal in (*signals, *lesson_signals) if signal is not None]


def _signal_from_event(event: Mapping[str, Any]) -> _Signal | None:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    taxonomy = _taxonomy_from_payload(payload)
    signal_kind = "failure_taxonomy"
    if taxonomy is None:
        taxonomy = _reviewer_disagreement_taxonomy(payload)
        signal_kind = "reviewer_disagreement"
    if taxonomy is None:
        taxonomy = _probe_cohort_taxonomy(payload)
        signal_kind = "probe_cohort"
    if taxonomy is None:
        return None
    gate = _text(payload.get("gate") or _trace(payload).get("gate") or "unknown").replace("-", "_")
    task_class = _task_class(payload)
    return _Signal(
        signal_kind=signal_kind,
        task_class=task_class,
        gate=gate,
        taxonomy_code=_taxonomy_code(taxonomy),
        run_id=_text(event.get("run_id")),
        event_id=int(event["event_id"]) if event.get("event_id") is not None else None,
        lesson_id="",
        implicated_paths=_implicated_paths(payload),
    )


def _signal_from_lesson(lesson: Mapping[str, Any]) -> _Signal | None:
    taxonomy_code = _text(lesson.get("taxonomy_code"))
    if not taxonomy_code:
        return None
    return _Signal(
        signal_kind="lesson",
        task_class=_text(lesson.get("task_class")) or "general",
        gate=(_text(lesson.get("gate")) or "unknown").replace("-", "_"),
        taxonomy_code=taxonomy_code,
        run_id=_text(lesson.get("source_run_id")),
        event_id=None,
        lesson_id=_text(lesson.get("lesson_id")),
        implicated_paths=(),
    )


def _cluster_signals(signals: Iterable[_Signal]) -> dict[tuple[str, str, str], list[_Signal]]:
    clusters: dict[tuple[str, str, str], list[_Signal]] = {}
    for signal in signals:
        key = (signal.task_class, signal.gate, signal.taxonomy_code)
        clusters.setdefault(key, []).append(signal)
    return clusters


def _provenance_payload(
    *,
    signal_key: str,
    signals: list[_Signal],
    implicated_paths: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema_version": AUTORESEARCH_DRAFT_SCHEMA_VERSION,
        "signal_key": signal_key,
        "signal_count": len(signals),
        "signal_kinds": sorted({signal.signal_kind for signal in signals}),
        "event_ids": sorted(signal.event_id for signal in signals if signal.event_id is not None),
        "source_run_ids": sorted({signal.run_id for signal in signals if signal.run_id}),
        "lesson_ids": sorted({signal.lesson_id for signal in signals if signal.lesson_id}),
        "implicated_paths": list(implicated_paths),
    }


def _attempt_for_experiment(
    experiment: AutoresearchExperiment,
    *,
    mutable_paths: tuple[str, ...],
) -> AutoresearchAttempt:
    changed_files = tuple(_attempt_changed_files(mutable_paths))
    return AutoresearchAttempt(
        attempt_id=f"{experiment.experiment_id}-attempt-1",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="autoresearch-auto-runner",
        hypothesis=experiment.hypothesis,
        changed_files=changed_files,
        metric_trials=(),
        metric_before=0.0,
        metric_source="pending",
        status="pending",
    )


def _attempt_changed_files(mutable_paths: tuple[str, ...]) -> list[str]:
    changed: list[str] = []
    for path in mutable_paths:
        clean = path.rstrip("/")
        if not clean:
            continue
        if Path(clean).suffix:
            changed.append(clean)
        else:
            changed.append(f"{clean}/autoresearch-signal.md")
    return changed


def _signal_key(*, task_class: str, gate: str, taxonomy_code: str) -> str:
    return "signal-" + sha256_json({
        "task_class": task_class,
        "gate": gate,
        "taxonomy_code": taxonomy_code,
    })[:24]


def _cluster_implicated_paths(
    signals: list[_Signal],
    *,
    gate: str,
    taxonomy_code: str,
) -> tuple[str, ...]:
    paths: list[str] = []
    for signal in signals:
        for path in signal.implicated_paths:
            if path and path not in paths:
                paths.append(path)
    if not paths:
        paths.extend(_default_paths_for_signal(gate=gate, taxonomy_code=taxonomy_code))
    return tuple(paths[:3])


def _default_paths_for_signal(*, gate: str, taxonomy_code: str) -> tuple[str, ...]:
    if gate in {"prd_review", "issues_review", "tdd_review"} or taxonomy_code.startswith("FM-1.1"):
        return ("docs/dual-agent",)
    if gate in {"execution", "outcome_review"}:
        return ("supervisor/autoresearch", "tests")
    return ("docs/dual-agent",)


def _hypothesis(
    *,
    task_class: str,
    gate: str,
    taxonomy_code: str,
    implicated_paths: tuple[str, ...],
) -> str:
    surface = ", ".join(implicated_paths) if implicated_paths else "the implicated supervisor surface"
    return (
        f"Investigate whether a targeted change to {surface} reduces recurring "
        f"{taxonomy_code} failures at {gate} for {task_class} tasks."
    )


def _implicates_immutable_surface(paths: tuple[str, ...]) -> bool:
    return any(_matches_any(path, DEFAULT_IMMUTABLE_PATHS) for path in paths)


def _taxonomy_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = payload.get("failure_taxonomy")
    if isinstance(direct, Mapping):
        return direct
    trace = _trace(payload)
    taxonomy = trace.get("failure_taxonomy")
    return taxonomy if isinstance(taxonomy, Mapping) else None


def _reviewer_disagreement_taxonomy(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    panel = payload.get("independent_reviewer_panel_decision")
    if isinstance(panel, Mapping) and _text(panel.get("decision")).lower() not in {"", "accept"}:
        return {"code": "reviewer_disagreement"}
    results = payload.get("independent_reviewer_results")
    if isinstance(results, list) and any(
        isinstance(item, Mapping) and item.get("accepted") is False for item in results
    ):
        return {"code": "reviewer_disagreement"}
    cursor_review = payload.get("cursor_review")
    if isinstance(cursor_review, Mapping) and cursor_review.get("accepted") is False:
        return {"code": "reviewer_disagreement"}
    return None


def _probe_cohort_taxonomy(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    classification = _text(payload.get("classification")).upper()
    if classification in {"FLIPPING", "DRIFT-1"}:
        return {"code": "probe_cohort_flipping"}
    return None


def _trace(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = payload.get("trace_envelope")
    return trace if isinstance(trace, Mapping) else {}


def _taxonomy_code(taxonomy: Mapping[str, Any]) -> str:
    return (
        _text(taxonomy.get("mast_code"))
        or _text(taxonomy.get("code"))
        or _text(taxonomy.get("subcategory"))
        or "unknown_failure"
    )


def _task_class(payload: Mapping[str, Any]) -> str:
    for key in ("lesson_task_class", "task_class", "dynamic_workflow_task_class", "task_complexity"):
        value = _text(payload.get(key))
        if value:
            return value.replace("-", "_")
    return "general"


def _implicated_paths(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("implicated_paths") or payload.get("changed_files") or payload.get("affected_paths")
    if not isinstance(raw, (list, tuple)):
        return ()
    paths: list[str] = []
    for item in raw:
        normalized = _normalise_path(_text(item))
        if normalized and normalized not in paths:
            paths.append(normalized)
    return tuple(paths)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _matches(path: str, pattern: str) -> bool:
    clean_path = _normalise_path(path)
    clean_pattern = _normalise_path(pattern).rstrip("/")
    if not clean_path or not clean_pattern:
        return False
    return clean_path == clean_pattern or clean_path.startswith(clean_pattern + "/")


def _normalise_path(path: str) -> str:
    parts: list[str] = []
    for part in str(path or "").strip().replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _event_run_id(signals: list[_Signal]) -> str:
    return next((signal.run_id for signal in signals if signal.run_id), "autoresearch-generator")


def _queue_event_run_id(row: Mapping[str, Any]) -> str:
    provenance = row.get("provenance") if isinstance(row.get("provenance"), Mapping) else {}
    source_runs = provenance.get("source_run_ids") if isinstance(provenance, Mapping) else []
    if isinstance(source_runs, list) and source_runs:
        return _text(source_runs[0]) or "autoresearch-generator"
    return _text(row.get("last_run_id")) or "autoresearch-generator"


def _write_event(state: Any, *, run_id: str, kind: str, payload: dict[str, Any]) -> None:
    writer = getattr(state, "write_event", None)
    if writer is None:
        return
    writer(run_id=run_id, source="autoresearch", kind=kind, payload=payload)


def _week_start(now: int) -> int:
    return int(now) - (int(now) % (7 * 24 * 60 * 60))


def _text(value: Any) -> str:
    return str(value or "").strip()
