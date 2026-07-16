from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from supervisor.agent_runtime import (
    AgentRunHandle,
    AgentRunResult,
    AgentTask,
    RuntimeEvent,
)
from supervisor.cursor_agent import CursorInvocationRequest
from supervisor.dual_agent import Outcome
from supervisor.model_client import (
    ModelRequest,
    ModelResponse,
    StructuredModelResponseError,
)
from supervisor.reviewer_registry import (
    ReviewerSpec,
    RuntimeReviewerAdapter,
    StructuredReviewerAdapter,
    configured_reviewers,
)
from supervisor.runtime_execution import RuntimeExecution


def _outcome(task_id: str, *, specialist: str) -> Outcome:
    return Outcome(
        task_id=task_id,
        summary="The independent review is complete.",
        specialists=[{"name": specialist, "decision": "accept"}],
        decisions=["accept"],
        objections=[],
        changed_files=[],
        tests=["focused fake reviewer"],
        test_status="passed",
        confidence=0.91,
        confidence_rationale="The supplied evidence satisfies the gate.",
        confidence_criteria=["typed outcome complete", "evidence inspected"],
        claims=[],
        critical_review={
            "strongest_objection": "none",
            "missing_evidence": [],
            "contradictions_checked": ["gate evidence"],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "Contradictory evidence.",
            "decision": "accept",
            "severity": "none",
            "reviewer_context_receipt": {
                "files_reviewed": [],
                "criteria_checked": [],
                "receipts_considered": [],
                "assumptions": [],
                "missing_context": [],
            },
        },
    )


def _runtime_execution(
    *,
    runtime: str,
    requested_model: str,
    resolved_model: str,
    task_id: str,
    specialist: str,
    status: str = "completed",
) -> RuntimeExecution:
    outcome = _outcome(task_id, specialist=specialist)
    event = RuntimeEvent(
        kind="tool.completed",
        payload={"command": "rg -n reviewer supervisor", "exit_code": 0},
        ts_ms=1_000,
    )
    handle = AgentRunHandle(
        run_id=f"{runtime}-run",
        task_id=task_id,
        runtime=runtime,
        session_id=f"{runtime}-session",
        capabilities={"stream": True, "cancel": True},
    )
    result = AgentRunResult(
        run_id=handle.run_id,
        task_id=task_id,
        runtime=runtime,
        session_id=handle.session_id,
        status=status,
        output=(
            f"<dual_agent_outcome>{outcome.model_dump_json()}"
            "</dual_agent_outcome>"
        ),
        events=(event,),
        started_at_ms=1_000,
        ended_at_ms=1_075,
        cost_usd=0.012,
        resolved_model=resolved_model,
        result_hash=f"{runtime}-result-hash",
        token_usage={"tokens_in": 120, "tokens_out": 80},
        model_provenance=f"{runtime}.response.model",
        cost_provenance=f"{runtime}.response.cost",
        token_provenance=f"{runtime}.response.usage",
        metadata={
            "requested_model": requested_model,
            "transport": "fake",
        },
    )
    return RuntimeExecution(handle=handle, events=(event,), result=result)


@pytest.mark.parametrize(
    ("runtime", "requested_model", "resolved_model", "provider_family"),
    [
        (
            "claude_code",
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            "anthropic",
        ),
        ("codex", "gpt-5.5", "gpt-5.5-2026-07-01", "openai"),
    ],
)
def test_runtime_reviewer_maps_request_and_preserves_normalized_run_diagnostics(
    tmp_path: Path,
    runtime: str,
    requested_model: str,
    resolved_model: str,
    provider_family: str,
) -> None:
    seen: list[AgentTask] = []
    specialist = "Neutral Runtime Reviewer"

    def runner(task: AgentTask) -> RuntimeExecution:
        seen.append(task)
        return _runtime_execution(
            runtime=runtime,
            requested_model=requested_model,
            resolved_model=resolved_model,
            task_id=task.task_id,
            specialist=specialist,
        )

    reviewer = RuntimeReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id=f"{runtime}-reviewer",
            runtime=runtime,
            model=requested_model,
            provider_family=provider_family,
            lineage=(provider_family, runtime, requested_model),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
        runner=runner,
    )
    request = CursorInvocationRequest(
        task_id="neutral-runtime-review",
        gate="outcome_review",
        instruction="Review the implementation evidence.",
        cwd=tmp_path,
        expected_specialists=(specialist,),
    )

    result = reviewer.review(request)

    assert result.probe.ok
    assert result.outcome is not None
    assert result.outcome.task_id == request.task_id
    assert result.agent_id == f"{runtime}-session"
    assert result.run_id == f"{runtime}-run"
    assert result.status == "completed"
    assert result.model == resolved_model
    assert result.reviewer_runtime == runtime
    assert result.reviewer_output_mode == "agent_runtime"
    assert result.duration_ms == 75
    assert result.reviewer_assurance == "tool_backed_primary"
    assert len(seen) == 1
    task = seen[0]
    assert task.task_id == request.task_id
    assert task.cwd == tmp_path.resolve()
    assert task.model == requested_model
    assert task.timeout_s == float(request.timeout_s)
    assert task.env == {}
    assert task.inherit_env is True
    assert request.instruction in task.instruction
    assert "dual_agent_outcome" in task.instruction
    assert task.metadata["reviewer_id"] == reviewer.spec.reviewer_id
    assert task.metadata["gate"] == request.gate
    assert result.diagnostics is not None
    diagnostics = result.diagnostics["agent_runtime"]
    assert diagnostics["requested_model"] == requested_model
    assert diagnostics["resolved_model"] == resolved_model
    assert diagnostics["run_id"] == f"{runtime}-run"
    assert diagnostics["session_id"] == f"{runtime}-session"
    assert diagnostics["runtime"] == runtime
    assert diagnostics["result_hash"] == f"{runtime}-result-hash"
    assert diagnostics["model_provenance"] == f"{runtime}.response.model"
    assert diagnostics["cost_provenance"] == f"{runtime}.response.cost"
    assert diagnostics["token_provenance"] == f"{runtime}.response.usage"
    assert diagnostics["token_usage"] == {"tokens_in": 120, "tokens_out": 80}
    assert diagnostics["cost_usd"] == 0.012


