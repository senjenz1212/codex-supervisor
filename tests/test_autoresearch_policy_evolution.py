from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

import supervisor.autoresearch.policy_evolution as policy_evolution_module
from supervisor.autoresearch.orchestrator import run_autoresearch_fixture
from supervisor.autoresearch.policy_evolution import (
    PolicyClaimAuthority,
    PolicyEvolutionError,
    approve_policy_proposal,
    create_policy_evolution_proposals,
    deny_policy_proposal,
    derive_policy_evolution_proposals_from_report,
    recover_policy_transactions,
    report_contains_derivable_policy_record,
    rollback_policy_proposal,
)
from supervisor.autoresearch.report import (
    autoresearch_report_sha256,
    build_autoresearch_report,
)
from supervisor.autoresearch.schema import (
    AutoresearchAttempt,
    AutoresearchExperiment,
)
from supervisor.autoresearch.validation import validate_attempt
from supervisor.claim_gate import ClaimGate, ClaimLevel
from supervisor.state import State
from tests.test_claim_gate import (
    _authoritative_causal_bundle,
    _claim_gate_kwargs,
)


BASE_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: base\n"
    "instruction_guidance_blocks: {}\n"
)
AFTER_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: proposal-1\n"
    "instruction_guidance_blocks:\n"
    "  outcome_review:\n"
    "    - Verify runtime-native evidence before accepting.\n"
)


@pytest.fixture(scope="module")
def policy_authority(tmp_path_factory) -> PolicyClaimAuthority:
    evidence_root = tmp_path_factory.mktemp("policy-claim-authority")
    evidence, ledger_resolver = _authoritative_causal_bundle(evidence_root)
    claim_kwargs = _claim_gate_kwargs(ledger_resolver)
    authority = PolicyClaimAuthority(
        evidence_bundle=evidence,
        evidence_root=evidence_root,
        ledger_verification_resolver=claim_kwargs.get(
            "ledger_verification_resolver"
        ),
        grade_authority=claim_kwargs.get("grade_authority"),
        trusted_verifier_attestors=claim_kwargs.get(
            "trusted_verifier_attestors"
        ),
        trusted_external_authorities=claim_kwargs.get(
            "trusted_external_authorities"
        ),
    )
    level = ClaimGate.max_claim_level(
        authority.evidence_bundle,
        evidence_root=authority.evidence_root,
        ledger_verification_resolver=(
            authority.ledger_verification_resolver
        ),
        grade_authority=authority.grade_authority,
        trusted_verifier_attestors=authority.trusted_verifier_attestors,
        trusted_external_authorities=(
            authority.trusted_external_authorities
        ),
    )
    assert level is not None
    assert tuple(ClaimLevel).index(level) >= tuple(ClaimLevel).index(
        ClaimLevel.L3
    )
    return authority


def _authorize_report(
    report: dict,
    authority: PolicyClaimAuthority,
) -> dict:
    return authority.derive_report(report)


def _bind_existing_artifact_hashes(
    report: dict,
    *,
    repo_root: Path,
) -> dict:
    for record in report.get("records") or []:
        if not isinstance(record, dict):
            continue
        artifact_hashes = dict(record.get("artifact_hashes") or {})
        for raw_path in record.get("changed_files") or []:
            path = repo_root / str(raw_path)
            if path.is_file():
                artifact_hashes[str(raw_path)] = _sha(path)
        record["artifact_hashes"] = artifact_hashes
    report["report_sha256"] = autoresearch_report_sha256(report)
    return report


def _authorized_report_context(
    report: dict,
    authority: PolicyClaimAuthority,
    *,
    repo_root: Path,
    state: State | None,
    run_id: str | None,
) -> tuple[dict, State, str]:
    authorized = _authorize_report(
        _bind_existing_artifact_hashes(
            report,
            repo_root=repo_root,
        ),
        authority,
    )
    journal = state or State(":memory:")
    authority_run_id = run_id or (
        "policy-authority-"
        + authorized["report_sha256"][:16]
    )
    if not any(
        event["kind"] == "autoresearch_report_emitted"
        for event in journal.read_events_since(
            authority_run_id,
            after_event_id=0,
            limit=20,
        )
    ):
        _record_report_authority(
            journal,
            run_id=authority_run_id,
            report=authorized,
        )
    return authorized, journal, authority_run_id


def _derive_authorized(
    report: dict,
    authority: PolicyClaimAuthority,
    **kwargs,
) -> list[dict]:
    repo_root = Path(kwargs["repo_root"])
    authorized, state, run_id = _authorized_report_context(
        report,
        authority,
        repo_root=repo_root,
        state=kwargs.get("state"),
        run_id=kwargs.get("run_id"),
    )
    call_kwargs = dict(kwargs)
    call_kwargs["state"] = state
    call_kwargs["run_id"] = run_id
    call_kwargs.update(authority.validation_kwargs())
    return derive_policy_evolution_proposals_from_report(
        authorized,
        **call_kwargs,
    )


def _create_authorized(
    report: dict,
    authority: PolicyClaimAuthority,
    **kwargs,
) -> list[dict]:
    repo_root = Path(kwargs["repo_root"])
    authorized, state, run_id = _authorized_report_context(
        report,
        authority,
        repo_root=repo_root,
        state=kwargs.get("state"),
        run_id=kwargs.get("run_id"),
    )
    call_kwargs = dict(kwargs)
    call_kwargs["state"] = state
    call_kwargs["run_id"] = run_id
    call_kwargs.update(authority.validation_kwargs())
    return create_policy_evolution_proposals(
        authorized,
        **call_kwargs,
    )


