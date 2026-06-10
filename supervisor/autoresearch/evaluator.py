"""Executable evaluator runner for live, report-only AutoResearch attempts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .schema import AutoresearchAttempt, AutoresearchExperiment


@dataclass(frozen=True)
class EvaluatorExecutionResult:
    metric_trials: tuple[float, ...]
    metric_source: str
    evaluator_run_ref: str
    evaluator_run_hash: str
    attempt_worktree_ref: str
    evidence_refs: tuple[str, ...]
    execution_errors: tuple[str, ...]
    cost_usd: float
    wall_clock_s: float
    job_id: str = ""
    resumed_from_trial_count: int = 0


class EvaluatorContractError(RuntimeError):
    """Raised before execution when the evaluator contract is not trustworthy."""


def run_evaluator_trials(
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: str | Path,
    output_dir: str | Path,
) -> EvaluatorExecutionResult:
    """Run the experiment evaluator in an isolated attempt worktree."""
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
            source_changed_paths = _changed_paths(source_before, source_after)
            if source_changed_paths:
                _restore_snapshot(
                    root=repo_root_path,
                    before=source_before_bytes,
                    changed_paths=source_changed_paths,
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

    run_artifact = {
        "schema_version": "supervisor-autoresearch-evaluator-run/v1",
        "experiment_id": experiment.experiment_id,
        "attempt_id": attempt.attempt_id,
        "evaluator_ref": evaluator_rel,
        "evaluator_hash": experiment.evaluator_hash,
        "metric_name": experiment.metric_name,
        "k_trials": experiment.k_trials,
        "metric_trials": metric_trials,
        "trial_records": trial_records,
        "execution_errors": execution_errors,
        "budget_usd": experiment.budget_usd,
        "timeout_s": experiment.timeout_s,
        "cost_usd": round(cost_usd, 6),
        "wall_clock_s": round(time.monotonic() - started, 6),
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
        resumed_from_trial_count=resumed_from_trial_count,
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
) -> dict[str, str]:
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
    return env


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
    }


def _write_progress(
    progress_path: Path,
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    evaluator_rel: str,
    trial_records: list[dict[str, Any]],
    cost_usd: float,
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
