"""Provider-neutral reviewer adapters for runtime and model-client seams."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import ValidationError

from .agent_mailbox import critical_review_prompt
from .agent_runtime import AgentTask
from .cursor_agent import (
    CursorFailureClassification,
    CursorInvocationRequest,
    CursorInvocationResult,
    _evaluate_cursor_contract_completeness,
    _outcome_block_contract,
)
from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity
from .model_client import (
    ModelClient,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StructuredModelResponseError,
    parse_structured_response,
)
from .redaction import redact
from .runtime_execution import RuntimeExecution, RuntimeTaskRunner


T = TypeVar("T")


class ReviewerIdentity(Protocol):
    reviewer_id: str
    runtime: str
    model: str | None


@dataclass(frozen=True)
class RuntimeReviewerAdapter:
    """Run an independent reviewer through a provider-neutral agent runtime."""

    spec: ReviewerIdentity
    runner: RuntimeTaskRunner
    environment: Mapping[str, str] = field(default_factory=dict)
    inherit_environment: bool = True
    task_metadata: Mapping[str, Any] = field(default_factory=dict)

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        started = time.monotonic()
        requested_model = _requested_model(self.spec, request)
        if not requested_model:
            return _failure_result(
                probe_id="RUNTIME_REVIEWER",
                classification="reviewer_contract_unmet",
                reason="runtime_reviewer_model_missing",
                reviewer_runtime=self.spec.runtime or "agent_runtime",
                reviewer_output_mode="agent_runtime",
                diagnostics={
                    "failure": {
                        "reason": "runtime_reviewer_model_missing",
                        "reviewer_id": self.spec.reviewer_id,
                    }
                },
            )

        retry_limit = max(0, int(request.reviewer_infra_retry_limit or 0))
        backoff_base_s = max(
            0.0,
            float(request.reviewer_infra_retry_backoff_s or 0.0),
        )
        failed_attempts: list[dict[str, Any]] = []
        retry_backoff_s: list[float] = []
        retry_reasons: list[str] = []
        task = AgentTask(
            task_id=request.task_id,
            instruction=build_reviewer_prompt(
                request,
                reviewer_name=self.spec.reviewer_id,
                structured_json=False,
            ),
            cwd=Path(request.cwd).expanduser().resolve(),
            model=requested_model,
            timeout_s=float(request.timeout_s),
            env=dict(self.environment),
            inherit_env=bool(self.inherit_environment),
            metadata=_runtime_task_metadata(
                request,
                reviewer_id=self.spec.reviewer_id,
                requested_model=requested_model,
                extra=self.task_metadata,
            ),
        )
        execution: RuntimeExecution | None = None
        for attempt_index in range(retry_limit + 1):
            attempt = attempt_index + 1
            try:
                execution = self.runner(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = "runtime_reviewer_invocation_failed"
                failed_attempts.append({
                    "attempt": attempt,
                    "reason": reason,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                retry_reasons.append(reason)
                if attempt_index >= retry_limit:
                    return _failure_result(
                        probe_id="RUNTIME_REVIEWER",
                        classification="reviewer_infrastructure_unavailable",
                        reason=reason,
                        reviewer_runtime=self.spec.runtime or "agent_runtime",
                        reviewer_output_mode="agent_runtime",
                        duration_ms=_duration_ms(started),
                        diagnostics={
                            "requested_model": requested_model,
                            "failure": failed_attempts[-1],
                            "infrastructure_retries": _retry_diagnostics(
                                retry_limit=retry_limit,
                                attempt_count=attempt,
                                exhausted=True,
                                attempts=failed_attempts,
                                backoff_s=retry_backoff_s,
                            ),
                        },
                        attempts=attempt,
                        retry_reasons=tuple(retry_reasons),
                    )
                delay_s = backoff_base_s * (2 ** attempt_index)
                retry_backoff_s.append(delay_s)
                if delay_s:
                    time.sleep(delay_s)
                continue

            result = execution.result
            if result.status == "completed":
                break
            reason = (
                "runtime_reviewer_cancelled"
                if result.status in {"cancelled", "canceled"}
                else "runtime_reviewer_not_completed"
            )
            diagnostics = _runtime_result_diagnostics(
                execution,
                requested_model=requested_model,
            )
            common = _runtime_result_common(execution, diagnostics=diagnostics)
            failed_attempts.append({
                "attempt": attempt,
                "reason": reason,
                "runtime_status": result.status,
                "run_id": result.run_id,
            })
            retry_reasons.append(reason)
            if (
                reason == "runtime_reviewer_cancelled"
                or attempt_index >= retry_limit
            ):
                diagnostics["infrastructure_retries"] = _retry_diagnostics(
                    retry_limit=retry_limit,
                    attempt_count=attempt,
                    exhausted=attempt_index >= retry_limit,
                    attempts=failed_attempts,
                    backoff_s=retry_backoff_s,
                )
                return _failure_result(
                    probe_id="RUNTIME_REVIEWER",
                    classification="reviewer_infrastructure_unavailable",
                    reason=reason,
                    attempts=attempt,
                    retry_reasons=tuple(retry_reasons),
                    **common,
                )
            delay_s = backoff_base_s * (2 ** attempt_index)
            retry_backoff_s.append(delay_s)
            if delay_s:
                time.sleep(delay_s)

        if execution is None:  # pragma: no cover - loop always returns or sets it.
            raise RuntimeError("runtime reviewer retry loop produced no execution")

        result = execution.result
        diagnostics = _runtime_result_diagnostics(
            execution,
            requested_model=requested_model,
        )
        diagnostics["infrastructure_retries"] = _retry_diagnostics(
            retry_limit=retry_limit,
            attempt_count=attempt,
            exhausted=False,
            attempts=failed_attempts,
            backoff_s=retry_backoff_s,
        )
        common = _runtime_result_common(execution, diagnostics=diagnostics)
        consistency_error = _runtime_consistency_error(execution, task)
        if consistency_error is not None:
            return _failure_result(
                probe_id="RUNTIME_REVIEWER",
                classification="reviewer_contract_unmet",
                reason=consistency_error,
                attempts=attempt,
                retry_reasons=tuple((*retry_reasons, consistency_error)),
                **common,
            )

        probe, outcome = _validate_typed_outcome(
            request,
            transcript=result.output or "",
        )
        if not probe.ok or outcome is None:
            return _failure_result(
                probe_id="RUNTIME_REVIEWER",
                classification="reviewer_contract_unmet",
                reason=probe.reason,
                probe_details=probe.details,
                attempts=attempt,
                retry_reasons=tuple((*retry_reasons, probe.reason)),
                **common,
            )

        tool_use = diagnostics.get("tool_use")
        command_count = (
            tool_use.get("command_execution_count", 0)
            if isinstance(tool_use, dict)
            else 0
        )
        return CursorInvocationResult(
            probe=ProbeResult(
                "RUNTIME_REVIEWER",
                "green",
                "runtime_review_ok",
                probe.details,
            ),
            outcome=outcome,
            reviewer_assurance=(
                "tool_backed_primary" if command_count else "self_reported"
            ),
            attempts=attempt,
            retry_reasons=tuple(retry_reasons),
            **common,
        )


@dataclass(frozen=True)
class StructuredReviewerAdapter:
    """Run a text-only reviewer through the provider-neutral ModelClient."""

    spec: ReviewerIdentity
    model_client: ModelClient
    request_metadata: Mapping[str, Any] = field(default_factory=dict)

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        started = time.monotonic()
        requested_model = _requested_model(self.spec, request)
        runtime = self.spec.runtime or "model_client_structured"
        diagnostics: dict[str, Any] = {
            "requested_model": requested_model,
            "model_client": {
                "client_type": type(self.model_client).__name__,
                "structured": True,
            },
        }
        if not requested_model:
            return _failure_result(
                probe_id="STRUCTURED_REVIEWER",
                classification="reviewer_contract_unmet",
                reason="structured_reviewer_model_missing",
                reviewer_runtime=runtime,
                reviewer_output_mode="model_client_structured",
                duration_ms=_duration_ms(started),
                diagnostics=diagnostics,
            )

        model_request = ModelRequest(
            model=requested_model,
            messages=(
                ModelMessage(
                    role="system",
                    content=(
                        "You are an independent software-review gate. "
                        "Do not implement or edit; judge only the supplied evidence."
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=build_reviewer_prompt(
                        request,
                        reviewer_name=self.spec.reviewer_id,
                        structured_json=True,
                    ),
                ),
            ),
            max_tokens=max(1, int(request.reviewer_max_tokens)),
            temperature=0.0,
            metadata={
                **dict(self.request_metadata),
                "task_id": request.task_id,
                "gate": request.gate,
                "reviewer_id": self.spec.reviewer_id,
                "reviewer_output_mode": "model_client_structured",
            },
        )
        retry_limit = max(0, int(request.reviewer_infra_retry_limit or 0))
        backoff_base_s = max(
            0.0,
            float(request.reviewer_infra_retry_backoff_s or 0.0),
        )
        failed_attempts: list[dict[str, Any]] = []
        retry_backoff_s: list[float] = []
        retry_reasons: list[str] = []
        response: ModelResponse | None = None
        attempt = 1
        for attempt_index in range(retry_limit + 1):
            attempt = attempt_index + 1
            try:
                response = _run_awaitable_blocking(
                    lambda: self.model_client.complete(model_request),
                    timeout_s=float(request.timeout_s),
                )
                break
            except asyncio.CancelledError:
                raise
            except (StructuredModelResponseError, ValidationError) as exc:
                return _failure_result(
                    probe_id="STRUCTURED_REVIEWER",
                    classification="reviewer_contract_unmet",
                    reason="structured_reviewer_contract_unmet",
                    reviewer_runtime=runtime,
                    reviewer_output_mode="model_client_structured",
                    duration_ms=_duration_ms(started),
                    diagnostics={
                        **diagnostics,
                        "failure": {
                            "reason": "structured_reviewer_contract_unmet",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    },
                    attempts=attempt,
                    retry_reasons=tuple(
                        (*retry_reasons, "structured_reviewer_contract_unmet")
                    ),
                )
            except Exception as exc:
                reason = (
                    "structured_reviewer_timeout"
                    if isinstance(exc, TimeoutError)
                    else "structured_reviewer_invocation_failed"
                )
                failed_attempts.append({
                    "attempt": attempt,
                    "reason": reason,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                retry_reasons.append(reason)
                if attempt_index >= retry_limit:
                    return _failure_result(
                        probe_id="STRUCTURED_REVIEWER",
                        classification="reviewer_infrastructure_unavailable",
                        reason=reason,
                        reviewer_runtime=runtime,
                        reviewer_output_mode="model_client_structured",
                        duration_ms=_duration_ms(started),
                        diagnostics={
                            **diagnostics,
                            "failure": failed_attempts[-1],
                            "infrastructure_retries": _retry_diagnostics(
                                retry_limit=retry_limit,
                                attempt_count=attempt,
                                exhausted=True,
                                attempts=failed_attempts,
                                backoff_s=retry_backoff_s,
                            ),
                        },
                        attempts=attempt,
                        retry_reasons=tuple(retry_reasons),
                    )
                delay_s = backoff_base_s * (2 ** attempt_index)
                retry_backoff_s.append(delay_s)
                if delay_s:
                    time.sleep(delay_s)

        if response is None:  # pragma: no cover - loop always returns or breaks.
            raise RuntimeError("structured reviewer produced no model response")
        if failed_attempts:
            diagnostics["infrastructure_retries"] = _retry_diagnostics(
                retry_limit=retry_limit,
                attempt_count=attempt,
                exhausted=False,
                attempts=failed_attempts,
                backoff_s=retry_backoff_s,
            )
        try:
            outcome = parse_structured_response(response.text, Outcome)
        except (StructuredModelResponseError, ValidationError) as exc:
            return _failure_result(
                probe_id="STRUCTURED_REVIEWER",
                classification="reviewer_contract_unmet",
                reason="structured_reviewer_contract_unmet",
                reviewer_runtime=runtime,
                reviewer_output_mode="model_client_structured",
                duration_ms=_duration_ms(started),
                diagnostics={
                    **diagnostics,
                    "failure": {
                        "reason": "structured_reviewer_contract_unmet",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                },
                attempts=attempt,
                retry_reasons=tuple(
                    (*retry_reasons, "structured_reviewer_contract_unmet")
                ),
            )
        diagnostics["model_response"] = {
            "resolved_model": response.resolved_model,
            "provider": response.provider,
            "usage": dict(response.usage),
            "cost_usd": float(response.cost_usd),
        }
        transcript = _outcome_transcript(outcome)
        probe, validated_outcome = _validate_typed_outcome(
            request,
            transcript=transcript,
        )
        if not probe.ok or validated_outcome is None:
            return _failure_result(
                probe_id="STRUCTURED_REVIEWER",
                classification="reviewer_contract_unmet",
                reason=probe.reason,
                probe_details=probe.details,
                transcript=transcript,
                status="completed",
                reviewer_runtime=runtime,
                reviewer_output_mode="model_client_structured",
                duration_ms=_duration_ms(started),
                diagnostics=diagnostics,
                attempts=attempt,
                retry_reasons=tuple((*retry_reasons, probe.reason)),
            )

        return CursorInvocationResult(
            probe=ProbeResult(
                "STRUCTURED_REVIEWER",
                "green",
                "structured_review_ok",
                probe.details,
            ),
            outcome=validated_outcome,
            transcript=transcript,
            status="completed",
            model=response.resolved_model,
            reviewer_runtime=runtime,
            reviewer_output_mode="model_client_structured",
            duration_ms=_duration_ms(started),
            reviewer_assurance="structured_text_only",
            diagnostics=diagnostics,
            attempts=attempt,
            retry_reasons=tuple(retry_reasons),
        )


# Short aliases keep the public vocabulary convenient without creating a
# second implementation.
RuntimeReviewer = RuntimeReviewerAdapter
StructuredReviewer = StructuredReviewerAdapter
ModelClientReviewer = StructuredReviewerAdapter


def build_reviewer_prompt(
    request: CursorInvocationRequest,
    *,
    reviewer_name: str,
    structured_json: bool,
) -> str:
    """Build one provider-neutral review packet from the legacy request shape."""

    artifact_lines = [
        f"- {artifact.kind}: {Path(artifact.path)}"
        for artifact in request.planning_artifacts
    ]
    receipt_lines = [
        f"- {receipt.get('receipt_id') or receipt.get('id') or 'receipt'}: "
        f"{json.dumps(receipt, sort_keys=True, default=str)[:2000]}"
        for receipt in request.tool_receipts
    ]
    review_packet = (
        json.dumps(request.review_packet, sort_keys=True, indent=2, default=str)
        if request.review_packet
        else "null"
    )
    primary_outcome = (
        json.dumps(request.claude_outcome, sort_keys=True, indent=2, default=str)
        if request.claude_outcome
        else "{}"
    )
    output_contract = (
        _structured_outcome_contract()
        if structured_json
        else _outcome_block_contract()
    )
    return "\n".join(
        [
            f"Independent reviewer gate: {request.gate}.",
            f"Task id: {request.task_id}.",
            "",
            (
                f"Role: {reviewer_name} is an independent reviewer/challenger, "
                "not the implementer."
            ),
            "Do not edit files. Inspect the supplied worktree evidence and judge the gate.",
            critical_review_prompt("primary outcome and gate evidence"),
            "",
            "Instruction:",
            request.instruction.strip(),
            "",
            "Planning artifacts:",
            "\n".join(artifact_lines) if artifact_lines else "- none",
            "",
            "Evidence receipts:",
            "\n".join(receipt_lines) if receipt_lines else "- none",
            "",
            "Supervisor review packet JSON:",
            review_packet,
            "",
            "Reviewer context receipt requirement:",
            (
                "Return critical_review.reviewer_context_receipt with "
                "files_reviewed, criteria_checked, receipts_considered, "
                "assumptions, and missing_context arrays. Copy reviewed "
                "changed_files[].path, acceptance_items[], and "
                "runtime_receipt_ids[].receipt_id values from the packet; "
                "record anything omitted in missing_context."
            ),
            "",
            "Primary outcome JSON:",
            primary_outcome,
            "",
            _specialist_instruction(request.expected_specialists),
            output_contract,
        ]
    )


def _requested_model(
    spec: ReviewerIdentity,
    request: CursorInvocationRequest,
) -> str:
    return str(spec.model or request.reviewer_model or request.model or "").strip()


def _runtime_task_metadata(
    request: CursorInvocationRequest,
    *,
    reviewer_id: str,
    requested_model: str,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    raw_result_metadata = extra.get("result_metadata")
    result_metadata = (
        dict(raw_result_metadata)
        if isinstance(raw_result_metadata, Mapping)
        else {}
    )
    metadata = {
        **dict(extra),
        "reviewer_id": reviewer_id,
        "gate": request.gate,
        "reviewer_output_mode": "agent_runtime",
        "read_only_review": True,
        "result_metadata": {
            **result_metadata,
            "reviewer_id": reviewer_id,
            "gate": request.gate,
            "requested_model": requested_model,
        },
    }
    return metadata


def _runtime_consistency_error(
    execution: RuntimeExecution,
    task: AgentTask,
) -> str | None:
    handle = execution.handle
    result = execution.result
    if not handle.run_id or not result.run_id:
        return "runtime_reviewer_run_id_missing"
    if not (result.session_id or handle.session_id):
        return "runtime_reviewer_session_id_missing"
    if not (result.runtime or handle.runtime):
        return "runtime_reviewer_runtime_missing"
    if handle.task_id != task.task_id or result.task_id != task.task_id:
        return "runtime_reviewer_task_id_mismatch"
    if result.run_id != handle.run_id:
        return "runtime_reviewer_run_id_mismatch"
    if result.runtime and handle.runtime and result.runtime != handle.runtime:
        return "runtime_reviewer_runtime_mismatch"
    return None


def _runtime_result_diagnostics(
    execution: RuntimeExecution,
    *,
    requested_model: str,
) -> dict[str, Any]:
    handle = execution.handle
    result = execution.result
    events = result.events or execution.events
    command_executions = [
        redact(dict(event.payload))
        for event in events
        if event.kind == "tool.completed"
    ]
    return {
        "requested_model": requested_model,
        "agent_runtime": {
            "schema_version": result.schema_version,
            "requested_model": requested_model,
            "run_id": result.run_id,
            "task_id": result.task_id,
            "session_id": result.session_id,
            "runtime": result.runtime,
            "status": result.status,
            "resolved_model": result.resolved_model,
            "result_hash": result.result_hash,
            "started_at_ms": result.started_at_ms,
            "ended_at_ms": result.ended_at_ms,
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "token_usage": dict(result.token_usage),
            "model_provenance": result.model_provenance,
            "cost_provenance": result.cost_provenance,
            "token_provenance": result.token_provenance,
            "metadata": redact(dict(result.metadata)),
            "handle": {
                "run_id": handle.run_id,
                "task_id": handle.task_id,
                "runtime": handle.runtime,
                "session_id": handle.session_id,
                "capabilities": dict(handle.capabilities),
            },
            "event_count": len(events),
            "event_kinds": [event.kind for event in events],
        },
        "tool_use": {
            "command_execution_count": len(command_executions),
            "command_executions": command_executions,
        },
    }


def _runtime_result_common(
    execution: RuntimeExecution,
    *,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    result = execution.result
    return {
        "transcript": result.output or "",
        "agent_id": result.session_id or execution.handle.session_id or None,
        "run_id": result.run_id or execution.handle.run_id or None,
        "status": result.status or None,
        "model": result.resolved_model or None,
        "reviewer_runtime": (
            result.runtime or execution.handle.runtime or "agent_runtime"
        ),
        "reviewer_output_mode": "agent_runtime",
        "duration_ms": result.duration_ms,
        "diagnostics": diagnostics,
    }


def _retry_diagnostics(
    *,
    retry_limit: int,
    attempt_count: int,
    exhausted: bool,
    attempts: list[dict[str, Any]],
    backoff_s: list[float],
) -> dict[str, Any]:
    return {
        "retry_limit": retry_limit,
        "attempt_count": attempt_count,
        "failed_attempt_count": len(attempts),
        "exhausted": exhausted,
        "attempts": list(attempts),
        "backoff_s": list(backoff_s),
    }


def _validate_typed_outcome(
    request: CursorInvocationRequest,
    *,
    transcript: str,
) -> tuple[ProbeResult, Outcome | None]:
    probe, outcome = evaluate_outcome_fidelity(
        transcript,
        expected_specialists=request.expected_specialists,
        expected_decisions=request.expected_decisions,
        expected_objections=request.expected_objections,
    )
    if not probe.ok or outcome is None:
        return probe, None
    if outcome.task_id != request.task_id:
        return (
            ProbeResult(
                "REVIEWER",
                "red",
                "outcome_task_id_mismatch",
                {
                    "expected_task_id": request.task_id,
                    "actual_task_id": outcome.task_id,
                },
            ),
            None,
        )
    completeness = _evaluate_cursor_contract_completeness(outcome)
    if not completeness.ok:
        return completeness, None
    critical_review = outcome.critical_review
    receipt = critical_review.get("reviewer_context_receipt")
    missing_receipt_fields = [
        field_name
        for field_name in (
            "files_reviewed",
            "criteria_checked",
            "receipts_considered",
            "assumptions",
            "missing_context",
        )
        if not isinstance(receipt, dict)
        or not isinstance(receipt.get(field_name), list)
    ]
    if missing_receipt_fields:
        return (
            ProbeResult(
                "REVIEWER",
                "red",
                "outcome_missing_required_fields",
                {
                    "fields": [
                        f"critical_review.reviewer_context_receipt.{field_name}"
                        for field_name in missing_receipt_fields
                    ]
                },
            ),
            None,
        )
    return completeness, outcome


def _failure_result(
    *,
    probe_id: str,
    classification: CursorFailureClassification,
    reason: str,
    probe_details: Mapping[str, Any] | None = None,
    transcript: str = "",
    agent_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    model: str | None = None,
    reviewer_runtime: str | None = None,
    reviewer_output_mode: str | None = None,
    duration_ms: int | None = None,
    diagnostics: dict[str, Any] | None = None,
    attempts: int = 1,
    retry_reasons: tuple[str, ...] | None = None,
) -> CursorInvocationResult:
    details = redact({
        "original_reason": reason,
        "recoverable": True,
        **dict(probe_details or {}),
    })
    safe_diagnostics = redact(dict(diagnostics or {}))
    safe_diagnostics.setdefault(
        "failure",
        {
            "reason": reason,
            "classification": classification,
        },
    )
    return CursorInvocationResult(
        probe=ProbeResult(
            probe_id,
            "red",
            classification,
            details,
        ),
        outcome=None,
        transcript=transcript,
        agent_id=agent_id,
        run_id=run_id,
        status=status,
        model=model,
        reviewer_runtime=reviewer_runtime,
        reviewer_output_mode=reviewer_output_mode,
        duration_ms=duration_ms,
        reviewer_assurance="unavailable",
        diagnostics=safe_diagnostics,
        failure_classification=classification,
        recoverable=True,
        attempts=attempts,
        retry_reasons=retry_reasons or (reason,),
    )


def _specialist_instruction(expected_specialists: tuple[str, ...]) -> str:
    names = [name.strip() for name in expected_specialists if name.strip()]
    if names:
        return (
            "Return specialist records with exactly these names: "
            f"{', '.join(names)}. Use decision accept only if the gate should advance."
        )
    return (
        "Return at least one specialist record. "
        "Use decision accept only if the gate should advance."
    )


def _structured_outcome_contract() -> str:
    return (
        "Return one JSON object only, with no Markdown fence or XML wrapper. "
        "It must include task_id, summary, specialists, decisions, objections, "
        "changed_files, tests, test_status, confidence, confidence_rationale, "
        "confidence_criteria, claims, and critical_review. "
        "critical_review must include strongest_objection, missing_evidence, "
        "contradictions_checked, assumptions_to_verify, what_would_change_my_mind, "
        "decision, severity, and reviewer_context_receipt. "
        "Every specialist must include string name and decision fields."
    )


def _outcome_transcript(outcome: Outcome) -> str:
    return (
        f"<dual_agent_outcome>{outcome.model_dump_json()}"
        "</dual_agent_outcome>"
    )


def _run_awaitable_blocking(
    factory: Callable[[], Awaitable[T]],
    *,
    timeout_s: float | None = None,
) -> T:
    """Run an async model client safely from the synchronous reviewer API."""

    async def _invoke() -> T:
        if timeout_s is None:
            return await factory()
        return await asyncio.wait_for(factory(), timeout_s)

    with ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="structured-reviewer",
    ) as pool:
        return pool.submit(lambda: asyncio.run(_invoke())).result()


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


__all__ = [
    "ModelClientReviewer",
    "RuntimeReviewer",
    "RuntimeReviewerAdapter",
    "StructuredReviewer",
    "StructuredReviewerAdapter",
    "build_reviewer_prompt",
]
