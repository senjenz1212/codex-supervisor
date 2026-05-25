from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mcp_tools.codex_supervisor_stdio import _maybe_artifact
from supervisor.dual_agent_artifacts import ScreenshotArtifact, export_dual_agent_run_artifacts
from supervisor.state import State


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


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
        "screenshots.md",
        "outcome-review.md",
        "interactions.md",
        "transcript.md",
        "transcript.jsonl",
        "manifest.json",
    ]
    assert "PRD accepted after tightening." in (result.output_dir / "prd.md").read_text()
    assert f"event_id: {prd_round}" in (result.output_dir / "prd.md").read_text()
    assert f"event_id: {prd_result}" in (result.output_dir / "prd.md").read_text()
    assert "Acceptance criteria missing." in (result.output_dir / "grill-findings.md").read_text()
    assert "No issue artifacts were recorded in the dual-agent ledger." in (result.output_dir / "issues.md").read_text()
    assert "No screenshot artifacts were supplied for this export." in (result.output_dir / "screenshots.md").read_text()
    assert "Outcome accepted." in (result.output_dir / "outcome-review.md").read_text()
    interactions = (result.output_dir / "interactions.md").read_text()
    assert "# Agent Interactions: task-1" in interactions
    assert "## 1. PRD Review" in interactions
    assert "Codex -> Claude Code" in interactions
    assert "Claude Code -> Codex" in interactions
    assert "Codex decision: `revise`" in interactions
    assert "Claude decision: `revise`" in interactions
    assert "Acceptance criteria missing." in interactions
    assert "Outcome summary: PRD accepted after tightening." in interactions
    assert "## 4. Outcome Review" in interactions
    assert "Outcome summary: Outcome accepted." in interactions
    assert "prd_review" in (result.output_dir / "transcript.md").read_text()
    transcript_jsonl = (result.output_dir / "transcript.jsonl").read_text()
    assert '"event_id": ' in transcript_jsonl
    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["run_id"] == "run-1"
    assert manifest["task_id"] == "task-1"
    assert manifest["events_count"] == 4
    assert manifest["files"]["transcript_jsonl"] == "transcript.jsonl"


