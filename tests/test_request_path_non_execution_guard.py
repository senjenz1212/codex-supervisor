from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from mcp_tools import codex_supervisor_axi as axi
from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI
from supervisor.config import Config
from supervisor.state import State
from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher


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
            "",
        ]),
        encoding="utf-8",
    )
    return path


def _cfg(tmp_path: Path) -> Config:
    return Config.load(_config_path(tmp_path))


def _seed_failure_signals(state: State) -> None:
    for index in range(3):
        state.write_event(
            run_id=f"signal-{index}",
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload={
                "task_id": "task",
                "task_class": "source_change",
                "lesson_task_class": "source_change",
                "gate": "execution",
                "status": "blocked",
                "implicated_paths": ["supervisor/autoresearch/orchestrator.py"],
                "trace_envelope": {"failure_taxonomy": {"mast_code": "FM-3.2"}},
            },
        )


def _seed_policy_proposal(state: State, root: Path) -> None:
    target = root / ".supervisor" / "policy-overlay.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("schema_version: supervisor-policy-overlay/v1\ninstruction_guidance_blocks: {}\n", encoding="utf-8")
    candidate = root / "candidates" / "policy-overlay.yaml"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        "schema_version: supervisor-policy-overlay/v1\n"
        "active_proposal_id: ARP-guard\n"
        "instruction_guidance_blocks:\n"
        "  execution:\n"
        "    - Guard request paths.\n",
        encoding="utf-8",
    )
    state.write_event(
        run_id="policy-run",
        source="autoresearch",
        kind="autoresearch_policy_proposal_created",
        payload={
            "proposal_id": "ARP-guard",
            "status": "draft",
            "changes": [{
                "target_path": ".supervisor/policy-overlay.yaml",
                "candidate_ref": "candidates/policy-overlay.yaml",
                "before_hash": sha256(target.read_bytes()).hexdigest(),
                "after_hash": sha256(candidate.read_bytes()).hexdigest(),
            }],
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_advanced": False,
        },
    )


def test_new_mcp_and_cli_verbs_do_not_drive_dispatcher_or_spawn_workers(monkeypatch, capsys, tmp_path):
    def fail_dispatch(*args, **kwargs):
        raise AssertionError("request path must not drive WorkflowJobDispatcher.run_once")

    class FailPopen:
        def __init__(self, *args, **kwargs):
            raise AssertionError("request path must not spawn worker processes")

    monkeypatch.setattr(WorkflowJobDispatcher, "run_once", fail_dispatch)
    monkeypatch.setattr("mcp_tools.codex_supervisor_stdio.subprocess.Popen", FailPopen)

    config = _config_path(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _seed_failure_signals(state)
    _seed_policy_proposal(state, tmp_path)
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    generated = api.generate_autoresearch_experiments(repo_root=str(Path.cwd()))
    assert generated["drafted_count"] == 1
    draft_id = generated["experiments"][0]["experiment_id"]
    assert api.list_autoresearch_experiments()["count"] == 1
    assert api.activate_autoresearch_experiment(
        experiment_id=draft_id,
        operator="operator@example.com",
        approval_channel="test",
    )["experiment"]["status"] == "runnable"

    assert axi.main(["--config", str(config), "--json", "experiments", "list"]) == 0
    json.loads(capsys.readouterr().out)
    assert axi.main([
        "--config",
        str(config),
        "--json",
        "approve",
        "--run-id",
        "policy-run",
        "--proposal-id",
        "ARP-guard",
        "--repo-root",
        str(tmp_path),
    ]) == 0
    json.loads(capsys.readouterr().out)
