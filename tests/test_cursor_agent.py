from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import supervisor.cursor_agent as cursor_agent
from supervisor.cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    build_cursor_prompt,
    cursor_accepts,
    invoke_cursor_agent,
    select_cursor_model,
)
from supervisor.dual_agent import Outcome, ProbeResult


def test_build_cursor_prompt_is_review_only_and_uses_typed_outcome_contract(tmp_path: Path):
    request = CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Challenge the test plan.",
        cwd=tmp_path,
        claude_outcome={"summary": "Claude accepted."},
        tool_receipts=(
            {
                "receipt_id": "pytest-focused",
                "kind": "test",
                "status": "passed",
            },
        ),
    )

    prompt = build_cursor_prompt(request)

    assert "Cursor is an independent reviewer/challenger, not the implementer" in prompt
    assert "Do not edit files" in prompt
    assert "Cursor Reviewer" in prompt
    assert "Always end with <dual_agent_outcome>" in prompt
    assert "Critical review:" in prompt
    assert "critical_review object" in prompt
    assert "Claude outcome JSON" in prompt
    assert "Evidence receipts" in prompt
    assert "pytest-focused" in prompt


def test_cursor_accepts_requires_green_probe_and_accept_decision():
    accepted = Outcome(
        task_id="tri-agent",
        summary="Accepted.",
        specialists=[{"name": "Cursor Reviewer", "decision": "accept"}],
        decisions=[],
        objections=[],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.9,
        claims=[],
    )
    revised = Outcome(
        task_id="tri-agent",
        summary="Needs revision.",
        specialists=[{"name": "Cursor Reviewer", "decision": "revise"}],
        decisions=["revise"],
        objections=["missing evidence"],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.7,
        claims=[],
    )

    assert cursor_accepts(CursorInvocationResult(
        probe=ProbeResult("CURSOR", "green", "cursor_review_ok"),
        outcome=accepted,
        transcript="",
    ))
    assert not cursor_accepts(CursorInvocationResult(
        probe=ProbeResult("CURSOR", "green", "cursor_review_ok"),
        outcome=revised,
        transcript="",
    ))
    assert not cursor_accepts(CursorInvocationResult(
        probe=ProbeResult("CURSOR", "red", "cursor_sdk_missing"),
        outcome=accepted,
        transcript="",
    ))


def test_select_cursor_model_defaults_to_documented_composer_model():
    assert select_cursor_model(quality="best") == "composer-2.5"
    assert select_cursor_model(quality="cheap") == "composer-2.5"
    assert select_cursor_model(quality="best", explicit_model="custom") == "custom"


def _complete_cursor_outcome(task_id: str = "tri-agent", *, decision: str = "accept") -> Outcome:
    return Outcome(
        task_id=task_id,
        summary="Cursor completed the review.",
        specialists=[{"name": "Cursor Reviewer", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else ["Cursor found an unresolved concern."],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.9,
        confidence_rationale="Cursor reviewed the provided gate evidence.",
        confidence_criteria=["typed outcome complete", "critical review present"],
        claims=[],
        critical_review={
            "strongest_objection": "none" if decision == "accept" else "unresolved concern",
            "missing_evidence": [],
            "contradictions_checked": ["gate evidence"],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "New contradictory evidence.",
            "decision": decision,
            "severity": "none" if decision == "accept" else "important",
        },
    )


def test_invoke_cursor_agent_retries_missing_outcome_with_contract_packet(tmp_path: Path, monkeypatch):
    calls: list[CursorInvocationRequest] = []

    def fake_run(request: CursorInvocationRequest):
        calls.append(request)
        if len(calls) == 1:
            return "Reviewed the gate but forgot the typed block.", {
                "agent_id": "agent-1",
                "run_id": "run-1",
                "status": "finished",
                "model": "composer-2.5",
                "duration_ms": 10,
            }
        outcome = _complete_cursor_outcome()
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-2",
            "run_id": "run-2",
            "status": "finished",
            "model": "composer-2.5",
            "duration_ms": 11,
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        contract_retry_limit=3,
    ))

    assert len(calls) == 2
    assert "Corrective retry" in calls[1].instruction
    assert "missing dual_agent_outcome block" in calls[1].instruction
    assert result.probe.ok
    assert result.outcome is not None
    assert result.attempts == 2
    assert result.retry_reasons == ("missing dual_agent_outcome block",)


