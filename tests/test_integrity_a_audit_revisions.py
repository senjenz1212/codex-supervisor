from __future__ import annotations

import json
import sqlite3

import pytest

from supervisor.schema_migrations import run_forward_migrations
from supervisor.state import State


def test_quality_trend_audits_append_revisions_and_project_the_latest(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="audit-run",
        task_id="audit-task",
        task_class="source_change",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=2.0,
        details={"source": "ledger_events"},
    )

    first_projection = state.update_quality_trend_audit(
        run_id="audit-run",
        gate="outcome_review",
        sample_size=2,
        false_accept_count=1,
        false_accept_denominator=2,
        audit_details={"revision": "first"},
    )
    second_projection = state.update_quality_trend_audit(
        run_id="audit-run",
        gate="outcome_review",
        sample_size=4,
        false_accept_count=0,
        false_accept_denominator=4,
        audit_details={"revision": "second"},
    )

    audits = state.list_quality_trend_audits(
        run_id="audit-run",
        gate="outcome_review",
    )
    assert len(audits) == 2
    assert [audit["audit_details"]["revision"] for audit in audits] == ["first", "second"]
    assert audits[0]["computed_at"] < audits[1]["computed_at"]
    assert audits[0]["false_accept_count"] == 1
    assert audits[1]["false_accept_count"] == 0

    assert first_projection is not None
    assert first_projection["false_accept_count"] == 1
    assert second_projection is not None
    assert second_projection["p11_audit_sample_size"] == 4
    assert second_projection["false_accept_count"] == 0
    assert second_projection["false_accept_denominator"] == 4
    assert second_projection["details"]["p11_audit"] == {"revision": "second"}

    [stored_projection] = state.list_quality_trend_rows(
        task_class="source_change",
        gate="outcome_review",
    )
    assert stored_projection == second_projection


def test_quality_trend_audit_migration_backfills_existing_projection(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE supervisor_quality_trends (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             task_class TEXT NOT NULL,
             gate TEXT NOT NULL,
             accepted INTEGER NOT NULL,
             first_pass_accepted INTEGER NOT NULL,
             revision_rounds INTEGER NOT NULL,
             time_to_accepted_outcome_s REAL,
             p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
             false_accept_count INTEGER NOT NULL DEFAULT 0,
             false_accept_denominator INTEGER NOT NULL DEFAULT 0,
             false_accept_rate REAL NOT NULL DEFAULT 0.0,
             details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             UNIQUE(run_id, gate)
           )"""
    )
    conn.execute(
        """INSERT INTO supervisor_quality_trends(
             run_id, task_id, task_class, gate, accepted,
             first_pass_accepted, revision_rounds,
             time_to_accepted_outcome_s, p11_audit_sample_size,
             false_accept_count, false_accept_denominator,
             false_accept_rate, details_json, computed_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "legacy-run",
            "legacy-task",
            "source_change",
            "outcome_review",
            1,
            1,
            0,
            1.0,
            3,
            1,
            3,
            0.0,
            json.dumps({"p11_audit": {"source": "legacy"}}),
            123,
        ),
    )

    run_forward_migrations(conn)

    [audit] = conn.execute(
        "SELECT * FROM quality_trend_audits WHERE run_id=? AND gate=?",
        ("legacy-run", "outcome_review"),
    ).fetchall()
    assert audit["sample_size"] == 3
    assert audit["false_accept_count"] == 1
    assert audit["false_accept_denominator"] == 3
    assert audit["false_accept_rate"] == pytest.approx(1 / 3)
    assert json.loads(audit["audit_details_json"]) == {"source": "legacy"}
    assert audit["computed_at"] == 123


def test_quality_trend_audit_migration_rejects_impossible_legacy_projection(
    tmp_path,
):
    conn = sqlite3.connect(tmp_path / "legacy-invalid.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE supervisor_quality_trends (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             run_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             task_class TEXT NOT NULL,
             gate TEXT NOT NULL,
             accepted INTEGER NOT NULL,
             first_pass_accepted INTEGER NOT NULL,
             revision_rounds INTEGER NOT NULL,
             time_to_accepted_outcome_s REAL,
             p11_audit_sample_size INTEGER NOT NULL DEFAULT 0,
             false_accept_count INTEGER NOT NULL DEFAULT 0,
             false_accept_denominator INTEGER NOT NULL DEFAULT 0,
             false_accept_rate REAL NOT NULL DEFAULT 0.0,
             details_json TEXT NOT NULL DEFAULT '{}',
             computed_at INTEGER NOT NULL,
             UNIQUE(run_id, gate)
           )"""
    )
    conn.execute(
        """INSERT INTO supervisor_quality_trends(
             run_id, task_id, task_class, gate, accepted,
             first_pass_accepted, revision_rounds,
             p11_audit_sample_size, false_accept_count,
             false_accept_denominator, false_accept_rate,
             details_json, computed_at)
           VALUES(
             'legacy-invalid', 'task', 'source_change', 'outcome_review',
             1, 1, 0, 2, 3, 2, 1.5, '{}', 123
           )"""
    )

    with pytest.raises(RuntimeError, match="invalid legacy quality audit counts"):
        run_forward_migrations(conn)


@pytest.mark.parametrize(
    ("sample_size", "false_accept_count", "false_accept_denominator"),
    (
        (-1, 0, 0),
        (2, -1, 2),
        (2, 3, 2),
        (2, 1, 3),
        (2, 1, 0),
    ),
)
def test_quality_trend_audit_rejects_impossible_counts_at_api_and_db_boundaries(
    tmp_path,
    sample_size,
    false_accept_count,
    false_accept_denominator,
):
    state = State(str(tmp_path / "state.db"))
    state.upsert_quality_trend_row(
        run_id="audit-run",
        task_id="audit-task",
        task_class="source_change",
        gate="outcome_review",
        accepted=True,
        first_pass_accepted=True,
        revision_rounds=0,
        time_to_accepted_outcome_s=2.0,
        details={},
    )

    with pytest.raises(ValueError, match="invalid quality audit counts"):
        state.update_quality_trend_audit(
            run_id="audit-run",
            gate="outcome_review",
            sample_size=sample_size,
            false_accept_count=false_accept_count,
            false_accept_denominator=false_accept_denominator,
        )

    with pytest.raises(sqlite3.IntegrityError):
        state._conn.execute(
            """INSERT INTO quality_trend_audits(
                 run_id, gate, sample_size, false_accept_count,
                 false_accept_denominator, false_accept_rate,
                 audit_details_json, computed_at)
               VALUES('audit-run', 'outcome_review', 2, 3, 2, 1.5, '{}', 1)"""
        )
