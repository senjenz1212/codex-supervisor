from __future__ import annotations

import asyncio
import contextlib
import io
import json
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from mcp_tools import codex_supervisor_axi as axi
from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
from supervisor.autoresearch.daemon_tasks import AutoResearchRunnerTask, WeeklyP11AuditTask
from supervisor.autoresearch.validation import DEFAULT_IMMUTABLE_PATHS
from supervisor.config import Config
from supervisor.dual_agent_runner import build_lead_replay_stdout
from supervisor.policy_overlay import draft_policy_regression_rollbacks_for_trend_rows
from supervisor.state import State


BASE_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "instruction_guidance_blocks: {}\n"
)

CANDIDATE_OVERLAY = (
    "schema_version: supervisor-policy-overlay/v1\n"
    "active_proposal_id: phase-e-candidate\n"
    "instruction_guidance_blocks:\n"
    "  execution:\n"
    "    - Verify the Phase E runtime evidence chain before accepting.\n"
)


class StageBreak(AssertionError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def _outcome_block(task_id: str) -> str:
    payload = {
        "task_id": task_id,
        "summary": "Changed app.py with supervisor-verifiable runtime evidence.",
        "specialists": [{"name": "lead", "decision": "accept"}],
        "decisions": ["accept"],
        "objections": [],
        "changed_files": ["app.py"],
        "tests": [],
        "test_status": "unknown",
        "confidence": 0.9,
        "confidence_rationale": "fixture lead outcome for public workflow finalization.",
        "confidence_criteria": ["changed file exists", "runtime diff receipt generated"],
        "claims": ["changed app.py"],
        "critical_review": {
            "strongest_objection": "fixture only",
            "missing_evidence": [],
            "contradictions_checked": [],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "runtime evidence failing",
            "decision": "accept",
            "severity": "low",
        },
    }
    return "<dual_agent_outcome>" + json.dumps(payload, sort_keys=True) + "</dual_agent_outcome>"


@dataclass(frozen=True)
class LoopProof:
    root: Path
    state: State
    config_path: Path
    seeded_failure_event_ids: list[int]
    draft_event_id: int
    activation_event_id: int
    auto_run_id: str
    report: dict[str, Any]
    report_event_id: int
    proposal: dict[str, Any]
    proposal_event_id: int
    approval: dict[str, Any]
    approval_event_id: int
    before_instruction: str
    after_instruction: str
    trend_rows: list[dict[str, Any]]
    weekly_audit_event_id: int
    regression_event_id: int
    rollback_event_id: int


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join([
            "target:",
            "  kind: codex",
            "  codex:",
            f"    sessions_root: {tmp_path / 'sessions'}",
            "    cli_command: codex",
            "orchestrator:",
            f"  run_registry_dir: {tmp_path / 'runs'}",
            "supervisor:",
            f"  state_db: {tmp_path / 'state.db'}",
            "telegram:",
            "  bot_token: fake",
            "  chat_id: '42'",
            "models:",
            "  realtime_critique_model: claude-haiku-4-5",
            "  drift_l3_model: claude-haiku-4-5",
            "  drift_l4_model: claude-sonnet-4-6",
            "  post_run_eval_model: claude-sonnet-4-6",
            "  embedding_model: text-embedding-3-small",
            "autoresearch:",
            "  signal_recurrence_threshold: 3",
            "  max_runnable_experiments_per_week: 2",
            "  evaluator_k_trials: 3",
            "  evaluator_budget_usd: 1.0",
            "  evaluator_timeout_s: 5",
            "  runner_interval_s: 5",
            "  p11_audit_cadence_s: 60",
            "  policy_regression_min_runs: 3",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def _prepare_repo(root: Path) -> None:
    (root / "app.py").write_text("before phase e\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "phase-e@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Phase E"], cwd=root, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    (root / "app.py").write_text("after phase e\n", encoding="utf-8")
    (root / ".supervisor").mkdir(parents=True, exist_ok=True)
    (root / ".supervisor/policy-overlay.yaml").write_text(BASE_OVERLAY, encoding="utf-8")
    (root / "candidates").mkdir(parents=True, exist_ok=True)
    (root / "candidates/policy-overlay.yaml").write_text(CANDIDATE_OVERLAY, encoding="utf-8")
    evaluator = root / "supervisor/autoresearch/evaluators/replay_corpus.py"
    evaluator.parent.mkdir(parents=True, exist_ok=True)
    evaluator.write_text(
        "#!/usr/bin/env python\n"
        "from __future__ import annotations\n"
        "import argparse, json\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--attempt-worktree')\n"
        "parser.add_argument('--trial-index', type=int, required=True)\n"
        "parser.add_argument('--metric-name', required=True)\n"
        "parser.add_argument('--attempt-json')\n"
        "args = parser.parse_args()\n"
        "attempt = json.loads(open(args.attempt_json, encoding='utf-8').read())\n"
        "control = attempt.get('evaluator_quality_control') or {}\n"
        "if control.get('kind') == 'noop':\n"
        "    value = 0.0\n"
        "elif control.get('kind') == 'harmful':\n"
        "    value = -0.1\n"
        "elif control.get('kind') == 'known_good':\n"
        "    value = 0.2\n"
        "elif control.get('kind') == 'determinism':\n"
        "    value = 0.75\n"
        "else:\n"
        "    value = 0.70 + (args.trial_index * 0.05)\n"
        "print(json.dumps({'metric_name': args.metric_name, 'metric_value': value, 'cost_usd': 0.0}))\n",
        encoding="utf-8",
    )


def _cfg(config_path: Path) -> Config:
    return Config.load(config_path)


def _axi_json(args: list[str]) -> dict[str, Any]:
    stream = io.StringIO()
    try:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        with contextlib.redirect_stdout(stream):
            exit_code = axi.main(args)
        payload = json.loads(stream.getvalue())
    except Exception as exc:
        raise StageBreak("operator_cli", f"AXI command failed before JSON output: {args}") from exc
    if exit_code != 0:
        raise StageBreak("operator_cli", f"AXI command failed: {payload}")
    return payload


def _write_failure_signal(state: State, *, run_id: str) -> int:
    return state.write_event(
        run_id=run_id,
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload={
            "task_id": f"task-{run_id}",
            "task_class": "phase_e_policy_overlay",
            "lesson_task_class": "phase_e_policy_overlay",
            "gate": "execution",
            "status": "blocked",
            "implicated_paths": ["candidates/policy-overlay.yaml"],
            "trace_envelope": {
                "failure_taxonomy": {
                    "schema_version": "dual-agent-failure-taxonomy/v1",
                    "mast_code": "FM-PHASE-E",
                }
            },
        },
    )


def _event_by_kind(state: State, *, run_id: str, kind: str) -> dict[str, Any]:
    matches = [
        event for event in state.read_events_since(run_id, after_event_id=0, limit=10_000)
        if event["kind"] == kind
    ]
    if not matches:
        raise StageBreak(kind, f"missing event kind {kind} for run {run_id}")
    return matches[-1]


def _maybe_break(condition: bool, stage: str, message: str) -> None:
    if condition:
        raise StageBreak(stage, message)


def _required_event_by_kind(
    state: State,
    *,
    run_id: str,
    kind: str,
    missing_stage: str,
) -> dict[str, Any]:
    try:
        return _event_by_kind(state, run_id=run_id, kind=kind)
    except StageBreak as exc:
        raise StageBreak(missing_stage, str(exc)) from exc


def _all_events(state: State) -> list[dict[str, Any]]:
    rows = state._conn.execute(  # type: ignore[attr-defined]
        "SELECT event_id, run_id, ts, source, kind, payload_json FROM events ORDER BY event_id ASC"
    ).fetchall()
    return [
        {
            "event_id": int(row["event_id"]),
            "run_id": str(row["run_id"]),
            "ts": int(row["ts"]),
            "source": str(row["source"]),
            "kind": str(row["kind"]),
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def _operator_touchpoint_events(state: State) -> list[dict[str, Any]]:
    operator_kinds = {
        "autoresearch_experiment_activation_recorded",
        "autoresearch_policy_proposal_approved",
        "autoresearch_policy_proposal_denied",
        "supervisor_axi_operator_decision",
    }
    return [
        event for event in _all_events(state)
        if event["kind"] in operator_kinds
    ]


def _public_workflow_finalization(
    *,
    api: CodexSupervisorMcpAPI,
    root: Path,
    run_id: str,
    task_id: str,
) -> dict[str, Any]:
    def runner(argv, **kwargs):
        if argv and argv[0] == "git":
            return subprocess.run(
                argv,
                cwd=kwargs.get("cwd"),
                capture_output=True,
                text=True,
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Workflow response.\n" + _outcome_block(task_id)),
            stderr="",
        )

    api.runner = runner
    api.codex_runner = runner
    api.cursor_runner = runner
    return asyncio.run(api.run_dual_agent_workflow(
        cwd=str(root),
        task_id=task_id,
        run_id=run_id,
        intent="Implement a tiny Phase E proof fixture change.",
        task_complexity="trivial",
        require_skill_receipts=False,
        cursor_review=False,
        reviewer_unavailable_policy="allow",
        agentic_lead_policy="off",
        no_mistakes_policy="off",
    ))


def _run_loop(tmp_path: Path, *, disabled_wire: str | None = None) -> LoopProof:
    immutable_before = tuple(DEFAULT_IMMUTABLE_PATHS)
    root = tmp_path / "repo"
    root.mkdir()
    _prepare_repo(root)
    config_path = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(config_path), state)

    seeded = [_write_failure_signal(state, run_id=f"phase-e-signal-{index}") for index in range(3)]
    state.write_event(
        run_id="phase-e-finalization",
        source="supervisor",
        kind="supervisor_lesson_injection",
        payload={"lesson_ids": ["lesson-phase-e"], "gate": "execution"},
    )
    result = _public_workflow_finalization(
        api=api,
        root=root,
        run_id="phase-e-finalization",
        task_id="phase-e-task",
    )
    _maybe_break(
        result["status"] != "accepted",
        "finalization_workflow",
        f"expected public workflow to finish accepted, got {result['status']}",
    )
    _maybe_break(
        result["autoresearch_drafts"]["drafted_count"] != 1,
        "finalization_draft",
        "expected exactly one drafted experiment",
    )
    lesson_feedback = _required_event_by_kind(
        state,
        run_id="phase-e-finalization",
        kind="supervisor_lesson_feedback_recorded",
        missing_stage="lesson_feedback",
    )
    [draft] = state.list_autoresearch_experiment_queue(status="draft")
    draft_event = _required_event_by_kind(
        state,
        run_id="phase-e-signal-0",
        kind="autoresearch_experiment_drafted",
        missing_stage="finalization_draft",
    )
    assert draft["status"] == "draft"
    assert seeded[-1] in draft["provenance"]["event_ids"]
    assert draft["attempt"]["metric_before"] == 0.0

    try:
        activation = _axi_json([
            "--config",
            str(config_path),
            "--json",
            "experiments",
            "activate",
            draft["experiment_id"],
            "--operator",
            "operator@example.com",
        ])
    except StageBreak as exc:
        raise StageBreak("operator_activation", str(exc)) from exc
    assert activation["experiment"]["status"] == "runnable"
    activation_event = _required_event_by_kind(
        state,
        run_id="phase-e-signal-0",
        kind="autoresearch_experiment_activation_recorded",
        missing_stage="operator_activation",
    )
    assert activation_event["payload"]["automatic_policy_mutation"] is False

    if disabled_wire == "T2_daemon_runner_executes_runnable":
        runner = AutoResearchRunnerTask(
            _cfg(config_path),
            state,
            repo_root=root,
            output_root=tmp_path / "autoruns",
            runner=lambda **_: [],
        )
    else:
        runner = AutoResearchRunnerTask(_cfg(config_path), state, repo_root=root, output_root=tmp_path / "autoruns")
    runner_result = asyncio.run(runner.tick_once(now=1_781_000_000))
    _maybe_break(
        runner_result["executed_count"] != 1,
        "daemon_runner",
        "expected daemon runner to execute one runnable experiment",
    )
    [queue_row] = state.list_autoresearch_experiment_queue(limit=10)
    _maybe_break(queue_row["status"] != "completed", "daemon_runner", "experiment did not complete")
    auto_run_id = str(queue_row["last_run_id"])
    report = runner_result["results"][0]["report"]
    assert report["records"][0]["metric_source"] == "evaluator_execution"
    assert report["records"][0]["validation_status"] == "accepted"
    assert report["records"][0]["metric_before"] == 0.0
    report_event = _event_by_kind(state, run_id=auto_run_id, kind="autoresearch_report_emitted")

    proposal_event = _required_event_by_kind(
        state,
        run_id=auto_run_id,
        kind="autoresearch_policy_proposal_created",
        missing_stage="derive_on_acceptance",
    )
    proposal = proposal_event["payload"]
    assert proposal["status"] == "draft"
    assert proposal["automatic_policy_mutation"] is False
    assert (root / ".supervisor/policy-overlay.yaml").read_text(encoding="utf-8") == BASE_OVERLAY

    before_instruction = api._workflow_gate_start_kwargs(
        run_id="phase-e-before-overlay",
        task_id="phase-e-task",
        gate="execution",
        intent="Implement Phase E proof.",
        corrective_context="",
        lesson_task_class="phase_e_policy_overlay",
        cwd=root,
        round_index=1,
    )["instruction"]

    try:
        approved = _axi_json([
            "--config",
            str(config_path),
            "--json",
            "approve",
            "--run-id",
            auto_run_id,
            "--proposal-id",
            proposal["proposal_id"],
            "--repo-root",
            str(root),
            "--approver",
            "operator@example.com",
        ])
    except StageBreak as exc:
        raise StageBreak("operator_approval", str(exc)) from exc
    approval = approved["approval"]
    approval_event = _required_event_by_kind(
        state,
        run_id=auto_run_id,
        kind="autoresearch_policy_proposal_approved",
        missing_stage="operator_approval",
    )
    assert approval["operator_approved"] is True
    assert approval["before_hash"] == proposal["changes"][0]["before_hash"]
    assert approval["after_hash"] == proposal["changes"][0]["after_hash"]

    after_kwargs = api._workflow_gate_start_kwargs(
        run_id="phase-e-after-overlay",
        task_id="phase-e-task",
        gate="execution",
        intent="Implement Phase E proof.",
        corrective_context="",
        lesson_task_class="phase_e_policy_overlay",
        cwd=root,
        round_index=1,
    )
    after_instruction = after_kwargs["instruction"]
    assert before_instruction != after_instruction
    assert "Supervisor policy overlay guidance" in after_instruction

    before_rows = [
        state.upsert_quality_trend_row(
            run_id=f"trend-before-{index}",
            task_id="phase-e-task",
            task_class="phase_e_policy_overlay",
            gate="execution",
            accepted=True,
            first_pass_accepted=True,
            revision_rounds=0,
            time_to_accepted_outcome_s=10.0,
            computed_at=1_781_000_100 + index,
        )
        for index in range(3)
    ]
    after_rows = [
        state.upsert_quality_trend_row(
            run_id=f"trend-after-{index}",
            task_id="phase-e-task",
            task_class="phase_e_policy_overlay",
            gate="execution",
            accepted=True,
            first_pass_accepted=False,
            revision_rounds=2,
            time_to_accepted_outcome_s=20.0,
            policy_overlay_hash=approval["after_hash"],
            policy_proposal_id=proposal["proposal_id"],
            computed_at=1_781_001_100 + index,
        )
        for index in range(3)
    ]
    assert before_rows and after_rows
    assert after_rows[-1]["policy_overlay_hash"] == approval["after_hash"]
    assert after_rows[-1]["policy_proposal_id"] == proposal["proposal_id"]

    weekly = WeeklyP11AuditTask(
        _cfg(config_path),
        state,
        run_id_provider=lambda: ["phase-e-audit-empty"],
    )
    weekly_result = asyncio.run(weekly.tick_once(now=1_781_001_500))
    _maybe_break(
        weekly_result["audited_run_count"] != 1,
        "weekly_audit",
        "expected weekly audit task to schedule one audit run",
    )
    weekly_event = _required_event_by_kind(
        state,
        run_id="phase-e-audit-empty",
        kind="supervisor_p11_audit_scheduled",
        missing_stage="weekly_audit",
    )

    rollback_results = draft_policy_regression_rollbacks_for_trend_rows(
        state,
        run_id="phase-e-regression",
        trend_rows=after_rows,
        min_runs=3,
        first_pass_drop_threshold=0.05,
        false_accept_increase_threshold=0.01,
        time_to_accept_increase_ratio=0.25,
        now=1_781_001_600,
    )
    rollback = rollback_results[-1]
    _maybe_break(
        rollback["status"] != "rollback_drafted",
        "regression_rollback",
        f"expected rollback draft, got {rollback}",
    )
    regression_event = _event_by_kind(state, run_id="phase-e-regression", kind="policy_regression_detected")
    rollback_event = _event_by_kind(
        state,
        run_id="phase-e-regression",
        kind="autoresearch_policy_rollback_proposal_drafted",
    )

    assert tuple(DEFAULT_IMMUTABLE_PATHS) == immutable_before
    assert all(row["status"] != "runnable" for row in state.list_autoresearch_experiment_queue(status="draft"))
    assert lesson_feedback["payload"]["gate_authority"] == "unchanged"

    return LoopProof(
        root=root,
        state=state,
        config_path=config_path,
        seeded_failure_event_ids=seeded,
        draft_event_id=draft_event["event_id"],
        activation_event_id=activation_event["event_id"],
        auto_run_id=auto_run_id,
        report=report,
        report_event_id=report_event["event_id"],
        proposal=proposal,
        proposal_event_id=proposal_event["event_id"],
        approval=approval,
        approval_event_id=approval_event["event_id"],
        before_instruction=before_instruction,
        after_instruction=after_instruction,
        trend_rows=after_rows,
        weekly_audit_event_id=weekly_event["event_id"],
        regression_event_id=regression_event["event_id"],
        rollback_event_id=rollback_event["event_id"],
    )


def test_auto_evolution_loop_end_to_end_through_axi_and_daemon(tmp_path):
    proof = _run_loop(tmp_path)

    assert proof.proposal["proposal_id"].startswith("ARP-")
    assert proof.approval["default_change_allowed"] is False
    assert proof.approval["gate_authority"] == "unchanged"
    assert proof.report["default_change_allowed"] is False
    assert proof.report["records"][0]["policy_mutated"] is False
    assert proof.report["records"][0]["gate_advanced"] is False
    assert proof.seeded_failure_event_ids
    assert proof.rollback_event_id > proof.regression_event_id


def test_auto_evolution_loop_requires_exactly_two_operator_touchpoints(tmp_path):
    proof = _run_loop(tmp_path)
    touchpoints = _operator_touchpoint_events(proof.state)

    assert len(touchpoints) == 2
    assert [event["event_id"] for event in touchpoints] == [
        proof.activation_event_id,
        proof.approval_event_id,
    ]
    activation = _event_by_kind(
        proof.state,
        run_id="phase-e-signal-0",
        kind="autoresearch_experiment_activation_recorded",
    )
    approval = _event_by_kind(
        proof.state,
        run_id=proof.auto_run_id,
        kind="autoresearch_policy_proposal_approved",
    )
    assert activation["payload"]["operator"] == "operator@example.com"
    assert approval["payload"]["approver"] == "operator@example.com"
    assert activation["payload"]["automatic_policy_mutation"] is False
    assert approval["payload"]["automatic_policy_mutation"] is False


@pytest.mark.parametrize(
    ("wire", "expected_stage"),
    [
        ("T1_finalization_drafts_experiment", "finalization_draft"),
        ("T2_daemon_runner_executes_runnable", "daemon_runner"),
        ("T3_derive_on_acceptance", "derive_on_acceptance"),
        ("T4_weekly_audit_task", "weekly_audit"),
        ("T5_operator_activation_cli", "operator_activation"),
        ("T5_operator_approval_cli", "operator_approval"),
        ("T7_lesson_feedback_recorded", "lesson_feedback"),
    ],
)
def test_auto_evolution_loop_wire_removal_alarm(monkeypatch, tmp_path, wire: str, expected_stage: str):
    if wire == "T1_finalization_drafts_experiment":
        import mcp_tools.codex_supervisor_stdio as stdio

        monkeypatch.setattr(stdio, "generate_autoresearch_experiment_drafts", lambda **_: [])
    elif wire == "T3_derive_on_acceptance":
        import supervisor.autoresearch.orchestrator as orchestrator

        monkeypatch.setattr(orchestrator, "derive_policy_evolution_proposals_from_report", lambda *_, **__: [])
    elif wire == "T4_weekly_audit_task":
        async def _disabled_weekly_tick(self, *, now=None):
            return {"status": "disabled", "audited_run_count": 0, "run_ids": []}

        monkeypatch.setattr(WeeklyP11AuditTask, "tick_once", _disabled_weekly_tick)
    elif wire == "T5_operator_activation_cli":
        original_axi_main = axi.main

        def _activation_disabled(args):
            if "experiments" in args and "activate" in args:
                return 1
            return original_axi_main(args)

        monkeypatch.setattr(axi, "main", _activation_disabled)
    elif wire == "T5_operator_approval_cli":
        original_axi_main = axi.main

        def _approval_disabled(args):
            if "approve" in args:
                return 1
            return original_axi_main(args)

        monkeypatch.setattr(axi, "main", _approval_disabled)
    elif wire == "T7_lesson_feedback_recorded":
        monkeypatch.setattr(
            CodexSupervisorMcpAPI,
            "_record_lesson_feedback_for_run",
            lambda self, *, run_id: {
                "lesson_ids": [],
                "updated": False,
                "advisory_only": True,
                "gate_authority": "unchanged",
            },
        )

    with pytest.raises(StageBreak) as exc_info:
        _run_loop(tmp_path, disabled_wire=wire)

    assert exc_info.value.stage == expected_stage


def test_auto_evolution_loop_demo_artifacts_are_internally_consistent():
    base = Path("docs/dual-agent/auto-evolution-loop-proof-20260610")
    manifest_path = base / "demo-manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    events = {
        (str(event["run_id"]), int(event["event_id"])): event
        for event in manifest["events"]
    }
    for ref in manifest["event_chain"]:
        assert (ref["run_id"], ref["event_id"]) in events, ref
    for artifact in manifest["artifacts"]:
        path = Path(artifact["path"])
        assert path.exists(), path
        assert sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert len(manifest["human_touchpoint_event_ids"]) == 2
    assert manifest["default_change_allowed"] is False
    assert manifest["policy_mutated"] is False
    assert manifest["gate_advanced"] is False


def test_loop_doc_is_generated_from_demo_manifest():
    doc = Path("docs/LOOP.md")
    manifest = Path("docs/dual-agent/auto-evolution-loop-proof-20260610/demo-manifest.json")

    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert manifest.as_posix() in text
    assert "codex-supervisor-axi experiments activate" in text
    assert "codex-supervisor-axi approve --proposal-id" in text
    assert "max_runnable_experiments_per_week" in text
    assert "p11_audit_cadence_s" in text
