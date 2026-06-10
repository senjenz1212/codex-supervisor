"""Deterministic validation for supervisor AutoResearch attempts."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .report import summarize_metric_trials
from .schema import AutoresearchAttempt, AutoresearchExperiment, AutoresearchValidationReport


DEFAULT_IMMUTABLE_PATHS: tuple[str, ...] = (
    "supervisor/state.py",
    "supervisor/postgres_state.py",
    "supervisor/config.py",
    "supervisor/dual_agent_workflow.py",
    "supervisor/dual_agent_runner.py",
    "supervisor/reviewer_registry.py",
    "supervisor/dynamic_workflow_receipts.py",
    "supervisor/autoresearch/validation.py",
    "supervisor/autoresearch/report.py",
    "tests/fixtures/agentic_eval/",
    "tests/fixtures/autoresearch/locked/",
    "config.example.yaml",
)


def validate_attempt(
    *,
    experiment: AutoresearchExperiment,
    attempt: AutoresearchAttempt,
    repo_root: str | Path = ".",
) -> AutoresearchValidationReport:
    """Validate one attempt as evidence, never as gate authority."""
    repo_root_path = Path(repo_root).expanduser().resolve()
    mutable_paths = tuple(dict.fromkeys(_normalise_patterns(experiment.mutable_paths, repo_root=repo_root_path)))
    immutable_paths = tuple(dict.fromkeys(
        _normalise_patterns((*DEFAULT_IMMUTABLE_PATHS, *experiment.immutable_paths), repo_root=repo_root_path)
    ))
    raw_changed_files = tuple(str(path) for path in attempt.changed_files)
    changed_files = tuple(_normalise_path(path, repo_root=repo_root_path) for path in raw_changed_files)
    gaming_flags: list[str] = []
    errors: list[str] = []

    noncanonical_touches = [
        raw_path for raw_path, normalized in zip(raw_changed_files, changed_files)
        if _is_noncanonical_path(raw_path, normalized)
    ]
    if noncanonical_touches:
        gaming_flags.append("noncanonical_changed_path")
        errors.append("changed paths must be repo-relative canonical paths: " + ", ".join(noncanonical_touches))

    immutable_touches = [
        path for path in changed_files
        if _matches_any(path, immutable_paths)
    ]
    if immutable_touches:
        gaming_flags.append("immutable_path_mutation")
        errors.append("changed immutable paths: " + ", ".join(immutable_touches))

    outside_mutable = [
        path for path in changed_files
        if not _matches_any(path, mutable_paths)
    ]
    if outside_mutable:
        gaming_flags.append("outside_mutable_surface")
        errors.append("changed paths outside mutable surface: " + ", ".join(outside_mutable))

    if not attempt.evidence_refs:
        gaming_flags.append("missing_evidence_refs")
        errors.append("attempt has no evidence refs")
    else:
        dangling_refs = _dangling_evidence_refs(attempt)
        if dangling_refs:
            gaming_flags.append("dangling_evidence_ref")
            errors.append("dangling evidence refs: " + ", ".join(dangling_refs))

    if attempt.execution_errors:
        errors.extend(attempt.execution_errors)
        if any("outside mutable surface" in error for error in attempt.execution_errors):
            gaming_flags.append("outside_mutable_surface")
        if any("budget_exceeded" in error for error in attempt.execution_errors):
            gaming_flags.append("budget_exceeded")
        if any("timeout" in error.lower() or "timed out" in error.lower() for error in attempt.execution_errors):
            gaming_flags.append("timeout")

    hash_errors = _artifact_hash_errors(attempt.artifact_hashes, repo_root=repo_root_path)
    if hash_errors:
        gaming_flags.append("artifact_hash_mismatch")
        errors.extend(hash_errors)

    evaluator_errors = _evaluator_hash_errors(experiment, repo_root=repo_root_path)
    if evaluator_errors:
        gaming_flags.append("evaluator_hash_mismatch")
        errors.extend(evaluator_errors)

    metrics = summarize_metric_trials(attempt.metric_trials)
    if metrics["trial_count"] == 0:
        gaming_flags.append("missing_metric_trials")
        errors.append("attempt has no metric trials")
    if attempt.metric_source != "evaluator_execution" or not attempt.evaluator_run_ref or not attempt.evaluator_run_hash:
        gaming_flags.append("evaluator_not_executed")
        errors.append("metric trials require evaluator_execution provenance")
    if len(attempt.metric_trials) > 1 and len(set(float(value) for value in attempt.metric_trials)) == 1:
        gaming_flags.append("zero_variance_trials")

    status = "accepted" if not errors else "rejected"
    recommendation = (
        "validated as report-only candidate; operator review required"
        if status == "accepted"
        else "reject attempt for validation errors; do not promote"
    )

    return AutoresearchValidationReport(
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        attempt_id=attempt.attempt_id,
        validation_status=status,
        recommendation=recommendation,
        metric_name=experiment.metric_name,
        metric_trials=tuple(float(value) for value in attempt.metric_trials),
        metric_source=attempt.metric_source,
        evaluator_run_ref=attempt.evaluator_run_ref,
        evaluator_run_hash=attempt.evaluator_run_hash,
        metric_median=metrics["metric_median"],  # type: ignore[arg-type]
        metric_iqr=metrics["metric_iqr"],  # type: ignore[arg-type]
        quality_unstable_across_trials=bool(metrics["quality_unstable_across_trials"]),
        changed_files=changed_files,
        mutable_paths=mutable_paths,
        immutable_paths=immutable_paths,
        artifact_hashes=dict(attempt.artifact_hashes),
        gaming_flags=tuple(sorted(set(gaming_flags))),
        validation_errors=tuple(errors),
        cost_usd=attempt.cost_usd,
        wall_clock_s=attempt.wall_clock_s,
        default_change_allowed=False,
        policy_mutated=False,
        gate_advanced=False,
    )


def _dangling_evidence_refs(attempt: AutoresearchAttempt) -> list[str]:
    dangling: list[str] = []
    for ref in attempt.evidence_refs:
        if ref.startswith("evaluator_run:"):
            expected = ref.removeprefix("evaluator_run:")
            if not attempt.evaluator_run_ref or expected != attempt.evaluator_run_ref:
                dangling.append(ref)
            continue
        if ref.startswith("artifact:"):
            expected = ref.removeprefix("artifact:")
            if expected not in attempt.artifact_hashes:
                dangling.append(ref)
            continue
        dangling.append(ref)
    return dangling


def _artifact_hash_errors(artifact_hashes: dict[str, str], *, repo_root: Path) -> list[str]:
    errors: list[str] = []
    for raw_path, expected_hash in sorted(artifact_hashes.items()):
        normalized_path = _normalise_path(raw_path, repo_root=repo_root)
        path = repo_root / normalized_path
        if not path.exists() or not path.is_file():
            errors.append(f"artifact missing for {normalized_path}")
            continue
        observed = sha256(path.read_bytes()).hexdigest()
        if observed != expected_hash:
            errors.append(f"artifact hash mismatch for {normalized_path}")
    return errors


def _evaluator_hash_errors(experiment: AutoresearchExperiment, *, repo_root: Path) -> list[str]:
    if not experiment.evaluator_ref and not experiment.evaluator_hash:
        return []
    normalized_path = _normalise_path(experiment.evaluator_ref, repo_root=repo_root)
    if not normalized_path:
        return ["evaluator_ref is required when evaluator_hash is supplied"]
    if not experiment.evaluator_hash:
        return [f"evaluator_hash is required for {normalized_path}"]
    path = repo_root / normalized_path
    if not path.exists() or not path.is_file():
        return [f"evaluator missing for {normalized_path}"]
    observed = sha256(path.read_bytes()).hexdigest()
    if observed != experiment.evaluator_hash:
        return [f"evaluator hash mismatch for {normalized_path}"]
    return []


def _normalise_patterns(values: tuple[str, ...], *, repo_root: Path) -> tuple[str, ...]:
    return tuple(_normalise_path(value, repo_root=repo_root) for value in values if str(value).strip())


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


def _is_noncanonical_path(raw_path: str, normalized_path: str) -> bool:
    raw = str(raw_path).strip().replace("\\", "/")
    if raw.startswith("/"):
        return True
    raw_parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in raw_parts):
        return True
    return "/".join(raw_parts) != normalized_path


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _matches(path: str, pattern: str) -> bool:
    clean_pattern = pattern.rstrip("/")
    if not clean_pattern:
        return False
    if path == clean_pattern:
        return True
    return path.startswith(clean_pattern + "/")
