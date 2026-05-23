from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mcp_tools.codex_supervisor_stdio import _maybe_artifact
from supervisor.dual_agent_artifacts import export_dual_agent_run_artifacts
from supervisor.state import State


def _state(tmp_path: Path) -> State:
    return State(str(tmp_path / "state.db"))


def _insert_event(
    state: State,
    *,
    run_id: str = "run-1",
    kind: str,
    payload: dict,
    ts: int = 1000,
) -> int:
    cur = state._conn.execute(
        "INSERT INTO events(run_id, ts, source, kind, payload_json) VALUES(?, ?, ?, ?, ?)",
        (run_id, ts, "dual_agent", kind, json.dumps(payload)),
    )
    state._conn.commit()
    return int(cur.lastrowid)


def _round_payload(
    *,
    task_id: str = "task-1",
    gate: str,
    round_index: int,
    codex_decision: str,
    claude_decision: str,
    objection: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "gate": gate,
        "round": {
            "round_index": round_index,
            "codex_decision": codex_decision,
            "claude_decision": claude_decision,
            "codex_confidence": 0.9,
            "claude_confidence": 0.8,
            "objection": objection,
        },
    }


def _result_payload(
    *,
    task_id: str = "task-1",
    gate: str,
    status: str = "accepted",
    summary: str,
    decisions: list[str],
    objections: list[str] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "gate": gate,
        "status": status,
        "attempts": 1,
        "handoff_packet_path": f"/tmp/.handoff/{task_id}.json",
        "probes": {
            "P1": {"probe_id": "P1", "status": "green", "reason": "planning_artifact_boundaries_ok", "details": {}},
            "P2": {"probe_id": "P2", "status": "green", "reason": "worker_orchestration_invocation_ok", "details": {}},
            "P3": {"probe_id": "P3", "status": "green", "reason": "outcome_fidelity_ok", "details": {}},
        },
        "outcome": {
            "task_id": task_id,
            "summary": summary,
            "specialists": [{"name": "Reviewer", "decision": decisions[0], "objection": None}],
            "decisions": decisions,
            "objections": objections or [],
            "changed_files": ["supervisor/example.py"],
            "tests": ["uv run pytest tests/test_example.py"],
            "test_status": "passed",
            "confidence": 0.91,
            "claims": ["Claim one"],
        },
        "escalation": None,
    }


def test_export_dual_agent_run_artifacts_writes_readable_gate_documents(tmp_path):
    state = _state(tmp_path)
    prd_round = _insert_event(
        state,
        kind="dual_agent_gate_round",
        payload=_round_payload(
            gate="prd_review",
            round_index=1,
            codex_decision="revise",
            claude_decision="revise",
            objection="Acceptance criteria missing.",
        ),
    )
    prd_result = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="prd_review",
            summary="PRD accepted after tightening.",
            decisions=["accept"],
        ),
        ts=1001,
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="tdd_review",
            summary="TDD plan accepted.",
            decisions=["accept"],
            objections=[],
        ),
        ts=1002,
    )
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Outcome accepted.",
            decisions=["accept"],
            objections=[],
        ),
        ts=1003,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    assert result.status == "ok"
    assert result.output_dir == tmp_path / "docs" / "dual-agent" / "task-1"
    assert [path.name for path in result.files] == [
        "index.md",
        "prd.md",
        "tdd.md",
        "grill-findings.md",
        "issues.md",
        "outcome-review.md",
        "transcript.md",
    ]
    assert "PRD accepted after tightening." in (result.output_dir / "prd.md").read_text()
    assert f"event_id: {prd_round}" in (result.output_dir / "prd.md").read_text()
    assert f"event_id: {prd_result}" in (result.output_dir / "prd.md").read_text()
    assert "Acceptance criteria missing." in (result.output_dir / "grill-findings.md").read_text()
    assert "No issue artifacts were recorded in the dual-agent ledger." in (result.output_dir / "issues.md").read_text()
    assert "Outcome accepted." in (result.output_dir / "outcome-review.md").read_text()
    assert "prd_review" in (result.output_dir / "transcript.md").read_text()


def test_export_dual_agent_run_artifacts_reports_not_found_without_writing(tmp_path):
    state = _state(tmp_path)
    output_dir = tmp_path / "missing"

    result = export_dual_agent_run_artifacts(
        state,
        run_id="missing",
        task_id="missing",
        output_dir=output_dir,
    )

    assert result.status == "not_found"
    assert result.files == ()
    assert not output_dir.exists()


def test_maybe_artifact_converts_mcp_payload_to_planning_artifact(tmp_path):
    artifact_path = tmp_path / "docs" / "dual-agent" / "task" / "prd.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# PRD\n")

    artifact = _maybe_artifact({"path": str(artifact_path), "kind": "prd", "mutable_by_worker": False})

    assert artifact is not None
    assert artifact.path == artifact_path
    assert artifact.kind == "prd"
    assert artifact.mutable_by_worker is False


@pytest.mark.asyncio
async def test_codex_supervisor_mcp_exports_artifacts_and_accepts_planning_artifacts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.config import Config
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    class FakeMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools = {}

        def tool(self):
            def decorate(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorate

    async def maybe(value):
        import inspect

        if inspect.isawaitable(value):
            return await value
        return value

    cfg = Config(**{
        "target": {"kind": "codex", "codex": {"sessions_root": str(tmp_path / "sessions"), "cli_command": "codex"}},
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
    state = State(str(tmp_path / "state.db"))
    artifact_dir = tmp_path / "docs" / "dual-agent" / "gate-1"
    artifact_dir.mkdir(parents=True)
    prd = artifact_dir / "prd.md"
    prd.write_text("# PRD\n")

    def fake_runner(argv, **kwargs):
        transcript = (
            "<dual_agent_outcome>"
            + json.dumps({
                "task_id": "gate-1",
                "summary": "Reviewed with artifacts.",
                "specialists": [{"name": "Planner", "decision": "accept"}],
                "decisions": ["accept"],
                "objections": [],
                "changed_files": [],
                "tests": [],
                "test_status": "passed",
                "confidence": 0.9,
                "claims": [],
            })
            + "</dual_agent_outcome>"
        )
        return subprocess.CompletedProcess(argv, 0, stdout=build_lead_replay_stdout(transcript), stderr="")

    server = build_codex_supervisor_mcp_server(cfg, state, mcp_cls=FakeMCP, runner=fake_runner)

    assert "export_gate_artifacts" in server.tools
    result = await maybe(server.tools["start_dual_agent_gate"](
        task_id="gate-1",
        run_id="run-1",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=str(tmp_path),
        expected_specialists=["Planner"],
        expected_decisions=["accept"],
        expected_objections=[],
        planning_artifacts=[{"path": str(prd), "kind": "prd", "mutable_by_worker": False}],
    ))
    exported = await maybe(server.tools["export_gate_artifacts"](
        run_id="run-1",
        task_id="gate-1",
        cwd=str(tmp_path),
    ))

    assert result["status"] == "accepted"
    assert result["probes"]["P1"]["status"] == "green"
    assert exported["status"] == "ok"
    assert "docs/dual-agent/gate-1/prd.md" in exported["files"]
