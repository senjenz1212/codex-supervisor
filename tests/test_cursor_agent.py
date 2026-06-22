from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import supervisor.cursor_agent as cursor_agent
from supervisor.cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    build_cursor_prompt,
    cursor_accepts,
    invoke_cursor_agent,
    select_cursor_model,
    select_reviewer_model,
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
    assert "criteria_checked must include acceptance_items[] strings" in prompt
    assert "receipts_considered must include runtime_receipt_ids[].receipt_id values" in prompt
    assert "runtime_receipt_ids are implementation/runtime evidence" in prompt
    assert "do not reject solely because the packet cannot yet include a sibling Cursor receipt" in prompt


def test_build_cursor_prompt_compacts_large_runtime_receipt_file_lists(tmp_path: Path):
    changed_files = [f"generated/file-{index}.txt" for index in range(25)]
    request = CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Challenge the outcome.",
        cwd=tmp_path,
        claude_outcome={"summary": "Claude accepted."},
        tool_receipts=(
            {
                "receipt_id": "runtime-git-diff-outcome_review-1",
                "kind": "git_diff",
                "status": "present",
                "source": "supervisor",
                "evidence_grade": "runtime_native",
                "changed_files": changed_files,
            },
        ),
    )

    prompt = build_cursor_prompt(request)

    assert "changed_files_count" in prompt
    assert "changed_files_omitted_count" in prompt
    assert "generated/file-19.txt" in prompt
    assert "generated/file-20.txt" not in prompt


def test_structured_reviewer_schema_allows_context_receipt():
    schema = cursor_agent._structured_outcome_json_schema()
    critical = schema["properties"]["critical_review"]
    receipt = critical["properties"]["reviewer_context_receipt"]

    assert "reviewer_context_receipt" in critical["required"]
    assert receipt["additionalProperties"] is False
    assert receipt["required"] == [
        "reviewer_id",
        "files_reviewed",
        "criteria_checked",
        "receipts_considered",
        "assumptions",
        "missing_context",
    ]


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


def test_select_cursor_model_defaults_to_catalog_auto_route():
    assert select_cursor_model(quality="best") == "default"
    assert select_cursor_model(quality="cheap") == "default"
    assert select_cursor_model(quality="best", explicit_model="custom") == "custom"


def test_select_reviewer_defaults_to_cursor_sdk_primary():
    assert select_reviewer_model(quality="best", reviewer_output_mode="cursor_sdk") == "default"
    assert CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=Path("."),
    ).reviewer_output_mode == "cursor_sdk"
    assert (
        select_reviewer_model(
            quality="best",
            reviewer_output_mode="litellm_structured",
        )
        == "claude-opus-4-6"
    )
    assert (
        select_reviewer_model(
            quality="best",
            reviewer_output_mode="litellm_structured",
            reviewer_model="custom-reviewer",
        )
        == "custom-reviewer"
    )


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


def _litellm_metadata(*, finish_reason: str = "stop") -> dict[str, object]:
    return {
        "agent_id": None,
        "run_id": "chatcmpl-1",
        "status": "finished",
        "model": "claude-opus-4-6",
        "reviewer_runtime": "litellm_structured",
        "reviewer_output_mode": "litellm_structured",
        "duration_ms": None,
        "finish_reason": finish_reason,
    }


def _init_clean_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=cursor-test@example.com",
            "-c",
            "user.name=Cursor Test",
            "commit",
            "-m",
            "seed",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_cursor_isolated_reviewer_cannot_mutate_source_worktree(tmp_path: Path, monkeypatch):
    source = tmp_path / "repo"
    _init_clean_git_repo(source)
    captured: dict[str, Path] = {}

    def fake_run(request: CursorInvocationRequest):
        captured["review_cwd"] = Path(request.cwd)
        (Path(request.cwd) / "cursor-note.txt").write_text("contained\n", encoding="utf-8")
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-1",
            "run_id": "run-1",
            "status": "finished",
            "model": "composer-2.5",
            "duration_ms": 10,
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=source,
        reviewer_output_mode="cursor_sdk",
        contract_retry_limit=0,
    ))

    assert result.probe.ok
    assert cursor_accepts(result)
    assert captured["review_cwd"].resolve() != source.resolve()
    assert not (source / "cursor-note.txt").exists()
    assert subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""


