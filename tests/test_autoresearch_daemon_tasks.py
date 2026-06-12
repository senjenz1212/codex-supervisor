from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.autoresearch.daemon_tasks import AutoResearchRunnerTask, WeeklyP11AuditTask
from supervisor.autoresearch.generator import (
    AutoResearchGeneratorConfig,
    activate_autoresearch_experiment,
    generate_autoresearch_experiment_drafts,
)
from supervisor.config import Config
from supervisor.state import State


def _cfg(tmp_path: Path) -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
        "autoresearch": {
            "runner_interval_s": 5,
            "max_runnable_experiments_per_week": 1,
            "p11_audit_cadence_s": 60,
        },
    })


def _write_taxonomy_failure(
    state: State,
    *,
    run_id: str,
    task_class: str = "source_change",
    gate: str = "execution",
    taxonomy_code: str = "FM-3.2",
) -> int:
    return state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": f"task-{run_id}",
            "task_class": task_class,
            "lesson_task_class": task_class,
            "gate": gate,
            "status": "blocked",
            "implicated_paths": ["supervisor/autoresearch/orchestrator.py"],
            "trace_envelope": {
                "failure_taxonomy": {
                    "schema_version": "dual-agent-failure-taxonomy/v1",
                    "mast_code": taxonomy_code,
                    "mast_mode": "No or incomplete verification",
                }
            },
        },
    )


@pytest.mark.asyncio
async def test_autoresearch_runner_tick_executes_only_activated_experiments(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        _write_taxonomy_failure(state, run_id=f"draft-{index}")
    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3),
    )
    calls: list[dict] = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        runnable = kwargs["state"].list_autoresearch_experiment_queue(status="runnable")
        return [{"status": "completed", "experiment_id": row["experiment_id"]} for row in runnable]

    task = AutoResearchRunnerTask(
        _cfg(tmp_path),
        state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        runner=fake_runner,
    )

    first = await task.tick_once(now=1_781_000_000)
    activate_autoresearch_experiment(
        state=state,
        experiment_id=draft["experiment_id"],
        operator="operator@example.com",
        approval_channel="test",
    )
    second = await task.tick_once(now=1_781_000_001)

    assert first["executed_count"] == 0
    assert second["executed_count"] == 1
    assert calls[0]["max_runnable_per_week"] == 1
    assert calls[0]["repo_root"] == Path.cwd().resolve()


@pytest.mark.asyncio
async def test_autoresearch_runner_tick_respects_weekly_cap(tmp_path):
    state = State(str(tmp_path / "state.db"))
    seen_caps: list[int] = []

    def fake_runner(**kwargs):
        seen_caps.append(kwargs["max_runnable_per_week"])
        return []

    task = AutoResearchRunnerTask(
        _cfg(tmp_path),
        state,
        repo_root=Path.cwd(),
        runner=fake_runner,
    )

    result = await task.tick_once(now=1_781_000_000)

    assert result["executed_count"] == 0
    assert seen_caps == [1]


@pytest.mark.asyncio
async def test_weekly_p11_audit_tick_writes_scheduled_audit_event(tmp_path):
    state = State(str(tmp_path / "state.db"))
    audited: list[dict] = []

    def fake_auditor(state_arg, **kwargs):
        audited.append(kwargs)
        event_id = state_arg.write_event(
            run_id=kwargs["run_id"],
            source="supervisor",
            kind="supervisor_p11_audit_scheduled",
            payload={
                "schema_version": "supervisor-p11-audit-schedule/v1",
                "scheduled_at": kwargs["now"],
                "observational_only": True,
                "gate_authority": "unchanged",
            },
        )
        return {"status": "audited", "schedule_event_id": event_id}

    task = WeeklyP11AuditTask(
        _cfg(tmp_path),
        state,
        run_id_provider=lambda: ["audit-run"],
        auditor=fake_auditor,
    )

    result = await task.tick_once(now=1_781_000_000)

    assert result["audited_run_count"] == 1
    assert audited[0]["cadence_s"] == 60
    events = state.read_events_since("audit-run", after_event_id=0, limit=10)
    assert [event["kind"] for event in events] == ["supervisor_p11_audit_scheduled"]