def _contains_authorized(
    report: dict,
    authority: PolicyClaimAuthority,
    *,
    repo_root: Path,
) -> bool:
    authorized, state, run_id = _authorized_report_context(
        report,
        authority,
        repo_root=repo_root,
        state=None,
        run_id=None,
    )
    return report_contains_derivable_policy_record(
        authorized,
        repo_root=repo_root,
        state=state,
        run_id=run_id,
        **authority.validation_kwargs(),
    )


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _quality_controls(**overrides) -> dict:
    base = {
        "source": "supervisor_control_execution",
        "evidence_grade": "runtime_native",
        "supervisor_runtime_origin": "run_evaluator_quality_controls",
        "candidate_affects_evaluated_path": True,
        "determinism": {
            "source": "repeated_execution",
            "evidence_grade": "runtime_native",
            "supervisor_runtime_origin": "run_evaluator_quality_controls",
            "output_hashes": ["deterministic-output", "deterministic-output"],
        },
        "controls": {
            "noop": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": 0.0,
                "candidate_ref": "evaluator-quality/noop.json",
                "candidate_hash": "noop-hash",
                "control_run_ref": "evaluator-quality/noop-run.json",
                "control_run_hash": "noop-run-hash",
                "verdict": "no_improvement",
            },
            "harmful": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": -0.1,
                "candidate_ref": "evaluator-quality/harmful.json",
                "candidate_hash": "harmful-hash",
                "control_run_ref": "evaluator-quality/harmful-run.json",
                "control_run_hash": "harmful-run-hash",
                "verdict": "regressed",
            },
            "known_good": {
                "source": "supervisor_control_execution",
                "evidence_grade": "runtime_native",
                "supervisor_runtime_origin": "run_evaluator_quality_controls",
                "metric_source": "evaluator_execution",
                "metric_delta": 0.2,
                "candidate_ref": "evaluator-quality/known-good.json",
                "candidate_hash": "known-good-hash",
                "control_run_ref": "evaluator-quality/known-good-run.json",
                "control_run_hash": "known-good-run-hash",
                "verdict": "improved",
            },
        },
    }
    base.update(overrides)
    if base.get("source") == "supervisor_control_execution":
        determinism = base.get("determinism")
        if isinstance(determinism, dict):
            determinism.setdefault("evidence_grade", "runtime_native")
            determinism.setdefault("supervisor_runtime_origin", "run_evaluator_quality_controls")
        controls = base.get("controls")
        if isinstance(controls, dict):
            for kind, control in controls.items():
                if not isinstance(control, dict):
                    continue
                control.setdefault("source", "supervisor_control_execution")
                control.setdefault("evidence_grade", "runtime_native")
                control.setdefault("supervisor_runtime_origin", "run_evaluator_quality_controls")
                control.setdefault("candidate_ref", f"evaluator-quality/{kind}.json")
                control.setdefault("candidate_hash", f"{kind}-hash")
                control.setdefault("control_run_ref", f"evaluator-quality/{kind}-run.json")
                control.setdefault("control_run_hash", f"{kind}-run-hash")
    return base


def _record(**overrides) -> dict:
    record = {
        "experiment_id": "exp-policy-1",
        "task_id": "task-policy-1",
        "attempt_id": "attempt-policy-1",
        "validation_status": "accepted",
        "recommendation": "validated as report-only candidate; operator review required",
        "metric_name": "reviewer_evidence_score",
        "metric_trials": [0.74, 0.82, 0.86],
        "metric_median": 0.82,
        "metric_iqr": 0.12,
        "empty_floor_comparison": {
            "metric_source": "evaluator_execution",
            "empty_floor_metric": 0.62,
            "candidate_metric": 0.82,
            "metric_delta": 0.20,
            "k_trials": 3,
        },
        "quality_unstable_across_trials": True,
        "metric_source": "evaluator_execution",
        "evaluator_run_ref": "docs/dual-agent/run/evaluator-runs/attempt-policy-1.json",
        "evaluator_run_hash": "evaluator-run-hash",
        "changed_files": ["candidates/outcome-review.md"],
        "evaluator_quality": _quality_controls(),
        "gaming_flags": [],
        "validation_errors": [],
        "cost_usd": 0.19,
        "wall_clock_s": 12.5,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }
    record.update(overrides)
    return record


def _report(*records: dict) -> dict:
    report = {
        "schema_version": "supervisor-autoresearch-summary/v1",
        "default_change_allowed": False,
        "report_only": {
            "default_change_allowed": False,
            "policy_mutated": False,
            "operator_review_required": True,
        },
        "records": list(records),
    }
    report["report_sha256"] = autoresearch_report_sha256(report)
    return report


def _record_report_authority(
    state: State,
    *,
    run_id: str,
    report: dict,
) -> int:
    return state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_report_emitted",
        payload={
            "schema_version": "supervisor-autoresearch/v1",
            "report_sha256": report["report_sha256"],
            "claim_gate": dict(report.get("claim_gate") or {}),
        },
    )


def _derived_record(**overrides) -> dict:
    record = _record(
        attempt_id="attempt-derived-1",
        changed_files=["candidates/policy-overlay.yaml"],
        policy_overlay_candidate_ref="candidates/policy-overlay.yaml",
        metric_before=0.62,
        metric_after=0.74,
        metric_delta=0.12,
    )
    record.update(overrides)
    return record