def test_cursor_isolated_reviewer_records_contained_mutation_diagnostic(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "repo"
    _init_clean_git_repo(source)

    def fake_run(request: CursorInvocationRequest):
        (Path(request.cwd) / "cursor-note.txt").write_text("contained\n", encoding="utf-8")
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-1",
            "run_id": "run-1",
            "status": "finished",
            "model": "composer-2.5",
            "duration_ms": 10,
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=source,
        reviewer_output_mode="cursor_sdk",
        contract_retry_limit=0,
    ))

    isolation = (result.diagnostics or {})["worktree_isolation"]
    assert isolation["enabled"] is True
    assert isolation["strategy"] == "copytree_public_reviewer_worktree"
    assert isolation["source_cwd"] == str(source.resolve())
    assert isolation["contained_mutation"] is True
    assert isolation["before_snapshot_sha256"] != isolation["after_snapshot_sha256"]
    assert "cursor-note.txt" in isolation["changed_paths"]


def test_cursor_original_worktree_mutation_blocks_full_panel_evidence(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "repo"
    _init_clean_git_repo(source)

    def fake_run(request: CursorInvocationRequest):
        (Path(request.cwd) / "cursor-note.txt").write_text("unsafe\n", encoding="utf-8")
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-1",
            "run_id": "run-1",
            "status": "finished",
            "model": "composer-2.5",
            "duration_ms": 10,
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=source,
        reviewer_output_mode="cursor_sdk",
        contract_retry_limit=0,
        reviewer_worktree_isolation="none",
    ))

    assert result.probe.status == "red"
    assert result.probe.reason == "cursor_modified_worktree"
    assert result.outcome is None
    assert not cursor_accepts(result)
    assert "?? cursor-note.txt" in result.probe.details["after"]


def test_cursor_oracle_material_excluded_from_isolated_worktree(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "repo"
    (source / ".mergeability").mkdir(parents=True)
    (source / ".mergeability" / "hidden.txt").write_text("secret\n", encoding="utf-8")
    (source / "oracle_outputs.json").write_text("secret\n", encoding="utf-8")
    (source / "FAIL_TO_PASS.txt").write_text("secret\n", encoding="utf-8")
    (source / "PASS_TO_PASS.txt").write_text("secret\n", encoding="utf-8")
    (source / "public.txt").write_text("visible\n", encoding="utf-8")

    def fake_run(request: CursorInvocationRequest):
        review_cwd = Path(request.cwd)
        assert (review_cwd / "public.txt").exists()
        assert not (review_cwd / ".mergeability").exists()
        assert not (review_cwd / "oracle_outputs.json").exists()
        assert not (review_cwd / "FAIL_TO_PASS.txt").exists()
        assert not (review_cwd / "PASS_TO_PASS.txt").exists()
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-1",
            "run_id": "run-1",
            "status": "finished",
            "model": "composer-2.5",
            "duration_ms": 10,
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=source,
        reviewer_output_mode="cursor_sdk",
        contract_retry_limit=0,
    ))

    assert result.probe.ok


