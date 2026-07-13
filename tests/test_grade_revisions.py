from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from supervisor.grade_revisions import (
    DecisionGradeCitation,
    GradeBook,
    GradeValidationError,
    RunEnvelopeRef,
    SupersessionConflict,
    project_gradebook_to_trace,
)
from supervisor.task_environment import FrozenTaskResult, Grade
from supervisor.trace_graph import EdgeType, NodeType, TraceGraphStore


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _frozen_result() -> FrozenTaskResult:
    return FrozenTaskResult.create(
        task_id="task-1",
        task_family="generic",
        task_spec_hash=_hash("task-spec"),
        run_result_hash=_hash("run-result"),
        patch="diff --git a/a.py b/a.py\n",
        output="done",
        frozen_at_ms=1,
    )


def _grade(
    frozen: FrozenTaskResult,
    *,
    version: str,
    score: float,
    evidence: dict[str, object],
) -> Grade:
    return Grade(
        verifier_id="official-verifier",
        verifier_version=version,
        verifier_hash=_hash(f"implementation-{version}"),
        frozen_result_hash=frozen.result_hash,
        passed=score == 1.0,
        score=score,
        evidence=evidence,
        failure_classification="" if score == 1.0 else "tests_failed",
        flake_classification="none",
    )


def test_gradebook_appends_a_hash_pinned_grade_revision(tmp_path: Path) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )
    source_evidence = {"tests": {"passed": 12, "failed": 0}}

    with GradeBook(tmp_path / "grades.db") as gradebook:
        revision = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence=source_evidence,
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        source_evidence["tests"] = {"passed": 0, "failed": 12}

        persisted = gradebook.get_revision(revision.grade_id)

    assert revision.grade_id.startswith("grade_")
    assert len(revision.revision_hash) == 64
    assert revision.revision_number == 1
    assert revision.run_envelope == run
    assert revision.verifier_implementation_hash == _hash("implementation-1.0")
    assert persisted.revision_hash == revision.revision_hash
    assert persisted.evidence == {"tests": {"passed": 12, "failed": 0}}


def test_insert_or_replace_cannot_replace_an_immutable_grade_revision(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        revision = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"tests": "passed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        assert gradebook._conn.execute(
            "PRAGMA recursive_triggers"
        ).fetchone()[0] == 1
        gradebook._conn.execute("PRAGMA recursive_triggers=OFF")

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            gradebook._conn.execute(
                """INSERT OR REPLACE INTO grade_revisions(
                     grade_id, revision_hash, revision_number,
                     run_id, run_envelope_hash, frozen_result_hash,
                     verifier_id, verifier_version, verifier_config_hash,
                     verifier_implementation_hash, passed, score,
                     evidence_json, failure_classification,
                     flake_classification, supersedes_grade_id,
                     recorded_at_ms)
                   SELECT grade_id, revision_hash, revision_number,
                          run_id, run_envelope_hash, frozen_result_hash,
                          verifier_id, verifier_version,
                          verifier_config_hash,
                          verifier_implementation_hash, passed, 0.5,
                          evidence_json, failure_classification,
                          flake_classification, supersedes_grade_id,
                          recorded_at_ms
                     FROM grade_revisions
                    WHERE grade_id=?""",
                (revision.grade_id,),
            )

        assert gradebook.get_revision(revision.grade_id).score == 1.0


def test_insert_or_replace_cannot_replace_an_immutable_grade_invalidation(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        revision = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"tests": "passed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        invalidation = gradebook.invalidate_grade(
            revision.grade_id,
            reason="original reason",
        )
        gradebook._conn.execute("PRAGMA recursive_triggers=OFF")

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            gradebook._conn.execute(
                """INSERT OR REPLACE INTO grade_invalidations(
                     invalidation_sequence, invalidation_id,
                     invalidation_hash, grade_id, grade_revision_hash,
                     kind, reason, replacement_grade_id,
                     replacement_revision_hash, recorded_at_ms)
                   SELECT invalidation_sequence, invalidation_id,
                          invalidation_hash, grade_id, grade_revision_hash,
                          kind, 'forged reason', replacement_grade_id,
                          replacement_revision_hash, recorded_at_ms
                     FROM grade_invalidations
                    WHERE invalidation_id=?""",
                (invalidation.invalidation_id,),
            )

        [persisted] = gradebook.list_invalidations(revision.grade_id)
        assert persisted.reason == "original reason"


