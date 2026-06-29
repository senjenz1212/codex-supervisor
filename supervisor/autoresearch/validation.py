"""Deterministic validation for supervisor AutoResearch attempts."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .report import summarize_metric_trials
from .schema import (
    AutoresearchAttempt,
    AutoresearchExperiment,
    AutoresearchValidationReport,
    stable_json_dumps,
)


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

    policy_candidate_changes = _normalise_policy_candidate_changes(
        attempt.policy_candidate_changes,
        repo_root=repo_root_path,
    )
    policy_overlay_candidate_ref = _normalise_path(
        attempt.policy_overlay_candidate_ref,
        repo_root=repo_root_path,
    )
    policy_candidate_refs = list(dict.fromkeys(
        ref for ref in (
            *policy_candidate_changes.values(),
            policy_overlay_candidate_ref,
            *_policy_overlay_candidates_from_changed_files(changed_files),
        )
        if ref
    ))
    missing_policy_candidates = [
        ref for ref in policy_candidate_refs if ref not in changed_files
    ]
    if missing_policy_candidates:
        gaming_flags.append("policy_candidate_missing_changed_file")
        errors.append(
            "policy candidate refs must be listed in changed_files: "
            + ", ".join(missing_policy_candidates)
        )
    evaluator_quality, quality_flags, quality_errors = _evaluator_quality_findings(
        attempt,
        policy_candidate_refs=tuple(policy_candidate_refs),
    )
    gaming_flags.extend(quality_flags)
    errors.extend(quality_errors)

    metrics = summarize_metric_trials(attempt.metric_trials)
    if metrics["trial_count"] == 0:
        gaming_flags.append("missing_metric_trials")
        errors.append("attempt has no metric trials")
    if attempt.metric_source != "evaluator_execution" or not attempt.evaluator_run_ref or not attempt.evaluator_run_hash:
        gaming_flags.append("evaluator_not_executed")
        errors.append("metric trials require evaluator_execution provenance")
    if len(attempt.metric_trials) > 1 and len(set(float(value) for value in attempt.metric_trials)) == 1:
        gaming_flags.append("zero_variance_trials")
    metric_before, metric_after, metric_delta = _metric_delta_fields(attempt, metrics)

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
        evaluator_ref=experiment.evaluator_ref,
        evaluator_hash=experiment.evaluator_hash,
        evaluator_run_ref=attempt.evaluator_run_ref,
        evaluator_run_hash=attempt.evaluator_run_hash,
        metric_before=metric_before,
        metric_after=metric_after,
        metric_delta=metric_delta,
        policy_overlay_candidate_ref=policy_overlay_candidate_ref,
        policy_candidate_changes=policy_candidate_changes,
        metric_median=metrics["metric_median"],  # type: ignore[arg-type]
        metric_iqr=metrics["metric_iqr"],  # type: ignore[arg-type]
        quality_unstable_across_trials=bool(metrics["quality_unstable_across_trials"]),
        changed_files=changed_files,
        mutable_paths=mutable_paths,
        immutable_paths=immutable_paths,
        artifact_hashes=dict(attempt.artifact_hashes),
        evaluator_quality=evaluator_quality,
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


def _metric_delta_fields(
    attempt: AutoresearchAttempt,
    metrics: dict[str, float | int | bool | list[float] | None],
) -> tuple[float | None, float | None, float | None]:
    before = attempt.metric_before
    after = attempt.metric_after
    if after is None and before is not None:
        median = metrics.get("metric_median")
        if median is not None:
            after = float(median)
    delta = attempt.metric_delta
    if delta is None and before is not None and after is not None:
        delta = float(after) - float(before)
    if before is None and delta is not None and after is not None:
        before = float(after) - float(delta)
    if after is None and delta is not None and before is not None:
        after = float(before) + float(delta)
    return (
        round(float(before), 6) if before is not None else None,
        round(float(after), 6) if after is not None else None,
        round(float(delta), 6) if delta is not None else None,
    )


def _evaluator_quality_findings(
    attempt: AutoresearchAttempt,
    *,
    policy_candidate_refs: tuple[str, ...],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Validate controls that make a candidate metric policy-derivable."""
    raw = dict(attempt.evaluator_quality or {})
    required = bool(policy_candidate_refs)
    flags: list[str] = []
    errors: list[str] = []
    if not required and not raw:
        return {
            "required": False,
            "verdict": "not_required",
            "control_refs": [],
        }, flags, errors

    controls = raw.get("controls")
    controls_map = controls if isinstance(controls, Mapping) else {}
    normalized_controls = {
        str(key): dict(value)
        for key, value in controls_map.items()
        if isinstance(value, Mapping)
    }
    determinism = _normalise_determinism_evidence(raw.get("determinism"))
    supervisor_generated = _quality_is_supervisor_generated(raw)
    if required and not supervisor_generated:
        flags.append("evaluator_quality_not_supervisor_generated")
        errors.append("evaluator-quality controls must be supervisor-generated runtime-native evidence")
    if required and raw.get("candidate_affects_evaluated_path") is not True:
        flags.append("candidate_not_evaluated")
        errors.append("candidate artifact must affect the evaluated path")

    determinism_hashes = tuple(str(value) for value in determinism.get("output_hashes") or ())
    if determinism.get("source") != "repeated_execution" or len(determinism_hashes) < 2:
        flags.append("determinism_unverified")
        errors.append("determinism requires repeated evaluator execution output hashes")
    elif len(set(determinism_hashes)) != 1:
        flags.append("determinism_hash_mismatch")
        errors.append("determinism repeated output hashes must match")
    else:
        determinism["verified"] = True

    required_controls = ("noop", "harmful", "known_good")
    for kind in required_controls:
        control = normalized_controls.get(kind)
        if control is None:
            flags.append(f"missing_{kind}_control")
            errors.append(f"{kind} control is required")
            continue
        if not _quality_is_supervisor_generated(control):
            flags.append(f"{kind}_control_not_supervisor_generated")
            errors.append(f"{kind} control must be supervisor-generated runtime-native evidence")
            continue
        if str(control.get("metric_source") or "") != "evaluator_execution":
            flags.append(f"{kind}_control_not_evaluator_backed")
            errors.append(f"{kind} control must come from evaluator_execution")
            continue
        delta = _quality_metric_delta(control)
        if delta is None:
            flags.append(f"{kind}_control_missing_delta")
            errors.append(f"{kind} control requires a metric delta")
            continue
        control["metric_delta"] = round(delta, 6)
        if kind == "noop" and delta > 0:
            flags.append("noop_control_improved")
            errors.append("noop control must not improve")
        elif kind == "harmful" and delta > 0:
            flags.append("harmful_control_improved")
            errors.append("harmful control must regress or fail")
        elif kind == "known_good" and delta <= 0:
            flags.append("known_good_control_no_improvement")
            errors.append("known-good control must improve")

    quality = {
        "required": required,
        "source": str(raw.get("source") or "caller_supplied_metadata"),
        "evidence_grade": str(raw.get("evidence_grade") or "self_reported"),
        "supervisor_runtime_origin": str(raw.get("supervisor_runtime_origin") or ""),
        "candidate_affects_evaluated_path": bool(raw.get("candidate_affects_evaluated_path")),
        "determinism": determinism,
        "controls": normalized_controls,
        "control_refs": [kind for kind in required_controls if kind in normalized_controls],
        "verdict": "accepted" if not errors else "rejected",
    }
    for key in ("quality_manifest_ref", "quality_manifest_hash", "validation_errors"):
        if key in raw:
            quality[key] = raw[key]
    return quality, flags, errors


