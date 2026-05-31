from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from supervisor.cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    build_cursor_prompt,
    cursor_accepts,
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
