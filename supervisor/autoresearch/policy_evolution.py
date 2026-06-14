"""Human-approved policy evolution from accepted AutoResearch evidence."""
from __future__ import annotations

import difflib
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol

from .schema import sha256_json
from ..policy_overlay import POLICY_OVERLAY_PATH, PolicyOverlayError, normalise_overlay_target


POLICY_PROPOSAL_SCHEMA_VERSION = "supervisor-autoresearch-policy-proposal/v1"
POLICY_APPROVAL_SCHEMA_VERSION = "supervisor-autoresearch-policy-approval/v1"
POLICY_DENIAL_SCHEMA_VERSION = "supervisor-autoresearch-policy-denial/v1"
POLICY_ROLLBACK_SCHEMA_VERSION = "supervisor-autoresearch-policy-rollback/v1"
POLICY_DERIVATION_SCHEMA_VERSION = "supervisor-autoresearch-policy-derivation/v1"


class EventWriter(Protocol):
    def write_event(self, *, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> int:
        ...


class PolicyEvolutionError(RuntimeError):
    """Raised when an operator-approved policy change cannot be applied safely."""


def create_policy_evolution_proposals(
    report: Mapping[str, Any],
    *,
    repo_root: str | Path,
    candidate_changes: Mapping[str, str],
    affected_gates: tuple[str, ...] | list[str],
    state: EventWriter | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Create non-mutating stability proposals from clean accepted AutoResearch records.

    `candidate_changes` maps target prompt/config artifact paths to candidate
    artifact refs produced by the accepted AutoResearch attempt. Proposal
    creation is intentionally read-only: applying requires `approve_policy_proposal`.
    """
    repo_root_path = Path(repo_root).expanduser().resolve()
    changes = tuple(
        (target, candidate)
        for target, candidate in sorted(candidate_changes.items())
    )
    if not changes:
        return []
    proposals: list[dict[str, Any]] = []
    records = report.get("records") if isinstance(report.get("records"), list) else []
    for record in records:
        if not isinstance(record, Mapping) or not _record_is_applyable(record):
            continue
        proposal = _build_policy_proposal(
            report=report,
            record=record,
            repo_root=repo_root_path,
            candidate_changes=changes,
            affected_gates=tuple(str(gate) for gate in affected_gates),
        )
        proposals.append(proposal)
        if state is not None and run_id:
            state.write_event(
                run_id=run_id,
                source="autoresearch",
                kind="autoresearch_policy_proposal_created",
                payload=proposal,
            )
    return proposals


def derive_policy_evolution_proposals_from_report(
    report: Mapping[str, Any],
    *,
    repo_root: str | Path,
    affected_gates: tuple[str, ...] | list[str],
    state: EventWriter | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Draft overlay proposals directly from accepted AutoResearch report records.

    This is the policy-evolution public boundary for the auto loop: it derives
    the overlay candidate from report evidence instead of accepting a
    caller-authored `candidate_changes` mapping.
    """
    repo_root_path = Path(repo_root).expanduser().resolve()
    gates = tuple(str(gate) for gate in affected_gates)
    records = report.get("records") if isinstance(report.get("records"), list) else []
    proposals: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping) or not _record_is_applyable(record):
            continue
        try:
            empty_floor = _empty_floor_win(record)
            metric_delta = _positive_metric_delta(record)
            candidate_ref = _derive_overlay_candidate_ref(record, repo_root=repo_root_path)
            proposal = _build_policy_proposal(
                report=report,
                record=record,
                repo_root=repo_root_path,
                candidate_changes=((POLICY_OVERLAY_PATH, candidate_ref),),
                affected_gates=gates,
            )
        except PolicyEvolutionError as exc:
            _write_derivation_skipped(
                state=state,
                run_id=run_id,
                report=report,
                record=record,
                reason=str(exc),
            )
            continue
        proposal["status"] = "draft"
        proposal["source"] = "autoresearch_deriver"
        proposal["derivation"] = {
            "schema_version": POLICY_DERIVATION_SCHEMA_VERSION,
            "report_ref": str(report.get("report_ref") or report.get("evaluator_run_ref") or ""),
            "report_sha256": str(report.get("report_sha256") or ""),
            "experiment_id": str(record.get("experiment_id") or report.get("experiment_id") or ""),
            "attempt_id": str(record.get("attempt_id") or ""),
            "candidate_ref": candidate_ref,
            "affected_gates": list(gates),
            "empty_floor_comparison": empty_floor,
            **metric_delta,
        }
        proposal["proposal_sha256"] = sha256_json({
            key: value for key, value in proposal.items() if key != "proposal_sha256"
        })
        proposals.append(proposal)
        if state is not None and run_id:
            state.write_event(
                run_id=run_id,
                source="autoresearch",
                kind="autoresearch_policy_proposal_created",
                payload=proposal,
            )
    return proposals


def report_contains_derivable_policy_record(
    report: Mapping[str, Any],
    *,
    repo_root: str | Path,
) -> bool:
    repo_root_path = Path(repo_root).expanduser().resolve()
    records = report.get("records") if isinstance(report.get("records"), list) else []
    for record in records:
        if not isinstance(record, Mapping) or not _record_is_applyable(record):
            continue
        try:
            _empty_floor_win(record)
            _positive_metric_delta(record)
            _derive_overlay_candidate_ref(record, repo_root=repo_root_path)
        except PolicyEvolutionError:
            continue
        return True
    return False


def approve_policy_proposal(
    proposal: Mapping[str, Any],
    *,
    state: EventWriter,
    run_id: str,
    repo_root: str | Path,
    approver: str,
    approval_channel: str,
    rollback_root: str | Path = ".handoff/policy-rollbacks",
) -> dict[str, Any]:
    """Apply exactly the proposal's recorded candidate artifacts after approval."""
    _require_operator(approver=approver, approval_channel=approval_channel)
    repo_root_path = Path(repo_root).expanduser().resolve()
    rollback_root_rel = _normalise_relative_path(str(rollback_root), repo_root=repo_root_path)
    rollback_root_path = repo_root_path / rollback_root_rel
    proposal_id = str(proposal.get("proposal_id") or "")
    if not proposal_id:
        raise PolicyEvolutionError("proposal_id is required")
    changes = _proposal_changes(proposal)
    prepared_changes: list[dict[str, Any]] = []
    for change in changes:
        target_rel = _normalise_relative_path(str(change["target_path"]), repo_root=repo_root_path)
        _require_policy_overlay_target(target_rel, repo_root=repo_root_path)
        candidate_rel = _normalise_relative_path(str(change["candidate_ref"]), repo_root=repo_root_path)
        target_path = repo_root_path / target_rel
        candidate_path = repo_root_path / candidate_rel
        before_hash = str(change["before_hash"])
        after_hash = str(change["after_hash"])
        target_existed = target_path.exists()
        current_bytes = target_path.read_bytes() if target_existed else b""
        current_hash = _sha256_bytes(current_bytes)
        if current_hash != before_hash:
            raise PolicyEvolutionError(
                f"current artifact hash mismatch for {target_rel}: "
                f"expected {before_hash}, observed {current_hash}"
            )
        if not candidate_path.exists() or not candidate_path.is_file():
            raise PolicyEvolutionError(f"candidate artifact missing: {candidate_rel}")
        candidate_bytes = candidate_path.read_bytes()
        candidate_hash = _sha256_bytes(candidate_bytes)
        if candidate_hash != after_hash:
            raise PolicyEvolutionError(
                f"candidate artifact hash mismatch for {candidate_rel}: "
                f"expected {after_hash}, observed {candidate_hash}"
            )
        prepared_changes.append({
            "target_rel": target_rel,
            "candidate_rel": candidate_rel,
            "target_path": target_path,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "target_existed": target_existed,
            "current_bytes": current_bytes,
            "candidate_bytes": candidate_bytes,
        })

    rollback_files: list[dict[str, Any]] = []
    applied_changes: list[dict[str, Any]] = []

    try:
        for change in prepared_changes:
            target_rel = change["target_rel"]
            target_path = change["target_path"]
            before_hash = change["before_hash"]
            after_hash = change["after_hash"]
            backup_rel = _rollback_backup_ref(
                rollback_root_rel=rollback_root_rel,
                proposal_id=proposal_id,
                target_path=target_rel,
            )
            backup_path = repo_root_path / backup_rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(change["current_bytes"])

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(change["candidate_bytes"])
            observed_after_hash = _sha256_bytes(target_path.read_bytes())
            if observed_after_hash != after_hash:
                raise PolicyEvolutionError(
                    f"applied artifact hash mismatch for {target_rel}: "
                    f"expected {after_hash}, observed {observed_after_hash}"
                )
            rollback_files.append({
                "target_path": target_rel,
                "backup_ref": backup_rel,
                "before_hash": before_hash,
                "after_hash": after_hash,
            })
            applied_changes.append({
                "target_path": target_rel,
                "candidate_ref": change["candidate_rel"],
                "before_hash": before_hash,
                "after_hash": after_hash,
            })
    except Exception:
        _restore_prepared_targets(prepared_changes)
        raise

    rollback_pointer = {
        "schema_version": POLICY_ROLLBACK_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "files": rollback_files,
    }
    first_change = applied_changes[0]
    payload = {
        "schema_version": POLICY_APPROVAL_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "status": "approved_applied",
        "approver": str(approver),
        "approval_channel": str(approval_channel),
        "before_hash": first_change["before_hash"],
        "after_hash": first_change["after_hash"],
        "changes": applied_changes,
        "rollback_pointer": rollback_pointer,
        **_authority_invariants(operator_approved=True),
    }
    payload["event_sha256"] = sha256_json({key: value for key, value in payload.items() if key != "event_sha256"})
    state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_proposal_approved",
        payload=payload,
    )
    return payload