def test_regrading_preserves_history_and_records_invalidation(tmp_path: Path) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"tests": {"failed": 1}},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        second = gradebook.regrade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=1.0,
                evidence={"tests": {"passed": 1}},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=first.grade_id,
            reason="pinned verifier rerun",
        )

        history = gradebook.list_revisions(run)
        invalidations = gradebook.list_invalidations(first.grade_id)

    assert [item.grade_id for item in history] == [first.grade_id, second.grade_id]
    assert second.revision_number == 2
    assert second.supersedes_grade_id == first.grade_id
    assert history[0].score == 0.0
    assert history[1].score == 1.0
    assert len(invalidations) == 1
    assert invalidations[0].kind == "superseded"
    assert invalidations[0].reason == "pinned verifier rerun"
    assert invalidations[0].grade_revision_hash == first.revision_hash
    assert invalidations[0].replacement_grade_id == second.grade_id
    assert invalidations[0].replacement_revision_hash == second.revision_hash


def test_normal_grade_append_is_idempotent_but_requires_explicit_regrade(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )
    grade = _grade(
        frozen,
        version="1.0",
        score=1.0,
        evidence={"tests": "passed"},
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=grade,
            verifier_config_hash=_hash("config-1.0"),
        )
        repeated = gradebook.append_grade(
            run=run,
            grade=grade,
            verifier_config_hash=_hash("config-1.0"),
        )

        with pytest.raises(SupersessionConflict, match="already has a grade"):
            gradebook.append_grade(
                run=run,
                grade=_grade(
                    frozen,
                    version="2.0",
                    score=0.0,
                    evidence={"tests": "failed"},
                ),
                verifier_config_hash=_hash("config-2.0"),
            )

        history = gradebook.list_revisions(run)

    assert repeated == first
    assert [item.grade_id for item in history] == [first.grade_id]


def test_grade_backed_decision_persists_exact_grade_lineage(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        revision = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"tests": "passed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        citation = DecisionGradeCitation(
            revision.grade_id,
            revision.revision_hash,
        )
        decision = gradebook.record_decision(
            decision_id="decision-1",
            decision={"status": "accepted"},
            citations=[citation],
        )
        repeated = gradebook.record_decision(
            decision_id="decision-1",
            decision={"status": "accepted"},
            citations=[citation],
        )

        with pytest.raises(
            GradeValidationError,
            match="missing_grade_citation",
        ):
            gradebook.record_decision(
                decision_id="decision-without-grade",
                decision={"status": "accepted"},
                citations=[],
            )
        with pytest.raises(
            GradeValidationError,
            match="grade_revision_hash_mismatch",
        ):
            gradebook.record_decision(
                decision_id="decision-wrong-grade",
                decision={"status": "accepted"},
                citations=[
                    DecisionGradeCitation(
                        revision.grade_id,
                        _hash("wrong-revision"),
                    )
                ],
            )
        with pytest.raises(
            GradeValidationError,
            match="decision discrepancy",
        ):
            gradebook.record_decision(
                decision_id="decision-1",
                decision={"status": "rejected"},
                citations=[citation],
            )

        gradebook._conn.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            gradebook._conn.execute(
                """INSERT OR REPLACE INTO grade_decisions(
                     decision_id, decision_hash, decision_json,
                     grade_citations_json, recorded_at_ms)
                   SELECT decision_id, decision_hash, '{"status":"forged"}',
                          grade_citations_json, recorded_at_ms
                     FROM grade_decisions
                    WHERE decision_id='decision-1'"""
            )

        persisted = gradebook.get_decision("decision-1")

    assert repeated == decision
    assert persisted == decision
    assert persisted.grade_citations[0].grade_id == revision.grade_id
    assert (
        persisted.grade_citations[0].revision_hash
        == revision.revision_hash
    )


def test_gradebook_prevents_branching_supersession(tmp_path: Path) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        root = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"attempt": 1},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=1.0,
                evidence={"attempt": 2},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=root.grade_id,
        )

        with pytest.raises(SupersessionConflict, match="already been superseded"):
            gradebook.append_grade(
                run=run,
                grade=_grade(
                    frozen,
                    version="3.0",
                    score=1.0,
                    evidence={"attempt": 3},
                ),
                verifier_config_hash=_hash("config-3.0"),
                supersedes_grade_id=root.grade_id,
            )

        assert len(gradebook.list_revisions(run)) == 2