def test_run_litellm_structured_calls_openai_schema_gateway(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    outcome = _complete_cursor_outcome(task_id="tri-agent")

    class FakeCompletions:
        def create(self, **kwargs):
            captured["completion_kwargs"] = kwargs
            return SimpleNamespace(
                id="chatcmpl-direct",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=outcome.model_dump_json()),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    transcript, metadata = cursor_agent._run_litellm_structured(
        CursorInvocationRequest(
            task_id="tri-agent",
            gate="tdd_review",
            instruction="Review the TDD plan.",
            cwd=tmp_path,
            openai_api_key="test-key",
            openai_base_url="https://litellm.example/v1",
            reviewer_model="gemini-test",
            reviewer_max_tokens=1234,
        )
    )

    assert captured["client_kwargs"] == {
        "api_key": "test-key",
        "base_url": "https://litellm.example/v1",
        "timeout": 600,
    }
    completion_kwargs = captured["completion_kwargs"]
    assert completion_kwargs["model"] == "gemini-test"
    assert completion_kwargs["temperature"] == 0
    assert completion_kwargs["max_tokens"] == 1234
    assert completion_kwargs["timeout"] == 600
    assert completion_kwargs["response_format"]["type"] == "json_schema"
    assert completion_kwargs["response_format"]["json_schema"]["strict"] is True
    assert completion_kwargs["response_format"]["json_schema"]["schema"]["required"] == [
        "task_id",
        "summary",
        "specialists",
        "decisions",
        "objections",
        "changed_files",
        "tests",
        "test_status",
        "confidence",
        "confidence_rationale",
        "confidence_criteria",
        "claims",
        "critical_review",
    ]
    assert completion_kwargs["messages"][0]["role"] == "system"
    assert "Do not edit files" in completion_kwargs["messages"][0]["content"]
    assert completion_kwargs["messages"][1]["role"] == "user"
    assert "Tri-agent review gate: tdd_review." in completion_kwargs["messages"][1]["content"]

    assert transcript == f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>"
    assert metadata == {
        "agent_id": None,
        "run_id": "chatcmpl-direct",
        "status": "finished",
        "model": "gemini-test",
        "reviewer_runtime": "litellm_structured",
        "reviewer_output_mode": "litellm_structured",
        "duration_ms": None,
        "finish_reason": "stop",
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "prompt_chars": metadata["prompt_chars"],
        "prompt_sha256": metadata["prompt_sha256"],
    }
    assert metadata["prompt_chars"] > 0
    assert len(str(metadata["prompt_sha256"])) == 64


def test_structured_litellm_reviewer_returns_fidelity_passing_outcome(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run(request: CursorInvocationRequest):
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.ok
    assert cursor_accepts(result)
    assert result.model == "claude-opus-4-6"
    assert result.reviewer_runtime == "litellm_structured"
    assert result.reviewer_output_mode == "litellm_structured"


def test_structured_litellm_reviewer_preserves_read_only_guard(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run(request: CursorInvocationRequest):
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    statuses = ["", " M supervisor/cursor_agent.py\n"]

    def status_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=statuses.pop(0), stderr="")

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(
        CursorInvocationRequest(
            task_id="tri-agent",
            gate="tdd_review",
            instruction="Review the TDD plan.",
            cwd=tmp_path,
            reviewer_output_mode="litellm_structured",
            contract_retry_limit=0,
        ),
        status_runner=status_runner,
    )

    assert result.probe.status == "red"
    assert result.probe.reason == "cursor_modified_worktree"
    assert not cursor_accepts(result)


def test_structured_litellm_reviewer_enforces_contract_completeness(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run(request: CursorInvocationRequest):
        incomplete = Outcome(
            task_id=request.task_id,
            summary="Structured reviewer omitted critical review fields.",
            specialists=[{"name": "Cursor Reviewer", "decision": "accept"}],
            decisions=["accept"],
            objections=[],
            changed_files=[],
            tests=[],
            test_status="unknown",
            confidence=0.9,
            claims=[],
        )
        return f"<dual_agent_outcome>{incomplete.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_contract_unmet"
    assert result.probe.details["original_reason"] == "outcome_missing_required_fields"
    assert result.failure_classification == "reviewer_contract_unmet"


def test_structured_litellm_reviewer_revise_blocks_workflow(tmp_path: Path, monkeypatch):
    def fake_run(request: CursorInvocationRequest):
        outcome = _complete_cursor_outcome(task_id=request.task_id, decision="revise")
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.ok
    assert result.outcome is not None
    assert not cursor_accepts(result)
    assert result.failure_classification is None


def test_structured_litellm_reviewer_deny_blocks_workflow(tmp_path: Path, monkeypatch):
    def fake_run(request: CursorInvocationRequest):
        outcome = _complete_cursor_outcome(task_id=request.task_id, decision="deny")
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.ok
    assert result.outcome is not None
    assert not cursor_accepts(result)
    assert result.failure_classification is None


def test_structured_litellm_failure_classifies_as_infrastructure_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run(request: CursorInvocationRequest):
        raise RuntimeError("gateway unavailable")

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    assert result.recoverable is True


def test_structured_litellm_access_denied_classifies_distinctly_without_retry(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    class GatewayAccessDenied(RuntimeError):
        status_code = 403
        body = {"error": {"message": "Access denied for claude-opus-4-6"}}

    def fake_run(request: CursorInvocationRequest):
        calls.append(request)
        raise GatewayAccessDenied("403 Access denied for configured reviewer route")

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        openai_api_key="secret-key-must-not-appear",
        openai_base_url="https://litellm.example/v1",
        contract_retry_limit=3,
    ))

    assert len(calls) == 1
    assert result.probe.reason == "reviewer_access_denied"
    assert result.failure_classification == "reviewer_access_denied"
    assert result.recoverable is False
    assert result.attempts == 1
    rendered = json.dumps(result.diagnostics, sort_keys=True)
    assert "secret-key-must-not-appear" not in rendered
    assert "litellm.example" in rendered
    assert "403" in rendered


def test_structured_litellm_invalid_json_classifies_as_contract_unmet(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run(request: CursorInvocationRequest):
        return "<dual_agent_outcome>{not-json}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_contract_unmet"
    assert str(result.probe.details["original_reason"]).startswith("invalid dual_agent_outcome block")
    assert result.failure_classification == "reviewer_contract_unmet"


def test_structured_litellm_truncation_classifies_as_contract_unmet(
    tmp_path: Path,
    monkeypatch,
):
    def fake_run(request: CursorInvocationRequest):
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return (
            f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>",
            _litellm_metadata(finish_reason="length"),
        )

    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_contract_unmet"
    assert result.probe.details["original_reason"] == "structured_reviewer_truncated"
    assert result.failure_classification == "reviewer_contract_unmet"


def test_cursor_sdk_is_default_invocation_and_records_diagnostics(tmp_path: Path, monkeypatch):
    calls: list[CursorInvocationRequest] = []

    def fake_run(request: CursorInvocationRequest):
        calls.append(request)
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-cursor",
            "run_id": "run-cursor",
            "status": "finished",
            "model": "composer-2.5",
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
            "duration_ms": 12,
            "prompt_chars": 2048,
            "prompt_sha256": "a" * 64,
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", fake_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        contract_retry_limit=0,
    ))

    assert calls and calls[0].reviewer_output_mode == "cursor_sdk"
    assert result.probe.ok
    assert result.reviewer_runtime == "cursor_sdk"
    assert result.reviewer_output_mode == "cursor_sdk"
    assert result.reviewer_assurance == "tool_backed_primary"
    assert result.diagnostics["prompt_chars"] == 2048
    assert result.diagnostics["prompt_sha256"] == "a" * 64
    assert result.diagnostics["worktree_isolation"]["enabled"] is True
    assert result.diagnostics["worktree_isolation"]["contained_mutation"] is False


def test_cursor_sdk_infra_retry_succeeds_before_fallback(tmp_path: Path, monkeypatch):
    calls: list[CursorInvocationRequest] = []

    def flaky_cursor(request: CursorInvocationRequest):
        calls.append(request)
        if len(calls) == 1:
            raise RuntimeError("internal: internal error")
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-cursor",
            "run_id": "run-cursor",
            "status": "finished",
            "model": "composer-2.5",
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
            "duration_ms": 42,
            "prompt_chars": 2048,
            "prompt_sha256": "c" * 64,
        }

    def fallback_run(request: CursorInvocationRequest):
        raise AssertionError("fallback must not run before infra retry succeeds")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", flaky_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fallback_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        reviewer_infra_retry_limit=1,
        reviewer_infra_retry_backoff_s=0,
        contract_retry_limit=0,
    ))

    assert len(calls) == 2
    assert result.probe.ok
    assert cursor_accepts(result)
    assert result.failure_classification is None
    retry_diag = result.diagnostics["infrastructure_retries"]
    assert retry_diag["retry_limit"] == 1
    assert retry_diag["exhausted"] is False
    assert retry_diag["attempt_count"] == 2
    assert retry_diag["attempts"][0]["reason"] == "reviewer_invocation_failed"
    assert retry_diag["attempts"][0]["error"] == "internal: internal error"
    assert retry_diag["backoff_s"] == [0.0]