def test_runtime_reviewer_fails_closed_on_non_completed_or_malformed_run(
    tmp_path: Path,
) -> None:
    specialist = "Neutral Runtime Reviewer"
    execution = _runtime_execution(
        runtime="codex",
        requested_model="gpt-5.5",
        resolved_model="gpt-5.5-2026-07-01",
        task_id="neutral-runtime-review",
        specialist=specialist,
    )
    reviewer = RuntimeReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="runtime-reviewer",
            runtime="codex",
            model="gpt-5.5",
            provider_family="openai",
        ),
        runner=lambda _: replace(
            execution,
            result=replace(execution.result, output="not a typed outcome"),
        ),
    )

    malformed = reviewer.review(
        CursorInvocationRequest(
            task_id="neutral-runtime-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
        )
    )

    assert not malformed.probe.ok
    assert malformed.outcome is None
    assert malformed.failure_classification == "reviewer_contract_unmet"

    failed_reviewer = replace(
        reviewer,
        runner=lambda _: replace(
            execution,
            result=replace(execution.result, status="failed"),
        ),
    )
    failed = failed_reviewer.review(
        CursorInvocationRequest(
            task_id="neutral-runtime-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
        )
    )

    assert not failed.probe.ok
    assert failed.outcome is None
    assert failed.failure_classification == "reviewer_infrastructure_unavailable"


def test_runtime_reviewer_retries_infrastructure_failure_and_records_attempts(
    tmp_path: Path,
) -> None:
    specialist = "Neutral Runtime Reviewer"
    calls = 0

    def runner(task: AgentTask) -> RuntimeExecution:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary runtime failure")
        return _runtime_execution(
            runtime="codex",
            requested_model="gpt-5.5",
            resolved_model="gpt-5.5-2026-07-01",
            task_id=task.task_id,
            specialist=specialist,
        )

    reviewer = RuntimeReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="runtime-reviewer",
            runtime="codex",
            model="gpt-5.5",
            provider_family="openai",
        ),
        runner=runner,
    )

    result = reviewer.review(
        CursorInvocationRequest(
            task_id="neutral-runtime-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
            reviewer_infra_retry_limit=1,
            reviewer_infra_retry_backoff_s=0,
        )
    )

    assert result.probe.ok
    assert calls == 2
    assert result.attempts == 2
    assert result.retry_reasons == ("runtime_reviewer_invocation_failed",)
    retry = result.diagnostics["infrastructure_retries"]
    assert retry["attempt_count"] == 2
    assert retry["failed_attempt_count"] == 1
    assert retry["exhausted"] is False


def test_runtime_reviewer_does_not_swallow_cancelled_error(
    tmp_path: Path,
) -> None:
    def runner(task: AgentTask) -> RuntimeExecution:
        raise asyncio.CancelledError

    reviewer = RuntimeReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="runtime-reviewer",
            runtime="codex",
            model="gpt-5.5",
        ),
        runner=runner,
    )

    with pytest.raises(asyncio.CancelledError):
        reviewer.review(
            CursorInvocationRequest(
                task_id="neutral-runtime-review",
                gate="outcome_review",
                instruction="Review.",
                cwd=tmp_path,
            )
        )


