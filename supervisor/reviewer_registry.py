"""Independent reviewer registry and panel result helpers."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from .cursor_agent import (
    DEFAULT_STRUCTURED_REVIEWER_MAX_TOKENS,
    CursorInvocationRequest,
    CursorInvocationResult,
    cursor_accepts,
    invoke_cursor_agent,
)
from .model_client import ModelClient
from .redaction import redact
from .reviewer_legacy_provider_edge import (
    CodexCliReviewer,
    CodexRunner,
    _codex_cli_reviewer_prompt,
)
from .reviewer_neutral import (
    RuntimeReviewerAdapter,
    StructuredReviewerAdapter,
)
from .runtime_execution import RuntimeTaskRunner

RuntimeReviewer = RuntimeReviewerAdapter
StructuredReviewer = StructuredReviewerAdapter
ModelClientReviewer = StructuredReviewerAdapter


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
        return self.runner(
            replace(
                request,
                reviewer_output_mode=self.spec.runtime,  # type: ignore[arg-type]
                reviewer_model=self.spec.model or request.reviewer_model,
            )
        )


@dataclass(frozen=True)
class LiteLLMReviewer:
    spec: ReviewerSpec
    runner: Callable[[CursorInvocationRequest], CursorInvocationResult] = invoke_cursor_agent
    openai_api_key: str | None = None
    openai_base_url: str | None = None

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        return self.runner(
            replace(
                request,
                reviewer_output_mode="litellm_structured",
                reviewer_model=self.spec.model or request.reviewer_model,
                openai_api_key=self.openai_api_key or request.openai_api_key,
                openai_base_url=self.openai_base_url or request.openai_base_url,
            )
        )


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
    codex_runner: CodexRunner | None = None,
    codex_model: str = "gpt-5.5",
    litellm_runner: Callable[[CursorInvocationRequest], CursorInvocationResult] | None = None,
    litellm_model: str | None = None,
    litellm_provider_family: str | None = None,
    litellm_openai_api_key: str | None = None,
    litellm_openai_base_url: str | None = None,
    reviewer_adapters: (
        list[ReviewerAdapter] | tuple[ReviewerAdapter, ...] | None
    ) = None,
    runtime_runner: RuntimeTaskRunner | None = None,
    model_client: ModelClient | None = None,
) -> list[ReviewerAdapter]:
    """Return the configured reviewer roster for a gate.

    This is intentionally small: reviewer 0 is the legacy Cursor-compatible
    slot, reviewer 1 is the GPT-family Codex CLI route proven by route
    evidence. An optional third LiteLLM reviewer is appended only when a
    caller opts in by passing ``litellm_model``; an empty or ``None`` value
    keeps the roster at two reviewers. Wider plugin mechanics are
    deliberately out of scope.

    Composition roots may instead inject a complete ``reviewer_adapters``
    roster, a provider-neutral ``runtime_runner`` for reviewer 1, or a
    ``model_client`` for structured slots. Omitting those seams preserves the
    legacy direct Cursor/Codex/LiteLLM construction used by existing callers.
    """
    if reviewer_adapters is not None:
        injected = list(reviewer_adapters)
        if not injected:
            raise ValueError("reviewer_adapters must contain at least one reviewer")
        return injected

    legacy_spec = ReviewerSpec(
        reviewer_id="independent-reviewer-0",
        runtime=reviewer_output_mode,
        model=reviewer_model,
        provider_family=_provider_family(reviewer_output_mode, reviewer_model),
        lineage=_lineage(reviewer_output_mode, reviewer_model),
        tool_access=_tool_access(reviewer_output_mode),
        assurance_grade=_assurance_grade_for_runtime(reviewer_output_mode),
    )
    codex_spec = ReviewerSpec(
        reviewer_id="independent-reviewer-1",
        runtime="codex_cli",
        model=codex_model,
        provider_family="openai",
        lineage=("openai", "codex_cli", codex_model),
        tool_access="codebase_tools",
        assurance_grade="agentic",
    )
    reviewers: list[ReviewerAdapter] = [
        (
            StructuredReviewerAdapter(
                spec=legacy_spec,
                model_client=model_client,
            )
            if model_client is not None
            and reviewer_output_mode == "litellm_structured"
            else CursorCompatibleReviewer(spec=legacy_spec, runner=runner)
        ),
        (
            RuntimeReviewerAdapter(
                spec=replace(
                    codex_spec,
                    runtime="codex",
                    lineage=("openai", "codex", codex_model),
                ),
                runner=runtime_runner,
                inherit_environment=False,
            )
            if runtime_runner is not None
            else CodexCliReviewer(
                spec=codex_spec,
                **({"runner": codex_runner} if codex_runner is not None else {}),
            )
        ),
    ]
    if litellm_model:
        litellm_family = (
            litellm_provider_family
            or _provider_family("litellm_structured", litellm_model)
        )
        litellm_spec = ReviewerSpec(
            reviewer_id="independent-reviewer-litellm",
            runtime="litellm_structured",
            model=litellm_model,
            provider_family=litellm_family,
            lineage=tuple(
                dict.fromkeys(
                    value
                    for value in (litellm_family, "litellm_structured", litellm_model)
                    if value and value != "unknown"
                )
            ),
            tool_access="text_only",
            assurance_grade="text_only",
        )
        if model_client is not None:
            reviewers.append(
                StructuredReviewerAdapter(
                    spec=litellm_spec,
                    model_client=model_client,
                )
            )
        else:
            reviewers.append(
                LiteLLMReviewer(
                    spec=litellm_spec,
                    runner=litellm_runner or runner,
                    openai_api_key=litellm_openai_api_key,
                    openai_base_url=litellm_openai_base_url,
                )
            )
    return reviewers


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
    summary = (
        str(outcome_payload.get("summary") or "")
        if isinstance(outcome_payload, dict)
        else ""
    )
    confidence_rationale = (
        str(outcome_payload.get("confidence_rationale") or "")
        if isinstance(outcome_payload, dict)
        else ""
    )
    runtime = result.reviewer_runtime or result.reviewer_output_mode or "unknown"
    model = result.model
    requested_model = (
        result.diagnostics.get("requested_model")
        if isinstance(result.diagnostics, dict)
        else None
    )
    provider_family, provider_family_verified, provider_family_source = (
        provider_family_verification_for_reviewer(runtime, model)
    )
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
        "summary": summary,
        "confidence_rationale": confidence_rationale,
        "runtime": runtime,
        "reviewer_runtime": result.reviewer_runtime,
        "reviewer_output_mode": result.reviewer_output_mode,
        "model": model,
        "requested_model": requested_model,
        "provider_family": provider_family,
        "provider_family_verified": provider_family_verified,
        "provider_family_source": provider_family_source,
        "lineage": list(_lineage(runtime, model)),
        "tool_access": _tool_access(runtime),
        "tool_backed_command_evidence": _has_tool_command_evidence(result),
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
        "tests": list(outcome_payload.get("tests") or []) if isinstance(outcome_payload, dict) else [],
        "failure_classification": result.failure_classification,
        "failure_details": redact(
            result.probe.details if isinstance(result.probe.details, dict) else {}
        ),
        "diagnostics_failure": redact(
            result.diagnostics.get("failure")
            if isinstance(result.diagnostics, dict)
            and isinstance(result.diagnostics.get("failure"), dict)
            else {}
        ),
        "recoverable": result.recoverable,
        "attempts": result.attempts,
        "worktree_isolation": (
            result.diagnostics.get("worktree_isolation")
            if isinstance(result.diagnostics, dict)
            else None
        ),
    }


def independent_reviewer_results_from_review_results(
    review_results: list[tuple[ReviewerSpec, CursorInvocationResult]]
    | tuple[tuple[ReviewerSpec, CursorInvocationResult], ...],
    *,
    task_id: str,
    gate: str,
    round_index: int,
) -> list[dict[str, Any]]:
    return [
        _result_with_spec_provenance(
            independent_reviewer_result_from_cursor_result(
                result,
                task_id=task_id,
                gate=gate,
                round_index=round_index,
                reviewer_id=spec.reviewer_id,
            ),
            spec,
        )
        for spec, result in review_results
    ]


def _result_with_spec_provenance(
    result: dict[str, Any],
    spec: ReviewerSpec,
) -> dict[str, Any]:
    payload = dict(result)
    result_runtime = str(payload.get("runtime") or "").strip()
    runtime_matches_spec = (
        not spec.runtime
        or result_runtime in {"", "unknown"}
        or result_runtime == spec.runtime
    )
    if result_runtime in {"", "unknown"} and spec.runtime:
        payload["runtime"] = spec.runtime
    result_reviewer_runtime = str(payload.get("reviewer_runtime") or "").strip()
    if result_reviewer_runtime in {"", "unknown"} and spec.runtime:
        payload["reviewer_runtime"] = spec.runtime
    served_model = str(payload.get("model") or "").strip()
    if (
        spec.model
        and runtime_matches_spec
        and served_model in {"", "unknown"}
        and not payload.get("requested_model")
    ):
        payload["requested_model"] = spec.model
    verified_family, verified, source = provider_family_verification_for_reviewer(
        payload.get("runtime"),
        served_model,
    )
    result_family = str(payload.get("provider_family") or "").strip()
    result_family_unproven = result_family in {"", "unknown", "openai_compatible"}
    if verified:
        payload["provider_family"] = verified_family
        payload["provider_family_verified"] = True
        payload["provider_family_source"] = source
        result_family_unproven = False
    elif (
        runtime_matches_spec
        and result_family_unproven
        and spec.provider_family
        and spec.provider_family not in {"unknown", "openai_compatible"}
    ):
        payload["provider_family"] = spec.provider_family
        payload["provider_family_verified"] = False
        payload["provider_family_source"] = "operator_config"
    else:
        if not payload.get("provider_family"):
            payload["provider_family"] = verified_family
        payload["provider_family_verified"] = bool(payload.get("provider_family_verified"))
        payload["provider_family_source"] = str(
            payload.get("provider_family_source") or source
        )
    final_family = str(payload.get("provider_family") or "").strip()
    final_family_unproven = final_family in {"", "unknown", "openai_compatible"}
    if (
        runtime_matches_spec
        and spec.lineage
        and (
            payload.get("provider_family_source") == "operator_config"
            or final_family_unproven
            or not payload.get("lineage")
        )
    ):
        payload["lineage"] = list(spec.lineage)
    result_tool_access = str(payload.get("tool_access") or "").strip()
    if (
        runtime_matches_spec
        and result_tool_access in {"", "unknown"}
        and spec.tool_access
        and spec.tool_access != "unknown"
    ):
        payload["tool_access"] = spec.tool_access
    result_assurance = str(payload.get("assurance_grade") or "").strip()
    if (
        runtime_matches_spec
        and result_assurance in {"", "self_reported"}
        and spec.assurance_grade
        and spec.assurance_grade != "self_reported"
    ):
        payload["assurance_grade"] = spec.assurance_grade
    return payload


def evaluate_reviewer_panel(
    results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    low_confidence_threshold: float = 0.0,
    calibration: dict[str, Any] | None = None,
    aggregation_mode: str = "conservative",
) -> dict[str, Any]:
    """Aggregate independent reviewers without weakening hard blocks."""
    threshold = _clamp_confidence(low_confidence_threshold)
    requested_aggregation_mode = str(aggregation_mode or "conservative").strip().lower()
    if requested_aggregation_mode not in {"conservative", "geometric_median"}:
        requested_aggregation_mode = "conservative"
    active_calibration = _normalise_panel_calibration(calibration)
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
    robust_aggregation: dict[str, Any] | None = None
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
    elif requested_aggregation_mode == "geometric_median":
        robust_aggregation = _majority_accept_summary(reviewer_inputs)
        if bool(robust_aggregation.get("accept")):
            decision = "accept"
            reason = "robust_geometric_median_accept"
        else:
            decision = "revise"
            reason = "robust_geometric_median_reject"
    calibrated_accept: dict[str, Any] | None = None
    if active_calibration is not None and not _calibration_covers_reviewer_inputs(
        active_calibration,
        reviewer_inputs,
    ):
        active_calibration = None
    if decision == "accept" and active_calibration is not None and reviewer_inputs:
        calibrated_accept = _calibrated_accept_summary(
            reviewer_inputs,
            calibration=active_calibration,
        )
        if calibrated_accept["aggregate_confidence"] < calibrated_accept["accept_confidence_threshold"]:
            decision = "escalate"
            reason = "calibrated_dependency_accept"

    payload = {
        "schema_version": "independent-reviewer-panel-decision/v1",
        "decision": decision,
        "reason": reason,
        "aggregation_mode": (
            "calibrated_weighted"
            if active_calibration is not None
            else requested_aggregation_mode
        ),
        "low_confidence_threshold": threshold,
        "available_reviewers": available_reviewers,
        "accepted_reviewers": accepted_reviewers,
        "blocking_reviewers": blocking_reviewers,
        "non_accepting_reviewers": non_accepting_reviewers,
        "missing_reviewers": missing_reviewers,
        "low_confidence_reviewers": low_confidence_reviewers,
        "reviewer_inputs": reviewer_inputs,
    }
    if active_calibration is not None:
        payload["calibration"] = _calibration_decision_summary(active_calibration)
    if calibrated_accept is not None:
        payload["calibrated_accept"] = calibrated_accept
    if robust_aggregation is not None:
        payload["robust_aggregation"] = robust_aggregation
    return payload


def load_reviewer_panel_calibration(path: str | Path | None) -> dict[str, Any] | None:
    if path is None or not str(path).strip():
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    payload["artifact_path"] = str(candidate)
    return _normalise_panel_calibration(payload)


def _calibration_covers_reviewer_inputs(
    calibration: dict[str, Any],
    reviewer_inputs: list[dict[str, Any]],
) -> bool:
    weights = calibration.get("reviewer_weights")
    if not isinstance(weights, dict):
        return False
    reviewer_ids = {
        str(item.get("reviewer_id") or "").strip()
        for item in reviewer_inputs
        if str(item.get("reviewer_id") or "").strip()
    }
    calibrated_ids = {
        str(reviewer_id or "").strip()
        for reviewer_id in weights
        if str(reviewer_id or "").strip()
    }
    if reviewer_ids != calibrated_ids:
        return False
    expected_roster_sha = str(calibration.get("reviewer_roster_sha256") or "").strip()
    if not _looks_like_sha256(expected_roster_sha):
        return False
    return _active_reviewer_roster_sha256(reviewer_inputs) == expected_roster_sha


def adjudicate_reviewer_panel(
    results: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    cwd: str | Path,
    max_evidence_refs: int = 5,
) -> dict[str, Any] | None:
    """Build a bounded adjudication packet for split or strong-objection panels.

    This is deliberately not a weighting function. It only preserves and checks
    the strongest objection so downstream gate logic can block or escalate
    without majority voting.
    """
    reviewer_inputs = [result for result in results if isinstance(result, dict)]
    if not reviewer_inputs:
        return None

    available_decisions = {
        _normalise_decision(result)
        for result in reviewer_inputs
        if bool(result.get("verdict_present")) and _normalise_decision(result) in {"accept", "revise", "deny"}
    }
    has_accept = "accept" in available_decisions
    has_non_accept = bool(available_decisions & {"revise", "deny"})
    disagreement = has_accept and has_non_accept
    strong_candidates = [
        result
        for result in reviewer_inputs
        if _strongest_objection_text(result)
        and _severity_rank(_result_severity(result)) >= _severity_rank("important")
        and bool(result.get("verdict_present"))
    ]
    if not disagreement and not strong_candidates:
        return None

    trigger = "disagreement" if disagreement else "strong_minority_objection"
    strongest = _select_strongest_objection(
        [
            result for result in reviewer_inputs
            if _normalise_decision(result) in {"revise", "deny"}
        ]
        if disagreement else strong_candidates
    )
    if strongest is None:
        strongest = _select_strongest_objection(strong_candidates or reviewer_inputs)
    if strongest is None:
        return None

    strongest_decision = _normalise_decision(strongest)
    strongest_severity = _result_severity(strongest)
    decision = (
        "block"
        if strongest_decision in {"revise", "deny"}
        and _severity_rank(strongest_severity) >= _severity_rank("important")
        else "escalate"
    )
    evidence_refs = _result_evidence_refs(strongest)
    tests = _text_list(strongest.get("tests"))
    evidence_checks = _check_evidence_refs(
        cwd=Path(cwd),
        refs=[*evidence_refs, *tests],
        max_evidence_refs=max_evidence_refs,
    )
    return {
        "schema_version": "independent-reviewer-adjudication/v1",
        "trigger": trigger,
        "decision": decision,
        "reason": (
            "real_reviewer_objection"
            if decision == "block"
            else "strong_accept_objection"
        ),
        "majority_vote_used": False,
        "bounded": True,
        "max_evidence_refs": max_evidence_refs,
        "reviewer_count": len(reviewer_inputs),
        "available_decisions": sorted(available_decisions),
        "strongest_objection": {
            "reviewer_id": str(strongest.get("reviewer_id") or "unknown-reviewer"),
            "decision": strongest_decision,
            "severity": strongest_severity,
            "confidence": _coerce_confidence(strongest.get("confidence")),
            "text": _strongest_objection_text(strongest),
            "evidence_refs": evidence_refs,
            "tests": tests,
            "transcript_refs": list(strongest.get("transcript_refs") or []),
            "transcript_sha256": strongest.get("transcript_sha256"),
            "output_sha256": strongest.get("output_sha256"),
            "runtime": strongest.get("runtime") or strongest.get("reviewer_runtime"),
            "model": strongest.get("model"),
            "provider_family": strongest.get("provider_family"),
            "lineage": list(strongest.get("lineage") or []),
            "tool_access": strongest.get("tool_access"),
            "assurance_grade": strongest.get("assurance_grade"),
        },
        "evidence_checks": evidence_checks,
    }


def _normalise_panel_calibration(calibration: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(calibration, dict):
        return None
    schema = str(calibration.get("schema_version") or "")
    if schema != "reviewer-panel-calibration/v1":
        return None
    if not _looks_like_sha256(calibration.get("source_report_sha256")):
        return None
    if not _looks_like_sha256(calibration.get("labeled_set_sha256")):
        return None
    if not _looks_like_sha256(calibration.get("calibration_sha256")):
        return None
    if calibration.get("calibration_sha256") != _calibration_sha256(calibration):
        return None
    pairwise_dependency = calibration.get("pairwise_dependency")
    if not isinstance(pairwise_dependency, dict) or not pairwise_dependency:
        return None
    if not _calibration_has_auditable_reviewer_provenance(
        calibration.get("source_provenance")
    ):
        return None
    if not _calibration_rows_match_declared_roster(
        calibration.get("source_provenance")
    ):
        return None
    weights = calibration.get("reviewer_weights")
    if not isinstance(weights, dict) or not weights:
        return None
    normalised_weights: dict[str, dict[str, Any]] = {}
    for reviewer_id, payload in weights.items():
        if not isinstance(payload, dict):
            continue
        clean_id = str(payload.get("reviewer_id") or reviewer_id or "").strip()
        if not clean_id:
            continue
        derived_from_pairwise = payload.get("derived_from_pairwise")
        if not isinstance(derived_from_pairwise, list) or not derived_from_pairwise:
            continue
        pair_ids = {
            str(source.get("pair_id") or "")
            for source in derived_from_pairwise
            if isinstance(source, dict)
        }
        if not pair_ids or not any(pair_id in pairwise_dependency for pair_id in pair_ids):
            continue
        weight = _coerce_confidence(payload.get("weight"))
        dependency_score = _coerce_confidence(payload.get("dependency_score"))
        if weight is None or dependency_score is None:
            continue
        expected_dependency = _derived_dependency_from_pairwise(
            derived_from_pairwise,
            pairwise_dependency=pairwise_dependency,
        )
        if expected_dependency is None:
            continue
        if not _close_confidence(dependency_score, expected_dependency):
            continue
        expected_weight = _expected_reviewer_weight(expected_dependency)
        if not _close_confidence(weight, expected_weight):
            continue
        normalised_weights[clean_id] = {
            **payload,
            "reviewer_id": clean_id,
            "weight": expected_weight,
            "dependency_score": expected_dependency,
        }
    if not normalised_weights:
        return None
    threshold = _coerce_confidence(calibration.get("accept_confidence_threshold"))
    return {
        **calibration,
        "reviewer_weights": normalised_weights,
        "accept_confidence_threshold": 0.7 if threshold is None else threshold,
    }


def _calibration_has_auditable_reviewer_provenance(provenance: Any) -> bool:
    if not isinstance(provenance, dict):
        return False
    real_count = _coerce_nonnegative_int(provenance.get("real_reviewer_output_count"))
    auditable_count = _coerce_nonnegative_int(
        provenance.get("auditable_real_output_count")
    )
    fixture_count = _coerce_nonnegative_int(provenance.get("fixture_row_count"))
    transcript_ref_count = _coerce_nonnegative_int(
        provenance.get("transcript_ref_count")
    )
    source_trace_paths = provenance.get("source_trace_paths")
    source_event_ids = provenance.get("source_event_ids")
    return bool(
        real_count is not None
        and auditable_count is not None
        and fixture_count == 0
        and real_count > 0
        and auditable_count == real_count
        and transcript_ref_count is not None
        and transcript_ref_count >= real_count
        and isinstance(source_trace_paths, list)
        and source_trace_paths
        and isinstance(source_event_ids, list)
        and source_event_ids
    )


def _calibration_rows_match_declared_roster(provenance: Any) -> bool:
    if not isinstance(provenance, dict):
        return False
    consistency = provenance.get("row_roster_consistency")
    if not isinstance(consistency, dict):
        return False
    mismatched = _coerce_nonnegative_int(consistency.get("mismatched_row_count"))
    return bool(
        consistency.get("all_rows_match_reviewer_roster") is True
        and mismatched == 0
    )


def _calibration_sha256(calibration: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in calibration.items()
        if key != "artifact_path"
    }
    stable["calibration_sha256"] = ""
    return hashlib.sha256(
        json.dumps(
            stable,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _active_reviewer_roster_sha256(reviewer_inputs: list[dict[str, Any]]) -> str:
    roster = [
        {
            "reviewer_id": str(item.get("reviewer_id") or "").strip(),
            "runtime": str(item.get("runtime") or "unknown"),
            "model": item.get("model"),
            "provider_family": str(item.get("provider_family") or "unknown"),
            "lineage": list(item.get("lineage") or []),
            "tool_access": str(item.get("tool_access") or "unknown"),
            "assurance_grade": str(item.get("assurance_grade") or "self_reported"),
        }
        for item in reviewer_inputs
        if str(item.get("reviewer_id") or "").strip()
    ]
    return _sha256_json(sorted(roster, key=lambda item: item["reviewer_id"]))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _looks_like_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def _coerce_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _derived_dependency_from_pairwise(
    derived_from_pairwise: list[Any],
    *,
    pairwise_dependency: dict[str, Any],
) -> float | None:
    scores: list[float] = []
    for source in derived_from_pairwise:
        if not isinstance(source, dict):
            return None
        pair_id = str(source.get("pair_id") or "")
        pair_payload = pairwise_dependency.get(pair_id)
        if not isinstance(pair_payload, dict):
            return None
        pair_score = _coerce_confidence(pair_payload.get("dependency_score"))
        source_score = _coerce_confidence(source.get("dependency_score"))
        if pair_score is None or source_score is None:
            return None
        if not _close_confidence(pair_score, source_score):
            return None
        scores.append(pair_score)
    if not scores:
        return None
    return round(max(scores), 6)


def _expected_reviewer_weight(dependency_score: float) -> float:
    return round(max(0.05, 1.0 - _clamp_confidence(dependency_score)), 6)


def _close_confidence(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) <= 0.000001


def _calibrated_accept_summary(
    reviewer_inputs: list[dict[str, Any]],
    *,
    calibration: dict[str, Any],
) -> dict[str, Any]:
    weights = calibration["reviewer_weights"]
    threshold = float(calibration["accept_confidence_threshold"])
    weighted_inputs: list[dict[str, Any]] = []
    weighted_confidence_sum = 0.0
    for item in reviewer_inputs:
        reviewer_id = item["reviewer_id"]
        weight_payload = weights.get(reviewer_id, {})
        weight = _clamp_confidence(float(weight_payload.get("weight", 1.0)))
        confidence = item["confidence"] if item["confidence"] is not None else 1.0
        severity = str(item.get("severity") or "none").strip().lower()
        severity_multiplier = _accept_severity_multiplier(severity)
        contribution = weight * _clamp_confidence(float(confidence)) * severity_multiplier
        weighted_confidence_sum += contribution
        weighted_inputs.append({
            "reviewer_id": reviewer_id,
            "confidence": _clamp_confidence(float(confidence)),
            "severity": severity,
            "severity_multiplier": severity_multiplier,
            "weight": weight,
            "weighted_confidence": round(contribution, 6),
            "dependency_score": _clamp_confidence(float(weight_payload.get("dependency_score", 0.0))),
            "provider_family": item.get("provider_family"),
        })
    denominator = max(1, len(reviewer_inputs))
    aggregate = weighted_confidence_sum / denominator
    return {
        "schema_version": "independent-reviewer-calibrated-accept/v1",
        "aggregate_confidence": round(aggregate, 6),
        "accept_confidence_threshold": threshold,
        "decision": "accept" if aggregate >= threshold else "escalate",
        "weighted_inputs": weighted_inputs,
        "source_calibration_sha256": calibration.get("calibration_sha256"),
        "source_report_sha256": calibration.get("source_report_sha256"),
    }


def _accept_severity_multiplier(severity: str) -> float:
    rank = _severity_rank(severity)
    if rank >= _severity_rank("critical"):
        return 0.35
    if rank >= _severity_rank("important"):
        return 0.65
    if rank >= _severity_rank("medium"):
        return 0.85
    if rank >= _severity_rank("low"):
        return 0.95
    return 1.0


def _calibration_decision_summary(calibration: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": calibration.get("schema_version"),
        "active": True,
        "artifact_path": calibration.get("artifact_path"),
        "calibration_sha256": calibration.get("calibration_sha256"),
        "source_report_sha256": calibration.get("source_report_sha256"),
        "labeled_set_sha256": calibration.get("labeled_set_sha256"),
        "reviewer_roster_sha256": calibration.get("reviewer_roster_sha256"),
        "source_provenance": calibration.get("source_provenance"),
        "reviewer_weights": {
            reviewer_id: {
                "weight": payload.get("weight"),
                "dependency_score": payload.get("dependency_score"),
                "provider_family": payload.get("provider_family"),
                "runtime": payload.get("runtime"),
                "model": payload.get("model"),
            }
            for reviewer_id, payload in sorted(calibration["reviewer_weights"].items())
        },
        "accept_confidence_threshold": calibration.get("accept_confidence_threshold"),
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
        "provider_family_verified": bool(result.get("provider_family_verified")),
        "provider_family_source": result.get("provider_family_source"),
        "lineage": list(result.get("lineage") or []),
        "tool_access": result.get("tool_access"),
        "assurance_grade": result.get("assurance_grade"),
    }


def _select_strongest_objection(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None
    return sorted(
        results,
        key=lambda result: (
            _severity_rank(_result_severity(result)),
            1 if _normalise_decision(result) in {"revise", "deny"} else 0,
            _coerce_confidence(result.get("confidence")) or 0.0,
            str(result.get("reviewer_id") or ""),
        ),
        reverse=True,
    )[0]


def _normalise_decision(result: dict[str, Any]) -> str:
    return str(result.get("decision") or "").strip().lower()


def _result_severity(result: dict[str, Any]) -> str:
    critical = result.get("critical_review") if isinstance(result.get("critical_review"), dict) else {}
    return str(critical.get("severity") or result.get("severity") or "none").strip().lower()


def _severity_rank(severity: str) -> int:
    return {
        "none": 0,
        "low": 1,
        "minor": 1,
        "medium": 2,
        "moderate": 2,
        "important": 3,
        "critical": 4,
    }.get(str(severity or "").strip().lower(), 0)


def _strongest_objection_text(result: dict[str, Any]) -> str:
    critical = result.get("critical_review") if isinstance(result.get("critical_review"), dict) else {}
    text = str(critical.get("strongest_objection") or "").strip()
    if text and text.lower() not in {"none", "n/a", "na", "no objection"}:
        return text
    objections = result.get("objections")
    if isinstance(objections, list):
        return next((str(item).strip() for item in objections if str(item).strip()), "")
    return ""


def _result_evidence_refs(result: dict[str, Any]) -> list[Any]:
    critical = result.get("critical_review") if isinstance(result.get("critical_review"), dict) else {}
    refs = critical.get("evidence_refs")
    if not isinstance(refs, list):
        refs = result.get("evidence_refs")
    return list(refs or []) if isinstance(refs, list) else []


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _check_evidence_refs(
    *,
    cwd: Path,
    refs: list[Any],
    max_evidence_refs: int,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    root = cwd.expanduser().resolve()
    for raw_ref in refs[:max(0, int(max_evidence_refs))]:
        ref, expected_sha256 = _normalise_evidence_ref(raw_ref)
        if not ref:
            continue
        check: dict[str, Any] = {"ref": ref}
        if _is_external_ref(ref):
            check["status"] = "skipped_external"
            checks.append(check)
            continue
        candidate = Path(ref).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            check["status"] = "skipped_unbounded"
            checks.append(check)
            continue
        if not resolved.exists() or not resolved.is_file():
            check["status"] = "missing"
            checks.append(check)
            continue
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        check["sha256"] = digest
        if expected_sha256 and expected_sha256 != digest:
            check["status"] = "hash_mismatch"
            check["expected_sha256"] = expected_sha256
        else:
            check["status"] = "verified"
        checks.append(check)
    if len(refs) > max_evidence_refs:
        checks.append({
            "status": "truncated",
            "skipped_count": len(refs) - max_evidence_refs,
            "max_evidence_refs": max_evidence_refs,
        })
    return checks


def _normalise_evidence_ref(value: Any) -> tuple[str, str | None]:
    if isinstance(value, dict):
        ref = str(value.get("path") or value.get("ref") or "").strip()
        expected = value.get("sha256") or value.get("hash") or value.get("expected_sha256")
        expected_text = str(expected).removeprefix("sha256:") if expected else None
        return ref, expected_text
    return str(value or "").strip(), None


def _is_external_ref(ref: str) -> bool:
    text = ref.strip().lower()
    return (
        "://" in text
        or text.startswith("event:")
        or text.startswith("independent_reviewer_review:")
        or text.startswith("tri_agent_cursor_review:")
    )


def _coerce_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return _clamp_confidence(float(value))
    except (TypeError, ValueError):
        return None


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _majority_accept_summary(
    reviewer_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    scored_inputs = [
        item
        for item in reviewer_inputs
        if item["verdict_present"] and item["decision"] in {"accept", "revise", "deny"}
    ]
    score_points = [
        1.0 if item["decision"] == "accept" else 0.0
        for item in scored_inputs
    ]
    if not score_points:
        return {
            "status": "unavailable",
            "accept": False,
            "score_points": [],
            "geometric_median_score": None,
            "accept_threshold": 0.5,
            "contaminated_judge_bound": 0,
        }
    ordered = sorted(score_points)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median_score = ordered[midpoint]
    else:
        median_score = (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    return {
        "status": "computed",
        "accept": median_score > 0.5,
        "score_points": score_points,
        "geometric_median_score": median_score,
        "accept_threshold": 0.5,
        "contaminated_judge_bound": max(0, (len(score_points) - 1) // 2),
    }


def _decision_from_result(result: CursorInvocationResult) -> str:
    if result.outcome is not None:
        for decision in result.outcome.decisions:
            if decision in {"accept", "revise", "deny"}:
                return decision
    return "accept" if cursor_accepts(result) else "revise"


def _provider_family(runtime: Any, model: Any) -> str:
    runtime_text = str(runtime or "").lower()
    model_text = str(model or "").lower()
    if "codex" in runtime_text:
        return "openai"
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
    if "openai" in model_text:
        return "openai"
    return "unknown"


provider_family_for_reviewer = _provider_family


def _provider_family_from_served_model(model: Any) -> str:
    model_text = str(model or "").strip().lower()
    if model_text in {"", "default", "proxy-default", "auto"}:
        return "unknown"
    if "gemini" in model_text or model_text.startswith("google/"):
        return "google"
    if "claude" in model_text or model_text.startswith("anthropic/"):
        return "anthropic"
    if (
        "gpt" in model_text
        or model_text.startswith(("o1", "o3", "o4", "openai/"))
        or "openai" in model_text
    ):
        return "openai"
    if "llama" in model_text or model_text.startswith("meta/"):
        return "meta"
    if "mistral" in model_text or "mixtral" in model_text:
        return "mistral"
    if "deepseek" in model_text:
        return "deepseek"
    if "grok" in model_text or model_text.startswith("xai/"):
        return "xai"
    if "command-r" in model_text or model_text.startswith("cohere/"):
        return "cohere"
    return "unknown"


def provider_family_verification_for_reviewer(
    runtime: Any,
    model: Any,
) -> tuple[str, bool, str]:
    served_family = _provider_family_from_served_model(model)
    if served_family not in {"", "unknown", "openai_compatible"}:
        return served_family, False, "served_model_name_inference"
    inferred = _provider_family(runtime, model)
    return inferred, False, "runtime_inference"


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
    if "codex_cli" in runtime_text:
        return "codebase_tools"
    if "cursor" in runtime_text:
        return "codebase_tools"
    if "litellm" in runtime_text:
        return "text_only"
    return "unknown"


def _assurance_grade(result: CursorInvocationResult) -> str:
    assurance = str(result.reviewer_assurance or "").lower()
    runtime = str(result.reviewer_runtime or result.reviewer_output_mode or "").lower()
    if "litellm" in runtime and not _has_tool_command_evidence(result):
        return "text_only"
    if "codex_cli" in runtime:
        if "tool" in assurance or _has_codex_cli_command_evidence(result):
            return "agentic"
        if "text" in assurance:
            return "text_only"
        return "self_reported"
    if "tool" in assurance or "cursor" in runtime:
        return "agentic"
    if "text" in assurance or "litellm" in runtime:
        return "text_only"
    return "self_reported"


def _has_tool_command_evidence(result: CursorInvocationResult) -> bool:
    diagnostics = result.diagnostics if isinstance(result.diagnostics, dict) else {}
    for key in ("codex_cli", "cursor_sdk", "tool_use", "commands"):
        payload = diagnostics.get(key)
        if not isinstance(payload, dict):
            continue
        count = payload.get("command_execution_count")
        if isinstance(count, int) and count > 0:
            return True
        executions = payload.get("command_executions")
        if isinstance(executions, list) and executions:
            return True
    return False


def _has_codex_cli_command_evidence(result: CursorInvocationResult) -> bool:
    diagnostics = result.diagnostics if isinstance(result.diagnostics, dict) else {}
    codex_diagnostics = diagnostics.get("codex_cli")
    if not isinstance(codex_diagnostics, dict):
        return False
    count = codex_diagnostics.get("command_execution_count")
    if isinstance(count, int) and count > 0:
        return True
    executions = codex_diagnostics.get("command_executions")
    return isinstance(executions, list) and len(executions) > 0


def _assurance_grade_for_runtime(runtime: str | None) -> str:
    runtime_text = str(runtime or "").lower()
    if "cursor" in runtime_text or "codex_cli" in runtime_text:
        return "agentic"
    if "litellm" in runtime_text:
        return "text_only"
    return "self_reported"