def test_cursor_sdk_infra_retries_exhaust_with_attempt_diagnostics(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    def failing_cursor(request: CursorInvocationRequest):
        calls.append(request)
        raise RuntimeError(f"internal: internal error {len(calls)}")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", failing_cursor)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_infra_retry_limit=2,
        reviewer_infra_retry_backoff_s=0,
        contract_retry_limit=0,
    ))

    assert len(calls) == 3
    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.probe.details["attempts"] == 3
    assert result.probe.details["retry_limit"] == 2
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    retry_diag = result.diagnostics["infrastructure_retries"]
    assert retry_diag["exhausted"] is True
    assert retry_diag["attempt_count"] == 3
    assert [item["attempt"] for item in retry_diag["attempts"]] == [1, 2, 3]
    assert retry_diag["attempts"][-1]["error"] == "internal: internal error 3"
    assert result.diagnostics["fallback"] == {
        "attempted": False,
        "reason": "missing_openai_api_key",
    }


def test_cursor_sdk_contract_retry_does_not_consume_infra_retry_budget(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    def malformed_then_valid(request: CursorInvocationRequest):
        calls.append(request)
        if len(calls) == 1:
            return "Reviewed the gate but omitted the typed block.", {
                "agent_id": "agent-1",
                "run_id": "run-1",
                "status": "finished",
                "model": "composer-2.5",
                "reviewer_runtime": "cursor_sdk",
                "reviewer_output_mode": "cursor_sdk",
                "duration_ms": 12,
            }
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {
            "agent_id": "agent-2",
            "run_id": "run-2",
            "status": "finished",
            "model": "composer-2.5",
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
            "duration_ms": 13,
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", malformed_then_valid)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_infra_retry_limit=5,
        reviewer_infra_retry_backoff_s=0,
        contract_retry_limit=1,
    ))

    assert len(calls) == 2
    assert "Corrective retry" in calls[1].instruction
    assert result.probe.ok
    assert result.retry_reasons == ("missing dual_agent_outcome block",)
    assert "infrastructure_retries" not in (result.diagnostics or {})