def deny_policy_proposal(
    proposal: Mapping[str, Any],
    *,
    state: EventWriter,
    run_id: str,
    approver: str,
    approval_channel: str,
    reason: str,
) -> dict[str, Any]:
    """Record an explicit operator denial without mutating artifacts."""
    _require_operator(approver=approver, approval_channel=approval_channel)
    payload = {
        "schema_version": POLICY_DENIAL_SCHEMA_VERSION,
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "status": "denied",
        "approver": str(approver),
        "approval_channel": str(approval_channel),
        "reason": str(reason or "").strip(),
        **_authority_invariants(operator_approved=False),
    }
    payload["event_sha256"] = sha256_json({key: value for key, value in payload.items() if key != "event_sha256"})
    state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_proposal_denied",
        payload=payload,
    )
    return payload


def rollback_policy_proposal(
    rollback_pointer: Mapping[str, Any],
    *,
    state: EventWriter,
    run_id: str,
    repo_root: str | Path,
    approver: str,
    approval_channel: str,
    reason: str = "",
) -> dict[str, Any]:
    """Restore previous artifact bytes through a recorded rollback pointer."""
    _require_operator(approver=approver, approval_channel=approval_channel)
    repo_root_path = Path(repo_root).expanduser().resolve()
    files = rollback_pointer.get("files") if isinstance(rollback_pointer.get("files"), list) else []
    if not files:
        raise PolicyEvolutionError("rollback pointer has no files")
    prepared_restores: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, Mapping):
            raise PolicyEvolutionError("rollback file entry must be an object")
        target_rel = _normalise_relative_path(str(item.get("target_path") or ""), repo_root=repo_root_path)
        _require_policy_overlay_target(target_rel, repo_root=repo_root_path)
        backup_rel = _normalise_relative_path(str(item.get("backup_ref") or ""), repo_root=repo_root_path)
        expected_hash = str(item.get("before_hash") or "")
        backup_path = repo_root_path / backup_rel
        if not backup_path.exists() or not backup_path.is_file():
            raise PolicyEvolutionError(f"rollback backup missing: {backup_rel}")
        backup_bytes = backup_path.read_bytes()
        observed_hash = _sha256_bytes(backup_bytes)
        if observed_hash != expected_hash:
            raise PolicyEvolutionError(
                f"rollback backup hash mismatch for {backup_rel}: "
                f"expected {expected_hash}, observed {observed_hash}"
            )
        prepared_restores.append({
            "target_rel": target_rel,
            "backup_bytes": backup_bytes,
            "expected_hash": expected_hash,
        })

    restored: list[dict[str, Any]] = []
    for prepared in prepared_restores:
        target_rel = str(prepared["target_rel"])
        target_path = repo_root_path / target_rel
        backup_bytes = bytes(prepared["backup_bytes"])
        expected_hash = str(prepared["expected_hash"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(backup_bytes)
        restored.append({
            "target_path": target_rel,
            "restored_hash": _sha256_bytes(target_path.read_bytes()),
            "expected_hash": expected_hash,
        })

    payload = {
        "schema_version": POLICY_ROLLBACK_SCHEMA_VERSION,
        "proposal_id": str(rollback_pointer.get("proposal_id") or ""),
        "status": "rolled_back",
        "approver": str(approver),
        "approval_channel": str(approval_channel),
        "reason": str(reason or "").strip(),
        "restored": restored,
        "rollback_pointer": dict(rollback_pointer),
        **_authority_invariants(operator_approved=True),
    }
    payload["event_sha256"] = sha256_json({key: value for key, value in payload.items() if key != "event_sha256"})
    state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_proposal_rolled_back",
        payload=payload,
    )
    return payload


def _build_policy_proposal(
    *,
    report: Mapping[str, Any],
    record: Mapping[str, Any],
    repo_root: Path,
    candidate_changes: tuple[tuple[str, str], ...],
    affected_gates: tuple[str, ...],
) -> dict[str, Any]:
    changed_files = {str(path) for path in record.get("changed_files", ())}
    changes: list[dict[str, Any]] = []
    for target_path, candidate_ref in candidate_changes:
        target_rel = _normalise_relative_path(target_path, repo_root=repo_root)
        _require_policy_overlay_target(target_rel, repo_root=repo_root)
        candidate_rel = _normalise_relative_path(candidate_ref, repo_root=repo_root)
        if candidate_rel not in changed_files:
            raise PolicyEvolutionError(
                f"candidate artifact {candidate_rel} is not listed in accepted attempt changed_files"
            )
        target = repo_root / target_rel
        candidate = repo_root / candidate_rel
        if not candidate.exists() or not candidate.is_file():
            raise PolicyEvolutionError(f"candidate artifact missing: {candidate_rel}")
        before_bytes = target.read_bytes() if target.exists() else b""
        after_bytes = candidate.read_bytes()
        before_text = before_bytes.decode("utf-8")
        after_text = after_bytes.decode("utf-8")
        changes.append({
            "target_path": target_rel,
            "candidate_ref": candidate_rel,
            "artifact_kind": _artifact_kind(target_rel),
            "before_hash": _sha256_bytes(before_bytes),
            "after_hash": _sha256_bytes(after_bytes),
            "diff": "".join(difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{target_rel}",
                tofile=f"b/{target_rel}",
            )),
        })

    evaluator_evidence = _evaluator_evidence(record)
    seed = {
        "report_sha256": str(report.get("report_sha256") or ""),
        "experiment_id": str(record.get("experiment_id") or report.get("experiment_id") or ""),
        "attempt_id": str(record.get("attempt_id") or ""),
        "affected_gates": list(affected_gates),
        "changes": [
            {
                "target_path": change["target_path"],
                "candidate_ref": change["candidate_ref"],
                "before_hash": change["before_hash"],
                "after_hash": change["after_hash"],
            }
            for change in changes
        ],
    }
    proposal_id = "ARP-" + sha256_json(seed)[:16]
    proposal = {
        "schema_version": POLICY_PROPOSAL_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "status": "proposed",
        "source": "autoresearch",
        "task_id": str(record.get("task_id") or report.get("task_id") or ""),
        "experiment_id": str(record.get("experiment_id") or ""),
        "attempt_id": str(record.get("attempt_id") or ""),
        "affected_gates": list(affected_gates),
        "evaluator_evidence": evaluator_evidence,
        "changes": changes,
        **_authority_invariants(operator_approved=False),
    }
    proposal["proposal_sha256"] = sha256_json({
        key: value for key, value in proposal.items() if key != "proposal_sha256"
    })
    return proposal


