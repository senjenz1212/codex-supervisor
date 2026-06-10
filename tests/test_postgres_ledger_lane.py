from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from supervisor.postgres_state import (
    POSTGRES_CLAIM_AVAILABLE_JOBS_SQL,
    POSTGRES_LOCK_ORDER,
    POSTGRES_SCHEMA_SQL,
    PostgresState,
)
from supervisor.dual_agent_workflow import workflow_resume_prompt
from supervisor.state import State, is_postgres_state_dsn


def test_state_uses_sqlite_for_filesystem_paths(tmp_path):
    state = State(str(tmp_path / "state.db"))

    assert type(state) is State
    assert state.write_event(
        run_id="sqlite-run",
        source="test",
        kind="event_msg",
        payload={"ok": True},
    ) == 1


def test_state_postgres_url_routes_to_postgres_lane(monkeypatch):
    def fake_init(self, dsn, *args, **kwargs):
        self.dsn = dsn

    monkeypatch.setattr(PostgresState, "__init__", fake_init)

    state = State("postgresql://localhost/codex_supervisor")

    assert isinstance(state, PostgresState)
    assert state.dsn == "postgresql://localhost/codex_supervisor"
    assert is_postgres_state_dsn("postgres://localhost/codex_supervisor")
    assert is_postgres_state_dsn("postgresql://localhost/codex_supervisor")


def test_postgres_claim_sql_uses_fenced_skip_locked_cte():
    normalized = re.sub(r"\s+", " ", POSTGRES_CLAIM_AVAILABLE_JOBS_SQL.strip())

    assert normalized.startswith("WITH c AS MATERIALIZED ( SELECT id")
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert f"ORDER BY {POSTGRES_LOCK_ORDER}" in normalized
    assert f"ORDER BY {POSTGRES_LOCK_ORDER} LIMIT %(limit)s FOR UPDATE SKIP LOCKED" in normalized
    assert normalized.index("LIMIT %(limit)s") < normalized.index(") UPDATE")
    assert "WHERE j.id = c.id" in normalized


def test_postgres_schema_carries_idempotency_and_partitioned_catch_up():
    assert "event_stream_sequences" in POSTGRES_SCHEMA_SQL
    assert "previous_event_id BIGINT" in POSTGRES_SCHEMA_SQL
    assert "CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id)" in POSTGRES_SCHEMA_SQL
    assert "dual_agent_workflows" in POSTGRES_SCHEMA_SQL
    assert "dual_agent_workflow_steps" in POSTGRES_SCHEMA_SQL
    assert "UNIQUE(run_id, task_id, gate)" in POSTGRES_SCHEMA_SQL
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in POSTGRES_SCHEMA_SQL
    assert "WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal'" in POSTGRES_SCHEMA_SQL
    assert "idx_dual_agent_workflow_jobs_dispatchable" in POSTGRES_SCHEMA_SQL
    assert "ON dual_agent_workflow_jobs(priority, created_at, id)" in POSTGRES_SCHEMA_SQL


def test_alembic_migration_and_make_target_exist():
    base_migration = Path("migrations/versions/20260604_0001_postgres_event_job_lane.py").read_text(
        encoding="utf-8"
    )
    lessons_migration = Path("migrations/versions/20260610_0001_supervisor_lessons.py").read_text(
        encoding="utf-8"
    )
    trends_migration = Path("migrations/versions/20260610_0002_supervisor_quality_trends.py").read_text(
        encoding="utf-8"
    )
    makefile = Path("Makefile").read_text(encoding="utf-8")
    config_example = Path("config.example.yaml").read_text(encoding="utf-8")

    assert "event_stream_sequences" in base_migration
    assert "dual_agent_workflows" in base_migration
    assert "dual_agent_workflow_steps" in base_migration
    assert "idx_dual_agent_workflow_jobs_active_idempotency_token" in base_migration
    assert "idx_dual_agent_workflow_jobs_dispatchable" in base_migration
    assert "supervisor_lessons" not in base_migration
    assert 'revision = "20260610_0001"' in lessons_migration
    assert 'down_revision = "20260604_0001"' in lessons_migration
    assert "supervisor_lessons" in lessons_migration
    assert 'revision = "20260610_0002"' in trends_migration
    assert 'down_revision = "20260610_0001"' in trends_migration
    assert "supervisor_quality_trends" in trends_migration
    assert "idx_supervisor_quality_trends_task_gate" in trends_migration
    assert "uv run --extra postgres alembic -c alembic.ini upgrade head" in makefile
    assert "PgBouncer" in config_example
    assert "state_db: ~/.codex-supervisor/state.db" in config_example