def test_cursor_sdk_terminal_empty_error_classifies_as_infrastructure_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-trigger-fallback")
    calls: list[CursorInvocationRequest] = []

    def terminal_empty_error(request: CursorInvocationRequest):
        calls.append(request)
        return "", {
            "agent_id": "agent-cursor",
            "run_id": "run-cursor",
            "status": "error",
            "model": "composer-2.5",
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
            "duration_ms": 1111,
            "prompt_chars": 2048,
            "prompt_sha256": "d" * 64,
        }

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", terminal_empty_error)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_infra_retry_limit=5,
        reviewer_infra_retry_backoff_s=0,
        contract_retry_limit=3,
    ))

    assert len(calls) == 1
    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.probe.details["original_reason"] == "cursor_sdk_terminal_empty_result"
    assert result.probe.details["status"] == "error"
    assert result.probe.details["run_id"] == "run-cursor"
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    assert result.recoverable is True
    assert result.attempts == 1
    assert result.retry_reasons == ()
    assert result.diagnostics["fallback"] == {
        "attempted": False,
        "reason": "missing_openai_api_key",
    }


def test_cursor_sdk_missing_module_does_not_consume_infra_retry_budget(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[CursorInvocationRequest] = []

    def missing_cursor_sdk(request: CursorInvocationRequest):
        calls.append(request)
        raise ModuleNotFoundError(
            "No module named 'cursor_sdk'",
            name="cursor_sdk",
            path=None,
        )

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", missing_cursor_sdk)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_infra_retry_limit=5,
        reviewer_infra_retry_backoff_s=0,
        contract_retry_limit=0,
    ))

    assert len(calls) == 1
    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.probe.details["original_reason"] == "cursor_sdk_missing"
    assert result.probe.details["attempts"] == 1
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    assert result.recoverable is True
    assert result.diagnostics["failure"]["probe"]["details"]["original_reason"] == (
        "cursor_sdk_missing"
    )
    assert "infrastructure_retries" not in (result.diagnostics or {})
    assert result.diagnostics["fallback"] == {
        "attempted": False,
        "reason": "missing_openai_api_key",
    }