def _record_is_applyable(record: Mapping[str, Any]) -> bool:
    return (
        str(record.get("validation_status") or "") == "accepted"
        and not list(record.get("gaming_flags") or [])
        and str(record.get("metric_source") or "") == "evaluator_execution"
        and bool(record.get("evaluator_run_ref"))
        and bool(record.get("evaluator_run_hash"))
        and record.get("default_change_allowed") is False
        and record.get("policy_mutated") is False
        and record.get("gate_advanced") is False
    )


def _evaluator_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    trials = [float(value) for value in record.get("metric_trials", ())]
    return {
        "validation_status": str(record.get("validation_status") or ""),
        "recommendation": str(record.get("recommendation") or ""),
        "metric_name": str(record.get("metric_name") or ""),
        "metric_trials": trials,
        "k_trials": len(trials),
        "metric_median": record.get("metric_median"),
        "metric_iqr": record.get("metric_iqr"),
        "empty_floor_comparison": _empty_floor_evidence_payload(record),
        "quality_unstable_across_trials": bool(record.get("quality_unstable_across_trials")),
        "metric_source": str(record.get("metric_source") or ""),
        "evaluator_run_ref": str(record.get("evaluator_run_ref") or ""),
        "evaluator_run_hash": str(record.get("evaluator_run_hash") or ""),
        "gaming_flags": list(record.get("gaming_flags") or []),
        "validation_errors": list(record.get("validation_errors") or []),
        "cost_usd": float(record.get("cost_usd") or 0.0),
        "wall_clock_s": float(record.get("wall_clock_s") or 0.0),
    }