def test_decision_validation_requires_exact_stale_grade_acknowledgement(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"tests": "old"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        second = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=1.0,
                evidence={"tests": "new"},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=first.grade_id,
        )
        invalidation = gradebook.list_invalidations(first.grade_id)[0]

        stale = gradebook.validate_decision(
            [DecisionGradeCitation(first.grade_id, first.revision_hash)]
        )
        wrong_revision = gradebook.validate_decision(
            [DecisionGradeCitation(second.grade_id, _hash("wrong-revision"))]
        )
        acknowledged_only = gradebook.validate_decision([
            DecisionGradeCitation(
                first.grade_id,
                first.revision_hash,
                acknowledged_invalidation_hashes=(
                    invalidation.invalidation_hash,
                ),
            )
        ])
        resolved = gradebook.validate_decision([
            DecisionGradeCitation(
                first.grade_id,
                first.revision_hash,
                acknowledged_invalidation_hashes=(
                    invalidation.invalidation_hash,
                ),
                resolution_grade_id=second.grade_id,
                resolution_revision_hash=second.revision_hash,
            )
        ])
        current = gradebook.validate_decision(
            [DecisionGradeCitation(second.grade_id, second.revision_hash)]
        )

    assert stale.accepted is False
    assert [blocker.code for blocker in stale.blockers] == [
        "stale_grade_unacknowledged"
    ]
    assert [blocker.code for blocker in wrong_revision.blockers] == [
        "grade_revision_hash_mismatch"
    ]
    assert acknowledged_only.accepted is False
    assert [blocker.code for blocker in acknowledged_only.blockers] == [
        "stale_grade_unresolved"
    ]
    assert resolved.accepted is True
    assert current.accepted is True


def test_explicit_invalidation_is_append_only_and_blocks_unacknowledged_use(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )
    database = tmp_path / "grades.db"

    with GradeBook(database) as gradebook:
        revision = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"tests": "passed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        invalidation = gradebook.invalidate_grade(
            revision.grade_id,
            reason="verifier fixture was contaminated",
        )

    with GradeBook(database) as reopened:
        history = reopened.list_revisions(run)
        records = reopened.list_invalidations(revision.grade_id)
        validation = reopened.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )

    assert [item.grade_id for item in history] == [revision.grade_id]
    assert [item.invalidation_hash for item in records] == [
        invalidation.invalidation_hash
    ]
    assert validation.accepted is False


def test_gradebook_rejects_grades_without_hash_pinned_verifier_provenance(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )
    unpinned = Grade(
        verifier_id="",
        verifier_version="1.0",
        verifier_hash="",
        frozen_result_hash=frozen.result_hash,
        passed=True,
        score=1.0,
        evidence={},
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        with pytest.raises(GradeValidationError):
            gradebook.append_grade(
                run=run,
                grade=unpinned,
                verifier_config_hash=_hash("config"),
            )


def test_gradebook_trace_projection_appends_exact_immutable_lineage(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-1",
        run_envelope_hash=_hash("run-envelope"),
        frozen_result=frozen,
    )
    trace_database = tmp_path / "trace.db"

    with GradeBook(tmp_path / "grades.db") as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"tests": {"failed": 1}},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        initial = project_gradebook_to_trace(
            gradebook,
            run,
            namespace="harness-v1/test-grades",
        )
        initial_first_node = initial.nodes[0]
        with TraceGraphStore(trace_database) as trace_store:
            trace_store.append(initial)

        second = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=1.0,
                evidence={"tests": {"passed": 1}},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=first.grade_id,
        )
        explicit_invalidation = gradebook.invalidate_grade(
            second.grade_id,
            reason="post-hoc verifier contamination",
        )
        supersession_invalidation = gradebook.list_invalidations(
            first.grade_id
        )[0]
        projected = gradebook.to_trace_graph(
            run,
            namespace="harness-v1/test-grades",
        )

    with TraceGraphStore(trace_database) as trace_store:
        trace_store.append(projected)
    with TraceGraphStore(trace_database) as reopened:
        reloaded = reopened.load()

    nodes_by_revision_hash = {
        node.identity.revision_hash: node for node in reloaded.nodes
    }
    edges = {
        (
            edge.source.revision_hash,
            edge.relation,
            edge.target.revision_hash,
        )
        for edge in reloaded.edges
    }

    assert reloaded.canonical_bytes() == projected.canonical_bytes()
    assert initial_first_node == nodes_by_revision_hash[first.revision_hash]
    assert initial_first_node.identity.node_type is NodeType.GRADE
    with pytest.raises(TypeError):
        initial_first_node.attributes["score"] = 0.5
    assert {
        first.revision_hash,
        second.revision_hash,
        supersession_invalidation.invalidation_hash,
        explicit_invalidation.invalidation_hash,
    } == set(nodes_by_revision_hash)
    assert (
        second.revision_hash,
        EdgeType.SUPERSEDES,
        first.revision_hash,
    ) in edges
    assert (
        supersession_invalidation.invalidation_hash,
        EdgeType.INVALIDATES,
        first.revision_hash,
    ) in edges
    assert (
        explicit_invalidation.invalidation_hash,
        EdgeType.INVALIDATES,
        second.revision_hash,
    ) in edges
    assert nodes_by_revision_hash[first.revision_hash].attributes[
        "revision_hash"
    ] == first.revision_hash
    assert nodes_by_revision_hash[
        supersession_invalidation.invalidation_hash
    ].attributes["invalidation_hash"] == (
        supersession_invalidation.invalidation_hash
    )
