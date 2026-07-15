from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from supervisor.grade_revisions import GradeBook
from supervisor.production_trace import (
    ProductionTraceError,
    ProductionTraceEvidence,
    ProductionTraceRecorder,
)
from supervisor.trace_graph import (
    NodeType,
    TraceGraphStore,
    TracePlanningArtifactRef,
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _thaw(value):
    if isinstance(value, Mapping):
        return {
            str(key): _thaw(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _evidence() -> ProductionTraceEvidence:
    run_envelope_hash = _hash("run-envelope")
    frozen_result_hash = _hash("frozen-result")
    return ProductionTraceEvidence(
        task_id="task-1",
        task_hash=_hash("task"),
        run_id="run-1",
        run_envelope_hash=run_envelope_hash,
        frozen_result_hash=frozen_result_hash,
        gate="execution",
        gate_hash=_hash("gate"),
        planning_artifacts=(
            TracePlanningArtifactRef(
                kind="implementation_plan",
                path="/repo/docs/implementation-plan.md",
                sha256=_hash("implementation-plan"),
            ),
        ),
        runtime_provenance={
            "assignment_id": "assignment-1",
            "arm": "supervisor",
            "runtime_kind": "codex",
            "run_envelope_hash": run_envelope_hash,
            "model": "provider/model",
        },
        result_provenance={
            "frozen_result_hash": frozen_result_hash,
            "result_kind": "repository_patch",
            "result_receipt_hash": _hash("result-receipt"),
        },
        source_event_id="event-1",
        source_event_hash=_hash("source-event"),
        source_event_state="completed",
        source_event_recorded_at_ms=1_784_000_000_000,
        final_gate_result={
            "status": "accepted",
            "gate_result_hash": _hash("gate-result"),
            "checks": {"tests": "passed"},
        },
    )


def test_records_a_closed_post_execution_process_trace(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.db"
    gradebook_path = tmp_path / "grades.db"
    evidence = _evidence()

    receipt = ProductionTraceRecorder(
        trace_store_path=trace_path,
        gradebook_path=gradebook_path,
    ).record(evidence)

    assert receipt.claim_cap == "L1"
    assert receipt.closure["status"] == "accepted"
    assert Path(receipt.trace_store_path) == trace_path.resolve()
    assert Path(receipt.gradebook_path) == gradebook_path.resolve()
    assert receipt.record_fingerprint == evidence.fingerprint
    assert receipt.source_event_id == evidence.source_event_id
    assert receipt.source_event_hash == evidence.source_event_hash
    assert len(receipt.trace_store_sha256) == 64
    assert len(receipt.gradebook_sha256) == 64

    with TraceGraphStore(trace_path) as store:
        graph = store.load()
    assert [node.identity.node_type for node in graph.promotion_trace(
        receipt.promotion
    )] == [
        NodeType.OBJ,
        NodeType.REQ,
        NodeType.TEST,
        NodeType.ASN,
        NodeType.RUN,
        NodeType.ART,
        NodeType.GRADE,
        NodeType.ANL,
        NodeType.DEC,
        NodeType.PROMOTION,
    ]

    with GradeBook(gradebook_path) as gradebook:
        revision = gradebook.get_revision(receipt.grade_citation.grade_id)
        terminal_commit = gradebook.get_terminal_commit(revision.grade_id)

    assert revision.revision_hash == receipt.grade_citation.revision_hash
    assert revision.evidence["claim_cap"] == "L1"
    assert revision.evidence["hidden_outcome_evidence"] is False
    assert (
        _thaw(revision.evidence["production_trace_evidence"])
        == evidence.to_dict()
    )
    assert (
        revision.verifier_config_hash
        == receipt.verifier_config_hash
    )
    assert (
        revision.verifier_implementation_hash
        == receipt.verifier_implementation_hash
    )
    assert terminal_commit is not None
    assert terminal_commit.arm == evidence.arm
    assert terminal_commit.terminal_state_hash == evidence.source_event_hash
    assert terminal_commit.commit_hash == receipt.grade_terminal_commit_hash


def test_exact_retry_is_idempotent_across_reopened_stores(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.db"
    gradebook_path = tmp_path / "grades.db"
    evidence = _evidence()

    first = ProductionTraceRecorder(
        trace_store_path=trace_path,
        gradebook_path=gradebook_path,
    ).record(evidence)
    second = ProductionTraceRecorder(
        trace_store_path=trace_path,
        gradebook_path=gradebook_path,
    ).record(evidence)

    assert second == first
    with TraceGraphStore(trace_path) as store:
        graph = store.load()
    with GradeBook(gradebook_path) as gradebook:
        revisions = gradebook.list_revisions(
            gradebook.get_revision(
                first.grade_citation.grade_id
            ).run_envelope
        )
    assert len(graph.nodes) == 10
    assert len(graph.edges) == 9
    assert len(revisions) == 1


def test_retry_with_changed_runtime_evidence_fails_closed(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.db"
    gradebook_path = tmp_path / "grades.db"
    evidence = _evidence()
    recorder = ProductionTraceRecorder(
        trace_store_path=trace_path,
        gradebook_path=gradebook_path,
    )
    first = recorder.record(evidence)

    changed_runtime = dict(evidence.runtime_provenance)
    changed_runtime["model"] = "different/model"
    with pytest.raises(
        ProductionTraceError,
        match="changed immutable evidence",
    ):
        recorder.record(replace(
            evidence,
            runtime_provenance=changed_runtime,
        ))

    with GradeBook(gradebook_path) as gradebook:
        revision = gradebook.get_revision(
            first.grade_citation.grade_id
        )
        revisions = gradebook.list_revisions(revision.run_envelope)
    assert len(revisions) == 1


def test_missing_source_event_hash_is_rejected_before_persistence(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="source_event_hash"):
        replace(_evidence(), source_event_hash="")

    assert not (tmp_path / "trace.db").exists()
    assert not (tmp_path / "grades.db").exists()


def test_missing_planning_or_runtime_pins_are_rejected() -> None:
    evidence = _evidence()

    with pytest.raises(ValueError, match="planning_artifacts"):
        replace(evidence, planning_artifacts=())
    with pytest.raises(ValueError, match="assignment_id"):
        replace(
            evidence,
            runtime_provenance={
                "run_envelope_hash": evidence.run_envelope_hash,
                "arm": "supervisor",
            },
        )


def test_recorder_derives_the_graph_without_a_caller_graph(
    tmp_path: Path,
) -> None:
    parameters = inspect.signature(
        ProductionTraceRecorder.record
    ).parameters
    trace_path = tmp_path / "trace.db"

    assert "trace_graph" not in parameters
    assert not trace_path.exists()
    receipt = ProductionTraceRecorder(
        trace_store_path=trace_path,
        gradebook_path=tmp_path / "grades.db",
    ).record(_evidence())

    assert receipt.closure["status"] == "accepted"
    assert trace_path.exists()


def test_process_grade_does_not_treat_gate_outcome_as_hidden_evidence(
    tmp_path: Path,
) -> None:
    evidence = replace(
        _evidence(),
        final_gate_result={
            "status": "blocked",
            "gate_result_hash": _hash("blocked-gate-result"),
            "checks": {"tests": "failed"},
        },
    )
    receipt = ProductionTraceRecorder(
        trace_store_path=tmp_path / "trace.db",
        gradebook_path=tmp_path / "grades.db",
    ).record(evidence)

    with GradeBook(receipt.gradebook_path) as gradebook:
        revision = gradebook.get_revision(
            receipt.grade_citation.grade_id
        )

    assert revision.passed is False
    assert revision.score == 0.0
    assert revision.failure_classification == "gate_failed"
    assert revision.evidence["claim_cap"] == "L1"
    assert revision.evidence["hidden_outcome_evidence"] is False
    assert (
        revision.evidence["final_gate_result"]["status"]
        == "blocked"
    )
