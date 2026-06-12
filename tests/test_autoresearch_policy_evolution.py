from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.autoresearch.orchestrator import run_autoresearch_fixture
from supervisor.autoresearch.policy_evolution import (
    PolicyEvolutionError,
    approve_policy_proposal,
    create_policy_evolution_proposals,
    deny_policy_proposal,
    derive_policy_evolution_proposals_from_report,
    rollback_policy_proposal,
)
from supervisor.autoresearch.report import build_autoresearch_report
from supervisor.autoresearch.schema import AutoresearchAttempt, AutoresearchExperiment
from supervisor.autoresearch.validation import validate_attempt
from supervisor.state import State


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


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


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
        "quality_unstable_across_trials": True,
        "metric_source": "evaluator_execution",
        "evaluator_run_ref": "docs/dual-agent/run/evaluator-runs/attempt-policy-1.json",
        "evaluator_run_hash": "evaluator-run-hash",
        "changed_files": ["candidates/outcome-review.md"],
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
    return {
        "schema_version": "supervisor-autoresearch-summary/v1",
        "report_sha256": "report-sha",
        "default_change_allowed": False,
        "report_only": {
            "default_change_allowed": False,
            "policy_mutated": False,
            "operator_review_required": True,
        },
        "records": list(records),
    }


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


