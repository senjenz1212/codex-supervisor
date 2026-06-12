from __future__ import annotations

import json
from pathlib import Path

from supervisor.autoresearch.generator import (
    AutoResearchGeneratorConfig,
    activate_autoresearch_experiment,
    generate_autoresearch_experiment_drafts,
    run_runnable_autoresearch_experiments,
)
from supervisor.config import Config
from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
from supervisor.state import State


def _write_taxonomy_failure(
    state: State,
    *,
    run_id: str,
    task_class: str = "source_change",
    gate: str = "execution",
    taxonomy_code: str = "FM-3.2",
    implicated_paths: tuple[str, ...] = ("supervisor/autoresearch/orchestrator.py",),
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
            "implicated_paths": list(implicated_paths),
            "trace_envelope": {
                "failure_taxonomy": {
                    "schema_version": "dual-agent-failure-taxonomy/v1",
                    "mast_code": taxonomy_code,
                    "mast_mode": "No or incomplete verification",
                }
            },
        },
    )


def _queue_rows(state: State) -> list[dict]:
    return state.list_autoresearch_experiment_queue(limit=20)


def _workflow_job_count(state: State) -> int:
    row = state._conn.execute("SELECT COUNT(*) AS count FROM dual_agent_workflow_jobs").fetchone()
    return int(row["count"])


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
    })


def test_autoresearch_generator_config_loads_budget_guards_from_supervisor_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
orchestrator:
  run_registry_dir: /tmp/runs
supervisor:
  state_db: /tmp/state.db
models:
  realtime_critique_model: model-a
  drift_l3_model: model-b
  drift_l4_model: model-c
  post_run_eval_model: model-d
  embedding_model: model-e
telegram:
  bot_token: token
  chat_id: chat
autoresearch:
  signal_recurrence_threshold: 4
  max_open_experiment_drafts: 8
  max_runnable_experiments_per_week: 1
  evaluator_budget_usd: 0.5
  evaluator_timeout_s: 12
  evaluator_k_trials: 5