def test_export_dual_agent_run_artifacts_renders_interaction_receipts(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "schema_version": "dual-agent-interaction/v1",
            "task_id": "task-1",
            "gate": "outcome_review",
            "sender": "codex",
            "recipient": "cursor",
            "message_type": "review_request",
            "content": "Challenge the receipt coverage.",
            "round_index": 1,
            "persona_id": "codex.lifecycle_reviewer",
            "addresses": ["event:12"],
            "confidence": {
                "value": 0.83,
                "source": "deterministic_policy",
                "criteria": ["receipt_required"],
                "rationale": "Codex needs Cursor to review missing push evidence.",
                "evidence": ["receipt:test:passed"],
            },
            "claims": ["tests passed"],
            "objections": ["push receipt missing"],
            "questions": ["Does the push receipt map to this commit?"],
            "tool_receipts": [{
                "receipt_id": "pytest-focused",
                "kind": "test",
                "status": "passed",
                "command": "uv run pytest -q",
            }],
            "evidence_refs": [{
                "kind": "pytest",
                "ref": "receipt:pytest-focused",
                "status": "passed",
            }],
            "raw_transcript_refs": [{
                "kind": "claude_stdout",
                "ref": ".handoff/task-1.stdout",
                "sha256": "abc123",
            }],
            "would_change_if": "A matching git_remote receipt appears.",
            "artifacts": [],
            "metadata": {
                "tool_calls": [
                    {"name": "start_dual_agent_gate", "status": "completed"},
                ],
            },
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_interaction_message",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
                "tool_calls": [
                    {"name": "start_dual_agent_gate", "status": "completed"},
                ],
                "artifacts": [],
                "claims": ["tests passed"],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `review_request`" in text
        assert "persona_id: `codex.lifecycle_reviewer`" in text
        assert "tests passed" in text
        assert "push receipt missing" in text
        assert "Does the push receipt map to this commit?" in text
        assert "pytest-focused" in text
        assert "receipt:pytest-focused" in text
        assert ".handoff/task-1.stdout" in text
        assert "A matching git_remote receipt appears." in text
        assert "start_dual_agent_gate" in text


def test_export_dual_agent_run_artifacts_renders_cursor_review_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "tdd_review",
            "cursor_review": {
                "accepted": False,
                "probe": {
                    "probe_id": "CURSOR",
                    "status": "red",
                    "reason": "cursor_review_failed",
                    "details": {"missing": ["receipt:git-diff"]},
                },
                "outcome": {
                    "task_id": "task-1",
                    "summary": "Cursor found missing diff evidence.",
                    "specialists": [{"name": "Cursor Reviewer", "decision": "revise"}],
                    "decisions": ["revise"],
                    "objections": ["diff receipt missing"],
                    "changed_files": [],
                    "tests": [],
                    "test_status": "unknown",
                    "confidence": 0.72,
                    "claims": ["receipt coverage incomplete"],
                },
                "agent_id": "cursor-agent-1",
                "run_id": "cursor-run-1",
                "status": "completed",
                "model": "composer-2.5",
                "duration_ms": 1234,
                "transcript_tail": "Cursor transcript tail.",
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `cursor_review`" in text
        assert "accepted: `False`" in text
        assert "Cursor found missing diff evidence." in text
        assert "receipt coverage incomplete" in text
        assert "diff receipt missing" in text
        assert "composer-2.5" in text
        assert "Cursor transcript tail." in text


def test_export_dual_agent_run_artifacts_renders_top_level_cursor_review_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "accepted": True,
            "probe": {
                "probe_id": "CURSOR",
                "status": "green",
                "reason": "cursor_review_ok",
                "details": {},
            },
            "outcome": {
                "task_id": "task-1",
                "summary": "Cursor accepted fixture fidelity while noting missing receipts.",
                "specialists": [{"name": "Cursor Reviewer", "decision": "accept"}],
                "decisions": ["accept"],
                "objections": [],
                "changed_files": [],
                "tests": [],
                "test_status": "unknown",
                "confidence": 0.92,
                "claims": ["implementation and test claims unsubstantiated in worktree"],
            },
            "agent_id": "cursor-agent-live",
            "cursor_run_id": "cursor-run-live",
            "status": "completed",
            "model": "composer-2.5",
            "duration_ms": 11701,
            "transcript_tail": "Top-level Cursor transcript tail.",
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "tri_agent_cursor_review",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
                "tool_calls": [
                    {"name": "invoke_cursor_agent", "status": "completed"},
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    transcript_jsonl = (result.output_dir / "transcript.jsonl").read_text()
    for text in (interactions, transcript):
        assert "accepted: `True`" in text
        assert "cursor-agent-live" in text
        assert "cursor-run-live" in text
        assert "composer-2.5" in text
        assert "11701" in text
        assert "Cursor accepted fixture fidelity while noting missing receipts." in text
        assert "implementation and test claims unsubstantiated in worktree" in text
        assert "Top-level Cursor transcript tail." in text
        assert "invoke_cursor_agent" in text
    assert '"cursor_run_id": "cursor-run-live"' in transcript_jsonl


def test_export_dual_agent_run_artifacts_renders_planning_validation_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_planning_validation",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "validator_version": "1.0.0",
            "artifact_hashes": {"prd": "a" * 64},
            "checks": {
                "PRD-001": "pass",
                "TDD-001": "fail: missing test names",
            },
            "verdict": "blocked",
            "artifacts": [
                {
                    "kind": "prd",
                    "path": "/tmp/prd.md",
                    "sha256": "a" * 64,
                    "status": "accepted",
                },
            ],
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_planning_validation",
                "policy_verdict": "blocked",
                "failure_taxonomy": {
                    "category": "system_design",
                    "subcategory": "invalid_or_missing_artifact",
                    "code": "planning_validation_failed",
                },
                "tool_calls": [
                    {"name": "validate_planning_artifacts", "status": "red"},
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    interactions = (result.output_dir / "interactions.md").read_text()
    transcript = (result.output_dir / "transcript.md").read_text()
    for text in (interactions, transcript):
        assert "interaction_type: `planning_validation`" in text
        assert "validator_version: `1.0.0`" in text
        assert "verdict: `blocked`" in text
        assert "TDD-001: fail: missing test names" in text
        assert "/tmp/prd.md" in text
        assert "validate_planning_artifacts" in text


def test_export_dual_agent_run_artifacts_writes_replay_manifest_with_handoff_content(tmp_path):
    state = _state(tmp_path)
    handoff = tmp_path / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text('{"task_id": "task-1", "gate": "outcome_review"}\n', encoding="utf-8")
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                summary="Outcome accepted.",
                decisions=["accept"],
            ),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())

    assert manifest["event_ids"] == [event_id]
    assert manifest["handoff_packets"][0]["path"] == str(handoff)
    assert manifest["handoff_packets"][0]["status"] == "captured"
    assert manifest["handoff_packets"][0]["content"] == handoff.read_text(encoding="utf-8")
    assert len(manifest["handoff_packets"][0]["sha256"]) == 64


def test_export_dual_agent_run_artifacts_writes_run_level_failure_summary(tmp_path):
    state = _state(tmp_path)
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Claims lacked receipts.",
                decisions=["revise"],
            ),
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "blocked",
                "failure_taxonomy": {
                    "category": "task_verification",
                    "subcategory": "missing_or_stale_receipt",
                    "code": "workflow_claim_verification_failed",
                },
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["failure_summary"] == {
        "event_id": event_id,
        "policy_verdict": "blocked",
        "failure_taxonomy": {
            "category": "task_verification",
            "subcategory": "missing_or_stale_receipt",
            "code": "workflow_claim_verification_failed",
        },
    }


def test_export_dual_agent_run_artifacts_copies_screenshots_and_writes_manifest(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Visual review accepted.",
            decisions=["accept"],
        ),
    )
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
        screenshots=(
            ScreenshotArtifact(
                path=screenshot,
                label="Desktop final state",
                note="Captured by Codex after implementation.",
                source="computer_use",
                validation_status="passed",
                validation_notes="Visual state matches the acceptance criteria.",
            ),
        ),
    )

    copied = result.output_dir / "screenshots" / "01-desktop-final-state.png"
    manifest = result.output_dir / "screenshots.md"

    assert copied.read_bytes() == screenshot.read_bytes()
    assert "![Desktop final state](screenshots/01-desktop-final-state.png)" in manifest.read_text()
    assert "Captured by Codex after implementation." in manifest.read_text()
    assert "- source: `computer_use`" in manifest.read_text()
    assert "- validation_status: `passed`" in manifest.read_text()
    assert "Visual state matches the acceptance criteria." in manifest.read_text()
    assert copied in result.files


