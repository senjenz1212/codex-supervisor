"""Independent reviewer registry and panel result helpers."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .cursor_agent import (
    DEFAULT_STRUCTURED_REVIEWER_MAX_TOKENS,
    CursorInvocationRequest,
    CursorInvocationResult,
    cursor_accepts,
    invoke_cursor_agent,
    _evaluate_cursor_contract_completeness,
    _outcome_block_contract,
)
from .dual_agent import ProbeResult, evaluate_outcome_fidelity
from .agent_mailbox import critical_review_prompt

CodexRunner = Callable[..., subprocess.CompletedProcess[str]]


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


@dataclass(frozen=True)
class CodexCliReviewer:
    spec: ReviewerSpec
    runner: CodexRunner = subprocess.run
    reasoning_effort: str = "xhigh"
    command: str = "codex"

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        started = time.monotonic()
        model = self.spec.model or "gpt-5.5"
        prompt = _codex_cli_reviewer_prompt(request, reviewer_name=self.spec.reviewer_id)
        argv = [
            self.command,
            "exec",
            "--json",
            "-C",
            str(Path(request.cwd).expanduser()),
            "-m",
            model,
            "-c",
            f'reasoning_effort="{self.reasoning_effort}"',
            "--sandbox",
            "read-only",
            prompt,
        ]
        try:
            completed = self.runner(
                argv,
                cwd=str(Path(request.cwd).expanduser()),
                capture_output=True,
                text=True,
                timeout=request.timeout_s,
            )
        except Exception as exc:  # pragma: no cover - concrete subprocess errors vary.
            return _codex_cli_infrastructure_result(
                reason="codex_cli_invocation_failed",
                model=model,
                transcript="",
                duration_ms=_duration_ms(started),
                details={"error": str(exc)},
            )

        raw_stdout = completed.stdout or ""
        raw_stderr = completed.stderr or ""
        metadata = _parse_codex_cli_jsonl(raw_stdout)
        transcript = "\n\n".join(
            item
            for item in (
                raw_stdout,
                f"[stderr]\n{raw_stderr}" if raw_stderr else "",
                "[agent_messages]\n" + "\n".join(metadata["agent_messages"])
                if metadata["agent_messages"] else "",
            )
            if item
        )
        if completed.returncode != 0:
            return _codex_cli_infrastructure_result(
                reason="codex_cli_nonzero_exit",
                model=model,
                transcript=transcript,
                duration_ms=_duration_ms(started),
                details={
                    "returncode": completed.returncode,
                    "stderr_tail": raw_stderr[-2000:],
                },
            )

        probe, outcome = evaluate_outcome_fidelity(
            "\n".join(metadata["agent_messages"]),
            expected_specialists=(self.spec.reviewer_id,),
            expected_decisions=request.expected_decisions,
            expected_objections=request.expected_objections,
        )
        if probe.ok and outcome is not None:
            probe = _evaluate_cursor_contract_completeness(outcome)
        if not probe.ok:
            return CursorInvocationResult(
                probe=ProbeResult(
                    "CODEX_REVIEWER",
                    "red",
                    "reviewer_contract_unmet",
                    {
                        "original_reason": probe.reason,
                        "details": probe.details,
                        "recoverable": True,
                    },
                ),
                outcome=None,
                transcript=transcript,
                agent_id=metadata.get("thread_id"),
                run_id=metadata.get("thread_id"),
                status="finished",
                model=model,
                reviewer_runtime="codex_cli",
                reviewer_output_mode="codex_cli",
                duration_ms=_duration_ms(started),
                reviewer_assurance="tool_backed_primary" if metadata["command_executions"] else "self_reported",
                diagnostics={
                    "codex_cli": {
                        "thread_id": metadata.get("thread_id"),
                        "command_executions": metadata["command_executions"],
                        "command_execution_count": len(metadata["command_executions"]),
                        "stdout_sha256": hashlib.sha256(raw_stdout.encode("utf-8")).hexdigest(),
                        "stderr_sha256": hashlib.sha256(raw_stderr.encode("utf-8")).hexdigest(),
                    }
                },
                failure_classification="reviewer_contract_unmet",
                recoverable=True,
                attempts=1,
                retry_reasons=(probe.reason,),
            )

        return CursorInvocationResult(
            probe=ProbeResult("CODEX_REVIEWER", "green", "codex_cli_review_ok", probe.details),
            outcome=outcome,
            transcript=transcript,
            agent_id=metadata.get("thread_id"),
            run_id=metadata.get("thread_id"),
            status="finished",
            model=model,
            reviewer_runtime="codex_cli",
            reviewer_output_mode="codex_cli",
            duration_ms=_duration_ms(started),
            reviewer_assurance="tool_backed_primary" if metadata["command_executions"] else "self_reported",
            diagnostics={
                "codex_cli": {
                    "thread_id": metadata.get("thread_id"),
                    "command_executions": metadata["command_executions"],
                    "command_execution_count": len(metadata["command_executions"]),
                    "stdout_sha256": hashlib.sha256(raw_stdout.encode("utf-8")).hexdigest(),
                    "stderr_sha256": hashlib.sha256(raw_stderr.encode("utf-8")).hexdigest(),
                }
            },
            attempts=1,
        )


def configured_reviewers(
    *,
    reviewer_output_mode: str,
    reviewer_model: str | None,
    runner: Callable[[CursorInvocationRequest], CursorInvocationResult] = invoke_cursor_agent,
    codex_runner: CodexRunner = subprocess.run,
    codex_model: str = "gpt-5.5",
) -> list[ReviewerAdapter]:
    """Return the configured reviewer roster for a gate.

    This is intentionally small: reviewer 0 is the legacy Cursor-compatible
    slot, reviewer 1 is the GPT-family Codex CLI route proven by route
    evidence. Wider plugin mechanics are deliberately out of scope.
    """
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
    return [
        CursorCompatibleReviewer(spec=legacy_spec, runner=runner),
        CodexCliReviewer(spec=codex_spec, runner=codex_runner),
    ]


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
        "tests": list(outcome_payload.get("tests") or []) if isinstance(outcome_payload, dict) else [],
        "failure_classification": result.failure_classification,
        "recoverable": result.recoverable,
        "attempts": result.attempts,
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
        independent_reviewer_result_from_cursor_result(
            result,
            task_id=task_id,
            gate=gate,
            round_index=round_index,
            reviewer_id=spec.reviewer_id,
        )
        for spec, result in review_results
    ]


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


def _decision_from_result(result: CursorInvocationResult) -> str:
    if result.outcome is not None:
        for decision in result.outcome.decisions:
            if decision in {"accept", "revise", "deny"}:
                return decision
    return "accept" if cursor_accepts(result) else "revise"


def _provider_family(runtime: str | None, model: str | None) -> str:
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


def _codex_cli_reviewer_prompt(
    request: CursorInvocationRequest,
    *,
    reviewer_name: str,
) -> str:
    artifact_lines = [
        f"- {artifact.kind}: {Path(artifact.path)}"
        for artifact in request.planning_artifacts
    ]
    receipt_lines = [
        f"- {receipt.get('receipt_id') or receipt.get('id') or 'receipt'}: "
        f"{json.dumps(receipt, sort_keys=True, default=str)[:2000]}"
        for receipt in request.tool_receipts
    ]
    return "\n".join([
        f"Independent reviewer gate: {request.gate}.",
        f"Task id: {request.task_id}.",
        "",
        f"Role: {reviewer_name} is an independent GPT-family reviewer, not the implementer.",
        "Do not edit files. Use read-only codebase inspection only.",
        critical_review_prompt("Claude outcome and gate evidence"),
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
        "Claude outcome JSON:",
        json.dumps(request.claude_outcome or {}, sort_keys=True, default=str),
        "",
        f"Return a specialist named {reviewer_name}. Use decision accept only if the gate should advance.",
        _outcome_block_contract(),
    ])


def _parse_codex_cli_jsonl(stdout: str) -> dict[str, Any]:
    agent_messages: list[str] = []
    command_executions: list[dict[str, Any]] = []
    thread_id: str | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = str(event.get("thread_id") or "")
        if event_type == "session_meta" and thread_id is None:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            thread_id = str(payload.get("id") or "") or None
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = item.get("type")
        if event_type == "item.completed" and item_type == "agent_message":
            text = str(item.get("text") or "")
            if text:
                agent_messages.append(text)
        if event_type == "item.completed" and item_type == "command_execution":
            command_executions.append({
                "command": item.get("command"),
                "exit_code": item.get("exit_code"),
                "status": item.get("status"),
            })
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        payload_type = payload.get("type")
        if event_type == "event_msg" and payload_type == "agent_message":
            text = str(payload.get("message") or "")
            if text:
                agent_messages.append(text)
        if (
            event_type == "response_item"
            and payload_type == "message"
            and payload.get("role") in {None, "assistant"}
        ):
            for text in _response_item_message_texts(payload):
                agent_messages.append(text)
        if event_type == "response_item" and payload_type == "function_call":
            command_executions.append(_codex_function_call_summary(payload))
        if event_type == "response_item" and payload_type == "function_call_output":
            command_executions.append({
                "call_id": payload.get("call_id"),
                "command": "function_call_output",
                "exit_code": None,
                "status": "completed",
            })
    return {
        "thread_id": thread_id or None,
        "agent_messages": agent_messages,
        "command_executions": command_executions,
    }


def _response_item_message_texts(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text") or block.get("message")
            if text:
                texts.append(str(text))
    text = payload.get("text")
    if text:
        texts.append(str(text))
    return texts


def _codex_function_call_summary(payload: dict[str, Any]) -> dict[str, Any]:
    arguments = payload.get("arguments")
    command = payload.get("name")
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict) and parsed.get("cmd"):
            command = parsed.get("cmd")
    return {
        "call_id": payload.get("call_id"),
        "command": command,
        "exit_code": None,
        "status": "started",
    }


def _codex_cli_infrastructure_result(
    *,
    reason: str,
    model: str,
    transcript: str,
    duration_ms: int,
    details: dict[str, Any],
) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "CODEX_REVIEWER",
            "red",
            "reviewer_infrastructure_unavailable",
            {
                "original_reason": reason,
                "recoverable": True,
                **details,
            },
        ),
        outcome=None,
        transcript=transcript,
        status="failed",
        model=model,
        reviewer_runtime="codex_cli",
        reviewer_output_mode="codex_cli",
        duration_ms=duration_ms,
        reviewer_assurance="unavailable",
        diagnostics={"codex_cli": {"reason": reason, **details}},
        failure_classification="reviewer_infrastructure_unavailable",
        recoverable=True,
        attempts=1,
        retry_reasons=(reason,),
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
