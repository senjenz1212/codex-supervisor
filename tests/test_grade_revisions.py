from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from supervisor.grade_revisions import (
    DecisionGradeCitation,
    GradeBook,
    GradeIntegrityError,
    GradeRevision,
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


def _commit_completed(
    gradebook: GradeBook,
    revision: GradeRevision,
    *,
    label: str,
) -> None:
    gradebook.commit_terminal_grade(
        grade_id=revision.grade_id,
        revision_hash=revision.revision_hash,
        experiment_id=f"experiment-{label}",
        task_id=f"task-{label}",
        arm="supervisor",
        terminal_state="completed",
        terminal_state_hash=_hash(f"terminal-state-{label}"),
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


def test_passing_grade_requires_a_durable_terminal_commit(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-terminal-commit",
        run_envelope_hash=_hash("run-envelope-terminal-commit"),
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
        uncommitted = gradebook.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )
        with pytest.raises(
            GradeValidationError,
            match="passing grade terminal commit must bind completed state",
        ):
            gradebook.commit_terminal_grade(
                grade_id=revision.grade_id,
                revision_hash=revision.revision_hash,
                experiment_id="experiment-1",
                task_id="task-1",
                arm="supervisor",
                terminal_state="failed",
                terminal_state_hash=_hash("failed-terminal-state"),
            )
        terminal_commit = gradebook.commit_terminal_grade(
            grade_id=revision.grade_id,
            revision_hash=revision.revision_hash,
            experiment_id="experiment-1",
            task_id="task-1",
            arm="supervisor",
            terminal_state="completed",
            terminal_state_hash=_hash("terminal-state"),
        )
        committed = gradebook.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )

    with GradeBook(database) as reopened:
        persisted_commit = reopened.get_terminal_commit(revision.grade_id)
        reopened_validation = reopened.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )

    assert [blocker.code for blocker in uncommitted.blockers] == [
        "grade_terminal_commit_missing"
    ]
    assert terminal_commit.grade_id == revision.grade_id
    assert terminal_commit.grade_revision_hash == revision.revision_hash
    assert terminal_commit.terminal_state == "completed"
    assert persisted_commit == terminal_commit
    assert committed.accepted is True
    assert reopened_validation.accepted is True


def test_failing_grade_requires_a_durable_failed_terminal_commit(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-failed-terminal-commit",
        run_envelope_hash=_hash("run-envelope-failed-terminal-commit"),
        frozen_result=frozen,
    )
    database = tmp_path / "grades.db"

    with GradeBook(database) as gradebook:
        revision = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"execution": "failed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        uncommitted = gradebook.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )
        terminal_commit = gradebook.commit_terminal_grade(
            grade_id=revision.grade_id,
            revision_hash=revision.revision_hash,
            experiment_id="experiment-1",
            task_id="task-1",
            arm="supervisor",
            terminal_state="failed",
            terminal_state_hash=_hash("failed-terminal-state"),
        )

    with GradeBook(database) as reopened:
        persisted_commit = reopened.get_terminal_commit(revision.grade_id)
        reopened_validation = reopened.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )

    assert [blocker.code for blocker in uncommitted.blockers] == [
        "grade_terminal_commit_missing"
    ]
    assert terminal_commit.terminal_state == "failed"
    assert persisted_commit == terminal_commit
    assert reopened_validation.accepted is True