def _positive_metric_delta(record: Mapping[str, Any]) -> dict[str, float]:
    before = _optional_float(
        record.get("metric_before", record.get("baseline_metric", record.get("metric_baseline")))
    )
    after = _optional_float(record.get("metric_after", record.get("candidate_metric", record.get("metric_median"))))
    explicit_delta = _optional_float(record.get("metric_delta"))
    if explicit_delta is None:
        if before is None or after is None:
            raise PolicyEvolutionError("positive metric delta is required for policy derivation")
        delta = after - before
    else:
        delta = explicit_delta
        if before is not None and after is not None and round(after - before, 6) != round(delta, 6):
            raise PolicyEvolutionError("metric delta must match metric before/after values")
        if after is None and before is not None:
            after = before + delta
        if before is None and after is not None:
            before = after - delta
    if before is None or after is None:
        raise PolicyEvolutionError("metric before/after values are required for policy derivation")
    if delta <= 0:
        raise PolicyEvolutionError("positive metric delta is required for policy derivation")
    return {
        "metric_before": round(float(before), 6),
        "metric_after": round(float(after), 6),
        "metric_delta": round(float(delta), 6),
    }


def _empty_floor_win(record: Mapping[str, Any]) -> dict[str, float | int | str]:
    comparison = record.get("empty_floor_comparison")
    if not isinstance(comparison, Mapping):
        raise PolicyEvolutionError("empty-floor comparison is required for policy derivation")
    source = str(comparison.get("metric_source") or "")
    if source != "evaluator_execution":
        raise PolicyEvolutionError("empty-floor comparison must come from evaluator_execution")
    empty_metric = _optional_float(
        comparison.get("empty_floor_metric", comparison.get("baseline_metric"))
    )
    candidate_metric = _optional_float(
        comparison.get("candidate_metric", comparison.get("metric_after", record.get("metric_after")))
    )
    explicit_delta = _optional_float(comparison.get("metric_delta"))
    if empty_metric is None or candidate_metric is None:
        raise PolicyEvolutionError("empty-floor comparison requires empty and candidate metrics")
    delta = candidate_metric - empty_metric if explicit_delta is None else explicit_delta
    if round(candidate_metric - empty_metric, 6) != round(delta, 6):
        raise PolicyEvolutionError("empty-floor metric delta must match empty/candidate values")
    if delta <= 0:
        raise PolicyEvolutionError("policy candidate must beat empty-floor metric")
    return {
        "metric_source": source,
        "empty_floor_metric": round(float(empty_metric), 6),
        "candidate_metric": round(float(candidate_metric), 6),
        "metric_delta": round(float(delta), 6),
        "k_trials": int(comparison.get("k_trials") or len(record.get("metric_trials") or [])),
    }


