"""Executable evaluator runner for live, report-only AutoResearch attempts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from .schema import AutoresearchAttempt, AutoresearchExperiment, stable_json_dumps


@dataclass(frozen=True)
class EvaluatorExecutionResult:
    """Execution-derived result of a single attempt's evaluator run.

    ``metric_before`` is the measured empty-floor metric from the pre-flight
    stripped-overlay evaluator pass (``None`` when the attempt has no policy
    overlay candidate, when the pre-flight failed, or when the field was not
    persisted by an older durable payload). ``metric_after`` is the median of
    ``metric_trials`` once any trials have been recorded, and ``metric_delta``
    is ``metric_after - metric_before`` when both are present.
    """

    metric_trials: tuple[float, ...]
    metric_source: str
    evaluator_run_ref: str
    evaluator_run_hash: str
    attempt_worktree_ref: str
    evidence_refs: tuple[str, ...]
    execution_errors: tuple[str, ...]
    cost_usd: float
    wall_clock_s: float
    evaluator_quality: dict[str, Any] = field(default_factory=dict)
    job_id: str = ""
    resumed_from_trial_count: int = 0
    metric_before: float | None = None
    metric_after: float | None = None
    metric_delta: float | None = None


class EvaluatorContractError(RuntimeError):
    """Raised before execution when the evaluator contract is not trustworthy."""


def run_evaluator_trials(
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: str | Path,
    output_dir: str | Path,
) -> EvaluatorExecutionResult:
    """Run the experiment evaluator in an isolated attempt worktree.

    When the attempt's ``policy_candidate_changes`` include a
    ``policy-overlay.yaml`` (or ``.yml``) candidate and the durable progress
    file does not already carry a measured empty floor, an additional
    pre-flight evaluator pass is executed against a copy of the worktree with
    those overlay candidates stripped to empty content. The pre-flight pass
    runs with ``AUTORESEARCH_EMPTY_FLOOR=1`` in its environment, and its
    metric becomes ``metric_before`` on the returned result. ``metric_after``
    is the median of the candidate trial metrics and ``metric_delta`` is the
    difference. If the pre-flight pass fails, the failure is recorded as an
    execution error (prefixed ``empty_floor:``), ``metric_before`` stays
    ``None``, and the candidate trials are still attempted; the pending seed
    is never reused as execution evidence.
    """
    repo_root_path = Path(repo_root).expanduser().resolve()
    output_dir_path = Path(output_dir).expanduser().resolve()
    evaluator_rel = _normalise_path(experiment.evaluator_ref, repo_root=repo_root_path)
    evaluator_path = repo_root_path / evaluator_rel
    _verify_evaluator_hash(evaluator_path, experiment.evaluator_hash, evaluator_rel=evaluator_rel)

    started = time.monotonic()
    artifact_dir = output_dir_path / "evaluator-runs"
    progress_path = artifact_dir / f"{attempt.attempt_id}.progress.json"
    progress = _load_progress(
        progress_path,
        experiment=experiment,
        attempt=attempt,
        evaluator_rel=evaluator_rel,
    )
    trial_records: list[dict[str, Any]] = list(progress["trial_records"])
    metric_trials: list[float] = [float(record["metric_value"]) for record in trial_records]
    execution_errors: list[str] = []
    cost_usd = float(progress["cost_usd"])
    resumed_from_trial_count = len(metric_trials)
    evaluator_quality: dict[str, Any] = {}
    empty_floor_record = dict(progress.get("empty_floor_record") or {})
    metric_before = _optional_float(progress.get("metric_before"))
    if metric_before is None:
        metric_before = _optional_float(progress.get("empty_floor_metric"))
    metric_after = _candidate_metric_after(metric_trials)
    metric_delta = _metric_delta(metric_before, metric_after)

    with tempfile.TemporaryDirectory(prefix="autoresearch-attempt-") as temp_dir:
        temp_path = Path(temp_dir)
        worktree = temp_path / "worktree"
        executable_evaluator = _copy_evaluator_for_execution(
            evaluator_path=evaluator_path,
            temp_path=temp_path,
        )
        _materialize_attempt_worktree(
            repo_root=repo_root_path,
            worktree=worktree,
            mutable_paths=experiment.mutable_paths,
            changed_files=attempt.changed_files,
        )
        attempt_json = temp_path / "attempt.json"
        attempt_json.write_text(
            json.dumps(attempt.to_payload(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        before = _snapshot_files(worktree)
        source_before_bytes = _snapshot_file_bytes(
            repo_root_path,
            exclude_dirs=_SOURCE_SNAPSHOT_EXCLUDED_DIRS,
            exclude_roots=(output_dir_path,),
        )
        source_before = _hash_bytes_snapshot(source_before_bytes)
        source_changed_paths: list[str] = []

        try:
            empty_floor_refs = _empty_floor_candidate_refs(attempt, repo_root=repo_root_path)
            if empty_floor_refs and metric_before is None:
                try:
                    empty_floor_record = _run_empty_floor_evaluator(
                        experiment=experiment,
                        repo_root=repo_root_path,
                        worktree=worktree,
                        temp_path=temp_path,
                        executable_evaluator=executable_evaluator,
                        progress_path=progress_path,
                        attempt_json=attempt_json,
                        candidate_refs=tuple(empty_floor_refs),
                    )
                except (EvaluatorContractError, subprocess.TimeoutExpired) as exc:
                    message = str(exc)
                    if isinstance(exc, subprocess.TimeoutExpired):
                        message = f"timeout: {message}"
                    execution_errors.append(f"empty_floor: {message}")
                else:
                    metric_before = _optional_float(empty_floor_record.get("empty_floor_metric"))
                    cost_usd += float(empty_floor_record.get("cost_usd") or 0.0)
                    metric_delta = _metric_delta(metric_before, metric_after)
                    _write_progress(
                        progress_path,
                        experiment=experiment,
                        attempt=attempt,
                        evaluator_rel=evaluator_rel,
                        trial_records=trial_records,
                        cost_usd=cost_usd,
                        empty_floor_record=empty_floor_record,
                        metric_before=metric_before,
                        metric_after=metric_after,
                        metric_delta=metric_delta,
                    )
            for trial_index in range(len(metric_trials), max(0, experiment.k_trials)):
                trial_started = time.monotonic()
                command = [
                    *_evaluator_command(executable_evaluator),
                    "--attempt-worktree",
                    str(worktree),
                    "--trial-index",
                    str(trial_index),
                    "--metric-name",
                    experiment.metric_name,
                    "--attempt-json",
                    str(attempt_json),
                ]
                completed = subprocess.run(
                    command,
                    cwd=worktree,
                    input="",
                    capture_output=True,
                    text=True,
                    timeout=max(0.001, float(experiment.timeout_s)),
                    check=False,
                    env=_evaluator_environment(
                        base_env=os.environ,
                        repo_root=repo_root_path,
                        worktree=worktree,
                        temp_path=temp_path,
                        progress_path=progress_path,
                        trial_index=trial_index,
                        metric_name=experiment.metric_name,
                        attempt_json=attempt_json,
                    ),
                )
                if completed.returncode != 0:
                    raise EvaluatorContractError(
                        f"evaluator exited {completed.returncode}: "
                        f"{completed.stderr.strip() or completed.stdout.strip()}"
                    )
                trial_payload = _parse_trial_payload(completed.stdout)
                metric_value = round(_metric_value(trial_payload, metric_name=experiment.metric_name), 12)
                metric_trials.append(metric_value)
                metric_after = _candidate_metric_after(metric_trials)
                metric_delta = _metric_delta(metric_before, metric_after)
                cost_usd += float(trial_payload.get("cost_usd") or 0.0)
                trial_records.append({
                    "trial_index": trial_index,
                    "metric_value": metric_value,
                    "stdout_sha256": sha256(completed.stdout.encode("utf-8")).hexdigest(),
                    "stderr_sha256": sha256(completed.stderr.encode("utf-8")).hexdigest(),
                    "duration_s": round(time.monotonic() - trial_started, 6),
                })
                _write_progress(
                    progress_path,
                    experiment=experiment,
                    attempt=attempt,
                    evaluator_rel=evaluator_rel,
                    trial_records=trial_records,
                    cost_usd=cost_usd,
                    empty_floor_record=empty_floor_record,
                    metric_before=metric_before,
                    metric_after=metric_after,
                    metric_delta=metric_delta,
                )
                if experiment.budget_usd > 0 and cost_usd > experiment.budget_usd:
                    execution_errors.append(
                        "budget_exceeded: "
                        f"cost_usd={round(cost_usd, 6)} budget_usd={experiment.budget_usd}"
                    )
                    break
        except (EvaluatorContractError, subprocess.TimeoutExpired) as exc:
            message = str(exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                message = f"timeout: {message}"
            execution_errors.append(message)
        finally:
            source_after = _hash_bytes_snapshot(_snapshot_file_bytes(
                repo_root_path,
                exclude_dirs=_SOURCE_SNAPSHOT_EXCLUDED_DIRS,
                exclude_roots=(output_dir_path,),
            ))
            all_source_changed_paths = _changed_paths(source_before, source_after)
            source_changed_paths = [
                path for path in all_source_changed_paths
                if not _is_ignorable_source_checkout_mutation(path)
            ]
            if all_source_changed_paths:
                _restore_snapshot(
                    root=repo_root_path,
                    before=source_before_bytes,
                    changed_paths=all_source_changed_paths,
                )

        after = _snapshot_files(worktree)
        changed_paths = _changed_paths(before, after)
        mutable_patterns = tuple(
            _normalise_path(path, repo_root=repo_root_path)
            for path in experiment.mutable_paths
            if str(path).strip()
        )
        outside_mutable = [
            path for path in changed_paths
            if not _matches_any(path, mutable_patterns)
        ]
        if outside_mutable:
            execution_errors.append(
                "evaluator modified paths outside mutable surface: " + ", ".join(outside_mutable)
            )
        if source_changed_paths:
            execution_errors.append(
                "evaluator modified source checkout outside mutable surface: "
                + ", ".join(source_changed_paths)
            )
        if execution_errors and not metric_trials:
            raise EvaluatorContractError("; ".join(execution_errors))
        if not execution_errors:
            quality_attempt = attempt
            if empty_floor_record and metric_before is not None:
                quality_attempt = replace(
                    attempt,
                    metric_before=metric_before,
                    metric_after=metric_after,
                    metric_delta=metric_delta,
                    metric_source="evaluator_execution",
                )
            evaluator_quality = _run_evaluator_quality_controls(
                experiment=experiment,
                attempt=quality_attempt,
                repo_root=repo_root_path,
                output_dir=output_dir_path,
                worktree=worktree,
                temp_path=temp_path,
                executable_evaluator=executable_evaluator,
                progress_path=progress_path,
            )

    run_artifact = {
        "schema_version": "supervisor-autoresearch-evaluator-run/v1",
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
        "evaluator_ref": evaluator_rel,
        "evaluator_hash": experiment.evaluator_hash,
        "metric_name": experiment.metric_name,
        "k_trials": experiment.k_trials,
        "metric_trials": metric_trials,
        "empty_floor_metric": metric_before,
        "empty_floor_record": empty_floor_record,
        "metric_before": metric_before,
        "metric_after": metric_after,
        "metric_delta": metric_delta,
        "trial_records": trial_records,
        "execution_errors": execution_errors,
        "budget_usd": experiment.budget_usd,
        "timeout_s": experiment.timeout_s,
        "cost_usd": round(cost_usd, 6),
        "wall_clock_s": round(time.monotonic() - started, 6),
        "evaluator_quality": evaluator_quality,
        "attempt_worktree_ref": "isolated:temporary",
        "progress_ref": progress_path.as_posix(),
        "resumed_from_trial_count": resumed_from_trial_count,
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{attempt.attempt_id}.json"
    artifact_path.write_text(
        json.dumps(run_artifact, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    artifact_hash = sha256(artifact_path.read_bytes()).hexdigest()

    return EvaluatorExecutionResult(
        metric_trials=tuple(metric_trials),
        metric_source="evaluator_execution",
        evaluator_run_ref=artifact_path.as_posix(),
        evaluator_run_hash=artifact_hash,
        attempt_worktree_ref="isolated:temporary",
        evidence_refs=(f"evaluator_run:{artifact_path.as_posix()}",),
        execution_errors=tuple(execution_errors),
        cost_usd=round(cost_usd, 6),
        wall_clock_s=round(time.monotonic() - started, 6),
        evaluator_quality=evaluator_quality,
        resumed_from_trial_count=resumed_from_trial_count,
        metric_before=metric_before,
        metric_after=metric_after,
        metric_delta=metric_delta,
    )


def _verify_evaluator_hash(path: Path, expected_hash: str, *, evaluator_rel: str) -> None:
    if not evaluator_rel:
        raise EvaluatorContractError("evaluator_ref is required for live execution")
    if not expected_hash:
        raise EvaluatorContractError(f"evaluator_hash is required for {evaluator_rel}")
    if not path.exists() or not path.is_file():
        raise EvaluatorContractError(f"evaluator missing for {evaluator_rel}")
    observed = sha256(path.read_bytes()).hexdigest()
    if observed != expected_hash:
        raise EvaluatorContractError(f"evaluator hash mismatch for {evaluator_rel}")


def _evaluator_command(path: Path) -> list[str]:
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    if os.access(path, os.X_OK):
        return [str(path)]
    raise EvaluatorContractError(f"evaluator is not executable: {path}")


def _copy_evaluator_for_execution(*, evaluator_path: Path, temp_path: Path) -> Path:
    execution_dir = temp_path / "evaluator"
    execution_dir.mkdir(parents=True, exist_ok=True)
    execution_path = execution_dir / evaluator_path.name
    shutil.copy2(evaluator_path, execution_path)
    return execution_path


_ENV_ALLOWLIST = {
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
}

_SOURCE_SNAPSHOT_EXCLUDED_DIRS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}

_IGNORABLE_SOURCE_MUTATION_PREFIXES = (
    ".git/objects/",
)


def _evaluator_environment(
    *,
    base_env: Mapping[str, str],
    repo_root: Path,
    worktree: Path,
    temp_path: Path,
    progress_path: Path,
    trial_index: int,
    metric_name: str,
    attempt_json: Path,
    control_kind: str = "",
) -> dict[str, str]:
    # Evaluator subprocesses observe a fixed set of AUTORESEARCH_* variables:
    # ATTEMPT_WORKTREE, SOURCE_ROOT, PROGRESS_PATH, TRIAL_INDEX, METRIC_NAME,
    # ATTEMPT_JSON, optional CONTROL_KIND (set for quality-control passes), and
    # optional EMPTY_FLOOR=1 (set only for the stripped-overlay pre-flight pass
    # added by the empty-floor evaluator path; custom evaluators may inspect it
    # to short-circuit when an overlay candidate is empty).
    env = {
        key: value
        for key, value in base_env.items()
        if key in _ENV_ALLOWLIST or key.startswith("LC_")
    }
    home = temp_path / "home"
    tmp = temp_path / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env.update({
        "AUTORESEARCH_ATTEMPT_WORKTREE": str(worktree),
        "AUTORESEARCH_SOURCE_ROOT": str(repo_root),
        "AUTORESEARCH_PROGRESS_PATH": str(progress_path),
        "AUTORESEARCH_TRIAL_INDEX": str(trial_index),
        "AUTORESEARCH_METRIC_NAME": metric_name,
        "AUTORESEARCH_ATTEMPT_JSON": str(attempt_json),
        "HOME": str(home),
        "OLDPWD": str(worktree),
        "PWD": str(worktree),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "TMPDIR": str(tmp),
        "PYTHONPATH": str(repo_root),
    })
    if control_kind:
        env["AUTORESEARCH_CONTROL_KIND"] = control_kind
    return env


def _empty_floor_candidate_refs(attempt: AutoresearchAttempt, *, repo_root: Path) -> list[str]:
    refs = [
        ref for ref in _policy_candidate_refs(attempt, repo_root=repo_root)
        if Path(ref).name in {"policy-overlay.yaml", "policy-overlay.yml"}
    ]
    return list(dict.fromkeys(refs))


def _run_empty_floor_evaluator(
    *,
    experiment: AutoresearchExperiment,
    repo_root: Path,
    worktree: Path,
    temp_path: Path,
    executable_evaluator: Path,
    progress_path: Path,
    attempt_json: Path,
    candidate_refs: tuple[str, ...],
) -> dict[str, Any]:
    started = time.monotonic()
    empty_worktree = temp_path / "empty-floor-worktree"
    if empty_worktree.exists():
        shutil.rmtree(empty_worktree)
    shutil.copytree(worktree, empty_worktree, dirs_exist_ok=True)
    for candidate_ref in candidate_refs:
        candidate_path = empty_worktree / _normalise_path(candidate_ref, repo_root=repo_root)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text("", encoding="utf-8")

    command = [
        *_evaluator_command(executable_evaluator),
        "--attempt-worktree",
        str(empty_worktree),
        "--trial-index",
        "0",
        "--metric-name",
        experiment.metric_name,
        "--attempt-json",
        str(attempt_json),
    ]
    completed = subprocess.run(
        command,
        cwd=empty_worktree,
        input="",
        capture_output=True,
        text=True,
        timeout=max(0.001, float(experiment.timeout_s)),
        check=False,
        env={
            **_evaluator_environment(
                base_env=os.environ,
                repo_root=repo_root,
                worktree=empty_worktree,
                temp_path=temp_path,
                progress_path=progress_path,
                trial_index=0,
                metric_name=experiment.metric_name,
                attempt_json=attempt_json,
            ),
            "AUTORESEARCH_EMPTY_FLOOR": "1",
        },
    )
    if completed.returncode != 0:
        raise EvaluatorContractError(
            f"empty-floor evaluator exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    trial_payload = _parse_trial_payload(completed.stdout)
    metric_value = round(_metric_value(trial_payload, metric_name=experiment.metric_name), 12)
    return {
        "schema_version": "supervisor-autoresearch-empty-floor-run/v1",
        "metric_source": "evaluator_execution",
        "empty_floor_metric": metric_value,
        "candidate_refs": list(candidate_refs),
        "stdout_sha256": sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "duration_s": round(time.monotonic() - started, 6),
        "cost_usd": round(float(trial_payload.get("cost_usd") or 0.0), 6),
    }


def _candidate_metric_after(metric_trials: list[float]) -> float | None:
    if not metric_trials:
        return None
    return round(float(median(metric_trials)), 6)


def _metric_delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(float(after) - float(before), 6)


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return round(float(value), 6)


def _run_evaluator_quality_controls(
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: Path,
    output_dir: Path,
    worktree: Path,
    temp_path: Path,
    executable_evaluator: Path,
    progress_path: Path,
) -> dict[str, Any]:
    policy_candidate_refs = _policy_candidate_refs(attempt, repo_root=repo_root)
    if not policy_candidate_refs:
        return {}

    started = time.monotonic()
    quality_dir = output_dir / "evaluator-quality" / attempt.attempt_id
    quality_dir.mkdir(parents=True, exist_ok=True)
    controls: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for control_kind in ("noop", "harmful", "known_good"):
        try:
            controls[control_kind] = _run_single_quality_control(
                control_kind=control_kind,
                experiment=experiment,
                attempt=attempt,
                repo_root=repo_root,
                worktree=worktree,
                temp_path=temp_path,
                executable_evaluator=executable_evaluator,
                progress_path=progress_path,
                quality_dir=quality_dir,
                primary_candidate_ref=policy_candidate_refs[0],
            )
        except (EvaluatorContractError, subprocess.TimeoutExpired) as exc:
            message = f"{control_kind} control failed: {exc}"
            errors.append(message)
            controls[control_kind] = _failed_quality_control(
                control_kind=control_kind,
                quality_dir=quality_dir,
                primary_candidate_ref=policy_candidate_refs[0],
                error=message,
            )

    determinism = _run_determinism_quality_check(
        experiment=experiment,
        attempt=attempt,
        repo_root=repo_root,
        worktree=worktree,
        temp_path=temp_path,
        executable_evaluator=executable_evaluator,
        progress_path=progress_path,
        quality_dir=quality_dir,
        primary_candidate_ref=policy_candidate_refs[0],
    )
    if determinism.get("verdict") != "passed":
        errors.append("determinism control failed")
    evaluated_path_derivation = _derive_candidate_affects_evaluated_path(
        experiment=experiment,
        attempt=attempt,
        repo_root=repo_root,
        controls=controls,
    )

    manifest = {
        "schema_version": "supervisor-autoresearch-evaluator-quality/v1",
        "source": "supervisor_control_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
        "candidate_affects_evaluated_path": evaluated_path_derivation["candidate_affects_evaluated_path"],
        "evaluated_path_derivation": evaluated_path_derivation,
        "policy_candidate_refs": policy_candidate_refs,
        "determinism": determinism,
        "controls": controls,
        "control_refs": [kind for kind in ("noop", "harmful", "known_good") if kind in controls],
        "validation_errors": errors,
        "wall_clock_s": round(time.monotonic() - started, 6),
    }
    manifest["verdict"] = "accepted" if not errors else "rejected"
    manifest_path = quality_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest["quality_manifest_ref"] = manifest_path.as_posix()
    manifest["quality_manifest_hash"] = sha256(manifest_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def _derive_candidate_affects_evaluated_path(
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: Path,
    controls: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    changed_files = tuple(
        _normalise_path(path, repo_root=repo_root)
        for path in attempt.changed_files
        if str(path).strip()
    )
    evaluated_patterns = tuple(
        _normalise_path(path, repo_root=repo_root)
        for path in experiment.mutable_paths
        if str(path).strip()
    )
    matching_changed_files = sorted(
        path for path in changed_files if _matches_any(path, evaluated_patterns)
    )
    control_deltas = {
        kind: control.get("metric_delta")
        for kind, control in sorted(controls.items())
        if isinstance(control, Mapping)
    }
    candidate_metric_delta = _candidate_metric_delta(attempt)
    if matching_changed_files:
        reason = "changed_evaluated_path"
        affects = True
    elif candidate_metric_delta is not None and abs(candidate_metric_delta) > 0.0:
        reason = "candidate_metric_delta"
        affects = True
    else:
        reason = "no_evaluated_path_delta"
        affects = False
    return {
        "schema_version": "supervisor-autoresearch-evaluated-path-derivation/v1",
        "candidate_affects_evaluated_path": affects,
        "reason": reason,
        "changed_files": list(changed_files),
        "evaluated_path_patterns": list(evaluated_patterns),
        "matching_changed_files": matching_changed_files,
        "candidate_metric_before": attempt.metric_before,
        "candidate_metric_after": attempt.metric_after,
        "candidate_metric_delta": candidate_metric_delta,
        "control_metric_deltas": control_deltas,
    }


def _candidate_metric_delta(attempt: AutoresearchAttempt) -> float | None:
    if attempt.metric_delta is not None:
        return round(float(attempt.metric_delta), 6)
    if attempt.metric_before is None or attempt.metric_after is None:
        return None
    return round(float(attempt.metric_after) - float(attempt.metric_before), 6)


def _run_single_quality_control(
    *,
    control_kind: str,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: Path,
    worktree: Path,
    temp_path: Path,
    executable_evaluator: Path,
    progress_path: Path,
    quality_dir: Path,
    primary_candidate_ref: str,
) -> dict[str, Any]:
    control_candidate = _write_control_candidate(
        quality_dir=quality_dir,
        attempt=attempt,
        control_kind=control_kind,
        primary_candidate_ref=primary_candidate_ref,
    )
    attempt_json = _write_control_attempt_json(
        quality_dir=quality_dir,
        attempt=attempt,
        control_kind=control_kind,
        control_candidate=control_candidate,
    )
    trial_payload, stdout_hash, stderr_hash, duration_s = _execute_evaluator_control(
        control_kind=control_kind,
        experiment=experiment,
        repo_root=repo_root,
        worktree=worktree,
        temp_path=temp_path,
        executable_evaluator=executable_evaluator,
        progress_path=progress_path,
        attempt_json=attempt_json,
    )
    metric_after = round(_metric_value(trial_payload, metric_name=experiment.metric_name), 12)
    metric_before = _control_metric_before(trial_payload, attempt=attempt)
    metric_delta = _control_metric_delta(trial_payload, before=metric_before, after=metric_after)
    record = {
        "schema_version": "supervisor-autoresearch-evaluator-quality-control/v1",
        "source": "supervisor_control_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "control_kind": control_kind,
        "candidate_ref": control_candidate["candidate_ref"],
        "candidate_hash": control_candidate["candidate_hash"],
        "metric_source": "evaluator_execution",
        "metric_before": metric_before,
        "metric_after": metric_after,
        "metric_delta": metric_delta,
        "verdict": _control_verdict(control_kind, metric_delta),
        "stdout_sha256": stdout_hash,
        "stderr_sha256": stderr_hash,
        "duration_s": duration_s,
    }
    record_path = quality_dir / f"{control_kind}.json"
    record_path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    record["control_run_ref"] = record_path.as_posix()
    record["control_run_hash"] = sha256(record_path.read_bytes()).hexdigest()
    record["evaluator_run_ref"] = record["control_run_ref"]
    record["evaluator_run_hash"] = record["control_run_hash"]
    record_path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return record


def _run_determinism_quality_check(
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: Path,
    worktree: Path,
    temp_path: Path,
    executable_evaluator: Path,
    progress_path: Path,
    quality_dir: Path,
    primary_candidate_ref: str,
) -> dict[str, Any]:
    control_candidate = _write_control_candidate(
        quality_dir=quality_dir,
        attempt=attempt,
        control_kind="determinism",
        primary_candidate_ref=primary_candidate_ref,
    )
    attempt_json = _write_control_attempt_json(
        quality_dir=quality_dir,
        attempt=attempt,
        control_kind="determinism",
        control_candidate=control_candidate,
    )
    output_hashes: list[str] = []
    records: list[dict[str, Any]] = []
    for index in range(2):
        try:
            trial_payload, stdout_hash, stderr_hash, duration_s = _execute_evaluator_control(
                control_kind="determinism",
                experiment=experiment,
                repo_root=repo_root,
                worktree=worktree,
                temp_path=temp_path,
                executable_evaluator=executable_evaluator,
                progress_path=progress_path,
                attempt_json=attempt_json,
            )
        except (EvaluatorContractError, subprocess.TimeoutExpired) as exc:
            records.append({"run_index": index, "error": str(exc)})
            output_hashes.append(f"error:{index}:{sha256(str(exc).encode('utf-8')).hexdigest()}")
            continue
        normalized_hash = sha256(stable_json_dumps(trial_payload).encode("utf-8")).hexdigest()
        output_hashes.append(normalized_hash)
        records.append({
            "run_index": index,
            "output_hash": normalized_hash,
            "stdout_sha256": stdout_hash,
            "stderr_sha256": stderr_hash,
            "duration_s": duration_s,
        })
    payload = {
        "schema_version": "supervisor-autoresearch-evaluator-determinism/v1",
        "source": "repeated_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "candidate_ref": control_candidate["candidate_ref"],
        "candidate_hash": control_candidate["candidate_hash"],
        "output_hashes": output_hashes,
        "records": records,
        "verified": len(output_hashes) >= 2 and len(set(output_hashes)) == 1,
    }
    payload["verdict"] = "passed" if payload["verified"] else "failed"
    path = quality_dir / "determinism.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    payload["control_run_ref"] = path.as_posix()
    payload["control_run_hash"] = sha256(path.read_bytes()).hexdigest()
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def _execute_evaluator_control(
    *,
    control_kind: str,
    experiment: AutoresearchExperiment,
    repo_root: Path,
    worktree: Path,
    temp_path: Path,
    executable_evaluator: Path,
    progress_path: Path,
    attempt_json: Path,
) -> tuple[dict[str, Any], str, str, float]:
    started = time.monotonic()
    command = [
        *_evaluator_command(executable_evaluator),
        "--attempt-worktree",
        str(worktree),
        "--trial-index",
        "0",
        "--metric-name",
        experiment.metric_name,
        "--attempt-json",
        str(attempt_json),
    ]
    completed = subprocess.run(
        command,
        cwd=worktree,
        input="",
        capture_output=True,
        text=True,
        timeout=max(0.001, float(experiment.timeout_s)),
        check=False,
        env=_evaluator_environment(
            base_env=os.environ,
            repo_root=repo_root,
            worktree=worktree,
            temp_path=temp_path,
            progress_path=progress_path,
            trial_index=0,
            metric_name=experiment.metric_name,
            attempt_json=attempt_json,
            control_kind=control_kind,
        ),
    )
    if completed.returncode != 0:
        raise EvaluatorContractError(
            f"evaluator {control_kind} control exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return (
        _parse_trial_payload(completed.stdout),
        sha256(completed.stdout.encode("utf-8")).hexdigest(),
        sha256(completed.stderr.encode("utf-8")).hexdigest(),
        round(time.monotonic() - started, 6),
    )


def _failed_quality_control(
    *,
    control_kind: str,
    quality_dir: Path,
    primary_candidate_ref: str,
    error: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "supervisor-autoresearch-evaluator-quality-control/v1",
        "source": "supervisor_control_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "control_kind": control_kind,
        "candidate_ref": f"heldout-control:{control_kind}:{primary_candidate_ref}",
        "candidate_hash": sha256(f"{control_kind}:{primary_candidate_ref}".encode("utf-8")).hexdigest(),
        "metric_source": "evaluator_execution",
        "metric_delta": None,
        "verdict": "failed",
        "error": error,
    }
    path = quality_dir / f"{control_kind}.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    payload["control_run_ref"] = path.as_posix()
    payload["control_run_hash"] = sha256(path.read_bytes()).hexdigest()
    payload["evaluator_run_ref"] = payload["control_run_ref"]
    payload["evaluator_run_hash"] = payload["control_run_hash"]
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def _write_control_candidate(
    *,
    quality_dir: Path,
    attempt: AutoresearchAttempt,
    control_kind: str,
    primary_candidate_ref: str,
) -> dict[str, str]:
    payload = {
        "schema_version": "supervisor-autoresearch-heldout-control-candidate/v1",
        "attempt_id": attempt.attempt_id,
        "control_kind": control_kind,
        "primary_candidate_ref": primary_candidate_ref,
    }
    path = quality_dir / "candidates" / f"{control_kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {
        "candidate_ref": path.as_posix(),
        "candidate_hash": sha256(path.read_bytes()).hexdigest(),
    }


def _write_control_attempt_json(
    *,
    quality_dir: Path,
    attempt: AutoresearchAttempt,
    control_kind: str,
    control_candidate: Mapping[str, str],
) -> Path:
    payload = attempt.to_payload()
    payload["evaluator_quality_control"] = {
        "kind": control_kind,
        "candidate_ref": control_candidate["candidate_ref"],
        "candidate_hash": control_candidate["candidate_hash"],
        "baseline_metric": attempt.metric_before,
    }
    path = quality_dir / f"{control_kind}.attempt.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _policy_candidate_refs(attempt: AutoresearchAttempt, *, repo_root: Path) -> list[str]:
    refs = [
        _normalise_path(ref, repo_root=repo_root)
        for ref in (*attempt.policy_candidate_changes.values(), attempt.policy_overlay_candidate_ref)
        if str(ref).strip()
    ]
    refs.extend(
        _normalise_path(ref, repo_root=repo_root)
        for ref in attempt.changed_files
        if Path(str(ref)).name in {"policy-overlay.yaml", "policy-overlay.yml"}
    )
    return list(dict.fromkeys(refs))


def _control_metric_before(payload: Mapping[str, Any], *, attempt: AutoresearchAttempt) -> float | None:
    for key in ("metric_before", "baseline_metric", "empty_floor_metric"):
        if payload.get(key) not in {None, ""}:
            return round(float(payload[key]), 6)
    if attempt.metric_before is not None:
        return round(float(attempt.metric_before), 6)
    return None


def _control_metric_delta(payload: Mapping[str, Any], *, before: float | None, after: float) -> float | None:
    if payload.get("metric_delta") not in {None, ""}:
        return round(float(payload["metric_delta"]), 6)
    if before is None:
        return None
    return round(float(after) - float(before), 6)


def _control_verdict(control_kind: str, metric_delta: float | None) -> str:
    if metric_delta is None:
        return "missing_delta"
    if control_kind == "noop":
        return "no_improvement" if metric_delta <= 0 else "unexpected_improvement"
    if control_kind == "harmful":
        return "regressed" if metric_delta <= 0 else "unexpected_improvement"
    if control_kind == "known_good":
        return "improved" if metric_delta > 0 else "no_improvement"
    return "observed"


def _load_progress(
    progress_path: Path,
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    evaluator_rel: str,
) -> dict[str, Any]:
    if not progress_path.exists():
        return {"trial_records": [], "cost_usd": 0.0}
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"trial_records": [], "cost_usd": 0.0}
    if not isinstance(payload, dict):
        return {"trial_records": [], "cost_usd": 0.0}
    if payload.get("experiment_id") != experiment.experiment_id:
        return {"trial_records": [], "cost_usd": 0.0}
    if payload.get("attempt_id") != attempt.attempt_id:
        return {"trial_records": [], "cost_usd": 0.0}
    if payload.get("evaluator_ref") != evaluator_rel:
        return {"trial_records": [], "cost_usd": 0.0}
    if payload.get("evaluator_hash") != experiment.evaluator_hash:
        return {"trial_records": [], "cost_usd": 0.0}
    raw_records = payload.get("trial_records")
    if not isinstance(raw_records, list):
        raw_records = []
    records = [
        record for record in raw_records
        if isinstance(record, dict)
        and isinstance(record.get("trial_index"), int)
        and "metric_value" in record
    ]
    records = sorted(records, key=lambda record: int(record["trial_index"]))
    contiguous: list[dict[str, Any]] = []
    for expected_index, record in enumerate(records):
        if int(record["trial_index"]) != expected_index:
            break
        contiguous.append(record)
    return {
        "trial_records": contiguous,
        "cost_usd": float(payload.get("cost_usd") or 0.0),
        "empty_floor_record": dict(payload.get("empty_floor_record") or {}),
        "empty_floor_metric": _optional_float(payload.get("empty_floor_metric")),
        "metric_before": _optional_float(payload.get("metric_before")),
        "metric_after": _optional_float(payload.get("metric_after")),
        "metric_delta": _optional_float(payload.get("metric_delta")),
    }


def _write_progress(
    progress_path: Path,
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    evaluator_rel: str,
    trial_records: list[dict[str, Any]],
    cost_usd: float,
    empty_floor_record: Mapping[str, Any] | None = None,
    metric_before: float | None = None,
    metric_after: float | None = None,
    metric_delta: float | None = None,
) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "supervisor-autoresearch-evaluator-progress/v1",
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
        "evaluator_ref": evaluator_rel,
        "evaluator_hash": experiment.evaluator_hash,
        "metric_name": experiment.metric_name,
        "k_trials": experiment.k_trials,
        "trial_records": trial_records,
        "completed_trial_count": len(trial_records),
        "cost_usd": round(float(cost_usd), 6),
    }
    if empty_floor_record:
        payload["empty_floor_record"] = dict(empty_floor_record)
        payload["empty_floor_metric"] = _optional_float(empty_floor_record.get("empty_floor_metric"))
    payload["metric_before"] = metric_before
    payload["metric_after"] = metric_after
    payload["metric_delta"] = metric_delta
    progress_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_trial_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise EvaluatorContractError("evaluator stdout must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise EvaluatorContractError("evaluator stdout must be a JSON object")
    return payload


def _metric_value(payload: dict[str, Any], *, metric_name: str) -> float:
    if "metric_value" in payload:
        return float(payload["metric_value"])
    metrics = payload.get("metrics")
    if isinstance(metrics, dict) and metric_name in metrics:
        return float(metrics[metric_name])
    if metric_name in payload:
        return float(payload[metric_name])
    if "metric" in payload:
        return float(payload["metric"])
    raise EvaluatorContractError(f"evaluator output missing metric value for {metric_name}")


def _materialize_attempt_worktree(
    *,
    repo_root: Path,
    worktree: Path,
    mutable_paths: tuple[str, ...],
    changed_files: tuple[str, ...],
) -> None:
    worktree.mkdir(parents=True, exist_ok=True)
    for raw_path in mutable_paths:
        rel = _normalise_path(raw_path, repo_root=repo_root)
        if not rel:
            continue
        src = repo_root / rel
        dst = worktree / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
    for raw_path in changed_files:
        rel = _normalise_path(raw_path, repo_root=repo_root)
        if rel:
            (worktree / rel).parent.mkdir(parents=True, exist_ok=True)


def _snapshot_files(root: Path, *, exclude_dirs: set[str] | None = None) -> dict[str, str]:
    return _hash_bytes_snapshot(_snapshot_file_bytes(root, exclude_dirs=exclude_dirs))


def _snapshot_file_bytes(
    root: Path,
    *,
    exclude_dirs: set[str] | None = None,
    exclude_roots: tuple[Path, ...] = (),
) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    excluded = exclude_dirs or set()
    resolved_exclude_roots = tuple(path.resolve(strict=False) for path in exclude_roots)
    for path in sorted(root.rglob("*")):
        if excluded and any(part in excluded for part in path.relative_to(root).parts[:-1]):
            continue
        resolved_path = path.resolve(strict=False)
        if any(_is_relative_to(resolved_path, excluded_root) for excluded_root in resolved_exclude_roots):
            continue
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            snapshot[rel] = path.read_bytes()
    return snapshot


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _hash_bytes_snapshot(snapshot: Mapping[str, bytes]) -> dict[str, str]:
    return {
        path: sha256(content).hexdigest()
        for path, content in snapshot.items()
    }


def _restore_snapshot(*, root: Path, before: Mapping[str, bytes], changed_paths: list[str]) -> None:
    for rel in changed_paths:
        target = root / rel
        original = before.get(rel)
        if original is None:
            if target.exists() and target.is_file():
                target.unlink()
                _remove_empty_parents(target.parent, stop_at=root)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original)


def _remove_empty_parents(path: Path, *, stop_at: Path) -> None:
    current = path
    stop = stop_at.resolve()
    while current != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    paths = sorted(set(before) | set(after))
    return [path for path in paths if before.get(path) != after.get(path)]


def _is_ignorable_source_checkout_mutation(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _IGNORABLE_SOURCE_MUTATION_PREFIXES)


def _normalise_path(value: str, *, repo_root: Path | None = None) -> str:
    raw = str(value).strip().replace("\\", "/")
    if repo_root is not None:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            try:
                raw = candidate.resolve(strict=False).relative_to(repo_root).as_posix()
            except ValueError:
                raw = candidate.resolve(strict=False).as_posix().lstrip("/")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _matches(path: str, pattern: str) -> bool:
    clean_pattern = pattern.rstrip("/")
    if not clean_pattern:
        return False
    if path == clean_pattern:
        return True
    return path.startswith(clean_pattern + "/")