class _FakeModelClient:
    def __init__(
        self,
        outcome: Outcome | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.error = error
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return ModelResponse(
            text=self.outcome.model_dump_json(),
            resolved_model="fixture-resolved-model",
            provider="fixture-provider",
            usage={"input_tokens": 11, "output_tokens": 7},
            cost_usd=0.004,
        )

    async def structured_complete(self, request: ModelRequest, schema: type):
        raise AssertionError(
            "reviewer must preserve ModelResponse provenance via complete"
        )


def test_structured_reviewer_uses_model_client_and_validates_typed_outcome(
    tmp_path: Path,
) -> None:
    specialist = "Neutral Structured Reviewer"
    model = "gemini-3.1-pro-preview"
    client = _FakeModelClient(
        _outcome("neutral-structured-review", specialist=specialist)
    )
    reviewer = StructuredReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="structured-reviewer",
            runtime="model_client_structured",
            model=model,
            provider_family="google",
            lineage=("google", "model_client_structured", model),
            tool_access="text_only",
            assurance_grade="text_only",
        ),
        model_client=client,
    )

    result = reviewer.review(
        CursorInvocationRequest(
            task_id="neutral-structured-review",
            gate="outcome_review",
            instruction="Review the structured evidence.",
            cwd=tmp_path,
            reviewer_max_tokens=777,
            expected_specialists=(specialist,),
        )
    )

    assert result.probe.ok
    assert result.outcome is not None
    assert len(client.requests) == 1
    model_request = client.requests[0]
    assert model_request.model == model
    assert model_request.max_tokens == 777
    assert model_request.temperature == 0.0
    assert model_request.metadata["reviewer_id"] == reviewer.spec.reviewer_id
    assert model_request.metadata["gate"] == "outcome_review"
    assert "Review the structured evidence." in model_request.messages[-1].content
    assert "Return one JSON object only" in model_request.messages[-1].content
    assert result.model == "fixture-resolved-model"
    assert result.reviewer_runtime == "model_client_structured"
    assert result.reviewer_output_mode == "model_client_structured"
    assert result.reviewer_assurance == "structured_text_only"
    assert result.diagnostics == {
        "requested_model": model,
        "model_client": {
            "client_type": "_FakeModelClient",
            "structured": True,
        },
        "model_response": {
            "resolved_model": "fixture-resolved-model",
            "provider": "fixture-provider",
            "usage": {"input_tokens": 11, "output_tokens": 7},
            "cost_usd": 0.004,
        },
    }


def test_structured_reviewer_fails_closed_when_model_client_rejects_output(
    tmp_path: Path,
) -> None:
    client = _FakeModelClient(
        error=StructuredModelResponseError("invalid structured output")
    )
    reviewer = StructuredReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="structured-reviewer",
            runtime="model_client_structured",
            model="fixture-model",
        ),
        model_client=client,
    )

    result = reviewer.review(
        CursorInvocationRequest(
            task_id="neutral-structured-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
        )
    )

    assert not result.probe.ok
    assert result.outcome is None
    assert result.failure_classification == "reviewer_contract_unmet"
    assert result.recoverable


def test_structured_reviewer_enforces_timeout_and_retries_before_failing(
    tmp_path: Path,
) -> None:
    class _HangingModelClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: ModelRequest):
            self.calls += 1
            await asyncio.sleep(60)

    client = _HangingModelClient()
    reviewer = StructuredReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="structured-reviewer",
            runtime="model_client_structured",
            model="fixture-model",
        ),
        model_client=client,
    )

    result = reviewer.review(
        CursorInvocationRequest(
            task_id="neutral-structured-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            timeout_s=1,
            reviewer_infra_retry_limit=1,
            reviewer_infra_retry_backoff_s=0.0,
        )
    )

    assert client.calls == 2
    assert not result.probe.ok
    assert result.failure_classification == "reviewer_infrastructure_unavailable"
    assert result.attempts == 2
    assert result.retry_reasons == (
        "structured_reviewer_timeout",
        "structured_reviewer_timeout",
    )
    retries = result.diagnostics["infrastructure_retries"]
    assert retries["retry_limit"] == 1
    assert retries["attempt_count"] == 2
    assert retries["exhausted"]
    assert retries["backoff_s"] == [0.0]