def test_export_dual_agent_run_artifacts_includes_artifact_rigor_details(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "blocked",
            "attempts": 0,
            "handoff_packet_path": None,
            "probes": {},
            "outcome": None,
            "escalation": {
                "type": "artifact_rigor",
                "reason": "required_artifacts_missing",
            },
            "artifact_rigor": {
                "status": "blocked",
                "reason": "required_artifacts_missing",
                "required_artifacts": ["prd", "tdd_plan", "grill_findings", "issues"],
                "present_artifacts": ["prd"],
                "missing_artifacts": ["tdd_plan", "grill_findings", "issues"],
                "required_prerequisite_gates": ["prd_review", "issues_review", "tdd_review"],
                "accepted_prerequisite_gates": ["prd_review"],
                "missing_prerequisite_gates": ["issues_review", "tdd_review"],
                "user_facing": True,
                "screenshots": [],
            },
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    outcome_review = (result.output_dir / "outcome-review.md").read_text()

    assert "### Artifact Rigor" in outcome_review
    assert "- status: `blocked`" in outcome_review
    assert "- reason: `required_artifacts_missing`" in outcome_review
    assert "- required_artifacts: `prd`, `tdd_plan`, `grill_findings`, `issues`" in outcome_review
    assert "- missing_artifacts: `tdd_plan`, `grill_findings`, `issues`" in outcome_review
    assert "- required_prerequisite_gates: `prd_review`, `issues_review`, `tdd_review`" in outcome_review
    assert "- accepted_prerequisite_gates: `prd_review`" in outcome_review
    assert "- missing_prerequisite_gates: `issues_review`, `tdd_review`" in outcome_review
    assert "- user_facing: `True`" in outcome_review


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
    prd.write_text((FIXTURE_ROOT / "prd" / "good.md").read_text(encoding="utf-8"), encoding="utf-8")
    screenshot = tmp_path / "desktop.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )

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
        screenshots=[{
            "path": str(screenshot),
            "label": "Desktop",
            "note": "Generated by Codex Browser.",
            "source": "browser",
            "validation_status": "passed",
            "validation_notes": "Browser screenshot reviewed against visual acceptance criteria.",
        }],
    ))

    assert result["status"] == "accepted"
    assert result["probes"]["P1"]["status"] == "green"
    assert exported["status"] == "ok"
    assert "docs/dual-agent/gate-1/prd.md" in exported["files"]
    assert "docs/dual-agent/gate-1/screenshots.md" in exported["files"]
    assert "docs/dual-agent/gate-1/screenshots/01-desktop.png" in exported["files"]
