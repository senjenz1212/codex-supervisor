"""Human-approved policy evolution from accepted AutoResearch evidence."""
from __future__ import annotations

import difflib
import json
import os
import posixpath
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol

from .report import autoresearch_report_sha256
from .schema import sha256_json
from ..claim_gate import (
    ClaimGate,
    ClaimGateError,
    ClaimLevel,
    EvidenceResolver,
    LedgerVerificationResolver,
    TrustedExternalAuthorities,
    TrustedVerifierAttestors,
)
from ..policy_overlay import (
    POLICY_OVERLAY_PATH,
    PolicyOverlayError,
    assert_repo_local_path,
    commit_staged_repo_file_no_follow,
    create_repo_file_no_follow,
    list_repo_directory_no_follow,
    normalise_overlay_target,
    read_repo_file_no_follow,
    remove_repo_file_no_follow,
    repo_root_lock_no_follow,
)


POLICY_PROPOSAL_SCHEMA_VERSION = "supervisor-autoresearch-policy-proposal/v1"
POLICY_APPROVAL_SCHEMA_VERSION = "supervisor-autoresearch-policy-approval/v1"
POLICY_DENIAL_SCHEMA_VERSION = "supervisor-autoresearch-policy-denial/v1"
POLICY_ROLLBACK_SCHEMA_VERSION = "supervisor-autoresearch-policy-rollback/v1"
POLICY_DERIVATION_SCHEMA_VERSION = "supervisor-autoresearch-policy-derivation/v1"
POLICY_TRANSACTION_SCHEMA_VERSION = (
    "supervisor-autoresearch-policy-transaction/v1"
)
POLICY_TRANSACTION_STATE_SCHEMA_VERSION = (
    "supervisor-autoresearch-policy-transaction-state/v1"
)
POLICY_TRANSACTION_ROOT = ".handoff/policy-transactions"
REPLAY_CORPUS_EVALUATOR_REF = "supervisor/autoresearch/evaluators/replay_corpus.py"
BENCHMARK_PROMOTION_EXPERIMENT_ID = "auto-evolve-benchmark-promotion"
BENCHMARK_PROMOTION_METRIC_NAME = "benchmark_evidence_conversion"
RESERVED_OPERATOR_IDENTITIES = frozenset({
    "codex-supervisor-axi",
    "codex-supervisor",
    "autoresearch",
    "auto",
    "automated",
    "system",
})


class EventWriter(Protocol):
    def write_event(self, *, run_id: str, source: str, kind: str, payload: dict[str, Any]) -> int:
        ...