def test_cursor_sdk_timeout_classifies_as_reviewer_infrastructure_unavailable(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-trigger-fallback")

    def slow_run(request: CursorInvocationRequest):
        time.sleep(3)
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", {}

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", slow_run)
    started = time.monotonic()

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        timeout_s=1,
        reviewer_infra_retry_limit=0,
        contract_retry_limit=0,
    ))

    assert time.monotonic() - started < 2.5
    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.probe.details["original_reason"] == "cursor_sdk_timeout"
    assert result.probe.details["timeout_s"] == 1
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    assert result.reviewer_assurance == "unavailable"
    assert result.diagnostics["fallback"]["attempted"] is False


def test_cursor_sdk_access_denied_does_not_retry_or_fallback(tmp_path: Path, monkeypatch):
    calls: list[CursorInvocationRequest] = []

    class CursorAccessDenied(RuntimeError):
        status_code = 403

    def denied_cursor(request: CursorInvocationRequest):
        calls.append(request)
        raise CursorAccessDenied("permission denied for Cursor reviewer")

    def fallback_run(request: CursorInvocationRequest):
        raise AssertionError("access denied must not fall back")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", denied_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fallback_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        reviewer_infra_retry_limit=5,
        reviewer_infra_retry_backoff_s=0,
        contract_retry_limit=0,
    ))

    assert len(calls) == 1
    assert result.probe.reason == "reviewer_access_denied"
    assert result.failure_classification == "reviewer_access_denied"
    assert result.recoverable is False
    assert "fallback" not in (result.diagnostics or {})


def test_cursor_sdk_fallback_requires_explicit_request_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-trigger-fallback")

    def failing_cursor(request: CursorInvocationRequest):
        raise RuntimeError("cursor transport closed")

    def fallback_run(request: CursorInvocationRequest):
        raise AssertionError("ambient OPENAI_API_KEY must not trigger fallback")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", failing_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fallback_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        reviewer_infra_retry_limit=0,
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.diagnostics["fallback"] == {
        "attempted": False,
        "reason": "missing_openai_api_key",
    }


def test_cursor_sdk_failure_falls_back_to_litellm_structured_with_lower_assurance(
    tmp_path: Path,
    monkeypatch,
):
    fallback_calls: list[CursorInvocationRequest] = []

    def failing_cursor(request: CursorInvocationRequest):
        raise RuntimeError("cursor transport closed")

    def fallback_run(request: CursorInvocationRequest):
        fallback_calls.append(request)
        outcome = _complete_cursor_outcome(task_id=request.task_id)
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", failing_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fallback_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        reviewer_model="composer-2.5",
        reviewer_infra_retry_limit=0,
        contract_retry_limit=0,
    ))

    assert fallback_calls and fallback_calls[0].reviewer_model is None
    assert result.probe.ok
    assert cursor_accepts(result)
    assert result.reviewer_runtime == "litellm_structured"
    assert result.reviewer_output_mode == "litellm_structured"
    assert result.reviewer_assurance == "fallback_text_only"
    assert result.fallback_from_runtime == "cursor_sdk"
    assert result.fallback_reason == "reviewer_invocation_failed"
    assert result.diagnostics["fallback"]["primary_failure"]["probe"]["reason"] == (
        "reviewer_infrastructure_unavailable"
    )