def test_invoke_cursor_agent_retries_parseable_but_contract_incomplete_outcome(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    def fake_run(request: CursorInvocationRequest):
        calls.append(request)
        if len(calls) == 1:
            incomplete = Outcome(
                task_id="tri-agent",
                summary="Cursor emitted a parseable but incomplete contract.",
                specialists=[{"name": "Cursor Reviewer", "decision": "accept"}],
                decisions=["accept"],
                objections=[],
                changed_files=[],
                tests=[],
                test_status="unknown",
                confidence=0.9,
                claims=[],
            )
            return f"<dual_agent_outcome>{incomplete.model_dump_json()}</dual_agent_outcome>", {}
        outcome = _complete_cursor_outcome()
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {}

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        contract_retry_limit=2,
    ))

    assert len(calls) == 2
    assert "outcome_missing_required_fields" in calls[1].instruction
    assert result.probe.ok
    assert result.failure_classification is None
    assert result.retry_reasons == ("outcome_missing_required_fields",)


def test_invoke_cursor_agent_returns_recoverable_contract_artifact_after_retry_cap(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    def fake_run(request: CursorInvocationRequest):
        calls.append(request)
        return "Reviewed the gate but still omitted the typed block.", {
            "agent_id": f"agent-{len(calls)}",
            "run_id": f"run-{len(calls)}",
            "status": "finished",
            "model": "composer-2.5",
            "duration_ms": 10 + len(calls),
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        contract_retry_limit=2,
    ))

    assert len(calls) == 3
    assert result.outcome is None
    assert result.probe.status == "red"
    assert result.probe.reason == "reviewer_contract_unmet"
    assert result.probe.details["original_reason"] == "missing dual_agent_outcome block"
    assert result.probe.details["recoverable"] is True
    assert result.failure_classification == "reviewer_contract_unmet"
    assert result.recoverable is True
    assert result.attempts == 3
    assert result.retry_reasons == (
        "missing dual_agent_outcome block",
        "missing dual_agent_outcome block",
        "missing dual_agent_outcome block",
    )


def test_invoke_cursor_agent_classifies_parseable_contract_miss_after_retry_cap(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    def fake_run(request: CursorInvocationRequest):
        calls.append(request)
        incomplete = Outcome(
            task_id="tri-agent",
            summary="Cursor emitted a parseable but incomplete contract.",
            specialists=[{"name": "Cursor Reviewer", "decision": "accept"}],
            decisions=["accept"],
            objections=[],
            changed_files=[],
            tests=[],
            test_status="unknown",
            confidence=0.9,
            claims=[],
        )
        return f"<dual_agent_outcome>{incomplete.model_dump_json()}</dual_agent_outcome>", {}

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        contract_retry_limit=1,
    ))

    assert len(calls) == 2
    assert result.outcome is None
    assert result.probe.reason == "reviewer_contract_unmet"
    assert result.probe.details["original_reason"] == "outcome_missing_required_fields"
    assert result.failure_classification == "reviewer_contract_unmet"
    assert result.recoverable is True
    assert result.retry_reasons == (
        "outcome_missing_required_fields",
        "outcome_missing_required_fields",
    )


def test_probe_cursor_sdk_live_writes_skipped_fixture_without_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    output_dir = tmp_path / "cursor-probe"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/probe_cursor_sdk_live.py",
            "--output-dir",
            str(output_dir),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "skipped"
    assert summary["reason"] == "missing_cursor_api_key"
    assert summary["api_key_present"] is False
    assert "CURSOR_API_KEY" not in completed.stdout