def test_gradebook_upgrades_pass_only_terminal_commit_schema(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    first_run = RunEnvelopeRef.from_frozen_result(
        run_id="run-schema-upgrade",
        run_envelope_hash=_hash("run-envelope-schema-upgrade"),
        frozen_result=frozen,
    )
    second_run = RunEnvelopeRef.from_frozen_result(
        run_id="run-schema-upgrade-failed",
        run_envelope_hash=_hash("run-envelope-schema-upgrade-failed"),
        frozen_result=frozen,
    )
    database = tmp_path / "grades.db"
    completed_hash = _hash("completed-terminal")

    with GradeBook(database) as gradebook:
        passing = gradebook.append_grade(
            run=first_run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"tests": "passed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        passing_commit = gradebook.commit_terminal_grade(
            grade_id=passing.grade_id,
            revision_hash=passing.revision_hash,
            experiment_id="experiment-1",
            task_id="task-1",
            arm="supervisor",
            terminal_state="completed",
            terminal_state_hash=completed_hash,
        )
        verifier_failure = gradebook.regrade(
            run=first_run,
            grade=_grade(
                frozen,
                version="2.0",
                score=0.0,
                evidence={"tests": "failed"},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=passing.grade_id,
            reason="pinned verifier rerun",
        )
        itt_failure = gradebook.append_grade(
            run=second_run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"execution": "failed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )

    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            DROP TRIGGER grade_terminal_commits_no_replace;
            DROP TRIGGER grade_terminal_commits_no_update;
            DROP TRIGGER grade_terminal_commits_no_delete;
            ALTER TABLE grade_terminal_commits
              RENAME TO grade_terminal_commits_current;
            CREATE TABLE grade_terminal_commits (
              commit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              commit_id TEXT NOT NULL UNIQUE,
              commit_hash TEXT NOT NULL UNIQUE,
              grade_id TEXT NOT NULL UNIQUE
                REFERENCES grade_revisions(grade_id),
              grade_revision_hash TEXT NOT NULL,
              experiment_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              arm TEXT NOT NULL,
              terminal_state TEXT NOT NULL
                CHECK(terminal_state = 'completed'),
              terminal_state_hash TEXT NOT NULL UNIQUE,
              recorded_at_ms INTEGER NOT NULL
            );
            INSERT INTO grade_terminal_commits
            SELECT * FROM grade_terminal_commits_current;
            DROP TABLE grade_terminal_commits_current;
            """
        )

    with GradeBook(database) as reopened:
        assert reopened.get_terminal_commit(passing.grade_id) == passing_commit
        verifier_commit = reopened.commit_terminal_grade(
            grade_id=verifier_failure.grade_id,
            revision_hash=verifier_failure.revision_hash,
            experiment_id="experiment-1",
            task_id="task-1",
            arm="supervisor",
            terminal_state="completed",
            terminal_state_hash=completed_hash,
        )
        itt_commit = reopened.commit_terminal_grade(
            grade_id=itt_failure.grade_id,
            revision_hash=itt_failure.revision_hash,
            experiment_id="experiment-2",
            task_id="task-2",
            arm="supervisor",
            terminal_state="failed",
            terminal_state_hash=_hash("failed-terminal"),
        )

    assert verifier_commit.terminal_state == "completed"
    assert verifier_commit.terminal_state_hash == completed_hash
    assert itt_commit.terminal_state == "failed"


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
        _commit_completed(gradebook, revision, label="decision")
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


def test_superseding_grades_cannot_fork_terminal_identity(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-terminal-lineage",
        run_envelope_hash=_hash("run-envelope-terminal-lineage"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"attempt": 1},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        second = gradebook.regrade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=0.0,
                evidence={"attempt": 2},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=first.grade_id,
            reason="pinned verifier rerun",
        )
        first_commit = gradebook.commit_terminal_grade(
            grade_id=first.grade_id,
            revision_hash=first.revision_hash,
            experiment_id="experiment-1",
            task_id="task-1",
            arm="supervisor",
            terminal_state="completed",
            terminal_state_hash=_hash("terminal-state-1"),
        )

        with pytest.raises(
            GradeValidationError,
            match="supersession terminal identity discrepancy",
        ):
            gradebook.commit_terminal_grade(
                grade_id=second.grade_id,
                revision_hash=second.revision_hash,
                experiment_id="experiment-1",
                task_id="task-1",
                arm="supervisor",
                terminal_state="completed",
                terminal_state_hash=_hash("terminal-state-2"),
            )

        second_commit = gradebook.commit_terminal_grade(
            grade_id=second.grade_id,
            revision_hash=second.revision_hash,
            experiment_id=first_commit.experiment_id,
            task_id=first_commit.task_id,
            arm=first_commit.arm,
            terminal_state=first_commit.terminal_state,
            terminal_state_hash=first_commit.terminal_state_hash,
        )

    assert second_commit.terminal_state_hash == (
        first_commit.terminal_state_hash
    )


def test_terminal_identity_check_is_order_independent_across_supersession(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-terminal-lineage-reverse",
        run_envelope_hash=_hash("run-envelope-terminal-lineage-reverse"),
        frozen_result=frozen,
    )

    with GradeBook(tmp_path / "grades.db") as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=0.0,
                evidence={"attempt": 1},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        second = gradebook.regrade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=1.0,
                evidence={"attempt": 2},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=first.grade_id,
            reason="pinned verifier rerun",
        )
        gradebook.commit_terminal_grade(
            grade_id=second.grade_id,
            revision_hash=second.revision_hash,
            experiment_id="experiment-1",
            task_id="task-1",
            arm="supervisor",
            terminal_state="completed",
            terminal_state_hash=_hash("terminal-state-1"),
        )

        with pytest.raises(
            GradeValidationError,
            match="supersession terminal identity discrepancy",
        ):
            gradebook.commit_terminal_grade(
                grade_id=first.grade_id,
                revision_hash=first.revision_hash,
                experiment_id="experiment-2",
                task_id="task-1",
                arm="supervisor",
                terminal_state="completed",
                terminal_state_hash=_hash("terminal-state-1"),
            )


def test_gradebook_rejects_legacy_conflicting_terminal_lineage(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-legacy-terminal-conflict",
        run_envelope_hash=_hash("run-envelope-legacy-terminal-conflict"),
        frozen_result=frozen,
    )
    database = tmp_path / "grades.db"

    with GradeBook(database) as gradebook:
        first = gradebook.append_grade(
            run=run,
            grade=_grade(
                frozen,
                version="1.0",
                score=1.0,
                evidence={"attempt": 1},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        second = gradebook.regrade(
            run=run,
            grade=_grade(
                frozen,
                version="2.0",
                score=0.0,
                evidence={"attempt": 2},
            ),
            verifier_config_hash=_hash("config-2.0"),
            supersedes_grade_id=first.grade_id,
            reason="pinned verifier rerun",
        )
        _commit_completed(gradebook, first, label="legacy")
        _commit_completed(gradebook, second, label="legacy")

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            DROP TRIGGER grade_terminal_commits_lineage_identity;
            DROP TRIGGER grade_terminal_commits_no_update;
            """
        )
        row = connection.execute(
            "SELECT * FROM grade_terminal_commits WHERE grade_id=?",
            (second.grade_id,),
        ).fetchone()
        assert row is not None
        payload = {
            "schema_version": "supervisor-grade-terminal-commit/v1",
            "commit_id": row["commit_id"],
            "grade_id": row["grade_id"],
            "grade_revision_hash": row["grade_revision_hash"],
            "experiment_id": "different-experiment",
            "task_id": row["task_id"],
            "arm": row["arm"],
            "terminal_state": row["terminal_state"],
            "terminal_state_hash": row["terminal_state_hash"],
            "recorded_at_ms": row["recorded_at_ms"],
        }
        commit_hash = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            UPDATE grade_terminal_commits
            SET experiment_id=?, commit_hash=?
            WHERE grade_id=?
            """,
            ("different-experiment", commit_hash, second.grade_id),
        )

    with pytest.raises(
        GradeIntegrityError,
        match="terminal identity discrepancy",
    ):
        GradeBook(database)


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
        _commit_completed(gradebook, first, label="current")
        _commit_completed(gradebook, second, label="current")
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


def test_emergency_quarantine_is_durable_and_cannot_authorize_a_decision(
    tmp_path: Path,
) -> None:
    frozen = _frozen_result()
    run = RunEnvelopeRef.from_frozen_result(
        run_id="run-quarantined",
        run_envelope_hash=_hash("run-envelope-quarantined"),
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
                evidence={"tests": "passed-before-terminal-write-failed"},
            ),
            verifier_config_hash=_hash("config-1.0"),
        )
        quarantine = gradebook.quarantine_grade(
            revision.grade_id,
            reason="terminal_persistence_failure",
        )
        live_validation = gradebook.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )

    with GradeBook(database) as reopened:
        [persisted] = reopened.list_invalidations(revision.grade_id)
        reopened_validation = reopened.validate_decision(
            [DecisionGradeCitation(revision.grade_id, revision.revision_hash)]
        )

    assert quarantine.kind == "quarantined"
    assert persisted == quarantine
    assert live_validation.accepted is False
    assert reopened_validation.accepted is False


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
