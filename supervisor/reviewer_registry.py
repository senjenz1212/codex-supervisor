"""Independent reviewer registry and panel result helpers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .cursor_agent import CursorInvocationRequest, CursorInvocationResult, cursor_accepts, invoke_cursor_agent


@dataclass(frozen=True)
class ReviewerSpec:
    reviewer_id: str
    runtime: str
    model: str | None = None
    provider_family: str = "unknown"
    lineage: tuple[str, ...] = ()
    tool_access: str = "unknown"
    assurance_grade: str = "self_reported"


class ReviewerAdapter(Protocol):
    spec: ReviewerSpec

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        ...


@dataclass(frozen=True)
class CursorCompatibleReviewer:
    spec: ReviewerSpec
    runner: Callable[[CursorInvocationRequest], CursorInvocationResult] = invoke_cursor_agent

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        return self.runner(request)


@dataclass(frozen=True)
class MockReviewer:
    spec: ReviewerSpec
    result: CursorInvocationResult

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        return self.result


def configured_reviewers(
    *,
    reviewer_output_mode: str,
    reviewer_model: str | None,
    runner: Callable[[CursorInvocationRequest], CursorInvocationResult] = invoke_cursor_agent,
) -> list[ReviewerAdapter]:
    """Return the configured reviewer roster for a gate.

    This is intentionally small: the current production roster is one
    Cursor-compatible reviewer slot whose runtime may be Cursor SDK or
    LiteLLM/Gemini. Additional real reviewers are a later slice.
    """
    spec = ReviewerSpec(
        reviewer_id="independent-reviewer-0",
        runtime=reviewer_output_mode,
        model=reviewer_model,
        provider_family=_provider_family(reviewer_output_mode, reviewer_model),
        lineage=_lineage(reviewer_output_mode, reviewer_model),
        tool_access=_tool_access(reviewer_output_mode),
        assurance_grade=_assurance_grade_for_runtime(reviewer_output_mode),
    )
    return [CursorCompatibleReviewer(spec=spec, runner=runner)]


def independent_reviewer_results_from_cursor_result(
    result: CursorInvocationResult,
    *,
    task_id: str,
    gate: str,
    round_index: int,
    reviewer_id: str = "independent-reviewer-0",
) -> list[dict[str, Any]]:
    return [
        independent_reviewer_result_from_cursor_result(
            result,
            task_id=task_id,
            gate=gate,
            round_index=round_index,
            reviewer_id=reviewer_id,
        )
    ]


def independent_reviewer_result_from_cursor_result(
    result: CursorInvocationResult,
    *,
    task_id: str,
    gate: str,
    round_index: int,
    reviewer_id: str = "independent-reviewer-0",
) -> dict[str, Any]:
    outcome_payload = result.outcome.model_dump() if result.outcome is not None else None
    output_json = json.dumps(outcome_payload, sort_keys=True, default=str) if outcome_payload else ""
    transcript = result.transcript or ""
    critical_review = (
        outcome_payload.get("critical_review")
        if isinstance(outcome_payload, dict) and isinstance(outcome_payload.get("critical_review"), dict)
        else {}
    )
    confidence = (
        outcome_payload.get("confidence")
        if isinstance(outcome_payload, dict)
        else None
    )
    runtime = result.reviewer_runtime or result.reviewer_output_mode or "unknown"
    model = result.model
    return {
        "schema_version": "independent-reviewer-panel-result/v1",
        "reviewer_id": reviewer_id,
        "task_id": task_id,
        "gate": gate,
        "round_index": round_index,
        "verdict_present": result.outcome is not None,
        "accepted": cursor_accepts(result),
        "decision": _decision_from_result(result),
        "severity": str(critical_review.get("severity") or ("none" if cursor_accepts(result) else "important")),
        "confidence": confidence,
        "runtime": runtime,
        "reviewer_runtime": result.reviewer_runtime,
        "reviewer_output_mode": result.reviewer_output_mode,
        "model": model,
        "provider_family": _provider_family(runtime, model),
        "lineage": list(_lineage(runtime, model)),
        "tool_access": _tool_access(runtime),
        "assurance_grade": _assurance_grade(result),
        "reviewer_assurance": result.reviewer_assurance,
        "transcript_refs": [
            {
                "kind": "reviewer_transcript_tail",
                "ref": f"independent_reviewer_review:{task_id}:{gate}:{round_index}:{reviewer_id}",
                "chars": min(len(transcript), 4000),
            }
        ],
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(output_json.encode("utf-8")).hexdigest() if output_json else None,
        "critical_review": critical_review,
        "failure_classification": result.failure_classification,
        "recoverable": result.recoverable,
        "attempts": result.attempts,
    }


def evaluate_reviewer_panel(
    results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    low_confidence_threshold: float = 0.0,
) -> dict[str, Any]:
    """Conservative non-weighted aggregation for independent reviewers."""
    threshold = _clamp_confidence(low_confidence_threshold)
    reviewer_inputs = [_reviewer_input_summary(result) for result in results if isinstance(result, dict)]
    available_reviewers = [
        item["reviewer_id"]
        for item in reviewer_inputs
        if item["verdict_present"] and item["decision"] in {"accept", "revise", "deny"}
    ]
    missing_reviewers = [
        item["reviewer_id"]
        for item in reviewer_inputs
        if not item["verdict_present"] or item["decision"] not in {"accept", "revise", "deny"}
    ]
    blocking_reviewers = [
        item["reviewer_id"]
        for item in reviewer_inputs
        if (
            item["verdict_present"]
            and item["decision"] in {"revise", "deny"}
            and item["severity"] in {"critical", "important"}
        )
    ]
    non_accepting_reviewers = [
        item["reviewer_id"]
        for item in reviewer_inputs
        if item["verdict_present"] and item["decision"] in {"revise", "deny"}
    ]
    low_confidence_reviewers = [
        item["reviewer_id"]
        for item in reviewer_inputs
        if (
            item["verdict_present"]
            and item["decision"] == "accept"
            and item["confidence"] is not None
            and item["confidence"] < threshold
        )
    ]
    accepted_reviewers = [
        item["reviewer_id"]
        for item in reviewer_inputs
        if item["verdict_present"] and item["decision"] == "accept"
    ]

    decision = "accept"
    reason = "all_available_reviewers_accept"
    if not reviewer_inputs:
        reason = "review_not_required"
    elif blocking_reviewers:
        decision = "revise"
        reason = "blocking_reviewer_objection"
    elif non_accepting_reviewers:
        decision = "revise"
        reason = "reviewer_non_accept"
    elif missing_reviewers:
        decision = "revise"
        reason = "missing_reviewer_verdict"
    elif low_confidence_reviewers:
        decision = "escalate"
        reason = "low_confidence_accept"

    return {
        "schema_version": "independent-reviewer-panel-decision/v1",
        "decision": decision,
        "reason": reason,
        "low_confidence_threshold": threshold,
        "available_reviewers": available_reviewers,
        "accepted_reviewers": accepted_reviewers,
        "blocking_reviewers": blocking_reviewers,
        "non_accepting_reviewers": non_accepting_reviewers,
        "missing_reviewers": missing_reviewers,
        "low_confidence_reviewers": low_confidence_reviewers,
        "reviewer_inputs": reviewer_inputs,
    }


def _reviewer_input_summary(result: dict[str, Any]) -> dict[str, Any]:
    reviewer_id = str(result.get("reviewer_id") or "unknown-reviewer")
    decision = str(result.get("decision") or "").strip().lower()
    severity = str(result.get("severity") or "none").strip().lower()
    verdict_present = bool(result.get("verdict_present", decision in {"accept", "revise", "deny"}))
    confidence = _coerce_confidence(result.get("confidence"))
    return {
        "reviewer_id": reviewer_id,
        "decision": decision,
        "severity": severity,
        "confidence": confidence,
        "accepted": bool(result.get("accepted")),
        "verdict_present": verdict_present,
        "runtime": result.get("runtime") or result.get("reviewer_runtime"),
        "model": result.get("model"),
        "provider_family": result.get("provider_family"),
        "assurance_grade": result.get("assurance_grade"),
    }


def _coerce_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return _clamp_confidence(float(value))
    except (TypeError, ValueError):
        return None


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _decision_from_result(result: CursorInvocationResult) -> str:
    if result.outcome is not None:
        for decision in result.outcome.decisions:
            if decision in {"accept", "revise", "deny"}:
                return decision
    return "accept" if cursor_accepts(result) else "revise"


def _provider_family(runtime: str | None, model: str | None) -> str:
    runtime_text = str(runtime or "").lower()
    model_text = str(model or "").lower()
    if "cursor" in runtime_text:
        return "cursor"
    if "gemini" in model_text:
        return "google"
    if "claude" in model_text:
        return "anthropic"
    if "gpt" in model_text or "openai" in runtime_text:
        return "openai"
    if "litellm" in runtime_text:
        return "openai_compatible"
    return "unknown"


def _lineage(runtime: str | None, model: str | None) -> tuple[str, ...]:
    provider = _provider_family(runtime, model)
    values = [provider]
    if runtime:
        values.append(str(runtime))
    if model:
        values.append(str(model))
    return tuple(dict.fromkeys(value for value in values if value and value != "unknown"))


def _tool_access(runtime: str | None) -> str:
    runtime_text = str(runtime or "").lower()
    if "cursor" in runtime_text:
        return "codebase_tools"
    if "litellm" in runtime_text:
        return "text_only"
    return "unknown"


def _assurance_grade(result: CursorInvocationResult) -> str:
    assurance = str(result.reviewer_assurance or "").lower()
    runtime = str(result.reviewer_runtime or result.reviewer_output_mode or "").lower()
    if "tool" in assurance or "cursor" in runtime:
        return "agentic"
    if "text" in assurance or "litellm" in runtime:
        return "text_only"
    return "self_reported"


def _assurance_grade_for_runtime(runtime: str | None) -> str:
    runtime_text = str(runtime or "").lower()
    if "cursor" in runtime_text:
        return "agentic"
    if "litellm" in runtime_text:
        return "text_only"
    return "self_reported"