def _unrecorded_proposal_fixture(
    root: Path,
) -> tuple[State, Path, Path, dict]:
    state = State(str(root / "state.db"))
    target = _write(root, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(root, "candidates/outcome-review.md", AFTER_OVERLAY)
    proposal = {
        "proposal_id": "proposal-fixture",
        "changes": [{
            "target_path": ".supervisor/policy-overlay.yaml",
            "candidate_ref": "candidates/outcome-review.md",
            "before_hash": _sha(target),
            "after_hash": _sha(candidate),
        }],
    }
    return state, target, candidate, proposal


def _proposal_fixture(
    root: Path,
    authority: PolicyClaimAuthority,
) -> tuple[State, Path, Path, dict]:
    state = State(str(root / "state.db"))
    target = _write(root, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(root, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = _create_authorized(
        _report(_record()),
        authority,
        repo_root=root,
        candidate_changes={
            ".supervisor/policy-overlay.yaml": "candidates/outcome-review.md",
        },
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )
    return state, target, candidate, proposal


def test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _report(_derived_record())
    report["report_ref"] = "docs/dual-agent/autoresearch/report.json"
    authorized_report, state, run_id = _authorized_report_context(
        report,
        policy_authority,
        repo_root=tmp_path,
        state=state,
        run_id="policy-run",
    )

    proposals = derive_policy_evolution_proposals_from_report(
        authorized_report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id=run_id,
        **policy_authority.validation_kwargs(),
    )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["status"] == "draft"
    assert proposal["source"] == "autoresearch_deriver"
    assert proposal["requires_operator_approval"] is True
    assert proposal["default_change_allowed"] is False
    assert proposal["automatic_policy_mutation"] is False
    assert proposal["gate_advanced"] is False
    assert proposal["derivation"]["report_ref"] == "docs/dual-agent/autoresearch/report.json"
    assert proposal["derivation"]["report_sha256"] == (
        authorized_report["report_sha256"]
    )
    assert proposal["derivation"]["attempt_id"] == "attempt-derived-1"
    assert proposal["derivation"]["candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["derivation"]["affected_gates"] == ["outcome_review"]
    assert proposal["derivation"]["metric_before"] == 0.62
    assert proposal["derivation"]["metric_after"] == 0.74
    assert proposal["derivation"]["metric_delta"] == 0.12
    assert proposal["evaluator_evidence"]["metric_trials"] == [0.74, 0.82, 0.86]
    assert proposal["evaluator_evidence"]["evaluator_run_ref"] == (
        "docs/dual-agent/run/evaluator-runs/attempt-policy-1.json"
    )
    assert proposal["evaluator_evidence"]["evaluator_run_hash"] == "evaluator-run-hash"
    [change] = proposal["changes"]
    assert change["target_path"] == ".supervisor/policy-overlay.yaml"
    assert change["candidate_ref"] == "candidates/policy-overlay.yaml"
    assert change["before_hash"] == _sha(target)
    assert change["after_hash"] == _sha(candidate)
    assert "--- a/.supervisor/policy-overlay.yaml" in change["diff"]
    assert "+++ b/.supervisor/policy-overlay.yaml" in change["diff"]

    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
    ]
    assert events[1]["payload"]["proposal_id"] == proposal["proposal_id"]


def test_policy_proposal_without_empty_floor_is_non_applyable(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = _derive_authorized(
        _report(_derived_record(empty_floor_comparison=None)),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    [event] = [
        item for item in events
        if item["kind"] == "autoresearch_policy_proposal_derivation_skipped"
    ]
    assert event["payload"]["reason"] == "empty-floor comparison is required for policy derivation"
    assert event["payload"]["automatic_policy_mutation"] is False


def test_autoresearch_noop_control_blocks_policy_proposal(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _report(
        _derived_record(
            evaluator_quality=_quality_controls(
                controls={
                    **_quality_controls()["controls"],
                    "noop": {
                        "metric_source": "evaluator_execution",
                        "metric_delta": 0.08,
                        "verdict": "improved",
                    },
                }
            )
        )
    )

    proposals = _derive_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    [event] = [item for item in events if item["kind"] == "autoresearch_policy_proposal_derivation_skipped"]
    assert event["payload"]["reason"] == "noop control must not improve"


def test_autoresearch_harmful_control_blocks_policy_proposal(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _report(
        _derived_record(
            evaluator_quality=_quality_controls(
                controls={
                    **_quality_controls()["controls"],
                    "harmful": {
                        "metric_source": "evaluator_execution",
                        "metric_delta": 0.04,
                        "verdict": "improved",
                    },
                }
            )
        )
    )

    proposals = _derive_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    [event] = [item for item in events if item["kind"] == "autoresearch_policy_proposal_derivation_skipped"]
    assert event["payload"]["reason"] == "harmful control must regress or fail"


def test_caller_supplied_quality_metadata_cannot_derive_policy_proposal(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _report(
        _derived_record(
            evaluator_quality=_quality_controls(
                source="caller_supplied_metadata",
                evidence_grade="self_reported",
                supervisor_runtime_origin="",
            )
        )
    )

    proposals = _derive_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert _contains_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
    ) is False
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    [event] = [item for item in events if item["kind"] == "autoresearch_policy_proposal_derivation_skipped"]
    assert event["payload"]["reason"] == (
        "evaluator-quality controls must be supervisor-generated runtime-native evidence"
    )


def test_autoresearch_known_good_control_allows_candidate_sensitive_derivation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _report(_derived_record(evaluator_quality=_quality_controls()))

    proposals = _derive_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    [proposal] = proposals
    assert proposal["status"] == "draft"
    assert proposal["source"] == "autoresearch_deriver"
    assert proposal["evaluator_evidence"]["evaluator_quality"]["verdict"] == "accepted"
    assert proposal["changes"][0]["candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["changes"][0]["after_hash"] == _sha(candidate)


def test_authorized_report_tampering_invalidates_policy_derivation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _authorize_report(
        _report(_derived_record(evaluator_quality=_quality_controls())),
        policy_authority,
    )
    report["records"][0]["metric_delta"] = 0.99

    proposals = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
        **policy_authority.validation_kwargs(),
    )

    assert proposals == []
    [event] = state.read_events_since(
        "policy-run",
        after_event_id=0,
        limit=10,
    )
    assert event["kind"] == (
        "autoresearch_policy_proposal_derivation_skipped"
    )
    assert event["payload"]["reason"] == (
        "report_sha256 does not match canonical report contents"
    )


def test_authorized_report_cannot_be_tampered_and_publicly_rehashed(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(
        tmp_path,
        "candidates/policy-overlay.yaml",
        AFTER_OVERLAY,
    )
    report = _authorize_report(
        _report(
            _derived_record(
                artifact_hashes={
                    "candidates/policy-overlay.yaml": _sha(candidate),
                },
            )
        ),
        policy_authority,
    )
    _record_report_authority(
        state,
        run_id="policy-run",
        report=report,
    )
    report["records"][0]["metric_delta"] = 0.99
    report["records"][0]["metric_after"] = 1.61
    report["report_sha256"] = autoresearch_report_sha256(report)

    proposals = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
        **policy_authority.validation_kwargs(),
    )

    assert proposals == []
    assert not any(
        event["kind"] == "autoresearch_policy_proposal_created"
        for event in state.read_events_since(
            "policy-run",
            after_event_id=0,
            limit=20,
        )
    )


def test_candidate_mutation_after_report_authorization_blocks_derivation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(
        tmp_path,
        "candidates/policy-overlay.yaml",
        AFTER_OVERLAY,
    )
    authorized_hash = _sha(candidate)
    report = _authorize_report(
        _report(
            _derived_record(
                artifact_hashes={
                    "candidates/policy-overlay.yaml": authorized_hash,
                },
            )
        ),
        policy_authority,
    )
    _record_report_authority(
        state,
        run_id="policy-run",
        report=report,
    )
    candidate.write_text("mutated after authorization\n", encoding="utf-8")

    proposals = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
        **policy_authority.validation_kwargs(),
    )

    assert proposals == []
    events = state.read_events_since(
        "policy-run",
        after_event_id=0,
        limit=20,
    )
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert "authorized artifact hash" in events[-1]["payload"]["reason"]


def test_validation_report_pipeline_derives_policy_proposal_without_operator_authored_changes(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    evaluator = _write(tmp_path, "evaluators/policy.py", "print('score')\n")
    experiment = AutoresearchExperiment(
        experiment_id="exp-policy-real",
        task_id="task-policy-real",
        hypothesis="Try a policy overlay candidate.",
        baseline_ref="baseline:current",
        mutable_paths=("candidates",),
        immutable_paths=(),
        evaluator_ref="evaluators/policy.py",
        evaluator_hash=_sha(evaluator),
        metric_name="reviewer_evidence_score",
        k_trials=3,
    )
    attempt = AutoresearchAttempt(
        attempt_id="attempt-policy-real",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="worker-policy",
        hypothesis="Add runtime evidence guidance.",
        changed_files=("candidates/policy-overlay.yaml",),
        metric_trials=(0.72, 0.78, 0.81),
        metric_before=0.62,
        policy_candidate_changes={
            ".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml",
        },
        evaluator_quality=_quality_controls(),
        metric_source="evaluator_execution",
        evaluator_run_ref="evaluator-runs/attempt-policy-real.json",
        evaluator_run_hash="run-hash",
        artifact_hashes={"candidates/policy-overlay.yaml": _sha(candidate)},
        evidence_refs=(
            "evaluator_run:evaluator-runs/attempt-policy-real.json",
            "artifact:candidates/policy-overlay.yaml",
        ),
    )
    validation = validate_attempt(
        experiment=experiment,
        attempt=attempt,
        repo_root=tmp_path,
    )
    report = build_autoresearch_report([validation])
    report["report_ref"] = "docs/dual-agent/autoresearch/report.json"

    [proposal] = _derive_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    record = report["records"][0]
    assert record["validation_status"] == "accepted"
    assert record["metric_before"] == 0.62
    assert record["metric_after"] == 0.78
    assert record["metric_delta"] == 0.16
    assert record["policy_candidate_changes"] == {
        ".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml",
    }
    assert proposal["source"] == "autoresearch_deriver"
    assert proposal["status"] == "draft"
    assert proposal["derivation"]["candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["derivation"]["metric_delta"] == 0.16
    assert target.read_text(encoding="utf-8") == BASE_OVERLAY


def test_autoresearch_report_acceptance_auto_derives_overlay_proposal(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    evaluator = _write(tmp_path, "evaluators/policy.py", "print('score')\n")
    fixture = {
        "experiment": AutoresearchExperiment(
            experiment_id="exp-auto-derive",
            task_id="task-auto-derive",
            hypothesis="Try an overlay candidate.",
            baseline_ref="baseline:current",
            mutable_paths=("candidates",),
            immutable_paths=(),
            evaluator_ref="evaluators/policy.py",
            evaluator_hash=_sha(evaluator),
            metric_name="reviewer_evidence_score",
            k_trials=3,
        ).to_payload(),
        "attempts": [
            AutoresearchAttempt(
                attempt_id="attempt-auto-derive",
                experiment_id="exp-auto-derive",
                task_id="task-auto-derive",
                worker_id="worker-policy",
                hypothesis="Add runtime evidence guidance.",
                changed_files=("candidates/policy-overlay.yaml",),
                metric_trials=(0.72, 0.78, 0.81),
                metric_before=0.62,
                policy_overlay_candidate_ref="candidates/policy-overlay.yaml",
                metric_source="evaluator_execution",
                evaluator_run_ref="evaluator-runs/attempt-auto-derive.json",
                evaluator_run_hash="run-hash",
                artifact_hashes={"candidates/policy-overlay.yaml": _sha(candidate)},
                evidence_refs=(
                    "evaluator_run:evaluator-runs/attempt-auto-derive.json",
                    "artifact:candidates/policy-overlay.yaml",
                ),
                evaluator_quality=_quality_controls(),
            ).to_payload()
        ],
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture, sort_keys=True), encoding="utf-8")

    report = run_autoresearch_fixture(
        fixture_path=fixture_path,
        state=state,
        run_id="autoresearch-run",
        repo_root=tmp_path,
        output_dir=tmp_path / "out",
        policy_claim_authority=policy_authority,
    )

    persisted_report = json.loads(
        (tmp_path / "out" / "report.json").read_text(encoding="utf-8")
    )
    assert report["records"][0]["validation_status"] == "accepted"
    assert report["claim_gate"]["max_claim_level"] == ClaimLevel.L3.value
    assert report["improvement_claim_allowed"] is True
    assert report["powered_improvement_claim_allowed"] is True
    assert persisted_report == report
    assert report["report_sha256"] == autoresearch_report_sha256(
        report
    )
    assert ClaimGate.validate_derived_report(
        persisted_report,
        policy_authority.evidence_bundle,
        evidence_root=policy_authority.evidence_root,
        evidence_resolver=policy_authority.evidence_resolver,
        ledger_verification_resolver=(
            policy_authority.ledger_verification_resolver
        ),
        grade_authority=policy_authority.grade_authority,
        trusted_verifier_attestors=(
            policy_authority.trusted_verifier_attestors
        ),
        trusted_external_authorities=(
            policy_authority.trusted_external_authorities
        ),
    ) is ClaimLevel.L3
    assert report["derived_policy_proposals"][0]["status"] == "draft"
    events = state.read_events_since("autoresearch-run", after_event_id=0, limit=50)
    kinds = [event["kind"] for event in events]
    assert kinds.count("autoresearch_report_emitted") == 1
    assert kinds.count("autoresearch_policy_proposal_created") == 1
    report_event = [
        event
        for event in events
        if event["kind"] == "autoresearch_report_emitted"
    ][0]
    proposal = [
        event
        for event in events
        if event["kind"] == "autoresearch_policy_proposal_created"
    ][0]
    assert report_event["payload"]["report_sha256"] == report["report_sha256"]
    assert report_event["payload"]["claim_gate"] == report["claim_gate"]
    assert proposal["payload"]["source"] == "autoresearch_deriver"
    assert proposal["payload"]["automatic_policy_mutation"] is False
    assert proposal["payload"]["claim_authority"] == {
        "schema_version": "supervisor-policy-claim-authority/v1",
        "report_sha256": report["report_sha256"],
        "claim_gate_schema_version": report["claim_gate"][
            "schema_version"
        ],
        "max_claim_level": ClaimLevel.L3.value,
        "evidence_bundle_sha256": report["claim_gate"][
            "evidence_bundle_sha256"
        ],
        "report_event_id": report_event["event_id"],
        "report_event_hash": report_event["event_hash"],
    }
    assert proposal["payload"]["derivation"]["report_sha256"] == (
        report["report_sha256"]
    )


def test_validation_report_derives_from_direct_policy_overlay_candidate_ref(
    tmp_path,
    policy_authority,
):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    evaluator = _write(tmp_path, "evaluators/policy.py", "print('score')\n")
    experiment = AutoresearchExperiment(
        experiment_id="exp-policy-direct",
        task_id="task-policy-direct",
        hypothesis="Try a directly referenced policy overlay candidate.",
        baseline_ref="baseline:current",
        mutable_paths=("candidates",),
        immutable_paths=(),
        evaluator_ref="evaluators/policy.py",
        evaluator_hash=_sha(evaluator),
        metric_name="reviewer_evidence_score",
        k_trials=3,
    )
    attempt = AutoresearchAttempt(
        attempt_id="attempt-policy-direct",
        experiment_id=experiment.experiment_id,
        task_id=experiment.task_id,
        worker_id="worker-policy",
        hypothesis="Add runtime evidence guidance.",
        changed_files=("candidates/policy-overlay.yaml",),
        metric_trials=(0.72, 0.78, 0.81),
        metric_before=0.62,
        policy_overlay_candidate_ref="candidates/policy-overlay.yaml",
        metric_source="evaluator_execution",
        evaluator_run_ref="evaluator-runs/attempt-policy-direct.json",
        evaluator_run_hash="run-hash",
        artifact_hashes={"candidates/policy-overlay.yaml": _sha(candidate)},
        evidence_refs=(
            "evaluator_run:evaluator-runs/attempt-policy-direct.json",
            "artifact:candidates/policy-overlay.yaml",
        ),
        evaluator_quality=_quality_controls(),
    )
    report = build_autoresearch_report([
        validate_attempt(experiment=experiment, attempt=attempt, repo_root=tmp_path)
    ])

    [proposal] = _derive_authorized(
        report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    )

    record = report["records"][0]
    assert record["policy_candidate_changes"] == {}
    assert record["policy_overlay_candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["derivation"]["candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["status"] == "draft"


def test_deriver_skips_gaming_flagged_and_non_positive_metric_reports(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = _derive_authorized(
        _report(
            _derived_record(attempt_id="gaming", gaming_flags=["zero_variance_trials"]),
            _derived_record(attempt_id="zero-delta", metric_before=0.74, metric_after=0.74, metric_delta=0.0),
            _derived_record(attempt_id="negative-delta", metric_before=0.8, metric_after=0.7, metric_delta=-0.1),
        ),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_derivation_skipped",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert all(
        event["payload"]["automatic_policy_mutation"] is False
        for event in events[1:]
    )


def test_deriver_rejects_inconsistent_explicit_metric_delta(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = _derive_authorized(
        _report(_derived_record(
            attempt_id="contradictory-delta",
            metric_before=0.7,
            metric_after=0.6,
            metric_delta=0.2,
        )),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert "metric delta must match" in events[1]["payload"]["reason"]


def test_deriver_skips_rejected_and_non_evaluator_backed_records_at_public_boundary(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = _derive_authorized(
        _report(
            _derived_record(attempt_id="rejected", validation_status="rejected"),
            _derived_record(attempt_id="fixture-metric", metric_source="fixture"),
            _derived_record(attempt_id="missing-run-ref", evaluator_run_ref=""),
            _derived_record(attempt_id="missing-run-hash", evaluator_run_hash=""),
        ),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    events = state.read_events_since(
        "policy-run",
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
    ]


def test_deriver_rejects_missing_candidate_artifact_with_skip_event(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)

    proposals = _derive_authorized(
        _report(_derived_record(
            attempt_id="missing-candidate",
            changed_files=["candidates/outcome-review.md"],
            policy_overlay_candidate_ref="",
            candidate_overlay_ref="",
            candidate_artifacts={},
        )),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert (
        "exactly one policy overlay candidate artifact is required"
        in events[1]["payload"]["reason"]
    )
    assert events[1]["payload"]["gate_advanced"] is False


def test_deriver_rejects_direct_non_overlay_candidate_ref_at_derivation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    prompt_candidate = _write(tmp_path, "candidates/execution.md", "new execution prompt\n")

    proposals = _derive_authorized(
        _report(_derived_record(
            attempt_id="direct-non-overlay-candidate",
            changed_files=["candidates/execution.md"],
            policy_overlay_candidate_ref="candidates/execution.md",
        )),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("execution",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    assert prompt_candidate.read_text(encoding="utf-8") == "new execution prompt\n"
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert (
        "derived policy candidate must be a policy-overlay.yaml artifact"
        in events[1]["payload"]["reason"]
    )
    assert events[1]["payload"]["gate_advanced"] is False


def test_deriver_rejects_non_overlay_candidate_at_derivation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    prompt = _write(tmp_path, "prompts/execution.md", "old prompt\n")
    _write(tmp_path, "candidates/execution.md", "new prompt\n")

    proposals = _derive_authorized(
        _report(_derived_record(
            policy_candidate_changes={"prompts/execution.md": "candidates/execution.md"},
            changed_files=["candidates/execution.md"],
        )),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("execution",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    assert prompt.read_text(encoding="utf-8") == "old prompt\n"
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert "may only target" in events[1]["payload"]["reason"]
    assert events[1]["payload"]["gate_advanced"] is False


def test_derived_proposal_still_requires_operator_approval(
    tmp_path,
    policy_authority,
):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    [proposal] = _derive_authorized(
        _report(_derived_record()),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    )

    assert proposal["status"] == "draft"
    assert proposal["requires_operator_approval"] is True
    assert proposal["operator_approved"] is False
    assert proposal["default_change_allowed"] is False
    assert proposal["automatic_policy_mutation"] is False
    assert proposal["gate_advanced"] is False
    assert proposal["gate_authority"] == "unchanged"
    assert proposal["reviewer_panel_authority"] == "unchanged"
    assert proposal["typed_outcome_authority"] == "unchanged"
    assert (tmp_path / ".supervisor/policy-overlay.yaml").read_text(encoding="utf-8") == BASE_OVERLAY


def test_accepted_autoresearch_attempt_creates_policy_proposal_without_mutation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    proposals = _create_authorized(
        _report(_record()),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["status"] == "proposed"
    assert proposal["requires_operator_approval"] is True
    assert proposal["default_change_allowed"] is False
    assert proposal["gate_advanced"] is False
    assert proposal["gate_authority"] == "unchanged"
    assert proposal["reviewer_panel_authority"] == "unchanged"
    assert proposal["typed_outcome_authority"] == "unchanged"
    assert proposal["affected_gates"] == ["outcome_review"]
    assert proposal["evaluator_evidence"]["metric_trials"] == [0.74, 0.82, 0.86]
    assert proposal["evaluator_evidence"]["k_trials"] == 3
    assert proposal["evaluator_evidence"]["gaming_flags"] == []
    assert proposal["evaluator_evidence"]["cost_usd"] == 0.19
    [change] = proposal["changes"]
    assert change["before_hash"] == _sha(target)
    assert change["after_hash"] == _sha(candidate)
    assert "--- a/.supervisor/policy-overlay.yaml" in change["diff"]
    assert "+++ b/.supervisor/policy-overlay.yaml" in change["diff"]

    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
    ]
    assert events[1]["payload"]["proposal_id"] == proposal["proposal_id"]


def test_rejected_or_gaming_flagged_attempt_creates_no_applyable_policy_proposal(
    tmp_path,
    policy_authority,
):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    proposals = _create_authorized(
        _report(
            _record(validation_status="rejected"),
            _record(attempt_id="attempt-policy-2", gaming_flags=["zero_variance_trials"]),
        ),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
    )

    assert proposals == []


def test_non_evaluator_backed_or_mutating_attempt_creates_no_applyable_policy_proposal(
    tmp_path,
    policy_authority,
):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    proposals = _create_authorized(
        _report(
            _record(attempt_id="fixture-metric", metric_source="fixture"),
            _record(attempt_id="missing-run-ref", evaluator_run_ref=""),
            _record(attempt_id="missing-run-hash", evaluator_run_hash=""),
            _record(attempt_id="default-change", default_change_allowed=True),
            _record(attempt_id="policy-mutated", policy_mutated=True),
            _record(attempt_id="gate-advanced", gate_advanced=True),
        ),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
    )

    assert proposals == []


def test_approved_policy_proposal_applies_exact_recorded_candidate_and_records_hashes(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = _create_authorized(
        _report(_record()),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )
    before_hash = _sha(target)
    after_hash = _sha(candidate)

    approval = approve_policy_proposal(
        proposal,
        state=state,
        run_id="policy-run",
        repo_root=tmp_path,
        approver="sam.zhang",
        approval_channel="codex_desktop",
    )

    assert target.read_text(encoding="utf-8") == candidate.read_text(encoding="utf-8")
    assert _sha(target) == proposal["changes"][0]["after_hash"] == after_hash
    assert approval["before_hash"] == before_hash
    assert approval["after_hash"] == after_hash
    assert approval["approver"] == "sam.zhang"
    assert approval["approval_channel"] == "codex_desktop"
    assert approval["operator_approved"] is True
    assert approval["default_change_allowed"] is False
    assert approval["gate_authority"] == "unchanged"
    assert approval["rollback_pointer"]["files"][0]["before_hash"] == before_hash

    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
        "autoresearch_policy_proposal_approved",
    ]
    assert events[2]["payload"]["after_hash"] == after_hash


def test_approval_rejects_unrecorded_caller_authored_proposal(tmp_path):
    state, target, _candidate, proposal = _unrecorded_proposal_fixture(
        tmp_path
    )

    with pytest.raises(
        PolicyEvolutionError,
        match="recorded proposal event",
    ):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert state.read_events_since(
        "policy-run",
        after_event_id=0,
        limit=20,
    ) == []


def test_approval_rejects_proposal_event_from_another_run(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )

    with pytest.raises(
        PolicyEvolutionError,
        match="not found in the specified run",
    ):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="different-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY


def test_approval_rejects_recorded_proposal_without_payload_hash(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    malformed = {
        key: value
        for key, value in proposal.items()
        if key not in {"proposal_event_id", "proposal_sha256"}
    }
    malformed["proposal_id"] = "proposal-missing-hash"
    malformed_event_id = state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=malformed,
    )

    with pytest.raises(
        PolicyEvolutionError,
        match="recorded proposal event hash is invalid",
    ):
        approve_policy_proposal(
            state=state,
            run_id="policy-run",
            proposal_event_id=malformed_event_id,
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY


def test_approval_rejects_duplicate_recorded_proposal_identity(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    recorded = {
        key: value
        for key, value in proposal.items()
        if key != "proposal_event_id"
    }
    state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=recorded,
    )

    with pytest.raises(
        PolicyEvolutionError,
        match="identity is missing or duplicated",
    ):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY


def test_approval_rejects_forged_report_authority_binding(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    forged = json.loads(json.dumps({
        key: value
        for key, value in proposal.items()
        if key != "proposal_event_id"
    }))
    forged["proposal_id"] = "proposal-forged-authority"
    forged["claim_authority"]["report_event_hash"] = "0" * 64
    forged["proposal_sha256"] = sha256(json.dumps(
        {
            key: value
            for key, value in forged.items()
            if key != "proposal_sha256"
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    forged_event_id = state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload=forged,
    )

    with pytest.raises(
        PolicyEvolutionError,
        match="report authority binding is invalid",
    ):
        approve_policy_proposal(
            state=state,
            run_id="policy-run",
            proposal_event_id=forged_event_id,
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY


def test_approval_rejects_stale_target_before_hash(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    target.write_text("operator changed this after proposal\n", encoding="utf-8")

    with pytest.raises(PolicyEvolutionError, match="current artifact hash mismatch"):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
    )

    assert target.read_text(encoding="utf-8") == "operator changed this after proposal\n"
    assert [
        event["kind"]
        for event in state.read_events_since(
            "policy-run",
            after_event_id=0,
            limit=10,
        )
    ] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
    ]


def test_approval_and_denial_require_operator_identity_before_mutation_or_events(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    initial_events = state.read_events_since(
        "policy-run",
        after_event_id=0,
        limit=10,
    )

    for kwargs in (
        {"approver": "", "approval_channel": "codex_desktop"},
        {"approver": "sam.zhang", "approval_channel": ""},
    ):
        with pytest.raises(PolicyEvolutionError):
            approve_policy_proposal(
                proposal,
                state=state,
                run_id="policy-run",
                repo_root=tmp_path,
                **kwargs,
            )
        assert target.read_text(encoding="utf-8") == BASE_OVERLAY
        assert state.read_events_since(
            "policy-run",
            after_event_id=0,
            limit=10,
        ) == initial_events

        with pytest.raises(PolicyEvolutionError):
            deny_policy_proposal(
                proposal,
                state=state,
                run_id="policy-run",
                reason="no operator identity",
                **kwargs,
            )
        assert target.read_text(encoding="utf-8") == BASE_OVERLAY
        assert state.read_events_since(
            "policy-run",
            after_event_id=0,
            limit=10,
        ) == initial_events


def test_rollback_requires_operator_identity_before_mutation_or_events(
    tmp_path,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    approval = approve_policy_proposal(
        proposal,
        state=state,
        run_id="policy-run",
        repo_root=tmp_path,
        approver="sam.zhang",
        approval_channel="codex_desktop",
    )
    assert target.read_text(encoding="utf-8") == AFTER_OVERLAY

    for kwargs in (
        {"approver": "", "approval_channel": "codex_desktop"},
        {"approver": "sam.zhang", "approval_channel": ""},
    ):
        with pytest.raises(PolicyEvolutionError):
            rollback_policy_proposal(
                approval["rollback_pointer"],
                state=state,
                run_id="policy-run",
                repo_root=tmp_path,
                reason="no operator identity",
                **kwargs,
            )
        assert target.read_text(encoding="utf-8") == AFTER_OVERLAY
        events = state.read_events_since("policy-run", after_event_id=0, limit=10)
        assert [event["kind"] for event in events] == [
            "autoresearch_report_emitted",
            "autoresearch_policy_proposal_created",
            "autoresearch_policy_proposal_approved",
        ]


def test_approval_rejects_tampered_candidate_after_hash(
    tmp_path,
    policy_authority,
):
    state, target, candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    candidate.write_text("tampered candidate\n", encoding="utf-8")

    with pytest.raises(PolicyEvolutionError, match="candidate artifact hash mismatch"):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
    )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert [
        event["kind"]
        for event in state.read_events_since(
            "policy-run",
            after_event_id=0,
            limit=10,
        )
    ] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
    ]


def test_approval_rejects_post_write_hash_mismatch(
    tmp_path,
    monkeypatch,
    policy_authority,
):
    state, target, _candidate, proposal = _proposal_fixture(
        tmp_path,
        policy_authority,
    )
    original_commit = (
        policy_evolution_module.commit_staged_repo_file_no_follow
    )

    def commit_then_corrupt(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        target.write_bytes(b"corrupted after atomic publish\n")
        return result

    monkeypatch.setattr(
        policy_evolution_module,
        "commit_staged_repo_file_no_follow",
        commit_then_corrupt,
    )

    with pytest.raises(
        PolicyEvolutionError,
        match="target is not in desired state",
    ):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == (
        "corrupted after atomic publish\n"
    )
    events = state.read_events_since(
        "policy-run",
        after_event_id=0,
        limit=10,
    )
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
        "autoresearch_policy_proposal_approved",
    ]
    event = events[-1]
    assert event["kind"] == "autoresearch_policy_proposal_approved"
    assert event["payload"]["audit_committed_before_filesystem"] is True

    monkeypatch.setattr(
        policy_evolution_module,
        "commit_staged_repo_file_no_follow",
        original_commit,
    )
    with pytest.raises(
        PolicyEvolutionError,
        match="compare-and-set mismatch",
    ):
        recover_policy_transactions(state=state, repo_root=tmp_path)


def test_policy_evolution_rejects_non_overlay_apply_target(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, "prompts/execution.md", "before\n")
    _write(tmp_path, "candidates/execution.md", "after\n")
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    with pytest.raises(PolicyEvolutionError, match="may only target"):
        _create_authorized(
            _report(_record(changed_files=["candidates/execution.md"])),
            policy_authority,
            repo_root=tmp_path,
            candidate_changes={"prompts/execution.md": "candidates/execution.md"},
            affected_gates=("execution",),
        )

    assert target.read_text(encoding="utf-8") == "before\n"

    [proposal] = _create_authorized(
        _report(_record()),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("execution",),
        state=state,
        run_id="policy-run",
    )
    tampered = {
        **proposal,
        "changes": [
            {**proposal["changes"][0], "target_path": "prompts/execution.md"},
        ],
    }
    with pytest.raises(
        PolicyEvolutionError,
        match="caller proposal bytes do not match",
    ):
        approve_policy_proposal(
            tampered,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )
    assert target.read_text(encoding="utf-8") == "before\n"
    assert [
        event["kind"]
        for event in state.read_events_since(
            "policy-run",
            after_event_id=0,
            limit=10,
        )
    ] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
    ]


def test_policy_rollback_rejects_non_overlay_target_pointer(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, "prompts/execution.md", "current prompt\n")
    backup = _write(tmp_path, ".handoff/policy-rollbacks/ARP-live/execution.before", "before prompt\n")
    pointer = {
        "schema_version": "supervisor-autoresearch-policy-rollback/v1",
        "proposal_id": "ARP-live",
        "files": [{
            "target_path": "prompts/execution.md",
            "backup_ref": ".handoff/policy-rollbacks/ARP-live/execution.before",
            "before_hash": _sha(backup),
            "after_hash": "after",
        }],
    }

    with pytest.raises(PolicyEvolutionError, match="may only target"):
        rollback_policy_proposal(
            pointer,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
            reason="crafted pointer",
        )

    assert target.read_text(encoding="utf-8") == "current prompt\n"
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_policy_rollback_validates_all_targets_before_writing(tmp_path):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    prompt = _write(tmp_path, "prompts/execution.md", "current prompt\n")
    overlay_backup = _write(
        tmp_path,
        ".handoff/policy-rollbacks/ARP-live/policy-overlay.before",
        AFTER_OVERLAY,
    )
    prompt_backup = _write(
        tmp_path,
        ".handoff/policy-rollbacks/ARP-live/execution.before",
        "before prompt\n",
    )
    pointer = {
        "schema_version": "supervisor-autoresearch-policy-rollback/v1",
        "proposal_id": "ARP-live",
        "files": [
            {
                "target_path": ".supervisor/policy-overlay.yaml",
                "backup_ref": ".handoff/policy-rollbacks/ARP-live/policy-overlay.before",
                "before_hash": _sha(overlay_backup),
                "after_hash": _sha(overlay),
            },
            {
                "target_path": "prompts/execution.md",
                "backup_ref": ".handoff/policy-rollbacks/ARP-live/execution.before",
                "before_hash": _sha(prompt_backup),
                "after_hash": _sha(prompt),
            },
        ],
    }

    with pytest.raises(PolicyEvolutionError, match="may only target"):
        rollback_policy_proposal(
            pointer,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
            reason="crafted mixed pointer",
        )

    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    assert prompt.read_text(encoding="utf-8") == "current prompt\n"
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_denied_policy_proposal_records_denial_and_applies_nothing(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = _create_authorized(
        _report(_record()),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    denial = deny_policy_proposal(
        proposal,
        state=state,
        run_id="policy-run",
        approver="sam.zhang",
        approval_channel="codex_desktop",
        reason="needs stronger evaluator evidence",
    )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert denial["status"] == "denied"
    assert denial["reason"] == "needs stronger evaluator evidence"
    assert denial["default_change_allowed"] is False
    assert denial["gate_authority"] == "unchanged"
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
        "autoresearch_policy_proposal_denied",
    ]


def test_policy_proposal_rollback_pointer_restores_previous_artifact(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = _create_authorized(
        _report(_record()),
        policy_authority,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )
    before_hash = _sha(target)
    approval = approve_policy_proposal(
        proposal,
        state=state,
        run_id="policy-run",
        repo_root=tmp_path,
        approver="sam.zhang",
        approval_channel="codex_desktop",
    )
    assert target.read_text(encoding="utf-8") == AFTER_OVERLAY

    rollback = rollback_policy_proposal(
        approval["rollback_pointer"],
        state=state,
        run_id="policy-run",
        repo_root=tmp_path,
        approver="sam.zhang",
        approval_channel="codex_desktop",
        reason="operator requested revert",
    )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert _sha(target) == before_hash
    assert rollback["restored"][0]["restored_hash"] == before_hash
    assert rollback["default_change_allowed"] is False
    assert rollback["gate_advanced"] is False
    assert rollback["gate_authority"] == "unchanged"
    assert rollback["reviewer_panel_authority"] == "unchanged"
    assert rollback["typed_outcome_authority"] == "unchanged"
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_report_emitted",
        "autoresearch_policy_proposal_created",
        "autoresearch_policy_proposal_approved",
        "autoresearch_policy_proposal_rolled_back",
    ]


def test_paired_acceptance_report_oracle_coupling_blocks_policy_derivation(
    tmp_path,
    policy_authority,
):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    baseline_record = _derived_record(attempt_id="well-formed-baseline")
    baseline_proposals = _derive_authorized(
        _report(baseline_record),
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run-baseline",
    )
    assert baseline_proposals, "well-formed record must still derive a policy proposal"
    assert _contains_authorized(
        _report(baseline_record),
        policy_authority,
        repo_root=tmp_path,
    ) is True

    top_level_oracle_report = _report(_derived_record(attempt_id="top-level-oracle-coupled"))
    top_level_oracle_report["metric_applyable"] = False
    top_level_oracle_report["improvement_claim_allowed"] = False
    top_level_oracle_report["gaming_flags"] = ["oracle_coupled_treatment_arm"]
    top_level_oracle_report["report_sha256"] = (
        autoresearch_report_sha256(top_level_oracle_report)
    )
    top_level_state = State(str(tmp_path / "state-top-level-oracle.db"))

    assert derive_policy_evolution_proposals_from_report(
        top_level_oracle_report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=top_level_state,
        run_id="policy-run-top-level-oracle",
    ) == []
    assert report_contains_derivable_policy_record(top_level_oracle_report, repo_root=tmp_path) is False
    assert create_policy_evolution_proposals(
        top_level_oracle_report,
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/policy-overlay.yaml"},
        affected_gates=("outcome_review",),
    ) == []
    [skip_event] = top_level_state.read_events_since(
        "policy-run-top-level-oracle",
        after_event_id=0,
        limit=10,
    )
    assert skip_event["kind"] == "autoresearch_policy_proposal_derivation_skipped"
    assert skip_event["payload"]["reason"] == (
        "report metric_applyable must not be false for policy derivation"
    )
    assert skip_event["payload"]["automatic_policy_mutation"] is False

    non_applyable_record = _derived_record(
        attempt_id="oracle-coupled-non-applyable",
        metric_applyable=False,
    )
    non_applyable_report = _report(non_applyable_record)
    non_applyable_proposals = _derive_authorized(
        non_applyable_report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=State(str(tmp_path / "state-non-applyable.db")),
        run_id="policy-run-non-applyable",
    )
    assert non_applyable_proposals == []
    assert _contains_authorized(
        non_applyable_report,
        policy_authority,
        repo_root=tmp_path,
    ) is False

    no_claim_record = _derived_record(
        attempt_id="oracle-coupled-no-claim",
        improvement_claim_allowed=False,
    )
    no_claim_report = _report(no_claim_record)
    with pytest.raises(ValueError, match="derived by ClaimGate"):
        _derive_authorized(
            no_claim_report,
            policy_authority,
            repo_root=tmp_path,
            affected_gates=("outcome_review",),
            state=State(str(tmp_path / "state-no-claim.db")),
            run_id="policy-run-no-claim",
        )

    oracle_coupled_record = _derived_record(
        attempt_id="oracle-coupled-gaming-flag",
        gaming_flags=["oracle_coupled_treatment_arm"],
    )
    oracle_coupled_report = _report(oracle_coupled_record)
    oracle_coupled_proposals = _derive_authorized(
        oracle_coupled_report,
        policy_authority,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=State(str(tmp_path / "state-oracle-coupled.db")),
        run_id="policy-run-oracle-coupled",
    )
    assert oracle_coupled_proposals == []
    assert _contains_authorized(
        oracle_coupled_report,
        policy_authority,
        repo_root=tmp_path,
    ) is False