def _normalise_determinism_evidence(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    determinism = dict(raw)
    repeated_outputs = determinism.get("repeated_outputs")
    if isinstance(repeated_outputs, list) and len(repeated_outputs) >= 2:
        determinism["output_hashes"] = [
            sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()
            for value in repeated_outputs
        ]
        determinism.setdefault("source", "repeated_execution")
    return determinism


def _quality_is_supervisor_generated(raw: Mapping[str, Any]) -> bool:
    return (
        str(raw.get("source") or "") == "supervisor_control_execution"
        and str(raw.get("evidence_grade") or "") == "runtime_native"
        and str(raw.get("supervisor_runtime_origin") or "") == "run_evaluator_quality_controls"
    )


def _quality_metric_delta(control: Mapping[str, Any]) -> float | None:
    explicit = control.get("metric_delta")
    if explicit not in {None, ""}:
        return float(explicit)
    before = control.get("metric_before", control.get("baseline_metric", control.get("empty_floor_metric")))
    after = control.get("metric_after", control.get("candidate_metric"))
    if before in {None, ""} or after in {None, ""}:
        return None
    return float(after) - float(before)


def _normalise_policy_candidate_changes(
    values: dict[str, str],
    *,
    repo_root: Path,
) -> dict[str, str]:
    return {
        _normalise_path(str(target), repo_root=repo_root): _normalise_path(str(candidate), repo_root=repo_root)
        for target, candidate in sorted(values.items())
        if str(target).strip() and str(candidate).strip()
    }


def _policy_overlay_candidates_from_changed_files(changed_files: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path for path in changed_files
        if Path(path).name in {"policy-overlay.yaml", "policy-overlay.yml"}
    )


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
