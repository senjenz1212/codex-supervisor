from __future__ import annotations

import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from mcp_tools.codex_supervisor_stdio import _maybe_artifact
from supervisor.dual_agent_artifacts import (
    ScreenshotArtifact,
    _file_tree_sha256,
    export_dual_agent_run_artifacts,
)
from supervisor.replay_versions import check_replay_schema_versions
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
        "triage.md",
        "prd.md",
        "tdd.md",
        "grill-findings.md",
        "issues.md",
        "screenshots.md",
        "outcome-review.md",
        "interactions.md",
        "transcript.md",
        "transcript.jsonl",
        "mast-coverage.md",
        "manifest.json",
        "workspace-snapshot.json",
        "mast-coverage.json",
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


def test_export_dual_agent_run_artifacts_preserves_worker_authored_outcome_review_before_review_gate(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="execution",
            summary="Execution report accepted.",
            decisions=["accept"],
        ),
        ts=1001,
    )
    output_dir = tmp_path / "docs" / "dual-agent" / "task-1"
    output_dir.mkdir(parents=True)
    report = output_dir / "outcome-review.md"
    report.write_text("# Production Confidence\n\nWorker-authored report.\n", encoding="utf-8")

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=output_dir,
    )

    assert result.status == "ok"
    assert report.read_text(encoding="utf-8") == "# Production Confidence\n\nWorker-authored report.\n"


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
            "critical_review": {
                "schema_version": "critical-review/v1",
                "strongest_objection": "push receipt missing",
                "missing_evidence": ["git_remote receipt"],
                "contradictions_checked": ["reported tests vs receipts"],
                "assumptions_to_verify": ["branch was pushed"],
                "what_would_change_my_mind": "A matching git_remote receipt appears.",
                "decision": "revise",
                "severity": "important",
            },
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
                "failure_taxonomy": {
                    "schema_version": "dual-agent-failure-taxonomy/v1",
                    "framework": "MAST-inspired",
                    "category": "task_verification",
                    "subcategory": "missing_or_stale_receipt",
                    "code": "workflow_claim_verification_failed",
                    "mast_code": "FM-3.2",
                    "mast_mode": "No or incomplete verification",
                    "mast_category": "Task Verification",
                },
                "tool_calls": [
                    {
                        "name": "start_dual_agent_gate",
                        "status": "completed",
                        "started_at_ms": 1000,
                        "ended_at_ms": 1035,
                        "duration_ms": 35,
                    },
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
        assert "### Critical Review" in text
        assert "push receipt missing" in text
        assert "reported tests vs receipts" in text
        assert "start_dual_agent_gate" in text
        assert "FM-3.2" in text
        assert "No or incomplete verification" in text
        assert "duration_ms" in text


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


def test_export_dual_agent_run_artifacts_renders_independent_reviewer_panel_events(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="independent_reviewer_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "independent_reviewer_results": [
                {
                    "reviewer_id": "independent-reviewer-0",
                    "accepted": True,
                    "decision": "accept",
                    "severity": "none",
                    "confidence": 0.91,
                    "runtime": "litellm_structured",
                    "model": "gemini-3.1-pro-preview",
                    "provider_family": "google",
                    "lineage": ["google", "litellm_structured", "gemini-3.1-pro-preview"],
                    "tool_access": "text_only",
                    "assurance_grade": "text_only",
                    "transcript_refs": [
                        {
                            "kind": "reviewer_transcript_tail",
                            "ref": "independent_reviewer_review:task-1:outcome_review:1:independent-reviewer-0",
                        }
                    ],
                    "transcript_sha256": "abc123",
                    "output_sha256": "def456",
                    "critical_review": {
                        "schema_version": "critical-review/v1",
                        "severity": "none",
                        "decision": "accept",
                    },
                }
            ],
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
        assert "interaction_type: `independent_reviewer_review`" in text
        assert "reviewer_count: `1`" in text
        assert "independent-reviewer-0" in text
        assert "gemini-3.1-pro-preview" in text
        assert "provider_family: `google`" in text
        assert "tool_access: `text_only`" in text
        assert "assurance_grade: `text_only`" in text
        assert "transcript_sha256: `abc123`" in text
        assert "output_sha256: `def456`" in text


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
            "raw_transcript_refs": [
                {
                    "kind": "cursor_transcript_fixture",
                    "ref": "tests/fixtures/dual_agent/cursor-transcript.txt",
                },
            ],
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
                    {
                        "name": "invoke_cursor_agent",
                        "status": "completed",
                        "requested_model": "composer-2.5",
                        "result_summary": {"probe_id": "CURSOR"},
                    },
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
        assert "full_reasoning: `transcript.jsonl event 1 transcript_tail`" in text
        assert "full_reasoning_ref: `tests/fixtures/dual_agent/cursor-transcript.txt`" in text
        assert "invoke_cursor_agent" in text
        assert "CURSOR" in text
        assert "requested_model" in text
    assert '"cursor_run_id": "cursor-run-live"' in transcript_jsonl


def test_export_dual_agent_run_artifacts_renders_not_invoked_gate_without_blank_claude_section(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "status": "blocked",
            "claude_gate_status": "not_invoked",
            "supervisor_final_status": "blocked",
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
                "missing_artifacts": ["prd"],
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
    assert "### Supervisor Block" in interactions
    assert "Claude Code was not invoked." in interactions
    assert "required_artifacts_missing" in interactions
    assert "Outcome summary: None recorded." not in interactions


def test_export_dual_agent_run_artifacts_renders_cursor_failure_reason_when_outcome_missing(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "cursor_review": {
                "accepted": False,
                "probe": {
                    "probe_id": "CURSOR",
                    "status": "red",
                    "reason": "cursor_invocation_failed",
                    "details": {"error": "missing_api_key"},
                },
                "outcome": None,
                "agent_id": None,
                "run_id": None,
                "status": None,
                "model": None,
                "duration_ms": None,
                "transcript_tail": "",
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
    assert "No typed Cursor outcome parsed." in interactions
    assert "### Cursor Failure" in interactions
    assert "- reason: `cursor_invocation_failed`" in interactions
    assert "missing_api_key" in interactions


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


def test_export_dual_agent_run_artifacts_renders_dynamic_workflow_receipt_validation(tmp_path):
    state = _state(tmp_path)
    state.write_event(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_dynamic_workflow_receipt_validation",
        payload={
            "task_id": "task-1",
            "gate": "workflow_start",
            "status": "blocked",
            "probe": {
                "probe_id": "P13",
                "status": "red",
                "reason": "missing_dynamic_workflow_receipts",
                "details": {
                    "dynamic_workflow_task_class": "codebase_audit",
                    "required_gates": [
                        "codex_and_lead_remain_supervision_layer",
                        "per_subagent_budget_caps_verified",
                    ],
                    "verified_gates": [],
                    "missing_gates": [
                        "codex_and_lead_remain_supervision_layer",
                        "per_subagent_budget_caps_verified",
                    ],
                    "receipt_ids": [],
                },
            },
            "tool_calls": [
                {"name": "verify_dynamic_workflow_receipts", "status": "red"},
            ],
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
        assert "interaction_type: `dynamic_workflow_receipt_validation`" in text
        assert "P13 Dynamic Workflow Receipt Validation" in text
        assert "missing_dynamic_workflow_receipts" in text
        assert "verify_dynamic_workflow_receipts" in text


def test_export_dual_agent_run_artifacts_links_tdd_grill_source_artifact(tmp_path):
    state = _state(tmp_path)
    state.write_event(
        run_id="run-1",
        source="dual_agent",
        kind="dual_agent_gate_result",
        payload=_result_payload(
            gate="outcome_review",
            summary="Accepted.",
            decisions=["accept"],
        ),
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    index = (result.output_dir / "index.md").read_text()
    assert "Source PRD Grill Findings" in index
    assert "source/grill-findings.md" in index
    assert "Source TDD Grill Findings" in index
    assert "source/grill-findings-tdd.md" in index


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
    assert check_replay_schema_versions(manifest)["status"] == "compatible"


def test_export_dual_agent_run_artifacts_writes_workspace_snapshot_manifest(tmp_path):
    state = _state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    (repo / "README.md").write_text("seed\nchanged\n", encoding="utf-8")
    source = repo / "docs" / "dual-agent" / "task-1" / "source"
    source.mkdir(parents=True)
    prd = source / "prd.md"
    prd.write_text("# PRD\n\nreal artifact body\n", encoding="utf-8")
    handoff = repo / ".handoff" / "task-1.json"
    handoff.parent.mkdir()
    handoff.write_text(
        json.dumps({
            "task_id": "task-1",
            "gate": "outcome_review",
            "cwd": str(repo),
            "planning_artifacts": [
                {
                    "kind": "prd",
                    "path": "docs/dual-agent/task-1/source/prd.md",
                    "sha256": sha256(prd.read_text(encoding="utf-8").encode()).hexdigest(),
                    "mutable_by_worker": False,
                },
            ],
        }) + "\n",
        encoding="utf-8",
    )
    _insert_event(
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
    snapshot_file = json.loads((result.output_dir / "replay" / "workspace-snapshot.json").read_text())
    snapshot = manifest["workspace_snapshot"]
    assert snapshot_file == snapshot
    assert snapshot["status"] == "captured"
    assert snapshot["root"] == str(repo)
    assert snapshot["git"]["head"]
    assert snapshot["git"]["head_sha"] == snapshot["git"]["head"]
    assert snapshot["git"]["head_ref"] == "HEAD"
    assert snapshot["git"]["head_label"] == "handoff_cwd_head"
    assert "README.md" in snapshot["git"]["status_short"]
    assert len(snapshot["git"]["diff_sha256"]) == 64
    assert len(snapshot["file_tree_sha256"]) == 64
    assert snapshot["source_artifact_hashes"]["prd"] == sha256(
        prd.read_text(encoding="utf-8").encode()
    ).hexdigest()


def test_workspace_snapshot_hash_ignores_runtime_cache_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)

    cache = repo / ".claude"
    cache.mkdir()
    (cache / "large-cache.bin").write_bytes(b"a" * 1024)
    before = _file_tree_sha256(repo)

    (cache / "large-cache.bin").write_bytes(b"b" * 1024)
    after = _file_tree_sha256(repo)

    assert after == before


def test_export_dual_agent_run_artifacts_writes_run_level_failure_summary(tmp_path):
    state = _state(tmp_path)
    taxonomy = {
        "category": "task_verification",
        "subcategory": "missing_or_stale_receipt",
        "code": "workflow_claim_verification_failed",
        "mast_code": "FM-3.2",
        "mast_mode": "No or incomplete verification",
        "mast_category": "Task Verification",
    }
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
                "failure_taxonomy": taxonomy,
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
        "failure_taxonomy": taxonomy,
    }
    interactions = (result.output_dir / "interactions.md").read_text()
    assert "FM-3.2" in interactions
    assert "No or incomplete verification" in interactions


def test_export_dual_agent_run_artifacts_writes_fast_triage_page_and_source_links(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Claims lacked receipts.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "blocked",
            "claim_verification": {
                "probe_id": "P11",
                "status": "red",
                "reason": "workflow_claim_verification_failed",
                "details": {
                    "failures": [
                        "tests_passed_without_test_receipt",
                        "implemented_without_diff_receipt",
                    ],
                    "receipts": [],
                },
            },
            "tool_calls": [
                {
                    "name": "start_dual_agent_gate",
                    "status": "completed",
                    "duration_ms": 2200,
                    "started_at_ms": 1000,
                    "ended_at_ms": 3200,
                    "result_summary": {
                        "claude_gate_status": "accepted",
                        "supervisor_final_status": "blocked",
                    },
                },
                {
                    "name": "verify_workflow_claims",
                    "status": "red",
                    "duration_ms": 13,
                    "started_at_ms": 3201,
                    "ended_at_ms": 3214,
                    "receipt_ids": [],
                    "result_summary": {
                        "reason": "workflow_claim_verification_failed",
                        "failures": [
                            "tests_passed_without_test_receipt",
                            "implemented_without_diff_receipt",
                        ],
                    },
                },
            ],
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
                    "mast_code": "FM-3.2",
                    "mast_mode": "No or incomplete verification",
                    "mast_category": "Task Verification",
                },
                "tool_calls": [
                    {
                        "name": "start_dual_agent_gate",
                        "status": "completed",
                        "duration_ms": 2200,
                        "started_at_ms": 1000,
                        "ended_at_ms": 3200,
                        "result_summary": {
                            "claude_gate_status": "accepted",
                            "supervisor_final_status": "blocked",
                        },
                    },
                    {
                        "name": "verify_workflow_claims",
                        "status": "red",
                        "duration_ms": 13,
                        "started_at_ms": 3201,
                        "ended_at_ms": 3214,
                        "receipt_ids": [],
                        "result_summary": {
                            "reason": "workflow_claim_verification_failed",
                            "failures": [
                                "tests_passed_without_test_receipt",
                                "implemented_without_diff_receipt",
                            ],
                        },
                    },
                ],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
    )

    _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "receipt_gate_decision",
            "content": "Blocked on missing receipts.",
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
                    {
                        "name": "verify_workflow_claims",
                        "status": "red",
                        "duration_ms": 13,
                        "started_at_ms": 3201,
                        "ended_at_ms": 3214,
                        "tokens_in": 5,
                        "tokens_out": 7,
                        "cost_usd": 0.12,
                        "result_summary": {
                            "reason": "workflow_claim_verification_failed",
                            "failures": [
                                "tests_passed_without_test_receipt",
                                "implemented_without_diff_receipt",
                            ],
                        },
                    },
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

    assert (result.output_dir / "triage.md") in result.files
    index = (result.output_dir / "index.md").read_text()
    triage = (result.output_dir / "triage.md").read_text()
    assert "[Triage](triage.md)" in index
    assert "[Source PRD](source/prd.md)" in index
    assert "[Source TDD](source/tdd.md)" in index
    assert "workflow_claim_verification_failed" in triage
    assert "FM-3.2" in triage
    assert "claude_gate_status: `accepted`" in triage
    assert "supervisor_final_status: `blocked`" in triage
    assert "## Run Totals" in triage
    assert "- unique_tool_calls: `2`" in triage
    assert "tests_passed_without_test_receipt" in triage
    assert "implemented_without_diff_receipt" in triage
    assert "missing:tests_passed_without_test_receipt" in triage
    assert "missing:implemented_without_diff_receipt" in triage
    assert "workflow_claim_verification_failed" in triage
    assert "| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |" in triage
    assert "verify_workflow_claims" in triage
    assert "claude_gate_status" in triage
    assert "supervisor_final_status" in triage
    assert "Next Safe Action" in triage
    assert (result.output_dir / "replay" / "workspace-snapshot.json") in result.files
    assert (result.output_dir / "mast-coverage.md") in result.files
    coverage = (result.output_dir / "mast-coverage.md").read_text()
    assert "| FM-3.2 | Task Verification | No or incomplete verification | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-1.3 | Specification Issues | Step repetition | not_observed_in_run | covered_by_deterministic_probe |" in coverage
    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    assert manifest["tool_call_totals"]["unique_tool_calls"] == 2
    assert manifest["tool_call_totals"]["total_tokens_in"] == 5
    assert manifest["tool_call_totals"]["total_tokens_out"] == 7
    assert manifest["tool_call_totals"]["total_cost_usd"] == 0.12
    assert manifest["mast_coverage"][0]["mast_code"] == "FM-1.1"
    assert manifest["mast_coverage"][0]["deterministic_status"] == "covered_by_deterministic_probe"
    mast_coverage_json = json.loads((result.output_dir / "replay" / "mast-coverage.json").read_text())
    assert mast_coverage_json == manifest["mast_coverage"]


def test_export_dual_agent_run_artifacts_writes_final_event_id_for_accepted_triage(tmp_path):
    state = _state(tmp_path)
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="accepted",
                summary="Both reviewers accepted.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "accepted",
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "observed",
                "failure_taxonomy": None,
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

    triage = (result.output_dir / "triage.md").read_text()
    assert f"- final_event_id: `{event_id}`" in triage
    assert "- supervisor_final_status: `accepted`" in triage


def test_export_dual_agent_run_artifacts_ignores_recovered_block_in_triage(tmp_path):
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="blocked",
                summary="Claim verification failed.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "blocked",
            "claim_verification": {
                "status": "red",
                "reason": "workflow_claim_verification_failed",
                "details": {
                    "failures": ["implemented_without_diff_receipt"],
                },
            },
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "blocked",
                "failure_taxonomy": {
                    "code": "claim_verification_failed",
                    "category": "task_verification",
                    "subcategory": "missing_or_stale_receipt",
                    "mast_code": "FM-3.2",
                    "mast_mode": "No or incomplete verification",
                },
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
        ts=1000,
    )
    event_id = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(
                gate="outcome_review",
                status="accepted",
                summary="Claim verification recovered.",
                decisions=["accept"],
            ),
            "claude_gate_status": "accepted",
            "supervisor_final_status": "accepted",
            "claim_verification": {
                "status": "green",
                "reason": "workflow_claims_verified",
                "details": {},
            },
            "trace_envelope": {
                "schema_version": "dual-agent-trace-envelope/v1",
                "run_id": "run-1",
                "task_id": "task-1",
                "gate": "outcome_review",
                "source": "dual_agent",
                "event_kind": "dual_agent_gate_result",
                "policy_verdict": "accepted",
                "failure_taxonomy": None,
                "tool_calls": [],
                "artifacts": [],
                "claims": [],
                "receipts": [],
            },
        },
        ts=1001,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    triage = (result.output_dir / "triage.md").read_text()
    assert f"- final_event_id: `{event_id}`" in triage
    assert "- policy_verdict: `observed`" in triage
    assert "- supervisor_final_status: `accepted`" in triage
    assert "No blocking failure taxonomy recorded." in triage
    assert "implemented_without_diff_receipt" not in triage


def test_export_dual_agent_run_artifacts_writes_sequence_failure_diagnostics(tmp_path):
    state = _state(tmp_path)
    first = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(gate="outcome_review", summary="Accepted too soon.", decisions=["accept"]),
            "required_probes": ["P1", "P2", "P3", "CURSOR"],
            "probes": {
                "P1": {"probe_id": "P1", "status": "green", "reason": "ok", "details": {}},
                "P2": {"probe_id": "P2", "status": "green", "reason": "ok", "details": {}},
                "P3": {"probe_id": "P3", "status": "green", "reason": "ok", "details": {}},
            },
            "handoff_packet_sha256": "same-packet",
        },
    )
    duplicate = _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(gate="outcome_review", summary="Accepted too soon.", decisions=["accept"]),
            "handoff_packet_sha256": "same-packet",
        },
        ts=1001,
    )
    round_event = _insert_event(
        state,
        kind="dual_agent_gate_round",
        payload=_round_payload(
            gate="outcome_review",
            round_index=2,
            codex_decision="deny",
            claude_decision="accept",
            objection="no tests added",
        ),
        ts=1002,
    )
    claude_response = _insert_event(
        state,
        kind="dual_agent_interaction_message",
        payload={
            "schema_version": "dual-agent-interaction/v1",
            "task_id": "task-1",
            "gate": "outcome_review",
            "sender": "claude_code",
            "recipient": "codex",
            "message_type": "gate_response",
            "content": "Ready to proceed.",
            "addresses": [],
            "claims": [],
            "objections": [],
            "metadata": {},
        },
        ts=1003,
    )
    cursor_reject = _insert_event(
        state,
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "task-1",
            "gate": "outcome_review",
            "accepted": False,
            "probe": {"probe_id": "CURSOR", "status": "red", "reason": "cursor_review_failed"},
        },
        ts=1004,
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    manifest = json.loads((result.output_dir / "replay" / "manifest.json").read_text())
    failures = manifest["sequence_failures"]
    by_code = {failure["mast_code"]: failure for failure in failures}
    assert by_code["FM-3.1"]["event_ids"] == [first]
    assert by_code["FM-1.3"]["event_ids"] == [first, duplicate]
    assert by_code["FM-2.5"]["event_ids"] == [round_event, claude_response]
    assert by_code["FM-3.3"]["event_ids"] == [duplicate, cursor_reject]
    coverage = (result.output_dir / "mast-coverage.md").read_text()
    assert "| FM-1.3 | Specification Issues | Step repetition | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-2.5 | Inter-Agent Misalignment | Ignored other agent input | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-3.1 | Task Verification | Premature termination | observed_in_run | covered_by_deterministic_probe |" in coverage
    assert "| FM-3.3 | Task Verification | Incorrect verification | observed_in_run | covered_by_deterministic_probe |" in coverage


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


def test_export_dual_agent_run_artifacts_labels_handoff_workspace_head(tmp_path):
    repo = tmp_path / "sandbox"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("# sandbox\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    handoff_dir = repo / ".handoff"
    handoff_dir.mkdir()
    handoff = handoff_dir / "task-1.json"
    handoff.write_text(json.dumps({"cwd": str(repo), "planning_artifacts": []}))
    state = _state(tmp_path)
    _insert_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            **_result_payload(gate="outcome_review", summary="ok", decisions=["accept"]),
            "handoff_packet_path": str(handoff),
        },
    )

    result = export_dual_agent_run_artifacts(
        state,
        run_id="run-1",
        task_id="task-1",
        output_dir=tmp_path / "docs" / "dual-agent" / "task-1",
    )

    snapshot = json.loads((result.output_dir / "replay" / "workspace-snapshot.json").read_text())
    assert snapshot["root_source"] == "handoff_cwd"
    assert snapshot["git"]["head_label"] == "handoff_cwd_head"


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