def test_configured_reviewers_accepts_neutral_adapters_and_runner_injection(
    tmp_path: Path,
) -> None:
    specialist = "Neutral Runtime Reviewer"
    execution = _runtime_execution(
        runtime="codex",
        requested_model="gpt-5.5",
        resolved_model="gpt-5.5-2026-07-01",
        task_id="configured-neutral-review",
        specialist=specialist,
    )
    calls: list[AgentTask] = []

    def runtime_runner(task: AgentTask) -> RuntimeExecution:
        calls.append(task)
        return replace(
            execution,
            handle=replace(execution.handle, task_id=task.task_id),
            result=replace(
                execution.result,
                task_id=task.task_id,
                output=(
                    "<dual_agent_outcome>"
                    f"{_outcome(task.task_id, specialist=specialist).model_dump_json()}"
                    "</dual_agent_outcome>"
                ),
            ),
        )

    runtime_adapter = RuntimeReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="injected-runtime",
            runtime="codex",
            model="gpt-5.5",
            provider_family="openai",
        ),
        runner=runtime_runner,
    )
    structured_adapter = StructuredReviewerAdapter(
        spec=ReviewerSpec(
            reviewer_id="injected-structured",
            runtime="model_client_structured",
            model="fixture-model",
        ),
        model_client=_FakeModelClient(
            _outcome("configured-neutral-review", specialist=specialist)
        ),
    )

    injected = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
        reviewer_adapters=(runtime_adapter, structured_adapter),
    )

    assert injected == [runtime_adapter, structured_adapter]

    constructed = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
        runtime_runner=runtime_runner,
        codex_model="gpt-5.5",
    )

    assert isinstance(constructed[1], RuntimeReviewerAdapter)
    assert constructed[1].inherit_environment is False
    result = constructed[1].review(
        CursorInvocationRequest(
            task_id="configured-neutral-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
        )
    )
    assert result.probe.ok
    assert calls


def test_configured_reviewers_uses_injected_model_client_for_structured_slot(
    tmp_path: Path,
) -> None:
    specialist = "Neutral Structured Reviewer"
    client = _FakeModelClient(
        _outcome("configured-structured-review", specialist=specialist)
    )

    reviewers = configured_reviewers(
        reviewer_output_mode="litellm_structured",
        reviewer_model="gemini-3.1-pro-preview",
        model_client=client,
    )

    assert isinstance(reviewers[0], StructuredReviewerAdapter)
    result = reviewers[0].review(
        CursorInvocationRequest(
            task_id="configured-structured-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
        )
    )
    assert result.probe.ok
    assert client.requests


def test_configured_runtime_reviewer_keeps_codex_cli_parity_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supervisor.agent_runtime import CodexRuntime

    monkeypatch.setenv("OPENAI_API_KEY", "codex-env-auth")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    specialist = "Neutral Runtime Reviewer"
    calls: list[AgentTask] = []

    def runtime_runner(task: AgentTask) -> RuntimeExecution:
        calls.append(task)
        execution = _runtime_execution(
            runtime="codex",
            requested_model="gpt-5.5",
            resolved_model="gpt-5.5-2026-07-01",
            task_id=task.task_id,
            specialist=specialist,
        )
        return execution

    constructed = configured_reviewers(
        reviewer_output_mode="cursor_sdk",
        reviewer_model=None,
        runtime_runner=runtime_runner,
        codex_model="gpt-5.5",
    )
    adapter = constructed[1]
    assert isinstance(adapter, RuntimeReviewerAdapter)
    assert adapter.inherit_environment is False
    assert adapter.task_metadata == {"reasoning_effort": "xhigh"}
    assert adapter.environment == {
        "OPENAI_API_KEY": "codex-env-auth",
        "CODEX_HOME": str(tmp_path / "codex-home"),
    }

    result = adapter.review(
        CursorInvocationRequest(
            task_id="parity-runtime-review",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
        )
    )
    assert result.probe.ok
    task = calls[0]
    assert task.env["OPENAI_API_KEY"] == "codex-env-auth"
    assert task.env["CODEX_HOME"] == str(tmp_path / "codex-home")
    assert task.inherit_env is False
    assert task.metadata["reasoning_effort"] == "xhigh"
    assert task.metadata["read_only_review"] is True

    argv = CodexRuntime().preview_start_argv(task)
    assert 'reasoning_effort="xhigh"' in argv
    sandbox_index = argv.index("--sandbox")
    assert argv[sandbox_index + 1] == "read-only"


def test_configured_structured_legacy_slot_defaults_reviewer_model(
    tmp_path: Path,
) -> None:
    from supervisor.cursor_agent import DEFAULT_STRUCTURED_REVIEWER_MODEL

    specialist = "Neutral Structured Reviewer"
    client = _FakeModelClient(
        _outcome("configured-structured-default-model", specialist=specialist)
    )

    reviewers = configured_reviewers(
        reviewer_output_mode="litellm_structured",
        reviewer_model=None,
        model_client=client,
    )

    assert isinstance(reviewers[0], StructuredReviewerAdapter)
    assert reviewers[0].spec.model == DEFAULT_STRUCTURED_REVIEWER_MODEL
    result = reviewers[0].review(
        CursorInvocationRequest(
            task_id="configured-structured-default-model",
            gate="outcome_review",
            instruction="Review.",
            cwd=tmp_path,
            expected_specialists=(specialist,),
        )
    )
    assert result.probe.ok
    assert client.requests
    assert client.requests[0].model == DEFAULT_STRUCTURED_REVIEWER_MODEL