def _empty_floor_evidence_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    comparison = record.get("empty_floor_comparison")
    return dict(comparison) if isinstance(comparison, Mapping) else {}


def _derive_overlay_candidate_ref(record: Mapping[str, Any], *, repo_root: Path) -> str:
    report_changes = record.get("policy_candidate_changes")
    if report_changes is None:
        report_changes = record.get("candidate_changes")
    if isinstance(report_changes, Mapping) and report_changes:
        if len(report_changes) != 1:
            raise PolicyEvolutionError("derived policy change must contain exactly one target")
        [(target, candidate)] = list(report_changes.items())
        target_rel = _normalise_relative_path(str(target), repo_root=repo_root)
        _require_policy_overlay_target(target_rel, repo_root=repo_root)
        candidate_rel = _normalise_relative_path(str(candidate), repo_root=repo_root)
        _require_policy_overlay_candidate_ref(candidate_rel)
        return candidate_rel

    candidate_ref = (
        record.get("policy_overlay_candidate_ref")
        or record.get("candidate_overlay_ref")
        or _candidate_artifact_ref(record)
        or _candidate_from_changed_files(record, repo_root=repo_root)
    )
    candidate_rel = _normalise_relative_path(str(candidate_ref), repo_root=repo_root)
    _require_policy_overlay_candidate_ref(candidate_rel)
    return candidate_rel