class EventJournal(EventWriter, Protocol):
    def read_events_since(
        self,
        run_id: str,
        after_event_id: int | None = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    def verify_event_ledger_structure(self, run_id: str) -> Any:
        ...


class PolicyEvolutionError(PolicyOverlayError):
    """Raised when an operator-approved policy change cannot be applied safely."""


@dataclass(frozen=True)
class PolicyClaimAuthority:
    """Trust-owned evidence context for policy proposal derivation."""

    evidence_bundle: Mapping[str, Any]
    evidence_root: str | Path | None = None
    evidence_resolver: EvidenceResolver | None = None
    ledger_verification_resolver: LedgerVerificationResolver | None = None
    trusted_verifier_attestors: TrustedVerifierAttestors | None = None
    grade_authority: Any | None = None
    trusted_external_authorities: TrustedExternalAuthorities | None = None

    def derive_report(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """Bind a report to the exact authorities used for policy validation."""
        derived = ClaimGate.derive_report(
            report,
            self.evidence_bundle,
            evidence_root=self.evidence_root,
            evidence_resolver=self.evidence_resolver,
            ledger_verification_resolver=(
                self.ledger_verification_resolver
            ),
            grade_authority=self.grade_authority,
            trusted_verifier_attestors=self.trusted_verifier_attestors,
            trusted_external_authorities=(
                self.trusted_external_authorities
            ),
        )
        derived["report_sha256"] = autoresearch_report_sha256(derived)
        return derived

    def validation_kwargs(self) -> dict[str, Any]:
        return {
            "claim_evidence_bundle": self.evidence_bundle,
            "claim_evidence_root": self.evidence_root,
            "claim_evidence_resolver": self.evidence_resolver,
            "ledger_verification_resolver": self.ledger_verification_resolver,
            "grade_authority": self.grade_authority,
            "trusted_verifier_attestors": self.trusted_verifier_attestors,
            "trusted_external_authorities": self.trusted_external_authorities,
        }


def ensure_recorded_policy_report(
    report: Mapping[str, Any],
    *,
    state: EventJournal,
    run_id: str,
) -> int:
    """Idempotently bind one canonical AutoResearch report to a run ledger."""
    _require_event_journal(state)
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise PolicyEvolutionError(
            "run_id is required to record policy report authority"
        )
    report_hash_error = _report_hash_error(report)
    if report_hash_error is not None:
        raise PolicyEvolutionError(report_hash_error)
    claim_gate = report.get("claim_gate")
    if not isinstance(claim_gate, Mapping):
        raise PolicyEvolutionError(
            "report ClaimGate receipt is required for policy derivation"
        )
    ledger_error = _ledger_structure_error(
        state,
        run_id=normalized_run_id,
    )
    if ledger_error is not None:
        raise PolicyEvolutionError(ledger_error)
    report_events = [
        event
        for event in _read_all_run_events(
            state,
            run_id=normalized_run_id,
        )
        if event.get("kind") == "autoresearch_report_emitted"
    ]
    if report_events:
        authorization, authorization_error = (
            _recorded_report_authorization(
                report,
                state=state,
                run_id=normalized_run_id,
            )
        )
        if authorization_error is not None:
            raise PolicyEvolutionError(authorization_error)
        return int(authorization["event_id"])
    return state.write_event(
        run_id=normalized_run_id,
        source="autoresearch",
        kind="autoresearch_report_emitted",
        payload={
            "schema_version": "supervisor-autoresearch/v1",
            "report_sha256": str(report["report_sha256"]),
            "claim_gate": dict(claim_gate),
        },
    )


def create_policy_evolution_proposals(
    report: Mapping[str, Any],
    *,
    repo_root: str | Path,
    candidate_changes: Mapping[str, str],
    affected_gates: tuple[str, ...] | list[str],
    state: EventJournal | None = None,
    run_id: str | None = None,
    claim_evidence_bundle: Mapping[str, Any] | None = None,
    claim_evidence_root: str | Path | None = None,
    claim_evidence_resolver: EvidenceResolver | None = None,
    ledger_verification_resolver: LedgerVerificationResolver | None = None,
    grade_authority: Any | None = None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None = None,
    trusted_external_authorities: TrustedExternalAuthorities | None = None,
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
    if _report_applyability_error(
        report,
        claim_evidence_bundle=claim_evidence_bundle,
        claim_evidence_root=claim_evidence_root,
        claim_evidence_resolver=claim_evidence_resolver,
        ledger_verification_resolver=ledger_verification_resolver,
        grade_authority=grade_authority,
        trusted_verifier_attestors=trusted_verifier_attestors,
        trusted_external_authorities=trusted_external_authorities,
    ) is not None:
        return []
    report_authorization, report_authorization_error = (
        _recorded_report_authorization(
            report,
            state=state,
            run_id=run_id,
        )
    )
    if report_authorization_error is not None:
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
            report_authorization=report_authorization,
        )
        proposals.append(proposal)
        assert state is not None
        assert run_id
        proposal_event_id = _journal_policy_proposal_created(
            state,
            run_id=run_id,
            proposal=proposal,
        )
        proposal["proposal_event_id"] = proposal_event_id
    return proposals


def derive_policy_evolution_proposals_from_report(
    report: Mapping[str, Any],
    *,
    repo_root: str | Path,
    affected_gates: tuple[str, ...] | list[str],
    state: EventJournal | None = None,
    run_id: str | None = None,
    claim_evidence_bundle: Mapping[str, Any] | None = None,
    claim_evidence_root: str | Path | None = None,
    claim_evidence_resolver: EvidenceResolver | None = None,
    ledger_verification_resolver: LedgerVerificationResolver | None = None,
    grade_authority: Any | None = None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None = None,
    trusted_external_authorities: TrustedExternalAuthorities | None = None,
) -> list[dict[str, Any]]:
    """Draft overlay proposals directly from accepted AutoResearch report records.

    This is the policy-evolution public boundary for the auto loop: it derives
    the overlay candidate from report evidence instead of accepting a
    caller-authored `candidate_changes` mapping.
    """
    repo_root_path = Path(repo_root).expanduser().resolve()
    gates = tuple(str(gate) for gate in affected_gates)
    records = report.get("records") if isinstance(report.get("records"), list) else []
    report_applyability_error = _report_applyability_error(
        report,
        claim_evidence_bundle=claim_evidence_bundle,
        claim_evidence_root=claim_evidence_root,
        claim_evidence_resolver=claim_evidence_resolver,
        ledger_verification_resolver=ledger_verification_resolver,
        grade_authority=grade_authority,
        trusted_verifier_attestors=trusted_verifier_attestors,
        trusted_external_authorities=trusted_external_authorities,
    )
    if report_applyability_error is not None:
        for record in records:
            if isinstance(record, Mapping):
                _write_derivation_skipped(
                    state=state,
                    run_id=run_id,
                    report=report,
                    record=record,
                    reason=report_applyability_error,
                )
        return []
    report_authorization, report_authorization_error = (
        _recorded_report_authorization(
            report,
            state=state,
            run_id=run_id,
        )
    )
    if report_authorization_error is not None:
        for record in records:
            if isinstance(record, Mapping):
                _write_derivation_skipped(
                    state=state,
                    run_id=run_id,
                    report=report,
                    record=record,
                    reason=report_authorization_error,
                )
        return []
    proposals: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        applyability_error = _record_applyability_error(record)
        if applyability_error is not None:
            if _should_record_applyability_skip(applyability_error):
                _write_derivation_skipped(
                    state=state,
                    run_id=run_id,
                    report=report,
                    record=record,
                    reason=applyability_error,
                )
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
                report_authorization=report_authorization,
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
        assert state is not None
        assert run_id
        proposal_event_id = _journal_policy_proposal_created(
            state,
            run_id=run_id,
            proposal=proposal,
        )
        proposal["proposal_event_id"] = proposal_event_id
    return proposals


def report_contains_derivable_policy_record(
    report: Mapping[str, Any],
    *,
    repo_root: str | Path,
    state: EventJournal | None = None,
    run_id: str | None = None,
    claim_evidence_bundle: Mapping[str, Any] | None = None,
    claim_evidence_root: str | Path | None = None,
    claim_evidence_resolver: EvidenceResolver | None = None,
    ledger_verification_resolver: LedgerVerificationResolver | None = None,
    grade_authority: Any | None = None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None = None,
    trusted_external_authorities: TrustedExternalAuthorities | None = None,
) -> bool:
    repo_root_path = Path(repo_root).expanduser().resolve()
    if _report_applyability_error(
        report,
        claim_evidence_bundle=claim_evidence_bundle,
        claim_evidence_root=claim_evidence_root,
        claim_evidence_resolver=claim_evidence_resolver,
        ledger_verification_resolver=ledger_verification_resolver,
        grade_authority=grade_authority,
        trusted_verifier_attestors=trusted_verifier_attestors,
        trusted_external_authorities=trusted_external_authorities,
    ) is not None:
        return False
    _report_authorization, report_authorization_error = (
        _recorded_report_authorization(
            report,
            state=state,
            run_id=run_id,
        )
    )
    if report_authorization_error is not None:
        return False
    records = report.get("records") if isinstance(report.get("records"), list) else []
    for record in records:
        if not isinstance(record, Mapping) or _record_applyability_error(record) is not None:
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
    proposal: Mapping[str, Any] | None = None,
    *,
    state: EventJournal,
    run_id: str,
    proposal_event_id: int | None = None,
    repo_root: str | Path,
    approver: str,
    approval_channel: str,
    rollback_root: str | Path = ".handoff/policy-rollbacks",
) -> dict[str, Any]:
    """Audit an approval intent before publishing the exact candidate bytes."""
    _require_operator(approver=approver, approval_channel=approval_channel)
    _require_event_journal(state)
    proposal = _recorded_policy_proposal(
        state=state,
        run_id=run_id,
        proposal=proposal,
        proposal_event_id=proposal_event_id,
    )
    repo_root_path = Path(repo_root).expanduser().resolve()
    rollback_root_rel = _normalise_relative_path(str(rollback_root), repo_root=repo_root_path)
    proposal_id = str(proposal.get("proposal_id") or "")
    if not proposal_id:
        raise PolicyEvolutionError("proposal_id is required")
    with repo_root_lock_no_follow(
        repo_root_path,
        label="policy transaction",
    ):
        _recover_policy_transactions_locked(state=state, repo_root=repo_root_path)
        _safe_repo_path(
            repo_root_path / rollback_root_rel,
            repo_root=repo_root_path,
            label="policy rollback root",
        )
        prepared_changes: list[dict[str, Any]] = []
        seen_targets: set[str] = set()
        for index, change in enumerate(_proposal_changes(proposal)):
            target_rel = _normalise_relative_path(
                str(change["target_path"]),
                repo_root=repo_root_path,
            )
            _require_policy_overlay_target(target_rel, repo_root=repo_root_path)
            if target_rel in seen_targets:
                raise PolicyEvolutionError(
                    f"proposal contains duplicate target: {target_rel}"
                )
            seen_targets.add(target_rel)
            candidate_rel = _normalise_relative_path(
                str(change["candidate_ref"]),
                repo_root=repo_root_path,
            )
            before_hash = str(change["before_hash"])
            after_hash = str(change["after_hash"])
            current = _read_repo_bytes(
                target_rel,
                repo_root=repo_root_path,
                label="policy overlay target",
                missing_ok=True,
            )
            target_existed = current is not None
            current_bytes = current or b""
            current_hash = _sha256_bytes(current_bytes)
            if current_hash != before_hash:
                raise PolicyEvolutionError(
                    f"current artifact hash mismatch for {target_rel}: "
                    f"expected {before_hash}, observed {current_hash}"
                )
            candidate_bytes = _read_repo_bytes(
                candidate_rel,
                repo_root=repo_root_path,
                label="policy candidate artifact",
                missing_ok=True,
            )
            if candidate_bytes is None:
                raise PolicyEvolutionError(
                    f"candidate artifact missing: {candidate_rel}"
                )
            candidate_hash = _sha256_bytes(candidate_bytes)
            if candidate_hash != after_hash:
                raise PolicyEvolutionError(
                    f"candidate artifact hash mismatch for {candidate_rel}: "
                    f"expected {after_hash}, observed {candidate_hash}"
                )
            prepared_changes.append({
                "index": index,
                "target_rel": target_rel,
                "candidate_rel": candidate_rel,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "before_exists": target_existed,
                "after_exists": True,
                "before_bytes": current_bytes,
                "after_bytes": candidate_bytes,
            })

        transaction_id = _fresh_policy_transaction_id(
            _policy_transaction_id(
                operation="approval",
                run_id=run_id,
                proposal_id=proposal_id,
                approver=approver,
                approval_channel=approval_channel,
                reason="",
                changes=prepared_changes,
            ),
            repo_root=repo_root_path,
        )
        rollback_files: list[dict[str, Any]] = []
        applied_changes: list[dict[str, Any]] = []
        for change in prepared_changes:
            target_rel = change["target_rel"]
            before_hash = change["before_hash"]
            after_hash = change["after_hash"]
            backup_rel = _rollback_backup_ref(
                rollback_root_rel=rollback_root_rel,
                proposal_id=proposal_id,
                target_path=target_rel,
            )
            change["backup_ref"] = backup_rel
            change["stage_ref"] = _transaction_stage_ref(
                target_rel=target_rel,
                transaction_id=transaction_id,
                index=int(change["index"]),
            )
            rollback_files.append({
                "target_path": target_rel,
                "backup_ref": backup_rel,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "before_exists": bool(change["before_exists"]),
                "after_exists": True,
            })
            applied_changes.append({
                "target_path": target_rel,
                "candidate_ref": change["candidate_rel"],
                "before_hash": before_hash,
                "after_hash": after_hash,
                "before_exists": bool(change["before_exists"]),
                "after_exists": True,
            })

        rollback_pointer = {
            "schema_version": POLICY_ROLLBACK_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "files": rollback_files,
        }
        first_change = applied_changes[0]
        payload_base = {
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
        manifest, payload = _build_policy_transaction_manifest(
            operation="approval",
            transaction_id=transaction_id,
            run_id=run_id,
            proposal_id=proposal_id,
            audit_kind="autoresearch_policy_proposal_approved",
            payload_base=payload_base,
            changes=prepared_changes,
        )
        _prepare_policy_transaction(
            manifest=manifest,
            changes=prepared_changes,
            repo_root=repo_root_path,
        )
        _policy_transaction_fault("prepare", manifest)
        event_id = _ensure_transaction_audit(
            state=state,
            manifest=manifest,
            payload=payload,
        )
        _append_transaction_state(
            manifest,
            state_name="audit_committed",
            repo_root=repo_root_path,
            details={"event_id": event_id},
        )
        _policy_transaction_fault("audit", manifest)
        _commit_policy_transaction(
            manifest=manifest,
            repo_root=repo_root_path,
        )
        _append_transaction_state(
            manifest,
            state_name="filesystem_committed",
            repo_root=repo_root_path,
            details={},
        )
        _policy_transaction_fault("write", manifest)
        _append_transaction_state(
            manifest,
            state_name="finalized",
            repo_root=repo_root_path,
            details={},
        )
        _policy_transaction_fault("finalize", manifest)
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
    state: EventJournal,
    run_id: str,
    repo_root: str | Path,
    approver: str,
    approval_channel: str,
    reason: str = "",
) -> dict[str, Any]:
    """Audit a rollback intent before restoring the recorded prior bytes."""
    _require_operator(approver=approver, approval_channel=approval_channel)
    _require_event_journal(state)
    repo_root_path = Path(repo_root).expanduser().resolve()
    proposal_id = str(rollback_pointer.get("proposal_id") or "")
    with repo_root_lock_no_follow(
        repo_root_path,
        label="policy transaction",
    ):
        _recover_policy_transactions_locked(state=state, repo_root=repo_root_path)
        files = (
            rollback_pointer.get("files")
            if isinstance(rollback_pointer.get("files"), list)
            else []
        )
        if not files:
            raise PolicyEvolutionError("rollback pointer has no files")
        prepared_restores: list[dict[str, Any]] = []
        seen_targets: set[str] = set()
        for index, item in enumerate(files):
            if not isinstance(item, Mapping):
                raise PolicyEvolutionError(
                    "rollback file entry must be an object"
                )
            target_rel = _normalise_relative_path(
                str(item.get("target_path") or ""),
                repo_root=repo_root_path,
            )
            _require_policy_overlay_target(target_rel, repo_root=repo_root_path)
            if target_rel in seen_targets:
                raise PolicyEvolutionError(
                    f"rollback pointer contains duplicate target: {target_rel}"
                )
            seen_targets.add(target_rel)
            backup_rel = _normalise_relative_path(
                str(item.get("backup_ref") or ""),
                repo_root=repo_root_path,
            )
            before_hash = str(item.get("before_hash") or "")
            after_hash = str(item.get("after_hash") or "")
            before_exists = bool(item.get("before_exists", True))
            after_exists = bool(item.get("after_exists", True))
            backup_bytes = _read_repo_bytes(
                backup_rel,
                repo_root=repo_root_path,
                label="policy rollback backup",
                missing_ok=True,
            )
            if backup_bytes is None:
                raise PolicyEvolutionError(
                    f"rollback backup missing: {backup_rel}"
                )
            observed_hash = _sha256_bytes(backup_bytes)
            if observed_hash != before_hash:
                raise PolicyEvolutionError(
                    f"rollback backup hash mismatch for {backup_rel}: "
                    f"expected {before_hash}, observed {observed_hash}"
                )
            current = _read_repo_bytes(
                target_rel,
                repo_root=repo_root_path,
                label="policy overlay target",
                missing_ok=True,
            )
            current_exists = current is not None
            current_hash = _sha256_bytes(current or b"")
            if current_exists != after_exists or current_hash != after_hash:
                raise PolicyEvolutionError(
                    f"rollback target compare-and-set mismatch for "
                    f"{target_rel}: expected exists={after_exists} "
                    f"hash={after_hash}, observed exists={current_exists} "
                    f"hash={current_hash}"
                )
            prepared_restores.append({
                "index": index,
                "target_rel": target_rel,
                "backup_ref": backup_rel,
                "before_hash": after_hash,
                "after_hash": before_hash,
                "before_exists": after_exists,
                "after_exists": before_exists,
                "before_bytes": current or b"",
                "after_bytes": backup_bytes,
            })

        transaction_id = _fresh_policy_transaction_id(
            _policy_transaction_id(
                operation="rollback",
                run_id=run_id,
                proposal_id=proposal_id,
                approver=approver,
                approval_channel=approval_channel,
                reason=str(reason or "").strip(),
                changes=prepared_restores,
            ),
            repo_root=repo_root_path,
        )
        restored: list[dict[str, Any]] = []
        for prepared in prepared_restores:
            prepared["stage_ref"] = (
                _transaction_stage_ref(
                    target_rel=str(prepared["target_rel"]),
                    transaction_id=transaction_id,
                    index=int(prepared["index"]),
                )
                if prepared["after_exists"]
                else None
            )
            restored.append({
                "target_path": str(prepared["target_rel"]),
                "restored_hash": str(prepared["after_hash"]),
                "expected_hash": str(prepared["after_hash"]),
                "restored_exists": bool(prepared["after_exists"]),
            })

        payload_base = {
            "schema_version": POLICY_ROLLBACK_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "status": "rolled_back",
            "approver": str(approver),
            "approval_channel": str(approval_channel),
            "reason": str(reason or "").strip(),
            "restored": restored,
            "rollback_pointer": dict(rollback_pointer),
            **_authority_invariants(operator_approved=True),
        }
        manifest, payload = _build_policy_transaction_manifest(
            operation="rollback",
            transaction_id=transaction_id,
            run_id=run_id,
            proposal_id=proposal_id,
            audit_kind="autoresearch_policy_proposal_rolled_back",
            payload_base=payload_base,
            changes=prepared_restores,
        )
        _prepare_policy_transaction(
            manifest=manifest,
            changes=prepared_restores,
            repo_root=repo_root_path,
        )
        _policy_transaction_fault("prepare", manifest)
        event_id = _ensure_transaction_audit(
            state=state,
            manifest=manifest,
            payload=payload,
        )
        _append_transaction_state(
            manifest,
            state_name="audit_committed",
            repo_root=repo_root_path,
            details={"event_id": event_id},
        )
        _policy_transaction_fault("audit", manifest)
        _commit_policy_transaction(
            manifest=manifest,
            repo_root=repo_root_path,
        )
        _append_transaction_state(
            manifest,
            state_name="filesystem_committed",
            repo_root=repo_root_path,
            details={},
        )
        _policy_transaction_fault("write", manifest)
        _append_transaction_state(
            manifest,
            state_name="finalized",
            repo_root=repo_root_path,
            details={},
        )
        _policy_transaction_fault("finalize", manifest)
        return payload


def recover_policy_transactions(
    *,
    state: EventJournal,
    repo_root: str | Path,
) -> list[dict[str, Any]]:
    """Deterministically finish or abort every durable policy transaction."""
    _require_event_journal(state)
    repo_root_path = Path(repo_root).expanduser().resolve()
    with repo_root_lock_no_follow(
        repo_root_path,
        label="policy transaction",
    ):
        return _recover_policy_transactions_locked(
            state=state,
            repo_root=repo_root_path,
        )


def _build_policy_proposal(
    *,
    report: Mapping[str, Any],
    record: Mapping[str, Any],
    repo_root: Path,
    candidate_changes: tuple[tuple[str, str], ...],
    affected_gates: tuple[str, ...],
    report_authorization: Mapping[str, Any],
) -> dict[str, Any]:
    changed_files = {str(path) for path in record.get("changed_files", ())}
    artifact_hashes = (
        record.get("artifact_hashes")
        if isinstance(record.get("artifact_hashes"), Mapping)
        else {}
    )
    changes: list[dict[str, Any]] = []
    for target_path, candidate_ref in candidate_changes:
        target_rel = _normalise_relative_path(target_path, repo_root=repo_root)
        _require_policy_overlay_target(target_rel, repo_root=repo_root)
        candidate_rel = _normalise_relative_path(candidate_ref, repo_root=repo_root)
        if candidate_rel not in changed_files:
            raise PolicyEvolutionError(
                f"candidate artifact {candidate_rel} is not listed in accepted attempt changed_files"
            )
        before_bytes = _read_repo_bytes(
            target_rel,
            repo_root=repo_root,
            label="policy overlay target",
            missing_ok=True,
        )
        after_bytes = _read_repo_bytes(
            candidate_rel,
            repo_root=repo_root,
            label="policy candidate artifact",
            missing_ok=True,
        )
        if after_bytes is None:
            raise PolicyEvolutionError(f"candidate artifact missing: {candidate_rel}")
        authorized_after_hash = str(
            artifact_hashes.get(candidate_rel) or ""
        ).strip().lower()
        if not _is_sha256(authorized_after_hash):
            raise PolicyEvolutionError(
                "accepted report must bind the candidate artifact hash for "
                f"{candidate_rel}"
            )
        observed_after_hash = _sha256_bytes(after_bytes)
        if observed_after_hash != authorized_after_hash:
            raise PolicyEvolutionError(
                "candidate artifact does not match the authorized artifact "
                f"hash for {candidate_rel}: expected "
                f"{authorized_after_hash}, observed {observed_after_hash}"
            )
        before_bytes = before_bytes or b""
        before_text = before_bytes.decode("utf-8")
        after_text = after_bytes.decode("utf-8")
        changes.append({
            "target_path": target_rel,
            "candidate_ref": candidate_rel,
            "artifact_kind": _artifact_kind(target_rel),
            "before_hash": _sha256_bytes(before_bytes),
            "after_hash": authorized_after_hash,
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
        "claim_authority": _claim_authority_binding(
            report,
            report_authorization=report_authorization,
        ),
        "changes": changes,
        **_authority_invariants(operator_approved=False),
    }
    proposal["proposal_sha256"] = sha256_json({
        key: value for key, value in proposal.items() if key != "proposal_sha256"
    })
    return proposal


def _claim_authority_binding(
    report: Mapping[str, Any],
    *,
    report_authorization: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = (
        report.get("claim_gate")
        if isinstance(report.get("claim_gate"), Mapping)
        else {}
    )
    return {
        "schema_version": "supervisor-policy-claim-authority/v1",
        "report_sha256": str(report.get("report_sha256") or ""),
        "claim_gate_schema_version": str(
            receipt.get("schema_version") or ""
        ),
        "max_claim_level": receipt.get("max_claim_level"),
        "evidence_bundle_sha256": str(
            receipt.get("evidence_bundle_sha256") or ""
        ),
        "report_event_id": int(
            report_authorization.get("event_id") or 0
        ),
        "report_event_hash": str(
            report_authorization.get("event_hash") or ""
        ),
    }


def _record_is_applyable(record: Mapping[str, Any]) -> bool:
    return _record_applyability_error(record) is None


def _report_applyability_error(
    report: Mapping[str, Any],
    *,
    claim_evidence_bundle: Mapping[str, Any] | None,
    claim_evidence_root: str | Path | None,
    claim_evidence_resolver: EvidenceResolver | None,
    ledger_verification_resolver: LedgerVerificationResolver | None,
    grade_authority: Any | None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None,
    trusted_external_authorities: TrustedExternalAuthorities | None,
) -> str | None:
    report_hash_error = _report_hash_error(report)
    if report_hash_error is not None:
        return report_hash_error
    if report.get("metric_applyable") is False:
        return "report metric_applyable must not be false for policy derivation"
    authority_error = _claim_gate_report_authority_error(
        report,
        claim_evidence_bundle=claim_evidence_bundle,
        claim_evidence_root=claim_evidence_root,
        claim_evidence_resolver=claim_evidence_resolver,
        ledger_verification_resolver=ledger_verification_resolver,
        grade_authority=grade_authority,
        trusted_verifier_attestors=trusted_verifier_attestors,
        trusted_external_authorities=trusted_external_authorities,
    )
    if authority_error is not None:
        return authority_error
    gaming_flags = list(report.get("gaming_flags") or [])
    if gaming_flags:
        return "report gaming flags present: " + ", ".join(str(flag) for flag in gaming_flags)
    return None


def _report_hash_error(report: Mapping[str, Any]) -> str | None:
    declared = str(report.get("report_sha256") or "").strip().lower()
    if not declared:
        return "report_sha256 is required for policy derivation"
    if declared != autoresearch_report_sha256(report):
        return (
            "report_sha256 does not match canonical report contents"
        )
    return None


def _should_record_applyability_skip(reason: str) -> bool:
    return reason.startswith((
        "candidate artifact",
        "determinism ",
        "evaluator-quality ",
        "noop ",
        "harmful ",
        "known-good ",
    ))


def _record_applyability_error(record: Mapping[str, Any]) -> str | None:
    if _record_is_benchmark_promotion(record):
        return "benchmark promotion records are operator-facing only"
    if _record_uses_replay_corpus_evaluator(record):
        return "replay-corpus evaluator is not an adoption signal"
    if str(record.get("validation_status") or "") != "accepted":
        return "accepted validation status is required for policy derivation"
    if record.get("metric_applyable") is False:
        return "metric_applyable must not be false for policy derivation"
    gaming_flags = list(record.get("gaming_flags") or [])
    if gaming_flags:
        return "gaming flags present: " + ", ".join(str(flag) for flag in gaming_flags)
    if str(record.get("metric_source") or "") != "evaluator_execution":
        return "metric source must be evaluator_execution"
    if not record.get("evaluator_run_ref"):
        return "evaluator_run_ref is required for policy derivation"
    if not record.get("evaluator_run_hash"):
        return "evaluator_run_hash is required for policy derivation"
    if record.get("default_change_allowed") is not False:
        return "default_change_allowed must remain false"
    if record.get("policy_mutated") is not False:
        return "policy_mutated must remain false"
    if record.get("gate_advanced") is not False:
        return "gate_advanced must remain false"
    return _record_quality_control_error(record)


def _claim_gate_report_authority_error(
    report: Mapping[str, Any],
    *,
    claim_evidence_bundle: Mapping[str, Any] | None,
    claim_evidence_root: str | Path | None,
    claim_evidence_resolver: EvidenceResolver | None,
    ledger_verification_resolver: LedgerVerificationResolver | None,
    grade_authority: Any | None,
    trusted_verifier_attestors: TrustedVerifierAttestors | None,
    trusted_external_authorities: TrustedExternalAuthorities | None,
) -> str | None:
    if not isinstance(claim_evidence_bundle, Mapping):
        return (
            "report ClaimGate evidence bundle is required for "
            "policy derivation"
        )
    try:
        level = ClaimGate.validate_derived_report(
            report,
            claim_evidence_bundle,
            evidence_root=claim_evidence_root,
            evidence_resolver=claim_evidence_resolver,
            ledger_verification_resolver=ledger_verification_resolver,
            grade_authority=grade_authority,
            trusted_verifier_attestors=trusted_verifier_attestors,
            trusted_external_authorities=trusted_external_authorities,
        )
    except ClaimGateError as exc:
        return f"report ClaimGate authority validation failed: {exc}"
    if (
        level is None
        or tuple(ClaimLevel).index(level)
        < tuple(ClaimLevel).index(ClaimLevel.L3)
    ):
        return (
            "report ClaimGate authority must support at least L3 for "
            "policy derivation"
        )
    return None


def _record_is_benchmark_promotion(record: Mapping[str, Any]) -> bool:
    return (
        str(record.get("experiment_id") or "") == BENCHMARK_PROMOTION_EXPERIMENT_ID
        or str(record.get("metric_name") or "") == BENCHMARK_PROMOTION_METRIC_NAME
        or bool(record.get("benchmark_report_sha256"))
        or record.get("policy_derivation_allowed") is False
    )


def _record_uses_replay_corpus_evaluator(record: Mapping[str, Any]) -> bool:
    evaluator_ref = _normalise_evaluator_ref(record.get("evaluator_ref"))
    return evaluator_ref == REPLAY_CORPUS_EVALUATOR_REF or evaluator_ref.endswith(
        "/" + REPLAY_CORPUS_EVALUATOR_REF
    )


def _normalise_evaluator_ref(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return posixpath.normpath(raw).removeprefix("./")


def _record_quality_control_error(record: Mapping[str, Any]) -> str | None:
    quality = record.get("evaluator_quality")
    if not isinstance(quality, Mapping):
        return "evaluator-quality controls are required for policy derivation"
    if not _quality_is_supervisor_generated(quality):
        return "evaluator-quality controls must be supervisor-generated runtime-native evidence"
    if quality.get("candidate_affects_evaluated_path") is not True:
        return "candidate artifact must affect the evaluated path"
    determinism = quality.get("determinism")
    if not isinstance(determinism, Mapping):
        return "determinism requires repeated evaluator execution output hashes"
    if not (
        str(determinism.get("evidence_grade") or "") == "runtime_native"
        and str(determinism.get("supervisor_runtime_origin") or "") == "run_evaluator_quality_controls"
    ):
        return "determinism requires supervisor-generated runtime-native evidence"
    output_hashes = tuple(str(value) for value in determinism.get("output_hashes") or ())
    if determinism.get("source") != "repeated_execution" or len(output_hashes) < 2:
        return "determinism requires repeated evaluator execution output hashes"
    if len(set(output_hashes)) != 1:
        return "determinism repeated output hashes must match"
    controls = quality.get("controls")
    if not isinstance(controls, Mapping):
        return "evaluator-quality controls are required for policy derivation"
    noop = controls.get("noop")
    harmful = controls.get("harmful")
    known_good = controls.get("known_good")
    if not isinstance(noop, Mapping):
        return "noop control is required"
    if not _quality_is_supervisor_generated(noop):
        return "noop control must be supervisor-generated runtime-native evidence"
    if _control_delta(noop) is None:
        return "noop control requires a metric delta"
    if _control_delta(noop) > 0:  # type: ignore[operator]
        return "noop control must not improve"
    if not isinstance(harmful, Mapping):
        return "harmful control is required"
    if not _quality_is_supervisor_generated(harmful):
        return "harmful control must be supervisor-generated runtime-native evidence"
    if _control_delta(harmful) is None:
        return "harmful control requires a metric delta"
    if _control_delta(harmful) > 0:  # type: ignore[operator]
        return "harmful control must regress or fail"
    if not isinstance(known_good, Mapping):
        return "known-good control is required"
    if not _quality_is_supervisor_generated(known_good):
        return "known-good control must be supervisor-generated runtime-native evidence"
    if _control_delta(known_good) is None:
        return "known-good control requires a metric delta"
    if _control_delta(known_good) <= 0:  # type: ignore[operator]
        return "known-good control must improve"
    if str(noop.get("metric_source") or "") != "evaluator_execution":
        return "noop control must come from evaluator_execution"
    if str(harmful.get("metric_source") or "") != "evaluator_execution":
        return "harmful control must come from evaluator_execution"
    if str(known_good.get("metric_source") or "") != "evaluator_execution":
        return "known-good control must come from evaluator_execution"
    return None


def _quality_is_supervisor_generated(value: Mapping[str, Any]) -> bool:
    return (
        str(value.get("source") or "") == "supervisor_control_execution"
        and str(value.get("evidence_grade") or "") == "runtime_native"
        and str(value.get("supervisor_runtime_origin") or "") == "run_evaluator_quality_controls"
    )


def _control_delta(control: Mapping[str, Any]) -> float | None:
    explicit = _optional_float(control.get("metric_delta"))
    if explicit is not None:
        return explicit
    before = _optional_float(control.get("metric_before", control.get("baseline_metric")))
    after = _optional_float(control.get("metric_after", control.get("candidate_metric")))
    if before is None or after is None:
        return None
    return after - before


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
        "evaluator_quality": _record_quality_evidence(record),
        "cost_usd": float(record.get("cost_usd") or 0.0),
        "wall_clock_s": float(record.get("wall_clock_s") or 0.0),
    }


def _record_quality_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    quality = record.get("evaluator_quality")
    payload = dict(quality) if isinstance(quality, Mapping) else {}
    controls = payload.get("controls")
    if isinstance(controls, Mapping):
        payload.setdefault(
            "control_refs",
            [kind for kind in ("noop", "harmful", "known_good") if kind in controls],
        )
    payload.setdefault(
        "verdict",
        "accepted" if _record_quality_control_error(record) is None else "rejected",
    )
    return payload


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
    normalized = str(approver or "").strip()
    if not normalized:
        raise PolicyEvolutionError("approver is required")
    if normalized.lower() in RESERVED_OPERATOR_IDENTITIES:
        raise PolicyEvolutionError("named human approver is required")
    if not str(approval_channel or "").strip():
        raise PolicyEvolutionError("approval_channel is required")


def _normalise_relative_path(value: str, *, repo_root: Path) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise PolicyEvolutionError("path is required")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            raw = Path(os.path.abspath(candidate)).relative_to(
                repo_root
            ).as_posix()
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


def _rollback_backup_ref(*, rollback_root_rel: str, proposal_id: str, target_path: str) -> str:
    proposal_slug = "".join(
        character
        if character.isalnum() or character in {"-", "_", "."}
        else "_"
        for character in str(proposal_id)
    ).strip("._")[:48]
    proposal_component = (
        f"{proposal_slug or 'proposal'}-"
        f"{sha256(str(proposal_id).encode('utf-8')).hexdigest()[:12]}"
    )
    safe_target = target_path.replace("/", "__")
    return (
        f"{rollback_root_rel.rstrip('/')}/{proposal_component}/"
        f"{safe_target}.before"
    )


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _is_sha256(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64:
        return False
    try:
        bytes.fromhex(normalized)
    except ValueError:
        return False
    return True


def _read_all_run_events(
    state: EventJournal,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = state.read_events_since(
            run_id,
            after_event_id=cursor,
            limit=1000,
        )
        if not page:
            break
        events.extend(page)
        cursor = int(page[-1]["event_id"])
        if len(page) < 1000:
            break
    return events


def _ledger_structure_error(
    state: EventJournal,
    *,
    run_id: str,
) -> str | None:
    try:
        verification = state.verify_event_ledger_structure(run_id)
    except Exception as exc:
        return f"event ledger structure verification failed: {exc}"
    if getattr(verification, "valid", None) is not True:
        return "event ledger structure is invalid"
    return None


def _recorded_report_authorization(
    report: Mapping[str, Any],
    *,
    state: EventJournal | None,
    run_id: str | None,
) -> tuple[dict[str, Any], str | None]:
    if state is None or not str(run_id or "").strip():
        return {}, "recorded report authority event is required"
    try:
        _require_event_journal(state)
    except PolicyEvolutionError as exc:
        return {}, str(exc)
    normalized_run_id = str(run_id)
    ledger_error = _ledger_structure_error(
        state,
        run_id=normalized_run_id,
    )
    if ledger_error is not None:
        return {}, ledger_error
    events = _read_all_run_events(
        state,
        run_id=normalized_run_id,
    )
    report_events = [
        event
        for event in events
        if event.get("kind") == "autoresearch_report_emitted"
    ]
    if not report_events:
        return (
            {},
            "exactly one recorded report authority event is required",
        )
    first_payload = report_events[0].get("payload")
    if any(
        event.get("payload") != first_payload
        or event.get("source") != report_events[0].get("source")
        for event in report_events[1:]
    ):
        return (
            {},
            "exactly one recorded report authority event is required",
        )
    event = report_events[0]
    payload = event.get("payload")
    if (
        event.get("source") != "autoresearch"
        or not isinstance(payload, Mapping)
        or payload.get("schema_version") != "supervisor-autoresearch/v1"
    ):
        return {}, "recorded report authority event is invalid"
    report_hash = str(report.get("report_sha256") or "").strip().lower()
    if str(payload.get("report_sha256") or "").strip().lower() != report_hash:
        return {}, "recorded report authority hash does not match report"
    report_claim_gate = report.get("claim_gate")
    event_claim_gate = payload.get("claim_gate")
    if (
        not isinstance(report_claim_gate, Mapping)
        or not isinstance(event_claim_gate, Mapping)
        or dict(event_claim_gate) != dict(report_claim_gate)
    ):
        return (
            {},
            "recorded report authority ClaimGate receipt does not match report",
        )
    event_hash = str(event.get("event_hash") or "").strip().lower()
    if not _is_sha256(event_hash):
        return {}, "recorded report authority event hash is invalid"
    return {
        "event_id": int(event["event_id"]),
        "event_hash": event_hash,
    }, None


def _recorded_identical_proposal_event_id(
    state: EventJournal,
    *,
    run_id: str,
    proposal: Mapping[str, Any],
) -> int | None:
    proposal_hash = sha256_json(dict(proposal))
    for event in _read_all_run_events(state, run_id=run_id):
        payload = event.get("payload")
        if (
            event.get("kind") == "autoresearch_policy_proposal_created"
            and event.get("source") == "autoresearch"
            and isinstance(payload, Mapping)
            and sha256_json(dict(payload)) == proposal_hash
        ):
            return int(event["event_id"])
    return None


def _journal_policy_proposal_created(
    state: EventJournal,
    *,
    run_id: str,
    proposal: Mapping[str, Any],
) -> int:
    existing_event_id = _recorded_identical_proposal_event_id(
        state,
        run_id=run_id,
        proposal=proposal,
    )
    if existing_event_id is not None:
        return existing_event_id
    write_event_once = getattr(state, "write_event_once", None)
    if callable(write_event_once):
        return int(write_event_once(
            run_id=run_id,
            source="autoresearch",
            kind="autoresearch_policy_proposal_created",
            payload=dict(proposal),
            idempotency_key=(
                "autoresearch-policy-proposal:"
                + sha256_json(dict(proposal))
            ),
        ))
    return state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=proposal,
    )


def _recorded_policy_proposal(
    *,
    state: EventJournal,
    run_id: str,
    proposal: Mapping[str, Any] | None,
    proposal_event_id: int | None,
) -> dict[str, Any]:
    event_id_value = proposal_event_id
    if event_id_value is None and isinstance(proposal, Mapping):
        raw_event_id = proposal.get("proposal_event_id")
        if raw_event_id not in (None, ""):
            event_id_value = int(raw_event_id)
    if event_id_value is None or int(event_id_value) <= 0:
        raise PolicyEvolutionError(
            "recorded proposal event id is required"
        )
    ledger_error = _ledger_structure_error(state, run_id=run_id)
    if ledger_error is not None:
        raise PolicyEvolutionError(ledger_error)
    events = _read_all_run_events(state, run_id=run_id)
    selected = [
        event
        for event in events
        if int(event.get("event_id") or 0) == int(event_id_value)
    ]
    if len(selected) != 1:
        raise PolicyEvolutionError(
            "recorded proposal event was not found in the specified run"
        )
    event = selected[0]
    payload = event.get("payload")
    if (
        event.get("source") != "autoresearch"
        or event.get("kind") != "autoresearch_policy_proposal_created"
        or not isinstance(payload, Mapping)
    ):
        raise PolicyEvolutionError("recorded proposal event is invalid")
    recorded = dict(payload)
    proposal_id = str(recorded.get("proposal_id") or "").strip()
    duplicate_events = [
        candidate
        for candidate in events
        if (
            candidate.get("kind")
            == "autoresearch_policy_proposal_created"
            and isinstance(candidate.get("payload"), Mapping)
            and str(
                candidate["payload"].get("proposal_id") or ""
            ).strip()
            == proposal_id
        )
    ]
    if not proposal_id or len(duplicate_events) != 1:
        raise PolicyEvolutionError(
            "recorded proposal event identity is missing or duplicated"
        )
    declared_hash = str(
        recorded.get("proposal_sha256") or ""
    ).strip().lower()
    body = {
        key: value
        for key, value in recorded.items()
        if key != "proposal_sha256"
    }
    if (
        not _is_sha256(declared_hash)
        or sha256_json(body) != declared_hash
    ):
        raise PolicyEvolutionError(
            "recorded proposal event hash is invalid"
        )
    if (
        recorded.get("schema_version")
        != POLICY_PROPOSAL_SCHEMA_VERSION
        or recorded.get("source")
        not in {"autoresearch", "autoresearch_deriver"}
        or recorded.get("status") not in {"proposed", "draft"}
    ):
        raise PolicyEvolutionError(
            "recorded proposal schema, source, or status is invalid"
        )
    if (
        recorded.get("requires_operator_approval") is not True
        or recorded.get("operator_approved") is not False
        or recorded.get("automatic_policy_mutation") is not False
        or recorded.get("default_change_allowed") is not False
    ):
        raise PolicyEvolutionError(
            "recorded proposal authority invariants are invalid"
        )
    claim_authority = recorded.get("claim_authority")
    if not isinstance(claim_authority, Mapping):
        raise PolicyEvolutionError(
            "recorded proposal claim authority is missing"
        )
    report_event_id = int(
        claim_authority.get("report_event_id") or 0
    )
    report_event_hash = str(
        claim_authority.get("report_event_hash") or ""
    ).strip().lower()
    report_events = [
        candidate
        for candidate in events
        if int(candidate.get("event_id") or 0) == report_event_id
    ]
    if len(report_events) != 1:
        raise PolicyEvolutionError(
            "recorded proposal report authority event is missing"
        )
    report_event = report_events[0]
    report_payload = report_event.get("payload")
    if (
        report_event_id >= int(event_id_value)
        or report_event.get("source") != "autoresearch"
        or report_event.get("kind") != "autoresearch_report_emitted"
        or str(report_event.get("event_hash") or "").strip().lower()
        != report_event_hash
        or not isinstance(report_payload, Mapping)
        or str(report_payload.get("report_sha256") or "").strip().lower()
        != str(claim_authority.get("report_sha256") or "").strip().lower()
    ):
        raise PolicyEvolutionError(
            "recorded proposal report authority binding is invalid"
        )
    report_claim_gate = report_payload.get("claim_gate")
    if (
        not isinstance(report_claim_gate, Mapping)
        or report_claim_gate.get("schema_version")
        != claim_authority.get("claim_gate_schema_version")
        or report_claim_gate.get("max_claim_level")
        != claim_authority.get("max_claim_level")
        or report_claim_gate.get("evidence_bundle_sha256")
        != claim_authority.get("evidence_bundle_sha256")
    ):
        raise PolicyEvolutionError(
            "recorded proposal ClaimGate authority binding is invalid"
        )
    if isinstance(proposal, Mapping):
        supplied = {
            key: value
            for key, value in proposal.items()
            if key != "proposal_event_id"
        }
        if supplied != recorded:
            raise PolicyEvolutionError(
                "caller proposal bytes do not match the recorded proposal event"
            )
    return recorded


def _require_policy_overlay_target(path: str, *, repo_root: Path) -> None:
    try:
        normalise_overlay_target(path, repo_root=repo_root)
    except PolicyOverlayError as exc:
        raise PolicyEvolutionError(str(exc)) from exc


def _require_event_journal(state: EventWriter) -> None:
    if (
        not callable(getattr(state, "read_events_since", None))
        or not callable(
            getattr(state, "verify_event_ledger_structure", None)
        )
    ):
        raise PolicyEvolutionError(
            "policy transactions require an append-only readable event journal"
        )


def _read_repo_bytes(
    path: str | Path,
    *,
    repo_root: Path,
    label: str,
    missing_ok: bool,
) -> bytes | None:
    try:
        return read_repo_file_no_follow(
            path,
            repo_root=repo_root,
            label=label,
            missing_ok=missing_ok,
        )
    except PolicyOverlayError as exc:
        raise PolicyEvolutionError(str(exc)) from exc


def _safe_repo_path(
    path: str | Path,
    *,
    repo_root: Path,
    label: str,
) -> Path:
    try:
        return assert_repo_local_path(
            path,
            repo_root=repo_root,
            label=label,
        )
    except PolicyOverlayError as exc:
        raise PolicyEvolutionError(str(exc)) from exc


def _create_repo_bytes(
    path: str | Path,
    data: bytes,
    *,
    repo_root: Path,
    label: str,
) -> Path:
    try:
        return create_repo_file_no_follow(
            path,
            data,
            repo_root=repo_root,
            label=label,
            exist_ok_same=True,
        )
    except PolicyOverlayError as exc:
        raise PolicyEvolutionError(str(exc)) from exc


def _policy_transaction_id(
    *,
    operation: str,
    run_id: str,
    proposal_id: str,
    approver: str,
    approval_channel: str,
    reason: str,
    changes: list[dict[str, Any]],
) -> str:
    return sha256_json({
        "schema_version": POLICY_TRANSACTION_SCHEMA_VERSION,
        "operation": operation,
        "run_id": str(run_id),
        "proposal_id": str(proposal_id),
        "approver": str(approver),
        "approval_channel": str(approval_channel),
        "reason": str(reason),
        "changes": [
            {
                "target_path": str(change["target_rel"]),
                "source_ref": str(
                    change.get("candidate_rel")
                    or change.get("backup_ref")
                    or ""
                ),
                "before_hash": str(change["before_hash"]),
                "after_hash": str(change["after_hash"]),
                "before_exists": bool(change["before_exists"]),
                "after_exists": bool(change["after_exists"]),
            }
            for change in changes
        ],
    })


def _is_policy_transaction_id(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef"
        for character in value
    )


def _transaction_dir(transaction_id: str) -> str:
    normalized = str(transaction_id).strip().lower()
    if not _is_policy_transaction_id(normalized):
        raise PolicyEvolutionError("invalid policy transaction id")
    return f"{POLICY_TRANSACTION_ROOT}/{normalized}"


def _transaction_is_settled(
    transaction_id: str,
    *,
    repo_root: Path,
) -> bool:
    transaction_dir = _transaction_dir(transaction_id)
    for state_name in ("finalized", "aborted"):
        filename = _TRANSACTION_STATE_FILENAMES[state_name]
        raw = _read_repo_bytes(
            f"{transaction_dir}/{filename}",
            repo_root=repo_root,
            label=f"policy transaction {state_name} state",
            missing_ok=True,
        )
        if raw is not None:
            return True
    return False


def _fresh_policy_transaction_id(
    base_transaction_id: str,
    *,
    repo_root: Path,
) -> str:
    transaction_id = base_transaction_id
    sequence = 0
    while _transaction_is_settled(transaction_id, repo_root=repo_root):
        sequence += 1
        transaction_id = sha256_json({
            "schema_version": POLICY_TRANSACTION_SCHEMA_VERSION,
            "base_transaction_id": base_transaction_id,
            "sequence": sequence,
        })
    return transaction_id


def _transaction_stage_ref(
    *,
    target_rel: str,
    transaction_id: str,
    index: int,
) -> str:
    target = Path(target_rel)
    filename = (
        f".policy-transaction-{transaction_id[:24]}-{int(index)}.stage"
    )
    parent = target.parent.as_posix()
    return filename if parent == "." else f"{parent}/{filename}"


def _build_policy_transaction_manifest(
    *,
    operation: str,
    transaction_id: str,
    run_id: str,
    proposal_id: str,
    audit_kind: str,
    payload_base: Mapping[str, Any],
    changes: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    transaction_dir = _transaction_dir(transaction_id)
    manifest_changes: list[dict[str, Any]] = []
    for change in changes:
        index = int(change["index"])
        manifest_changes.append({
            "index": index,
            "target_path": str(change["target_rel"]),
            "source_ref": str(
                change.get("candidate_rel")
                or change.get("backup_ref")
                or ""
            ),
            "backup_ref": str(change.get("backup_ref") or ""),
            "stage_ref": (
                str(change.get("stage_ref"))
                if change.get("stage_ref") is not None
                else None
            ),
            "before_blob_ref": (
                f"{transaction_dir}/change-{index}.before"
            ),
            "after_blob_ref": (
                f"{transaction_dir}/change-{index}.after"
            ),
            "before_hash": str(change["before_hash"]),
            "after_hash": str(change["after_hash"]),
            "before_exists": bool(change["before_exists"]),
            "after_exists": bool(change["after_exists"]),
        })
    manifest_body = {
        "schema_version": POLICY_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "operation": operation,
        "run_id": str(run_id),
        "proposal_id": str(proposal_id),
        "audit_kind": audit_kind,
        "audit_payload_base": dict(payload_base),
        "changes": manifest_changes,
    }
    manifest_hash = sha256_json(manifest_body)
    manifest = {
        **manifest_body,
        "manifest_sha256": manifest_hash,
    }
    payload = {
        **dict(payload_base),
        "transaction_protocol": POLICY_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "journal_manifest_sha256": manifest_hash,
        "audit_committed_before_filesystem": True,
        "recovery_required_until_finalized": True,
    }
    payload["event_sha256"] = sha256_json({
        key: value
        for key, value in payload.items()
        if key != "event_sha256"
    })
    return manifest, payload


def _prepare_policy_transaction(
    *,
    manifest: Mapping[str, Any],
    changes: list[dict[str, Any]],
    repo_root: Path,
) -> None:
    transaction_dir = _transaction_dir(str(manifest["transaction_id"]))
    _create_repo_bytes(
        f"{transaction_dir}/manifest.json",
        _canonical_json_bytes(manifest),
        repo_root=repo_root,
        label="policy transaction manifest",
    )
    by_index = {int(change["index"]): change for change in changes}
    for manifest_change in manifest["changes"]:
        index = int(manifest_change["index"])
        change = by_index[index]
        before_bytes = bytes(change["before_bytes"])
        after_bytes = bytes(change["after_bytes"])
        if _sha256_bytes(before_bytes) != str(manifest_change["before_hash"]):
            raise PolicyEvolutionError(
                "policy transaction before bytes do not match manifest"
            )
        if _sha256_bytes(after_bytes) != str(manifest_change["after_hash"]):
            raise PolicyEvolutionError(
                "policy transaction after bytes do not match manifest"
            )
        _create_repo_bytes(
            str(manifest_change["before_blob_ref"]),
            before_bytes,
            repo_root=repo_root,
            label="policy transaction before blob",
        )
        _create_repo_bytes(
            str(manifest_change["after_blob_ref"]),
            after_bytes,
            repo_root=repo_root,
            label="policy transaction after blob",
        )
        backup_ref = str(manifest_change.get("backup_ref") or "")
        if backup_ref and manifest.get("operation") == "approval":
            _create_repo_bytes(
                backup_ref,
                before_bytes,
                repo_root=repo_root,
                label="policy rollback backup",
            )
        stage_ref = manifest_change.get("stage_ref")
        if bool(manifest_change["after_exists"]):
            if not stage_ref:
                raise PolicyEvolutionError(
                    "policy transaction stage ref is required"
                )
            _create_repo_bytes(
                str(stage_ref),
                after_bytes,
                repo_root=repo_root,
                label="policy transaction staged artifact",
            )
    _verify_prepared_transaction_artifacts(
        manifest,
        repo_root=repo_root,
    )
    _append_transaction_state(
        manifest,
        state_name="prepared",
        repo_root=repo_root,
        details={},
    )


_TRANSACTION_STATE_FILENAMES = {
    "prepared": "10-prepared.json",
    "audit_committed": "20-audit-committed.json",
    "filesystem_committed": "30-filesystem-committed.json",
    "finalized": "40-finalized.json",
    "aborted": "90-aborted.json",
}


def _append_transaction_state(
    manifest: Mapping[str, Any],
    *,
    state_name: str,
    repo_root: Path,
    details: Mapping[str, Any],
) -> None:
    filename = _TRANSACTION_STATE_FILENAMES.get(state_name)
    if filename is None:
        raise PolicyEvolutionError(
            f"unknown policy transaction state: {state_name}"
        )
    transaction_id = str(manifest["transaction_id"])
    payload = {
        "schema_version": POLICY_TRANSACTION_STATE_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "state": state_name,
        "details": dict(details),
    }
    _create_repo_bytes(
        f"{_transaction_dir(transaction_id)}/{filename}",
        _canonical_json_bytes(payload),
        repo_root=repo_root,
        label=f"policy transaction {state_name} state",
    )


def _transaction_state_exists(
    manifest: Mapping[str, Any],
    *,
    state_name: str,
    repo_root: Path,
) -> bool:
    filename = _TRANSACTION_STATE_FILENAMES[state_name]
    transaction_id = str(manifest["transaction_id"])
    raw = _read_repo_bytes(
        f"{_transaction_dir(transaction_id)}/{filename}",
        repo_root=repo_root,
        label=f"policy transaction {state_name} state",
        missing_ok=True,
    )
    if raw is None:
        return False
    record = _decode_json_mapping(
        raw,
        label=f"policy transaction {state_name} state",
    )
    if (
        record.get("schema_version")
        != POLICY_TRANSACTION_STATE_SCHEMA_VERSION
        or record.get("transaction_id") != transaction_id
        or record.get("manifest_sha256")
        != manifest.get("manifest_sha256")
        or record.get("state") != state_name
    ):
        raise PolicyEvolutionError(
            f"policy transaction {state_name} state is invalid"
        )
    return True


def _ensure_transaction_audit(
    *,
    state: EventJournal,
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> int:
    matches = _matching_transaction_audits(state=state, manifest=manifest)
    if len(matches) > 1:
        raise PolicyEvolutionError(
            "duplicate append-only policy transaction audit events"
        )
    if matches:
        return int(matches[0]["event_id"])
    try:
        event_id = state.write_event(
            run_id=str(manifest["run_id"]),
            source="autoresearch",
            kind=str(manifest["audit_kind"]),
            payload=dict(payload),
        )
    except Exception:
        matches = _matching_transaction_audits(
            state=state,
            manifest=manifest,
        )
        if len(matches) == 1:
            return int(matches[0]["event_id"])
        raise
    matches = _matching_transaction_audits(state=state, manifest=manifest)
    if len(matches) != 1:
        raise PolicyEvolutionError(
            "policy transaction audit event was not durably observable"
        )
    if int(matches[0]["event_id"]) != int(event_id):
        raise PolicyEvolutionError(
            "policy transaction audit event id mismatch"
        )
    return int(event_id)


def _matching_transaction_audits(
    *,
    state: EventJournal,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected_payload = _transaction_payload_from_manifest(manifest)
    matches: list[dict[str, Any]] = []
    cursor = 0
    while True:
        events = state.read_events_since(
            str(manifest["run_id"]),
            after_event_id=cursor,
            limit=500,
        )
        if not events:
            break
        for event in events:
            cursor = max(cursor, int(event.get("event_id") or 0))
            if (
                event.get("source") != "autoresearch"
                or event.get("kind") != manifest.get("audit_kind")
            ):
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if payload.get("transaction_id") != manifest.get(
                "transaction_id"
            ):
                continue
            if dict(payload) != expected_payload:
                raise PolicyEvolutionError(
                    "policy transaction audit payload does not match "
                    "the durable journal"
                )
            matches.append(dict(event))
        if len(events) < 500:
            break
    return matches


def _transaction_payload_from_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        **dict(manifest["audit_payload_base"]),
        "transaction_protocol": POLICY_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": str(manifest["transaction_id"]),
        "journal_manifest_sha256": str(manifest["manifest_sha256"]),
        "audit_committed_before_filesystem": True,
        "recovery_required_until_finalized": True,
    }
    payload["event_sha256"] = sha256_json({
        key: value
        for key, value in payload.items()
        if key != "event_sha256"
    })
    return payload


def _commit_policy_transaction(
    *,
    manifest: Mapping[str, Any],
    repo_root: Path,
) -> None:
    for change in manifest["changes"]:
        target_rel = str(change["target_path"])
        stage_ref = change.get("stage_ref")
        if bool(change["after_exists"]) and stage_ref:
            staged = _read_repo_bytes(
                str(stage_ref),
                repo_root=repo_root,
                label="policy transaction staged artifact",
                missing_ok=True,
            )
            current = _read_repo_bytes(
                target_rel,
                repo_root=repo_root,
                label="policy overlay target",
                missing_ok=True,
            )
            current_exists = current is not None
            current_hash = _sha256_bytes(current or b"")
            if staged is None and not (
                current_exists is True
                and current_hash == str(change["after_hash"])
            ):
                after_bytes = _read_required_transaction_blob(
                    change,
                    blob_kind="after",
                    repo_root=repo_root,
                )
                _create_repo_bytes(
                    str(stage_ref),
                    after_bytes,
                    repo_root=repo_root,
                    label="policy transaction staged artifact",
                )
        try:
            commit_staged_repo_file_no_follow(
                target_rel,
                str(stage_ref) if stage_ref is not None else None,
                repo_root=repo_root,
                expected_hash=str(change["before_hash"]),
                expected_exists=bool(change["before_exists"]),
                desired_hash=str(change["after_hash"]),
                desired_exists=bool(change["after_exists"]),
                label="policy overlay target",
            )
        except PolicyOverlayError as exc:
            raise PolicyEvolutionError(str(exc)) from exc
    _verify_transaction_state(
        manifest,
        repo_root=repo_root,
        desired=True,
    )


def _verify_prepared_transaction_artifacts(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
) -> None:
    for change in manifest["changes"]:
        before_bytes = _read_required_transaction_blob(
            change,
            blob_kind="before",
            repo_root=repo_root,
        )
        after_bytes = _read_required_transaction_blob(
            change,
            blob_kind="after",
            repo_root=repo_root,
        )
        backup_ref = str(change.get("backup_ref") or "")
        if backup_ref and manifest.get("operation") == "approval":
            backup = _read_repo_bytes(
                backup_ref,
                repo_root=repo_root,
                label="policy rollback backup",
                missing_ok=False,
            )
            if backup != before_bytes:
                raise PolicyEvolutionError(
                    "policy rollback backup does not match transaction "
                    "before bytes"
                )
        stage_ref = change.get("stage_ref")
        if bool(change["after_exists"]):
            if not stage_ref:
                raise PolicyEvolutionError(
                    "policy transaction stage ref is required"
                )
            stage = _read_repo_bytes(
                str(stage_ref),
                repo_root=repo_root,
                label="policy transaction staged artifact",
                missing_ok=False,
            )
            if stage != after_bytes:
                raise PolicyEvolutionError(
                    "policy transaction staged artifact does not match "
                    "transaction after bytes"
                )


def _recover_policy_transactions_locked(
    *,
    state: EventJournal,
    repo_root: Path,
) -> list[dict[str, Any]]:
    try:
        entries = list_repo_directory_no_follow(
            POLICY_TRANSACTION_ROOT,
            repo_root=repo_root,
            label="policy transaction journal",
            missing_ok=True,
        )
    except PolicyOverlayError as exc:
        raise PolicyEvolutionError(str(exc)) from exc
    results: list[dict[str, Any]] = []
    for entry in entries:
        entry_name = str(entry)
        if not _is_policy_transaction_id(entry_name):
            results.append({
                "entry": entry_name,
                "status": "ignored_foreign_entry",
            })
            continue
        manifest_raw = _read_repo_bytes(
            f"{POLICY_TRANSACTION_ROOT}/{entry_name}/manifest.json",
            repo_root=repo_root,
            label="policy transaction manifest",
            missing_ok=True,
        )
        if manifest_raw is None:
            results.append({
                "transaction_id": entry_name,
                "status": "ignored_unpublished_manifest",
            })
            continue
        manifest = _load_transaction_manifest(
            transaction_id=entry_name,
            repo_root=repo_root,
        )
        transaction_id = str(manifest["transaction_id"])
        if _transaction_state_exists(
            manifest,
            state_name="finalized",
            repo_root=repo_root,
        ):
            audits = _matching_transaction_audits(
                state=state,
                manifest=manifest,
            )
            if len(audits) != 1:
                raise PolicyEvolutionError(
                    "finalized policy transaction lacks exactly one "
                    "matching audit event"
                )
            results.append({
                "transaction_id": transaction_id,
                "status": "already_finalized",
            })
            continue
        if _transaction_state_exists(
            manifest,
            state_name="aborted",
            repo_root=repo_root,
        ):
            results.append({
                "transaction_id": transaction_id,
                "status": "already_aborted",
            })
            continue

        audits = _matching_transaction_audits(
            state=state,
            manifest=manifest,
        )
        if len(audits) > 1:
            raise PolicyEvolutionError(
                "duplicate append-only policy transaction audit events"
            )
        if not audits:
            _verify_transaction_state(
                manifest,
                repo_root=repo_root,
                desired=False,
            )
            _remove_transaction_stages(
                manifest,
                repo_root=repo_root,
            )
            _append_transaction_state(
                manifest,
                state_name="aborted",
                repo_root=repo_root,
                details={"reason": "audit_not_committed"},
            )
            results.append({
                "transaction_id": transaction_id,
                "status": "aborted_before_audit",
            })
            continue

        _append_transaction_state(
            manifest,
            state_name="audit_committed",
            repo_root=repo_root,
            details={"event_id": int(audits[0]["event_id"])},
        )
        _commit_policy_transaction(
            manifest=manifest,
            repo_root=repo_root,
        )
        _append_transaction_state(
            manifest,
            state_name="filesystem_committed",
            repo_root=repo_root,
            details={},
        )
        _append_transaction_state(
            manifest,
            state_name="finalized",
            repo_root=repo_root,
            details={"recovered": True},
        )
        results.append({
            "transaction_id": transaction_id,
            "status": "recovered_finalized",
        })
    return results


def _load_transaction_manifest(
    *,
    transaction_id: str,
    repo_root: Path,
) -> dict[str, Any]:
    transaction_dir = _transaction_dir(transaction_id)
    raw = _read_repo_bytes(
        f"{transaction_dir}/manifest.json",
        repo_root=repo_root,
        label="policy transaction manifest",
        missing_ok=False,
    )
    assert raw is not None
    manifest = _decode_json_mapping(
        raw,
        label="policy transaction manifest",
    )
    manifest_hash = str(manifest.get("manifest_sha256") or "")
    body = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    if (
        manifest.get("schema_version") != POLICY_TRANSACTION_SCHEMA_VERSION
        or manifest.get("transaction_id") != transaction_id
        or sha256_json(body) != manifest_hash
        or not isinstance(manifest.get("changes"), list)
        or not isinstance(manifest.get("audit_payload_base"), Mapping)
    ):
        raise PolicyEvolutionError(
            "policy transaction manifest failed integrity validation"
        )
    return manifest


def _verify_transaction_state(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
    desired: bool,
) -> None:
    for change in manifest["changes"]:
        target = _read_repo_bytes(
            str(change["target_path"]),
            repo_root=repo_root,
            label="policy overlay target",
            missing_ok=True,
        )
        observed_exists = target is not None
        observed_hash = _sha256_bytes(target or b"")
        expected_exists = bool(
            change["after_exists"] if desired else change["before_exists"]
        )
        expected_hash = str(
            change["after_hash"] if desired else change["before_hash"]
        )
        if (
            observed_exists != expected_exists
            or observed_hash != expected_hash
        ):
            state_name = "desired" if desired else "pre-transaction"
            raise PolicyEvolutionError(
                f"policy transaction target is not in {state_name} state: "
                f"{change['target_path']}; expected exists={expected_exists} "
                f"hash={expected_hash}, observed exists={observed_exists} "
                f"hash={observed_hash}"
            )


def _remove_transaction_stages(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path,
) -> None:
    for change in manifest["changes"]:
        stage_ref = change.get("stage_ref")
        if not stage_ref:
            continue
        try:
            remove_repo_file_no_follow(
                str(stage_ref),
                repo_root=repo_root,
                label="policy transaction staged artifact",
                missing_ok=True,
            )
        except PolicyOverlayError as exc:
            raise PolicyEvolutionError(str(exc)) from exc


def _read_required_transaction_blob(
    change: Mapping[str, Any],
    *,
    blob_kind: str,
    repo_root: Path,
) -> bytes:
    ref = str(change[f"{blob_kind}_blob_ref"])
    data = _read_repo_bytes(
        ref,
        repo_root=repo_root,
        label=f"policy transaction {blob_kind} blob",
        missing_ok=False,
    )
    assert data is not None
    expected_hash = str(change[f"{blob_kind}_hash"])
    observed_hash = _sha256_bytes(data)
    if observed_hash != expected_hash:
        raise PolicyEvolutionError(
            f"policy transaction {blob_kind} blob hash mismatch: "
            f"expected {expected_hash}, observed {observed_hash}"
        )
    return data


def _decode_json_mapping(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PolicyEvolutionError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise PolicyEvolutionError(f"{label} must be a JSON object")
    return decoded


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _policy_transaction_fault(
    boundary: str,
    manifest: Mapping[str, Any],
) -> None:
    """Test seam for simulating process death after a durable boundary."""
    del boundary, manifest