""",
        encoding="utf-8",
    )

    config = AutoResearchGeneratorConfig.from_config(Config.load(config_path))

    assert config.recurrence_threshold == 4
    assert config.max_open_experiment_drafts == 8
    assert config.max_runnable_experiments_per_week == 1
    assert config.default_budget_usd == 0.5
    assert config.default_timeout_s == 12
    assert config.default_k_trials == 5


def test_autoresearch_signal_generator_drafts_one_experiment_for_repeated_taxonomy_failures(tmp_path):
    state = State(str(tmp_path / "state.db"))
    config = AutoResearchGeneratorConfig(recurrence_threshold=3, default_k_trials=2)

    for index in range(2):
        _write_taxonomy_failure(state, run_id=f"below-threshold-{index}")

    assert generate_autoresearch_experiment_drafts(state=state, repo_root=Path.cwd(), config=config) == []
    assert _queue_rows(state) == []

    third_event_id = _write_taxonomy_failure(state, run_id="threshold-3")
    drafts = generate_autoresearch_experiment_drafts(state=state, repo_root=Path.cwd(), config=config)
    duplicate = generate_autoresearch_experiment_drafts(state=state, repo_root=Path.cwd(), config=config)

    assert len(drafts) == 1
    assert duplicate == []
    draft = drafts[0]
    assert draft["status"] == "draft"
    assert draft["task_class"] == "source_change"
    assert draft["gate"] == "execution"
    assert draft["taxonomy_code"] == "FM-3.2"
    assert third_event_id in draft["provenance"]["event_ids"]
    assert draft["experiment"]["evaluator_ref"].endswith(
        "supervisor/autoresearch/evaluators/replay_corpus.py"
    )
    assert draft["experiment"]["evaluator_hash"]
    assert draft["experiment"]["metric_name"] == "pass_rate"
    assert draft["experiment"]["k_trials"] == 2
    assert draft["experiment"]["mutable_paths"] == ["supervisor/autoresearch/orchestrator.py"]
    assert len(_queue_rows(state)) == 1


def test_workflow_finalization_generates_autoresearch_draft_from_recurring_failures(tmp_path):
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    repo_root = Path.cwd()
    event_ids = [
        _write_taxonomy_failure(state, run_id=f"finalized-signal-{index}")
        for index in range(3)
    ]

    result = api._workflow_result(
        run_id="finalized-run",
        task_id="task-finalized",
        status="accepted",
        current_gate="outcome_review",
        steps=[],
        final_gate_result=None,
        cwd=str(repo_root),
        screenshots=[],
        visual_evidence_policy={},
        workflow_route={"lesson_task_class": "source_change", "task_complexity": "large"},
        mandatory_artifacts={"status": "not_checked"},
    )

    assert result["autoresearch_drafts"]["drafted_count"] == 1
    [draft] = _queue_rows(state)
    assert draft["status"] == "draft"
    assert set(event_ids) <= set(draft["provenance"]["event_ids"])


def test_workflow_finalization_below_threshold_generates_no_draft(tmp_path):
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)
    for index in range(2):
        _write_taxonomy_failure(state, run_id=f"below-finalized-signal-{index}")

    result = api._workflow_result(
        run_id="below-finalized-run",
        task_id="task-finalized",
        status="accepted",
        current_gate="outcome_review",
        steps=[],
        final_gate_result=None,
        cwd=str(Path.cwd()),
        screenshots=[],
        visual_evidence_policy={},
        workflow_route={"lesson_task_class": "source_change", "task_complexity": "large"},
        mandatory_artifacts={"status": "not_checked"},
    )

    assert result["autoresearch_drafts"]["drafted_count"] == 0
    assert _queue_rows(state) == []


def test_autoresearch_signal_generator_reads_reviewer_probe_and_lesson_signals(tmp_path):
    state = State(str(tmp_path / "state.db"))
    config = AutoResearchGeneratorConfig(recurrence_threshold=3, default_k_trials=1)

    for index in range(3):
        state.write_event(
            run_id=f"reviewer-{index}",
            source="dual_agent",
            kind="independent_reviewer_review",
            payload={
                "task_class": "review_task",
                "gate": "outcome_review",
                "implicated_paths": ["reviews/outcome"],
                "independent_reviewer_panel_decision": {"decision": "revise"},
            },
        )
        state.write_event(
            run_id=f"probe-{index}",
            source="drift",
            kind="supervisor_probe_cohort",
            payload={
                "task_class": "probe_task",
                "gate": "execution",
                "classification": "FLIPPING",
                "implicated_paths": ["docs/dual-agent/probes"],
            },
        )
        state.record_supervisor_lesson(
            task_class="lesson_task",
            gate="tdd_review",
            taxonomy_code="FM-1.1",
            root_cause="planning artifact gap",
            remediation="repair the PRD-to-TDD trace before implementation",
            source_run_id=f"lesson-{index}",
            created_at=100 + index,
        )

    drafts = generate_autoresearch_experiment_drafts(state=state, repo_root=Path.cwd(), config=config)

    by_code = {(draft["task_class"], draft["gate"], draft["taxonomy_code"]): draft for draft in drafts}
    assert ("review_task", "outcome_review", "reviewer_disagreement") in by_code
    assert ("probe_task", "execution", "probe_cohort_flipping") in by_code
    assert ("lesson_task", "tdd_review", "FM-1.1") in by_code
    assert by_code[("review_task", "outcome_review", "reviewer_disagreement")]["provenance"]["event_ids"]
    assert by_code[("probe_task", "execution", "probe_cohort_flipping")]["provenance"]["event_ids"]
    assert by_code[("lesson_task", "tdd_review", "FM-1.1")]["provenance"]["lesson_ids"]
    assert by_code[("lesson_task", "tdd_review", "FM-1.1")]["provenance"]["signal_count"] == 3


def test_autoresearch_draft_cannot_run_until_operator_marks_runnable(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        _write_taxonomy_failure(state, run_id=f"run-{index}")
    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3, default_k_trials=1),
    )

    skipped = run_runnable_autoresearch_experiments(
        state=state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        run_id_prefix="autorun",
    )

    assert skipped == []
    assert _workflow_job_count(state) == 0

    activation = activate_autoresearch_experiment(
        state=state,
        experiment_id=draft["experiment_id"],
        operator="operator@example.com",
        approval_channel="test",
    )
    assert activation["status"] == "runnable"

    [result] = run_runnable_autoresearch_experiments(
        state=state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        run_id_prefix="autorun",
    )

    assert result["status"] == "completed"
    assert result["report"]["records"][0]["metric_source"] == "evaluator_execution"
    assert result["report"]["records"][0]["validation_status"] == "accepted"
    assert result["report"]["default_change_allowed"] is False
    assert _workflow_job_count(state) == 1
    [row] = _queue_rows(state)
    assert row["status"] == "completed"
    assert row["last_run_id"].startswith("autorun-")


def test_autoresearch_auto_runner_fails_rejected_evaluator_report(monkeypatch, tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        _write_taxonomy_failure(state, run_id=f"run-{index}")
    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3, default_k_trials=1),
    )
    activate_autoresearch_experiment(
        state=state,
        experiment_id=draft["experiment_id"],
        operator="operator@example.com",
        approval_channel="test",
    )

    def fake_rejected_fixture(**kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "records": [{
                "attempt_id": "attempt-1",
                "metric_source": "pending",
                "validation_status": "rejected",
            }],
            "default_change_allowed": False,
            "policy_mutated": False,
            "gate_advanced": False,
        }
        (output_dir / "report.json").write_text(
            json.dumps(report, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report

    monkeypatch.setattr(
        "supervisor.autoresearch.generator.run_autoresearch_fixture",
        fake_rejected_fixture,
    )

    [result] = run_runnable_autoresearch_experiments(
        state=state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        run_id_prefix="autorun",
    )

    assert result["status"] == "failed"
    assert "validation_status=rejected" in result["error"]
    [row] = _queue_rows(state)
    assert row["status"] == "failed"


def test_autoresearch_immutable_surface_signal_becomes_report_only(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for index in range(3):
        _write_taxonomy_failure(
            state,
            run_id=f"immutable-{index}",
            implicated_paths=("supervisor/state.py",),
        )

    [draft] = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3),
    )

    assert draft["status"] == "report_only"
    assert draft["report_only_reason"] == "immutable_surface_implicated"
    assert draft["proposal_pointer"]["proposal_kind"] == "stability_proposal_required"
    assert draft["experiment"]["mutable_paths"] == []

    activation = activate_autoresearch_experiment(
        state=state,
        experiment_id=draft["experiment_id"],
        operator="operator@example.com",
        approval_channel="test",
    )
    assert activation["status"] == "report_only"
    assert run_runnable_autoresearch_experiments(
        state=state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        run_id_prefix="autorun",
    ) == []


def test_autoresearch_auto_runner_respects_weekly_cap(tmp_path):
    state = State(str(tmp_path / "state.db"))
    for group in ("FM-3.2", "FM-1.1"):
        for index in range(3):
            _write_taxonomy_failure(
                state,
                run_id=f"{group}-{index}",
                taxonomy_code=group,
                implicated_paths=(f"docs/dual-agent/{group.lower()}",),
            )
    drafts = generate_autoresearch_experiment_drafts(
        state=state,
        repo_root=Path.cwd(),
        config=AutoResearchGeneratorConfig(recurrence_threshold=3, default_k_trials=1),
    )
    assert len(drafts) == 2
    for draft in drafts:
        activate_autoresearch_experiment(
            state=state,
            experiment_id=draft["experiment_id"],
            operator="operator@example.com",
            approval_channel="test",
        )

    first_batch = run_runnable_autoresearch_experiments(
        state=state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        run_id_prefix="autorun",
        max_runnable_per_week=1,
        now=1_781_000_000,
    )
    second_batch = run_runnable_autoresearch_experiments(
        state=state,
        repo_root=Path.cwd(),
        output_root=tmp_path / "out",
        run_id_prefix="autorun",
        max_runnable_per_week=1,
        now=1_781_000_100,
    )

    assert len(first_batch) == 1
    assert second_batch == []
    statuses = [row["status"] for row in _queue_rows(state)]
    assert statuses.count("completed") == 1
    assert statuses.count("runnable") == 1