def test_postgres_inline_schema_and_alembic_migration_stay_structurally_equivalent():
    migration = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path("migrations/versions").glob("*.py"))
    )
    inline_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", POSTGRES_SCHEMA_SQL))
    migration_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+)", migration))
    inline_indexes = set(re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS ([a-z_]+)", POSTGRES_SCHEMA_SQL))
    migration_indexes = set(re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS ([a-z_]+)", migration))

    assert migration_tables == inline_tables
    assert migration_indexes == inline_indexes
    for required_snippet in (
        "CONSTRAINT events_run_event_unique UNIQUE(run_id, event_id)",
        "CONSTRAINT events_previous_id_shape CHECK",
        "event_id > 1 AND previous_event_id = event_id - 1",
        "UNIQUE(run_id, task_id, gate)",
        "idx_dual_agent_workflow_jobs_active_idempotency_token",
        "WHERE idempotency_token IS NOT NULL AND recovery_point != 'terminal'",
        "idx_dual_agent_workflow_jobs_dispatchable",
        "recovery_point IN ('reserved', 'request_written')",
        "supervisor_lessons",
        "idx_supervisor_lessons_task_gate",
        "supervisor_quality_trends",
        "idx_supervisor_quality_trends_task_gate",
        "UNIQUE(run_id, gate)",
    ):
        assert required_snippet in POSTGRES_SCHEMA_SQL
        assert required_snippet in migration


def test_alembic_lessons_revision_upgrades_from_applied_base(monkeypatch):
    dsn = os.environ.get("CODEX_SUPERVISOR_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("CODEX_SUPERVISOR_POSTGRES_TEST_DSN is not set")
    psycopg = pytest.importorskip("psycopg")
    command = pytest.importorskip("alembic.command")
    alembic_config = pytest.importorskip("alembic.config")

    schema = f"cs_migrate_{uuid.uuid4().hex}"
    dsn_with_schema = _dsn_with_search_path(dsn, schema)
    with psycopg.connect(dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.commit()
    try:
        cfg = alembic_config.Config("alembic.ini")
        monkeypatch.setenv("DATABASE_URL", dsn_with_schema)
        monkeypatch.delenv("POSTGRES_DSN", raising=False)

        command.upgrade(cfg, "20260604_0001")
        with psycopg.connect(dsn_with_schema) as conn:
            row = conn.execute("SELECT to_regclass('supervisor_lessons') AS table_name").fetchone()
            assert row[0] is None

        command.upgrade(cfg, "head")
        with psycopg.connect(dsn_with_schema) as conn:
            row = conn.execute("SELECT to_regclass('supervisor_lessons') AS table_name").fetchone()
            assert row[0] == "supervisor_lessons"
            row = conn.execute("SELECT to_regclass('supervisor_quality_trends') AS table_name").fetchone()
            assert row[0] == "supervisor_quality_trends"
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()


def _dsn_with_search_path(dsn: str, schema: str) -> str:
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _postgres_dsn() -> str:
    dsn = os.environ.get("CODEX_SUPERVISOR_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("CODEX_SUPERVISOR_POSTGRES_TEST_DSN is not set")
    pytest.importorskip("psycopg")
    return dsn


@pytest.fixture()
def postgres_state():
    dsn = _postgres_dsn()
    schema = f"cs_lane_{uuid.uuid4().hex}"
    state = PostgresState(dsn, schema=schema)
    try:
        yield state
    finally:
        state.close()
        psycopg = pytest.importorskip("psycopg")
        from psycopg import sql

        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _reserve_job(
    state: PostgresState,
    tmp_path: Path,
    *,
    job_id: str,
    token: str,
) -> tuple[dict, bool]:
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
    payload = {
        "cwd": str(tmp_path),
        "task_id": "task",
        "run_id": "run",
        "intent": "postgres lane test",
        "tool_receipts": [],
    }
    return state.reserve_dual_agent_workflow_job(
        job_id=job_id,
        run_id="run",
        task_id="task",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(job_dir / "request.json"),
        result_path=str(job_dir / "result.json"),
        log_path=str(job_dir / "worker.log"),
        idempotency_token=token,
        request_payload_json=json.dumps(payload, sort_keys=True),
        config_path=str(tmp_path / "config.yaml"),
    )


def test_postgres_partitioned_per_run_catch_up(postgres_state):
    a1 = postgres_state.write_event(
        run_id="run-a",
        source="test",
        kind="event_msg",
        payload={"run": "a", "index": 1},
    )
    b1 = postgres_state.write_event(
        run_id="run-b",
        source="test",
        kind="event_msg",
        payload={"run": "b", "index": 1},
    )
    a2 = postgres_state.write_event(
        run_id="run-a",
        source="test",
        kind="event_msg",
        payload={"run": "a", "index": 2},
    )

    assert (a1, b1, a2) == (1, 1, 2)
    assert postgres_state.latest_event_id("run-a") == 2
    assert postgres_state.latest_event_id("run-b") == 1
    assert [
        (event["event_id"], event["previous_event_id"], event["payload"]["index"])
        for event in postgres_state.read_events_since("run-a", after_event_id=0, limit=10)
    ] == [(1, None, 1), (2, 1, 2)]
    assert [
        event["event_id"]
        for event in postgres_state.read_events_since("run-b", after_event_id=0, limit=10)
    ] == [1]


def test_postgres_gate_event_rows_keep_sqlite_payload_shape(postgres_state):
    postgres_state.write_event(
        run_id="run-shape",
        source="test",
        kind="dual_agent_gate_result",
        payload={"task_id": "task", "gate": "outcome_review", "status": "accepted"},
    )

    [row] = postgres_state.read_dual_agent_gate_events("run-shape")

    assert isinstance(row["payload_json"], str)
    assert json.loads(row["payload_json"])["status"] == "accepted"
    assert isinstance(
        postgres_state.get_event(run_id="run-shape", event_id=1)["payload_json"],
        str,
    )


def test_postgres_workflow_resume_prompt_uses_workflow_metadata(postgres_state, tmp_path):
    postgres_state.upsert_dual_agent_workflow(
        run_id="resume-run",
        task_id="resume-task",
        cwd=str(tmp_path),
        intent="resume prompt smoke",
        current_gate="execution",
        status="blocked",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    postgres_state.record_dual_agent_workflow_step(
        run_id="resume-run",
        task_id="resume-task",
        gate="execution",
        status="blocked",
        attempt_count=1,
        latest_event_id=7,
    )
    postgres_state.update_dual_agent_workflow(
        run_id="resume-run",
        task_id="resume-task",
        status="running",
        current_gate="outcome_review",
    )

    prompt = workflow_resume_prompt(
        postgres_state,
        run_id="resume-run",
        task_id="resume-task",
    )

    assert prompt["status"] == "ok"
    assert prompt["current_gate"] == "outcome_review"
    assert "outcome_review" in prompt["prompt"]
    assert prompt["steps"] == [{
        "gate": "execution",
        "status": "blocked",
        "attempt_count": 1,
        "latest_event_id": 7,
    }]


def test_postgres_supervisor_lesson_record_query_and_list(postgres_state):
    lesson, created = postgres_state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify supervisor-generated receipts before accepting.",
        source_run_id="source-run",
        created_at=10,
    )
    duplicate, duplicate_created = postgres_state.record_supervisor_lesson(
        task_class="large",
        gate="execution",
        taxonomy_code="FM-3.2",
        root_cause="No or incomplete verification",
        remediation="Verify supervisor-generated receipts before accepting.",
        source_run_id="source-run",
        created_at=20,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["lesson_id"] == lesson["lesson_id"]
    assert postgres_state.query_supervisor_lessons(task_class="large", gate="execution") == [lesson]
    assert postgres_state.query_supervisor_lessons(task_class="small", gate="execution") == []
    assert postgres_state.list_supervisor_lessons() == [lesson]


def test_postgres_multi_writer_double_submit_creates_one_job(postgres_state, tmp_path):
    dsn = postgres_state.dsn
    schema = postgres_state.schema
    token = "same-token"

    def reserve(index: int) -> tuple[str, bool]:
        state = PostgresState(dsn, schema=schema, apply_schema=False)
        try:
            row, created = _reserve_job(
                state,
                tmp_path,
                job_id=f"job-{index}",
                token=token,
            )
            return row["job_id"], created
        finally:
            state.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))

    job_ids = [job_id for job_id, _created in results]
    created_count = sum(1 for _job_id, created in results if created)
    count_row = postgres_state._conn.execute(
        """SELECT COUNT(*) AS count
           FROM dual_agent_workflow_jobs
           WHERE idempotency_token=%s AND recovery_point != 'terminal'""",
        (token,),
    ).fetchone()

    assert created_count == 1
    assert len(set(job_ids)) == 1
    assert count_row["count"] == 1


def test_postgres_reserve_replays_terminal_token(postgres_state, tmp_path):
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="job-terminal-original",
        token="terminal-token",
    )
    assert created is True
    postgres_state.complete_dual_agent_workflow_job(
        job_id=row["job_id"],
        status="accepted",
        terminal_outcome={"status": "accepted", "task_id": "task", "run_id": "run"},
    )

    replayed, replay_created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="job-terminal-duplicate",
        token="terminal-token",
    )

    assert replay_created is False
    assert replayed["job_id"] == "job-terminal-original"
    assert replayed["recovery_point"] == "terminal"


def test_postgres_recovery_point_claim_is_compare_and_set(postgres_state, tmp_path):
    row, created = _reserve_job(
        postgres_state,
        tmp_path,
        job_id="claim-phase-job",
        token="claim-phase-token",
    )
    assert created is True

    claimed = postgres_state.claim_dual_agent_workflow_job_recovery_point(
        job_id=row["job_id"],
        expected_recovery_point="reserved",
        claim_token="claim-1",
        claim_ttl_s=60,
    )
    denied = postgres_state.claim_dual_agent_workflow_job_recovery_point(
        job_id=row["job_id"],
        expected_recovery_point="reserved",
        claim_token="claim-2",
        claim_ttl_s=60,
    )

    assert claimed is not None
    assert claimed["recovery_claim_token"] == "claim-1"
    assert denied is None

    postgres_state._conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET recovery_claimed_at=0
            WHERE job_id=%s""",
        (row["job_id"],),
    )
    reclaimed = postgres_state.claim_dual_agent_workflow_job_recovery_point(
        job_id=row["job_id"],
        expected_recovery_point="reserved",
        claim_token="claim-3",
        claim_ttl_s=1,
    )

    assert reclaimed is not None
    assert reclaimed["recovery_claim_token"] == "claim-3"


def test_postgres_concurrent_skip_locked_claimers_get_disjoint_jobs(postgres_state, tmp_path):
    dsn = postgres_state.dsn
    schema = postgres_state.schema
    for index in range(9):
        _reserve_job(
            postgres_state,
            tmp_path,
            job_id=f"claim-job-{index}",
            token=f"claim-token-{index}",
        )

    def claim(index: int) -> list[str]:
        state = PostgresState(dsn, schema=schema, apply_schema=False)
        try:
            rows = state.claim_dual_agent_workflow_jobs_for_dispatch(
                dispatcher_id=f"dispatcher-{index}",
                lease_ttl_s=60,
                now=1000,
                limit=3,
            )
            return [row["job_id"] for row in rows]
        finally:
            state.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(claim, range(3)))

    claimed = [job_id for batch in batches for job_id in batch]

    assert len(claimed) == 9
    assert len(set(claimed)) == 9
    assert all(len(batch) == 3 for batch in batches)


def test_postgres_claim_limit_is_bounded_by_cte(postgres_state, tmp_path):
    for index in range(5):
        _reserve_job(
            postgres_state,
            tmp_path,
            job_id=f"limit-job-{index}",
            token=f"limit-token-{index}",
        )

    claimed = postgres_state.claim_dual_agent_workflow_jobs_for_dispatch(
        dispatcher_id="dispatcher-limit",
        lease_ttl_s=60,
        now=1000,
        limit=2,
    )

    assert len(claimed) == 2
