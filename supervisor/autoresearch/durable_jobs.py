"""Durable evaluator job adapter for report-only AutoResearch runs."""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from .evaluator import EvaluatorContractError, EvaluatorExecutionResult, run_evaluator_trials
from .schema import AutoresearchAttempt, AutoresearchExperiment, sha256_json


LEASE_TTL_S = 60
DEFAULT_REPLAY_CORPUS_EVALUATOR = Path("supervisor/autoresearch/evaluators/replay_corpus.py")


def resolve_evaluator_defaults(
    experiment: AutoresearchExperiment,
    *,
    repo_root: str | Path,
) -> AutoresearchExperiment:
    """Fill the repo-native replay-corpus evaluator when no evaluator is supplied."""
    if experiment.evaluator_ref or experiment.evaluator_hash:
        return experiment
    repo_root_path = Path(repo_root).expanduser().resolve()
    evaluator_path = repo_root_path / DEFAULT_REPLAY_CORPUS_EVALUATOR
    evaluator_hash = sha256(evaluator_path.read_bytes()).hexdigest()
    return replace(
        experiment,
        evaluator_ref=DEFAULT_REPLAY_CORPUS_EVALUATOR.as_posix(),
        evaluator_hash=evaluator_hash,
        metric_name="pass_rate",
    )