def test_cursor_sdk_both_primary_and_fallback_fail_remains_recoverable(
    tmp_path: Path,
    monkeypatch,
):
    def failing_cursor(request: CursorInvocationRequest):
        raise RuntimeError("cursor transport closed")

    def failing_fallback(request: CursorInvocationRequest):
        raise RuntimeError("structured gateway closed")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", failing_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", failing_fallback)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        reviewer_infra_retry_limit=0,
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_infrastructure_unavailable"
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    assert result.recoverable is True
    assert result.diagnostics["fallback"]["attempted"] is True
    assert result.diagnostics["fallback"]["fallback_failure"]["probe"]["reason"] == (
        "reviewer_infrastructure_unavailable"
    )


def test_cursor_sdk_fallback_access_denied_surfaces_access_denied(
    tmp_path: Path,
    monkeypatch,
):
    class GatewayAccessDenied(RuntimeError):
        status_code = 403

    def failing_cursor(request: CursorInvocationRequest):
        raise RuntimeError("cursor transport closed")

    def denied_fallback(request: CursorInvocationRequest):
        raise GatewayAccessDenied("403 Access denied for configured reviewer route")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", failing_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", denied_fallback)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        openai_base_url="https://litellm.example/v1",
        reviewer_infra_retry_limit=0,
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_access_denied"
    assert result.failure_classification == "reviewer_access_denied"
    assert result.recoverable is False
    assert result.fallback_from_runtime == "cursor_sdk"
    assert result.diagnostics["fallback"]["primary_failure"]["probe"]["reason"] == (
        "reviewer_infrastructure_unavailable"
    )
    assert result.diagnostics["fallback"]["fallback_failure"]["probe"]["reason"] == (
        "reviewer_access_denied"
    )


def test_cursor_sdk_contract_miss_does_not_fall_back_even_with_explicit_key(
    tmp_path: Path,
    monkeypatch,
):
    def invalid_cursor_contract(request: CursorInvocationRequest):
        return "No typed outcome here.", {
            "agent_id": "agent-cursor",
            "run_id": "run-cursor",
            "status": "finished",
            "model": "composer-2.5",
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
            "duration_ms": 12,
            "prompt_chars": 2048,
            "prompt_sha256": "b" * 64,
        }

    def fallback_run(request: CursorInvocationRequest):
        raise AssertionError("contract failures must not fall back to LiteLLM")

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", invalid_cursor_contract)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fallback_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        reviewer_infra_retry_limit=0,
        contract_retry_limit=0,
    ))

    assert result.probe.reason == "reviewer_contract_unmet"
    assert result.failure_classification == "reviewer_contract_unmet"
    assert result.recoverable is True
    assert result.reviewer_runtime == "cursor_sdk"
    assert result.reviewer_assurance == "tool_backed_primary"
    assert "fallback" not in result.diagnostics


def test_cursor_sdk_fallback_revise_still_blocks(tmp_path: Path, monkeypatch):
    def failing_cursor(request: CursorInvocationRequest):
        raise RuntimeError("cursor transport closed")

    def fallback_run(request: CursorInvocationRequest):
        outcome = _complete_cursor_outcome(task_id=request.task_id, decision="revise")
        return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>", _litellm_metadata()

    monkeypatch.setattr(cursor_agent, "_run_cursor_sdk", failing_cursor)
    monkeypatch.setattr(cursor_agent, "_run_litellm_structured", fallback_run)

    result = invoke_cursor_agent(CursorInvocationRequest(
        task_id="tri-agent",
        gate="outcome_review",
        instruction="Review the outcome.",
        cwd=tmp_path,
        openai_api_key="fallback-key",
        contract_retry_limit=0,
    ))

    assert result.probe.ok
    assert result.outcome is not None
    assert not cursor_accepts(result)
    assert result.failure_classification is None
    assert result.reviewer_assurance == "fallback_text_only"