def _candidate_artifact_ref(record: Mapping[str, Any]) -> str:
    artifacts = record.get("candidate_artifacts")
    if isinstance(artifacts, Mapping):
        value = artifacts.get(POLICY_OVERLAY_PATH)
        if value:
            return str(value)
    return ""


def _candidate_from_changed_files(record: Mapping[str, Any], *, repo_root: Path) -> str:
    candidates: list[str] = []
    for raw in record.get("changed_files", ()):
        rel = _normalise_relative_path(str(raw), repo_root=repo_root)
        if rel == POLICY_OVERLAY_PATH:
            raise PolicyEvolutionError("policy derivation requires a candidate artifact, not the live overlay path")
        if Path(rel).name in {"policy-overlay.yaml", "policy-overlay.yml"}:
            candidates.append(rel)
    if len(candidates) != 1:
        raise PolicyEvolutionError("exactly one policy overlay candidate artifact is required")
    return candidates[0]


def _require_policy_overlay_candidate_ref(candidate_rel: str) -> None:
    if candidate_rel == POLICY_OVERLAY_PATH:
        raise PolicyEvolutionError("policy derivation requires a candidate artifact, not the live overlay path")
    if Path(candidate_rel).name not in {"policy-overlay.yaml", "policy-overlay.yml"}:
        raise PolicyEvolutionError(
            f"derived policy candidate must be a policy-overlay.yaml artifact: {candidate_rel}"
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _write_derivation_skipped(
    *,
    state: EventWriter | None,
    run_id: str | None,
    report: Mapping[str, Any],
    record: Mapping[str, Any],
    reason: str,
) -> None:
    if state is None or not run_id:
        return
    state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_proposal_derivation_skipped",
        payload={
            "schema_version": POLICY_DERIVATION_SCHEMA_VERSION,
            "status": "skipped",
            "reason": reason,
            "report_sha256": str(report.get("report_sha256") or ""),
            "experiment_id": str(record.get("experiment_id") or report.get("experiment_id") or ""),
            "attempt_id": str(record.get("attempt_id") or ""),
            **_authority_invariants(operator_approved=False),
        },
    )


def _proposal_changes(proposal: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    changes = proposal.get("changes")
    if not isinstance(changes, list) or not changes:
        raise PolicyEvolutionError("proposal has no changes")
    for change in changes:
        if not isinstance(change, Mapping):
            raise PolicyEvolutionError("proposal change entries must be objects")
    return changes


def _authority_invariants(*, operator_approved: bool) -> dict[str, Any]:
    return {
        "requires_operator_approval": not operator_approved,
        "operator_approved": bool(operator_approved),
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "gate_advanced": False,
        "gate_authority": "unchanged",
        "reviewer_panel_authority": "unchanged",
        "typed_outcome_authority": "unchanged",
    }


def _require_operator(*, approver: str, approval_channel: str) -> None:
    if not str(approver or "").strip():
        raise PolicyEvolutionError("approver is required")
    if not str(approval_channel or "").strip():
        raise PolicyEvolutionError("approval_channel is required")


def _normalise_relative_path(value: str, *, repo_root: Path) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise PolicyEvolutionError("path is required")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            raw = candidate.resolve(strict=False).relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise PolicyEvolutionError(f"path is outside repo root: {value}") from exc
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise PolicyEvolutionError(f"path traversal is not allowed: {value}")
        parts.append(part)
    if not parts:
        raise PolicyEvolutionError("path is required")
    return "/".join(parts)


def _artifact_kind(path: str) -> str:
    if path == POLICY_OVERLAY_PATH:
        return "policy_overlay"
    suffix = Path(path).suffix.lower()
    if suffix in {".json", ".toml", ".yaml", ".yml"} or "config" in path:
        return "config"
    return "prompt"


def _restore_prepared_targets(prepared_changes: list[dict[str, Any]]) -> None:
    for change in prepared_changes:
        target_path = change["target_path"]
        if change["target_existed"]:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(change["current_bytes"])
        else:
            target_path.unlink(missing_ok=True)


def _rollback_backup_ref(*, rollback_root_rel: str, proposal_id: str, target_path: str) -> str:
    safe_target = target_path.replace("/", "__")
    return f"{rollback_root_rel.rstrip('/')}/{proposal_id}/{safe_target}.before"


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _require_policy_overlay_target(path: str, *, repo_root: Path) -> None:
    try:
        normalise_overlay_target(path, repo_root=repo_root)
    except PolicyOverlayError as exc:
        raise PolicyEvolutionError(str(exc)) from exc