def run_durable_evaluator_trials(
    *,
    state: Any,
    run_id: str,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: str | Path,
    output_dir: str | Path,
) -> EvaluatorExecutionResult:
    """Run evaluator trials through the existing job ledger claim/recovery lane."""
    repo_root_path = Path(repo_root).expanduser().resolve()
    output_dir_path = Path(output_dir).expanduser().resolve()
    job_paths = _job_paths(output_dir=output_dir_path, attempt_id=attempt.attempt_id)
    job_id = _job_id(run_id=run_id, experiment=experiment, attempt=attempt)
    idempotency_token = _idempotency_token(run_id=run_id, experiment=experiment, attempt=attempt)
    request_payload = {
        "schema_version": "supervisor-autoresearch-evaluator-request/v1",
        "kind": "autoresearch_evaluator",
        "job_id": job_id,
        "run_id": run_id,
        "experiment": experiment.to_payload(),
        "attempt": attempt.to_payload(),
        "repo_root": repo_root_path.as_posix(),
        "output_dir": output_dir_path.as_posix(),
    }
    row, _created = state.reserve_dual_agent_workflow_job(
        job_id=job_id,
        run_id=run_id,
        task_id=f"autoresearch:{experiment.task_id}",
        cwd=repo_root_path.as_posix(),
        status="submitted",
        request_path=job_paths["request"].as_posix(),
        result_path=job_paths["result"].as_posix(),
        log_path=job_paths["log"].as_posix(),
        idempotency_token=idempotency_token,
        request_payload_json=json.dumps(request_payload, sort_keys=True),
        config_path=None,
    )

    if _row_value(row, "terminal_outcome_json"):
        terminal = _execution_from_terminal(row=row, result_path=job_paths["result"])
        if terminal is not None:
            return replace(terminal, job_id=job_id)

    claimed = state.claim_next_dual_agent_workflow_job_for_dispatch(
        dispatcher_id=f"autoresearch-dispatcher:{os.getpid()}",
        lease_ttl_s=LEASE_TTL_S,
        now=int(time.time()),
        job_id=job_id,
    )
    if claimed is None:
        refreshed = state.get_dual_agent_workflow_job(job_id=job_id)
        terminal = _execution_from_terminal(row=refreshed, result_path=job_paths["result"]) if refreshed else None
        if terminal is not None:
            return replace(terminal, job_id=job_id)
        raise EvaluatorContractError(f"autoresearch evaluator job is not claimable: {job_id}")
    row = claimed

    if str(_row_value(row, "recovery_point") or "reserved") == "reserved":
        job_paths["request"].parent.mkdir(parents=True, exist_ok=True)
        job_paths["request"].write_text(
            json.dumps({**request_payload, "job_id": job_id}, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        state.update_dual_agent_workflow_job(
            job_id=job_id,
            status="submitted",
            recovery_point="request_written",
        )
        _write_job_phase_event(
            state,
            run_id=run_id,
            job_id=job_id,
            attempt_id=attempt.attempt_id,
            status="submitted",
            recovery_point="request_written",
        )

    state.upsert_dual_agent_workflow_job(
        job_id=job_id,
        run_id=run_id,
        task_id=f"autoresearch:{experiment.task_id}",
        cwd=repo_root_path.as_posix(),
        status="running",
        pid=os.getpid(),
        request_path=job_paths["request"].as_posix(),
        result_path=job_paths["result"].as_posix(),
        log_path=job_paths["log"].as_posix(),
        idempotency_token=idempotency_token,
        recovery_point="spawned",
        request_payload_json=json.dumps(request_payload, sort_keys=True),
    )
    state.update_dual_agent_workflow_job(
        job_id=job_id,
        leased_by=f"autoresearch-worker:{os.getpid()}",
        lease_expires_at=int(time.time()) + LEASE_TTL_S,
        heartbeat_at=int(time.time()),
        clear_next_dispatch_at=True,
    )
    _write_job_phase_event(
        state,
        run_id=run_id,
        job_id=job_id,
        attempt_id=attempt.attempt_id,
        status="running",
        recovery_point="spawned",
        pid=os.getpid(),
    )

    try:
        execution = run_evaluator_trials(
            experiment=experiment,
            attempt=attempt,
            repo_root=repo_root_path,
            output_dir=output_dir_path,
        )
    except EvaluatorContractError as exc:
        _complete_failed_job(
            state=state,
            row=row,
            job_id=job_id,
            result_path=job_paths["result"],
            experiment=experiment,
            attempt=attempt,
            error=str(exc),
        )
        raise

    if _retryable_evaluator_crash(execution=execution, experiment=experiment):
        error = "; ".join(execution.execution_errors)
        state.upsert_dual_agent_workflow_job(
            job_id=job_id,
            run_id=run_id,
            task_id=f"autoresearch:{experiment.task_id}",
            cwd=repo_root_path.as_posix(),
            status="submitted",
            pid=None,
            request_path=job_paths["request"].as_posix(),
            result_path=job_paths["result"].as_posix(),
            log_path=job_paths["log"].as_posix(),
            idempotency_token=idempotency_token,
            recovery_point="request_written",
            request_payload_json=json.dumps(request_payload, sort_keys=True),
            error=error,
        )
        state.update_dual_agent_workflow_job(job_id=job_id, clear_lease=True)
        _write_job_phase_event(
            state,
            run_id=run_id,
            job_id=job_id,
            attempt_id=attempt.attempt_id,
            status="submitted",
            recovery_point="request_written",
            error=error,
        )
        raise EvaluatorContractError(f"retryable_evaluator_crash: {error}")

    execution = replace(execution, job_id=job_id)
    terminal_status = "failed" if execution.execution_errors else "completed"
    result_payload = _terminal_payload(
        status=terminal_status,
        job_id=job_id,
        experiment=experiment,
        attempt=attempt,
        execution=execution,
    )
    job_paths["result"].parent.mkdir(parents=True, exist_ok=True)
    job_paths["result"].write_text(
        json.dumps(result_payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    state.complete_dual_agent_workflow_job(
        job_id=job_id,
        status=terminal_status,
        terminal_status=terminal_status,
        terminal_outcome=result_payload,
        returncode=1 if execution.execution_errors else 0,
        error="; ".join(execution.execution_errors),
    )
    return execution


def _complete_failed_job(
    *,
    state: Any,
    row: Any,
    job_id: str,
    result_path: Path,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    error: str,
) -> None:
    payload = {
        "schema_version": "supervisor-autoresearch-evaluator-terminal/v1",
        "status": "failed",
        "job_id": job_id,
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
        "execution_errors": [error],
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    state.complete_dual_agent_workflow_job(
        job_id=job_id,
        status="failed",
        terminal_status="failed",
        terminal_outcome=payload,
        returncode=1,
        error=error,
    )
    _write_job_phase_event(
        state,
        run_id=str(_row_value(row, "run_id") or ""),
        job_id=job_id,
        attempt_id=attempt.attempt_id,
        status="failed",
        recovery_point="terminal",
        error=error,
    )


def _terminal_payload(
    *,
    status: str,
    job_id: str,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    execution: EvaluatorExecutionResult,
) -> dict[str, Any]:
    return {
        "schema_version": "supervisor-autoresearch-evaluator-terminal/v1",
        "status": status,
        "job_id": job_id,
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
        "execution": _execution_payload(execution),
    }


def _execution_payload(execution: EvaluatorExecutionResult) -> dict[str, Any]:
    return {
        "metric_trials": list(execution.metric_trials),
        "metric_before": execution.metric_before,
        "metric_after": execution.metric_after,
        "metric_delta": execution.metric_delta,
        "metric_source": execution.metric_source,
        "evaluator_run_ref": execution.evaluator_run_ref,
        "evaluator_run_hash": execution.evaluator_run_hash,
        "attempt_worktree_ref": execution.attempt_worktree_ref,
        "evidence_refs": list(execution.evidence_refs),
        "execution_errors": list(execution.execution_errors),
        "cost_usd": execution.cost_usd,
        "wall_clock_s": execution.wall_clock_s,
        "evaluator_quality": execution.evaluator_quality,
        "job_id": execution.job_id,
        "resumed_from_trial_count": execution.resumed_from_trial_count,
    }


def _execution_from_terminal(*, row: Any, result_path: Path) -> EvaluatorExecutionResult | None:
    payload: dict[str, Any] | None = None
    if result_path.exists():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            payload = None
    if payload is None and row is not None:
        raw = _row_value(row, "terminal_outcome_json")
        if isinstance(raw, dict):
            payload = raw
        elif raw:
            try:
                loaded = json.loads(str(raw))
                payload = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                payload = None
    if not payload:
        return None
    execution = payload.get("execution")
    if not isinstance(execution, dict):
        errors = payload.get("execution_errors")
        return EvaluatorExecutionResult(
            metric_trials=(),
            metric_source="evaluator_execution",
            evaluator_run_ref="",
            evaluator_run_hash="",
            attempt_worktree_ref="",
            evidence_refs=(),
            execution_errors=tuple(str(error) for error in errors or []),
            cost_usd=0.0,
            wall_clock_s=0.0,
            evaluator_quality={},
            job_id=str(payload.get("job_id") or ""),
            metric_before=None,
            metric_after=None,
            metric_delta=None,
        )
    return EvaluatorExecutionResult(
        metric_trials=tuple(float(value) for value in execution.get("metric_trials") or ()),
        metric_before=_optional_float(execution.get("metric_before")),
        metric_after=_optional_float(execution.get("metric_after")),
        metric_delta=_optional_float(execution.get("metric_delta")),
        metric_source=str(execution.get("metric_source") or "evaluator_execution"),
        evaluator_run_ref=str(execution.get("evaluator_run_ref") or ""),
        evaluator_run_hash=str(execution.get("evaluator_run_hash") or ""),
        attempt_worktree_ref=str(execution.get("attempt_worktree_ref") or ""),
        evidence_refs=tuple(str(ref) for ref in execution.get("evidence_refs") or ()),
        execution_errors=tuple(str(error) for error in execution.get("execution_errors") or ()),
        cost_usd=float(execution.get("cost_usd") or 0.0),
        wall_clock_s=float(execution.get("wall_clock_s") or 0.0),
        evaluator_quality=dict(execution.get("evaluator_quality") or {}),
        job_id=str(execution.get("job_id") or payload.get("job_id") or ""),
        resumed_from_trial_count=int(execution.get("resumed_from_trial_count") or 0),
    )


def _retryable_evaluator_crash(
    *,
    execution: EvaluatorExecutionResult,
    experiment: AutoresearchExperiment,
) -> bool:
    if len(execution.metric_trials) >= max(0, experiment.k_trials):
        return False
    errors = list(execution.execution_errors)
    if not errors:
        return False
    joined = "\n".join(errors).lower()
    if "budget_exceeded" in joined or "timeout" in joined or "outside mutable surface" in joined:
        return False
    return any(error.startswith("evaluator exited") or "timeout" in error.lower() for error in errors)


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return round(float(value), 6)


def _job_id(*, run_id: str, experiment: AutoresearchExperiment, attempt: AutoresearchAttempt) -> str:
    digest = sha256_json({
        "run_id": run_id,
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
    })[:24]
    return f"autoresearch-{digest}"


def _idempotency_token(*, run_id: str, experiment: AutoresearchExperiment, attempt: AutoresearchAttempt) -> str:
    return f"autoresearch:{run_id}:{experiment.experiment_id}:{attempt.attempt_id}"


def _job_paths(*, output_dir: Path, attempt_id: str) -> dict[str, Path]:
    job_dir = output_dir / "evaluator-jobs" / attempt_id
    return {
        "request": job_dir / "request.json",
        "result": job_dir / "result.json",
        "log": job_dir / "worker.log",
    }


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[key] if key in row.keys() else None


def _write_job_phase_event(
    state: Any,
    *,
    run_id: str,
    job_id: str,
    attempt_id: str,
    status: str,
    recovery_point: str,
    pid: int | None = None,
    error: str | None = None,
) -> None:
    writer = getattr(state, "write_event", None)
    if writer is None or not run_id:
        return
    writer(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_evaluator_job_phase",
        payload={
            "schema_version": "supervisor-autoresearch/v1",
            "job_id": job_id,
            "attempt_id": attempt_id,
            "status": status,
            "recovery_point": recovery_point,
            "pid": pid,
            "error": error,
        },
    )