def test_structured_fallback_prompt_compacts_large_receipts(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    outcome = _complete_cursor_outcome(task_id="tri-agent")
    huge_receipt_result = "receipt-output-" + ("x" * 20_000)
    huge_transcript = "claude-transcript-" + ("y" * 20_000)

    class FakeCompletions:
        def create(self, **kwargs):
            captured["completion_kwargs"] = kwargs
            return SimpleNamespace(
                id="chatcmpl-compact",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=outcome.model_dump_json()),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=200),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    cursor_agent._run_litellm_structured(
        CursorInvocationRequest(
            task_id="tri-agent",
            gate="outcome_review",
            instruction="Review the outcome.",
            cwd=tmp_path,
            claude_outcome={
                "summary": "Claude accepted.",
                "transcript": huge_transcript,
                "changed_files": ["supervisor/cursor_agent.py"],
                "tests": ["uv run --extra dev pytest tests/test_cursor_agent.py -q"],
                "critical_review": {"decision": "accept"},
            },
            tool_receipts=(
                {
                    "receipt_id": "huge-pytest",
                    "kind": "test",
                    "status": "passed",
                    "result": huge_receipt_result,
                    "claims": ["tests passed"],
                },
            ),
            openai_api_key="fallback-key",
            openai_base_url="https://litellm.example/v1",
        )
    )

    prompt = captured["completion_kwargs"]["messages"][1]["content"]
    assert len(prompt) < 20_000
    assert "huge-pytest" in prompt
    assert "tests passed" in prompt
    assert "Always end with <dual_agent_outcome>" in prompt
    assert "critical_review object" in prompt
    assert "x" * 5000 not in prompt
    assert "y" * 5000 not in prompt


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
        reviewer_output_mode="cursor_sdk",
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
        reviewer_output_mode="cursor_sdk",
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
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-trigger-fallback")
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
        reviewer_output_mode="cursor_sdk",
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
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-trigger-fallback")
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
        reviewer_output_mode="cursor_sdk",
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
            "--no-codex-mcp-env",
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


def test_probe_env_loader_can_supply_cursor_api_key(tmp_path: Path, monkeypatch):
    from mcp_tools.codex_supervisor_workflow_cli import load_codex_mcp_env

    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    codex_config = tmp_path / "config.toml"
    codex_config.write_text(
        "\n".join([
            "[mcp_servers.codex_supervisor]",
            'command = "/tmp/codex-supervisor-mcp"',
            "",
            "[mcp_servers.codex_supervisor.env]",
            'CURSOR_API_KEY = "cursor-from-config"',
        ]),
        encoding="utf-8",
    )

    loaded = load_codex_mcp_env(codex_config)

    assert loaded == {"CURSOR_API_KEY": "cursor-from-config"}
    assert os.environ["CURSOR_API_KEY"] == "cursor-from-config"


def test_cursor_sdk_receives_sandbox_options_when_available(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    outcome = _complete_cursor_outcome(task_id="tri-agent")

    class FakeSandboxOptions:
        def __init__(self, **kwargs):
            self.enabled = kwargs.get("enabled")
            captured["sandbox_options_kwargs"] = kwargs

    class FakeLocalAgentOptions:
        def __init__(self, **kwargs):
            captured["local_options_kwargs"] = kwargs

    class FakeRun:
        id = "cursor-run-1"
        status = "finished"
        model = "composer-2.5"
        duration_ms = 12

        def text(self):
            return f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>"

    class FakeAgent:
        agent_id = "cursor-agent-1"
        model = "composer-2.5"

        @classmethod
        def create(cls, **kwargs):
            captured["agent_kwargs"] = kwargs
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def send(self, prompt, options):
            captured["send_options"] = options
            return FakeRun()

    monkeypatch.setitem(
        sys.modules,
        "cursor_sdk",
        SimpleNamespace(Agent=FakeAgent, LocalAgentOptions=FakeLocalAgentOptions),
    )
    monkeypatch.setitem(
        sys.modules,
        "cursor_sdk.types",
        SimpleNamespace(SandboxOptions=FakeSandboxOptions),
    )

    cursor_agent._run_cursor_sdk(CursorInvocationRequest(
        task_id="tri-agent",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        reviewer_output_mode="cursor_sdk",
        reviewer_worktree_isolation="none",
    ))

    local_options = captured["local_options_kwargs"]
    sandbox_options = local_options["sandbox_options"]
    assert isinstance(sandbox_options, FakeSandboxOptions)
    assert sandbox_options.enabled is True
    assert captured["sandbox_options_kwargs"] == {"enabled": True}
    assert captured["send_options"] == {"mode": "plan"}