def _proposal_fixture(root: Path) -> tuple[State, Path, Path, dict]:
    state = State(str(root / "state.db"))
    target = _write(root, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(root, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = create_policy_evolution_proposals(
        _report(_record()),
        repo_root=root,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
    )
    return state, target, candidate, proposal


def test_accepted_report_derives_overlay_policy_proposal_without_candidate_changes_input(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)
    report = _report(_derived_record())
    report["report_ref"] = "docs/dual-agent/autoresearch/report.json"

    proposals = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
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
    assert proposal["derivation"]["report_sha256"] == report["report_sha256"]
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
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_created"]
    assert events[0]["payload"]["proposal_id"] == proposal["proposal_id"]


def test_validation_report_pipeline_derives_policy_proposal_without_operator_authored_changes(tmp_path):
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

    [proposal] = derive_policy_evolution_proposals_from_report(
        report,
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


def test_autoresearch_report_acceptance_auto_derives_overlay_proposal(tmp_path):
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
    )

    assert report["records"][0]["validation_status"] == "accepted"
    assert report["derived_policy_proposals"][0]["status"] == "draft"
    events = state.read_events_since("autoresearch-run", after_event_id=0, limit=50)
    kinds = [event["kind"] for event in events]
    assert kinds.count("autoresearch_report_emitted") == 1
    assert kinds.count("autoresearch_policy_proposal_created") == 1
    proposal = [event for event in events if event["kind"] == "autoresearch_policy_proposal_created"][0]
    assert proposal["payload"]["source"] == "autoresearch_deriver"
    assert proposal["payload"]["automatic_policy_mutation"] is False


def test_validation_report_derives_from_direct_policy_overlay_candidate_ref(tmp_path):
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
    )
    report = build_autoresearch_report([
        validate_attempt(experiment=experiment, attempt=attempt, repo_root=tmp_path)
    ])

    [proposal] = derive_policy_evolution_proposals_from_report(
        report,
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
    )

    record = report["records"][0]
    assert record["policy_candidate_changes"] == {}
    assert record["policy_overlay_candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["derivation"]["candidate_ref"] == "candidates/policy-overlay.yaml"
    assert proposal["status"] == "draft"


def test_deriver_skips_gaming_flagged_and_non_positive_metric_reports(tmp_path):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = derive_policy_evolution_proposals_from_report(
        _report(
            _derived_record(attempt_id="gaming", gaming_flags=["zero_variance_trials"]),
            _derived_record(attempt_id="zero-delta", metric_before=0.74, metric_after=0.74, metric_delta=0.0),
            _derived_record(attempt_id="negative-delta", metric_before=0.8, metric_after=0.7, metric_delta=-0.1),
        ),
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == [
        "autoresearch_policy_proposal_derivation_skipped",
        "autoresearch_policy_proposal_derivation_skipped",
    ]
    assert all(event["payload"]["automatic_policy_mutation"] is False for event in events)


def test_deriver_rejects_inconsistent_explicit_metric_delta(tmp_path):
    state = State(str(tmp_path / "state.db"))
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = derive_policy_evolution_proposals_from_report(
        _report(_derived_record(
            attempt_id="contradictory-delta",
            metric_before=0.7,
            metric_after=0.6,
            metric_delta=0.2,
        )),
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_derivation_skipped"]
    assert "metric delta must match" in events[0]["payload"]["reason"]


def test_deriver_skips_rejected_and_non_evaluator_backed_records_at_public_boundary(tmp_path):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    proposals = derive_policy_evolution_proposals_from_report(
        _report(
            _derived_record(attempt_id="rejected", validation_status="rejected"),
            _derived_record(attempt_id="fixture-metric", metric_source="fixture"),
            _derived_record(attempt_id="missing-run-ref", evaluator_run_ref=""),
            _derived_record(attempt_id="missing-run-hash", evaluator_run_hash=""),
        ),
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_deriver_rejects_missing_candidate_artifact_with_skip_event(tmp_path):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)

    proposals = derive_policy_evolution_proposals_from_report(
        _report(_derived_record(
            attempt_id="missing-candidate",
            changed_files=["candidates/outcome-review.md"],
            policy_overlay_candidate_ref="",
            candidate_overlay_ref="",
            candidate_artifacts={},
        )),
        repo_root=tmp_path,
        affected_gates=("outcome_review",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_derivation_skipped"]
    assert "exactly one policy overlay candidate artifact is required" in events[0]["payload"]["reason"]
    assert events[0]["payload"]["gate_advanced"] is False


def test_deriver_rejects_direct_non_overlay_candidate_ref_at_derivation(tmp_path):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    prompt_candidate = _write(tmp_path, "candidates/execution.md", "new execution prompt\n")

    proposals = derive_policy_evolution_proposals_from_report(
        _report(_derived_record(
            attempt_id="direct-non-overlay-candidate",
            changed_files=["candidates/execution.md"],
            policy_overlay_candidate_ref="candidates/execution.md",
        )),
        repo_root=tmp_path,
        affected_gates=("execution",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    assert prompt_candidate.read_text(encoding="utf-8") == "new execution prompt\n"
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_derivation_skipped"]
    assert "derived policy candidate must be a policy-overlay.yaml artifact" in events[0]["payload"]["reason"]
    assert events[0]["payload"]["gate_advanced"] is False


def test_deriver_rejects_non_overlay_candidate_at_derivation(tmp_path):
    state = State(str(tmp_path / "state.db"))
    overlay = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    prompt = _write(tmp_path, "prompts/execution.md", "old prompt\n")
    _write(tmp_path, "candidates/execution.md", "new prompt\n")

    proposals = derive_policy_evolution_proposals_from_report(
        _report(_derived_record(
            policy_candidate_changes={"prompts/execution.md": "candidates/execution.md"},
            changed_files=["candidates/execution.md"],
        )),
        repo_root=tmp_path,
        affected_gates=("execution",),
        state=state,
        run_id="policy-run",
    )

    assert proposals == []
    assert overlay.read_text(encoding="utf-8") == BASE_OVERLAY
    assert prompt.read_text(encoding="utf-8") == "old prompt\n"
    events = state.read_events_since("policy-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_derivation_skipped"]
    assert "may only target" in events[0]["payload"]["reason"]
    assert events[0]["payload"]["gate_advanced"] is False


def test_derived_proposal_still_requires_operator_approval(tmp_path):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/policy-overlay.yaml", AFTER_OVERLAY)

    [proposal] = derive_policy_evolution_proposals_from_report(
        _report(_derived_record()),
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


def test_accepted_autoresearch_attempt_creates_policy_proposal_without_mutation(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    proposals = create_policy_evolution_proposals(
        _report(_record()),
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
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_created"]
    assert events[0]["payload"]["proposal_id"] == proposal["proposal_id"]


def test_rejected_or_gaming_flagged_attempt_creates_no_applyable_policy_proposal(tmp_path):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    proposals = create_policy_evolution_proposals(
        _report(
            _record(validation_status="rejected"),
            _record(attempt_id="attempt-policy-2", gaming_flags=["zero_variance_trials"]),
        ),
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
    )

    assert proposals == []


def test_non_evaluator_backed_or_mutating_attempt_creates_no_applyable_policy_proposal(tmp_path):
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    proposals = create_policy_evolution_proposals(
        _report(
            _record(attempt_id="fixture-metric", metric_source="fixture"),
            _record(attempt_id="missing-run-ref", evaluator_run_ref=""),
            _record(attempt_id="missing-run-hash", evaluator_run_hash=""),
            _record(attempt_id="default-change", default_change_allowed=True),
            _record(attempt_id="policy-mutated", policy_mutated=True),
            _record(attempt_id="gate-advanced", gate_advanced=True),
        ),
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
    )

    assert proposals == []


def test_approved_policy_proposal_applies_exact_recorded_candidate_and_records_hashes(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    candidate = _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = create_policy_evolution_proposals(
        _report(_record()),
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
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
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_approved"]
    assert events[0]["payload"]["after_hash"] == after_hash


def test_approval_rejects_stale_target_before_hash(tmp_path):
    state, target, _candidate, proposal = _proposal_fixture(tmp_path)
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
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_approval_and_denial_require_operator_identity_before_mutation_or_events(tmp_path):
    state, target, _candidate, proposal = _proposal_fixture(tmp_path)

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
        assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []

        with pytest.raises(PolicyEvolutionError):
            deny_policy_proposal(
                proposal,
                state=state,
                run_id="policy-run",
                reason="no operator identity",
                **kwargs,
            )
        assert target.read_text(encoding="utf-8") == BASE_OVERLAY
        assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_rollback_requires_operator_identity_before_mutation_or_events(tmp_path):
    state, target, _candidate, proposal = _proposal_fixture(tmp_path)
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
            "autoresearch_policy_proposal_approved",
        ]


def test_approval_rejects_tampered_candidate_after_hash(tmp_path):
    state, target, candidate, proposal = _proposal_fixture(tmp_path)
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
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_approval_rejects_post_write_hash_mismatch(tmp_path, monkeypatch):
    state, target, _candidate, proposal = _proposal_fixture(tmp_path)
    original_write_bytes = Path.write_bytes
    target_path = target.resolve()
    corrupted_once = False

    def corrupt_target_write(path: Path, data: bytes) -> int:
        nonlocal corrupted_once
        if path.resolve() == target_path and not corrupted_once:
            corrupted_once = True
            return original_write_bytes(path, b"corrupted write\n")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", corrupt_target_write)

    with pytest.raises(PolicyEvolutionError, match="applied artifact hash mismatch"):
        approve_policy_proposal(
            proposal,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )

    assert target.read_text(encoding="utf-8") == BASE_OVERLAY
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


def test_policy_evolution_rejects_non_overlay_apply_target(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, "prompts/execution.md", "before\n")
    _write(tmp_path, "candidates/execution.md", "after\n")
    _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)

    with pytest.raises(PolicyEvolutionError, match="may only target"):
        create_policy_evolution_proposals(
            _report(_record(changed_files=["candidates/execution.md"])),
            repo_root=tmp_path,
            candidate_changes={"prompts/execution.md": "candidates/execution.md"},
            affected_gates=("execution",),
        )

    assert target.read_text(encoding="utf-8") == "before\n"

    [proposal] = create_policy_evolution_proposals(
        _report(_record()),
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("execution",),
    )
    tampered = {
        **proposal,
        "changes": [
            {**proposal["changes"][0], "target_path": "prompts/execution.md"},
        ],
    }
    with pytest.raises(PolicyEvolutionError, match="may only target"):
        approve_policy_proposal(
            tampered,
            state=state,
            run_id="policy-run",
            repo_root=tmp_path,
            approver="sam.zhang",
            approval_channel="codex_desktop",
        )
    assert target.read_text(encoding="utf-8") == "before\n"
    assert state.read_events_since("policy-run", after_event_id=0, limit=10) == []


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


def test_denied_policy_proposal_records_denial_and_applies_nothing(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = create_policy_evolution_proposals(
        _report(_record()),
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
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
    assert [event["kind"] for event in events] == ["autoresearch_policy_proposal_denied"]


def test_policy_proposal_rollback_pointer_restores_previous_artifact(tmp_path):
    state = State(str(tmp_path / "state.db"))
    target = _write(tmp_path, ".supervisor/policy-overlay.yaml", BASE_OVERLAY)
    _write(tmp_path, "candidates/outcome-review.md", AFTER_OVERLAY)
    [proposal] = create_policy_evolution_proposals(
        _report(_record()),
        repo_root=tmp_path,
        candidate_changes={".supervisor/policy-overlay.yaml": "candidates/outcome-review.md"},
        affected_gates=("outcome_review",),
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
        "autoresearch_policy_proposal_approved",
        "autoresearch_policy_proposal_rolled_back",
    ]
