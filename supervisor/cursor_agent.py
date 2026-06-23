"""Cursor SDK invocation boundary for tri-agent workflow review.

The supervisor treats Cursor as an optional independent reviewer. Cursor must
return the same typed outcome block as Claude so the existing validators remain
the acceptance boundary.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import json
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
import threading
from typing import Any, Callable, Literal
from urllib.parse import urlparse

from .dual_agent import Outcome, ProbeResult, evaluate_outcome_fidelity, outcome_accepts
from .dual_agent_lead import GateName, ModelQuality, PlanningArtifact
from .agent_mailbox import critical_review_prompt

CursorFailureClassification = Literal[
    "reviewer_contract_unmet",
    "reviewer_infrastructure_unavailable",
    "reviewer_access_denied",
]
ReviewerOutputMode = Literal["litellm_structured", "cursor_sdk"]
ReviewerWorktreeIsolation = Literal["copy", "none"]
DEFAULT_STRUCTURED_REVIEWER_MODEL = "claude-opus-4-6"
DEFAULT_STRUCTURED_REVIEWER_MAX_TOKENS = 4096
DEFAULT_CURSOR_SDK_MODEL = "default"
CURSOR_REVIEWER_WORKTREE_EXCLUDED_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".runtime-evidence",
    ".scratch",
    ".mergeability",
    "oracle_outputs.json",
    "hidden_test_commands.json",
})
CURSOR_REVIEWER_WORKTREE_EXCLUDED_MARKERS = (
    ".mergeability",
    "fail_to_pass",
    "pass_to_pass",
    "hidden_test",
    "hidden_oracle",
    "oracle_outputs",
)


@dataclass(frozen=True)
class CursorInvocationRequest:
    task_id: str
    gate: GateName
    instruction: str
    cwd: str | Path
    claude_outcome: dict[str, Any] | None = None
    planning_artifacts: tuple[PlanningArtifact, ...] = ()
    quality: ModelQuality = "best"
    model: str | None = None
    reviewer_model: str | None = None
    reviewer_output_mode: ReviewerOutputMode = "cursor_sdk"
    reviewer_max_tokens: int = DEFAULT_STRUCTURED_REVIEWER_MAX_TOKENS
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    timeout_s: int = 600
    api_key: str | None = None
    mode: str = "plan"
    tool_receipts: tuple[dict[str, Any], ...] = ()
    review_packet: dict[str, Any] | None = None
    expected_specialists: tuple[str, ...] = ("Cursor Reviewer",)
    expected_decisions: tuple[str, ...] = ()
    expected_objections: tuple[str, ...] = ()
    contract_retry_limit: int = 3
    reviewer_infra_retry_limit: int = 2
    reviewer_infra_retry_backoff_s: float = 1.0
    reviewer_worktree_isolation: ReviewerWorktreeIsolation = "copy"
    cursor_sdk_sandbox_enabled: bool = True


@dataclass(frozen=True)
class CursorInvocationResult:
    probe: ProbeResult
    outcome: Outcome | None
    transcript: str
    agent_id: str | None = None
    run_id: str | None = None
    status: str | None = None
    model: str | None = None
    reviewer_runtime: str | None = None
    reviewer_output_mode: str | None = None
    duration_ms: int | None = None
    reviewer_assurance: str | None = None
    fallback_from_runtime: str | None = None
    fallback_reason: str | None = None
    diagnostics: dict[str, Any] | None = None
    failure_classification: CursorFailureClassification | None = None
    recoverable: bool = False
    attempts: int = 1
    retry_reasons: tuple[str, ...] = ()


CursorRunner = Callable[[CursorInvocationRequest], CursorInvocationResult]
StatusRunner = Callable[..., subprocess.CompletedProcess[str]]


class CursorSdkTimeoutError(TimeoutError):
    """Raised when the supervisor-side watchdog bounds Cursor SDK execution."""


def select_cursor_model(
    *,
    quality: ModelQuality,
    explicit_model: str | None = None,
) -> str:
    if explicit_model:
        return explicit_model
    # Cursor's SDK catalog exposes an Auto route named "default". Live probes
    # on 2026-06-14 showed explicit model ids can terminally error with empty
    # output while this route remains healthy, so keep the supervisor default
    # on the catalog-backed Auto lane unless an operator pins a model.
    return DEFAULT_CURSOR_SDK_MODEL


def select_reviewer_model(
    *,
    quality: ModelQuality,
    reviewer_output_mode: ReviewerOutputMode = "litellm_structured",
    reviewer_model: str | None = None,
    cursor_model: str | None = None,
) -> str:
    if reviewer_model:
        return reviewer_model
    if reviewer_output_mode == "cursor_sdk":
        return select_cursor_model(quality=quality, explicit_model=cursor_model)
    return DEFAULT_STRUCTURED_REVIEWER_MODEL


def build_cursor_prompt(request: CursorInvocationRequest, *, compact: bool = False) -> str:
    artifact_lines = [
        f"- {artifact.kind}: {Path(artifact.path)}"
        for artifact in request.planning_artifacts
    ]
    artifacts = "\n".join(artifact_lines) if artifact_lines else "- none"
    receipt_lines = [
        f"- {receipt.get('receipt_id') or receipt.get('id') or 'receipt'}: "
        f"{_receipt_prompt_payload(receipt, compact=compact)}"
        for receipt in request.tool_receipts
    ]
    receipts = "\n".join(receipt_lines) if receipt_lines else "- none"
    review_packet = (
        json.dumps(request.review_packet, sort_keys=True, indent=2, default=str)
        if request.review_packet
        else "null"
    )
    claude_outcome = request.claude_outcome or {}
    claude_outcome_text = _claude_outcome_prompt_payload(
        claude_outcome,
        compact=compact,
    )
    specialist_instruction = _specialist_return_instruction(request.expected_specialists)
    return "\n".join([
        f"Tri-agent review gate: {request.gate}.",
        f"Task id: {request.task_id}.",
        "",
        "Role: Cursor is an independent reviewer/challenger, not the implementer.",
        "Do not edit files. Inspect the worktree and artifacts, then judge the gate.",
        critical_review_prompt("Claude outcome and gate evidence"),
        "",
        "Instruction:",
        request.instruction.strip(),
        "",
        "Planning artifacts:",
        artifacts,
        "",
        "Evidence receipts:",
        receipts,
        "",
        "Supervisor review packet JSON:",
        review_packet,
        "",
        "Reviewer context receipt requirement:",
        (
            "Return critical_review.reviewer_context_receipt with files_reviewed, "
            "criteria_checked, receipts_considered, assumptions, and missing_context. "
            "For traceability, copy exact values from the supervisor packet: "
            "files_reviewed must include changed_files[].path values you inspected; "
            "criteria_checked must include acceptance_items[] strings; "
            "receipts_considered must include runtime_receipt_ids[].receipt_id values. "
            "Put any omitted packet item in missing_context. "
            "Important: runtime_receipt_ids are implementation/runtime evidence, not "
            "sibling reviewer receipts. The supervisor records and enforces the live "
            "Cursor/cursor_sdk receipt for this gate outside your review packet, so do "
            "not reject solely because the packet cannot yet include a sibling Cursor "
            "receipt; note that limitation in missing_context and judge the artifacts."
        ),
        "",
        "Claude outcome JSON:",
        claude_outcome_text,
        "",
        specialist_instruction,
        _outcome_block_contract(),
    ])


def _specialist_return_instruction(expected_specialists: tuple[str, ...]) -> str:
    names = [str(name).strip() for name in expected_specialists if str(name).strip()]
    if len(names) == 1:
        return (
            f"Return a specialist named {names[0]}. "
            "Use decision accept only if the gate should advance."
        )
    if names:
        joined = ", ".join(names)
        return (
            "Return specialist records with exactly these names: "
            f"{joined}. Use decision accept only if the gate should advance."
        )
    return (
        "Return a specialist record. "
        "Use decision accept only if the gate should advance."
    )


def cursor_accepts(result: CursorInvocationResult | None) -> bool:
    if result is None or not result.probe.ok or result.outcome is None:
        return False
    return outcome_accepts(result.outcome)


def invoke_cursor_agent(
    request: CursorInvocationRequest,
    *,
    status_runner: StatusRunner = subprocess.run,
) -> CursorInvocationResult:
    guard_source_worktree = (
        request.reviewer_output_mode != "cursor_sdk"
        or request.reviewer_worktree_isolation == "none"
    )
    before_status = (
        _git_status(request.cwd, status_runner=status_runner)
        if guard_source_worktree
        else None
    )
    max_attempts = 1 + max(0, int(request.contract_retry_limit))
    attempt_transcripts: list[str] = []
    retry_reasons: list[str] = []
    last_probe: ProbeResult | None = None
    last_outcome: Outcome | None = None
    last_metadata: dict[str, Any] = {}

    for attempt in range(1, max_attempts + 1):
        attempt_request = request
        if attempt > 1:
            previous_reason = last_probe.reason if last_probe is not None else "unknown"
            attempt_request = replace(
                request,
                instruction=_contract_corrective_instruction(
                    request.instruction,
                    previous_reason=previous_reason,
                    attempt=attempt,
                    max_attempts=max_attempts,
                ),
            )
        try:
            if attempt_request.reviewer_output_mode == "cursor_sdk":
                with _cursor_reviewer_runtime_request(attempt_request) as (
                    runtime_request,
                    worktree_isolation,
                ):
                    retry_result = _run_cursor_sdk_with_infra_retries(runtime_request)
                if worktree_isolation is not None:
                    if isinstance(retry_result, CursorInvocationResult):
                        retry_result = _with_worktree_isolation_diagnostics(
                            retry_result,
                            worktree_isolation,
                        )
                    else:
                        transcript, metadata = retry_result
                        retry_result = (
                            transcript,
                            {
                                **metadata,
                                "worktree_isolation": worktree_isolation,
                            },
                        )
                if isinstance(retry_result, CursorInvocationResult):
                    return _fallback_or_primary_failure(
                        retry_result,
                        request,
                        status_runner=status_runner,
                    )
                transcript, metadata = retry_result
            else:
                transcript, metadata = _run_litellm_structured(attempt_request)
        except CursorSdkTimeoutError:
            primary_failure = _cursor_infrastructure_result(
                reason="cursor_sdk_timeout",
                attempts=attempt,
                retry_reasons=tuple(retry_reasons),
                details={"timeout_s": max(1, int(attempt_request.timeout_s))},
                reviewer_output_mode=attempt_request.reviewer_output_mode,
            )
            return _fallback_or_primary_failure(primary_failure, request, status_runner=status_runner)
        except ModuleNotFoundError as e:
            if e.name in {"cursor_sdk", "openai"}:
                primary_failure = _cursor_infrastructure_result(
                    reason=f"{e.name}_missing",
                    attempts=attempt,
                    retry_reasons=tuple(retry_reasons),
                    reviewer_output_mode=attempt_request.reviewer_output_mode,
                )
                return _fallback_or_primary_failure(primary_failure, request, status_runner=status_runner)
            raise
        except Exception as e:  # pragma: no cover - exact SDK exception classes vary.
            access_denied_details = _reviewer_access_denied_details(e, attempt_request)
            if access_denied_details is not None:
                primary_failure = _cursor_access_denied_result(
                    attempts=attempt,
                    retry_reasons=tuple(retry_reasons),
                    details=access_denied_details,
                    reviewer_output_mode=attempt_request.reviewer_output_mode,
                )
                return _fallback_or_primary_failure(primary_failure, request, status_runner=status_runner)
            primary_failure = _cursor_infrastructure_result(
                reason="reviewer_invocation_failed",
                attempts=attempt,
                retry_reasons=tuple(retry_reasons),
                details={"error": str(e)},
                reviewer_output_mode=attempt_request.reviewer_output_mode,
            )
            return _fallback_or_primary_failure(primary_failure, request, status_runner=status_runner)

        attempt_transcripts.append(
            f"[cursor attempt {attempt}/{max_attempts}]\n{transcript}"
        )
        last_metadata = metadata
        terminal_failure = _cursor_terminal_empty_result_failure(
            transcript=transcript,
            metadata=metadata,
            attempts=attempt,
            retry_reasons=tuple(retry_reasons),
            reviewer_output_mode=attempt_request.reviewer_output_mode,
        )
        if terminal_failure is not None:
            return _fallback_or_primary_failure(
                terminal_failure,
                request,
                status_runner=status_runner,
            )
        probe, outcome = evaluate_outcome_fidelity(
            transcript,
            expected_specialists=request.expected_specialists,
            expected_decisions=request.expected_decisions,
            expected_objections=request.expected_objections,
        )
        if probe.ok and outcome is not None:
            probe = _evaluate_cursor_contract_completeness(outcome)
        if probe.ok and str(last_metadata.get("finish_reason") or "") == "length":
            probe = ProbeResult(
                "CURSOR",
                "red",
                "structured_reviewer_truncated",
                {"finish_reason": "length"},
            )
        last_probe = probe
        last_outcome = outcome
        if _should_retry_cursor_outcome(probe):
            retry_reasons.append(probe.reason)
            if attempt < max_attempts:
                continue

        after_status = (
            _git_status(request.cwd, status_runner=status_runner)
            if guard_source_worktree
            else None
        )
        if before_status is not None and after_status is not None and before_status != after_status:
            last_probe = ProbeResult(
                "CURSOR",
                "red",
                "cursor_modified_worktree",
                {"before": before_status, "after": after_status},
            )
            last_outcome = None
        elif last_probe.ok:
            last_probe = ProbeResult("CURSOR", "green", "cursor_review_ok", last_probe.details)

        break

    transcript = "\n\n".join(attempt_transcripts)
    assert last_probe is not None
    if _should_retry_cursor_outcome(last_probe):
        original_reason = last_probe.reason
        last_probe = ProbeResult(
            "CURSOR",
            "red",
            "reviewer_contract_unmet",
            {
                **last_probe.details,
                "original_reason": original_reason,
                "attempts": len(attempt_transcripts),
                "retry_reasons": list(retry_reasons),
                "recoverable": True,
            },
        )
        contract_failure = CursorInvocationResult(
            probe=last_probe,
            outcome=None,
            transcript=transcript,
            agent_id=last_metadata.get("agent_id"),
            run_id=last_metadata.get("run_id"),
            status=last_metadata.get("status"),
            model=last_metadata.get("model"),
            duration_ms=last_metadata.get("duration_ms"),
            reviewer_runtime=last_metadata.get("reviewer_runtime"),
            reviewer_output_mode=last_metadata.get("reviewer_output_mode"),
            reviewer_assurance=_reviewer_assurance(last_metadata),
            diagnostics=_metadata_diagnostics(last_metadata),
            failure_classification="reviewer_contract_unmet",
            recoverable=True,
            attempts=len(attempt_transcripts),
            retry_reasons=tuple(retry_reasons),
        )
        return _fallback_or_primary_failure(contract_failure, request, status_runner=status_runner)

    return CursorInvocationResult(
        probe=last_probe,
        outcome=last_outcome,
        transcript=transcript,
        agent_id=last_metadata.get("agent_id"),
        run_id=last_metadata.get("run_id"),
        status=last_metadata.get("status"),
        model=last_metadata.get("model"),
        duration_ms=last_metadata.get("duration_ms"),
        reviewer_runtime=last_metadata.get("reviewer_runtime"),
        reviewer_output_mode=last_metadata.get("reviewer_output_mode"),
        reviewer_assurance=_reviewer_assurance(last_metadata),
        diagnostics=_metadata_diagnostics(last_metadata),
        attempts=max(1, len(attempt_transcripts)),
        retry_reasons=tuple(retry_reasons),
    )


def _fallback_or_primary_failure(
    primary_failure: CursorInvocationResult,
    request: CursorInvocationRequest,
    *,
    status_runner: StatusRunner,
) -> CursorInvocationResult:
    if request.reviewer_output_mode != "cursor_sdk":
        return _with_failure_diagnostics(primary_failure)
    if primary_failure.failure_classification != "reviewer_infrastructure_unavailable":
        return _with_failure_diagnostics(primary_failure)
    if not _structured_fallback_available(request):
        return _with_failure_diagnostics(
            primary_failure,
            extra={"fallback": {"attempted": False, "reason": "missing_openai_api_key"}},
        )

    fallback_reason = _fallback_reason(primary_failure)
    fallback_request = replace(
        request,
        reviewer_output_mode="litellm_structured",
        reviewer_model=None,
    )
    fallback_result = invoke_cursor_agent(fallback_request, status_runner=status_runner)
    fallback_payload = _result_diagnostics(fallback_result)
    primary_payload = _result_diagnostics(primary_failure)

    if fallback_result.failure_classification == "reviewer_access_denied":
        return _with_failure_diagnostics(
            replace(
                fallback_result,
                fallback_from_runtime="cursor_sdk",
                fallback_reason=fallback_reason,
            ),
            extra={
                "fallback": {
                    "attempted": True,
                    "from_runtime": "cursor_sdk",
                    "to_runtime": "litellm_structured",
                    "reason": fallback_reason,
                    "primary_failure": primary_payload,
                    "fallback_failure": fallback_payload,
                },
            },
        )

    if fallback_result.probe.ok and fallback_result.outcome is not None:
        return replace(
            fallback_result,
            reviewer_assurance="fallback_text_only",
            fallback_from_runtime="cursor_sdk",
            fallback_reason=fallback_reason,
            diagnostics={
                **(fallback_result.diagnostics or {}),
                "fallback": {
                    "attempted": True,
                    "from_runtime": "cursor_sdk",
                    "to_runtime": "litellm_structured",
                    "reason": fallback_reason,
                    "primary_failure": primary_payload,
                },
            },
            retry_reasons=tuple([
                *primary_failure.retry_reasons,
                *fallback_result.retry_reasons,
            ]),
        )

    return _with_failure_diagnostics(
        primary_failure,
        extra={
            "fallback": {
                "attempted": True,
                "from_runtime": "cursor_sdk",
                "to_runtime": "litellm_structured",
                "reason": fallback_reason,
                "primary_failure": primary_payload,
                "fallback_failure": fallback_payload,
            },
        },
    )


def _run_cursor_sdk_with_infra_retries(
    request: CursorInvocationRequest,
) -> tuple[str, dict[str, Any]] | CursorInvocationResult:
    retry_limit = max(0, int(request.reviewer_infra_retry_limit or 0))
    backoff_base_s = max(0.0, float(request.reviewer_infra_retry_backoff_s or 0.0))
    max_attempts = 1 + retry_limit
    attempts: list[dict[str, Any]] = []
    backoffs: list[float] = []

    for infra_attempt in range(1, max_attempts + 1):
        try:
            with _cursor_sdk_timeout(request.timeout_s):
                transcript, metadata = _run_cursor_sdk(request)
        except CursorSdkTimeoutError:
            attempts.append({
                "attempt": infra_attempt,
                "reason": "cursor_sdk_timeout",
                "timeout_s": max(1, int(request.timeout_s)),
            })
        except ModuleNotFoundError:
            raise
        except Exception as e:  # pragma: no cover - exact SDK exception classes vary.
            access_denied_details = _reviewer_access_denied_details(e, request)
            if access_denied_details is not None:
                return _cursor_access_denied_result(
                    attempts=infra_attempt,
                    retry_reasons=(),
                    details=access_denied_details,
                    reviewer_output_mode=request.reviewer_output_mode,
                )
            attempts.append({
                "attempt": infra_attempt,
                "reason": "reviewer_invocation_failed",
                "error": str(e),
            })
        else:
            if attempts:
                metadata = {
                    **metadata,
                    "infrastructure_retries": _infrastructure_retry_diagnostics(
                        attempts=attempts,
                        retry_limit=retry_limit,
                        backoffs=backoffs,
                        exhausted=False,
                    ),
                }
            return transcript, metadata

        if infra_attempt < max_attempts:
            delay_s = backoff_base_s * (2 ** (infra_attempt - 1))
            backoffs.append(delay_s)
            if delay_s > 0:
                time.sleep(delay_s)

    final = attempts[-1] if attempts else {
        "attempt": 1,
        "reason": "reviewer_invocation_failed",
        "error": "cursor sdk failed without attempt diagnostics",
    }
    diagnostics = _infrastructure_retry_diagnostics(
        attempts=attempts,
        retry_limit=retry_limit,
        backoffs=backoffs,
        exhausted=True,
    )
    details = {
        **{key: value for key, value in final.items() if key != "attempt"},
        "attempts": len(attempts),
        "retry_limit": retry_limit,
        "infrastructure_retries": diagnostics,
    }
    primary_failure = _cursor_infrastructure_result(
        reason=str(final.get("reason") or "reviewer_invocation_failed"),
        attempts=len(attempts),
        retry_reasons=(),
        details=details,
        reviewer_output_mode=request.reviewer_output_mode,
    )
    return replace(
        primary_failure,
        diagnostics={
            **(primary_failure.diagnostics or {}),
            "infrastructure_retries": diagnostics,
        },
    )


def _infrastructure_retry_diagnostics(
    *,
    attempts: list[dict[str, Any]],
    retry_limit: int,
    backoffs: list[float],
    exhausted: bool,
) -> dict[str, Any]:
    return {
        "attempt_count": len(attempts) + (0 if exhausted else 1),
        "failed_attempt_count": len(attempts),
        "retry_limit": retry_limit,
        "attempts": attempts,
        "backoff_s": backoffs,
        "exhausted": exhausted,
    }


def _structured_fallback_available(request: CursorInvocationRequest) -> bool:
    return bool(request.openai_api_key)


def _fallback_reason(result: CursorInvocationResult) -> str:
    details = result.probe.details if isinstance(result.probe.details, dict) else {}
    original = str(details.get("original_reason") or "").strip()
    return original or str(result.failure_classification or result.probe.reason or "cursor_sdk_failed")


def _with_failure_diagnostics(
    result: CursorInvocationResult,
    *,
    extra: dict[str, Any] | None = None,
) -> CursorInvocationResult:
    diagnostics = {
        **(result.diagnostics or {}),
        "failure": _result_diagnostics(result),
        **(extra or {}),
    }
    return replace(
        result,
        reviewer_assurance=result.reviewer_assurance or "unavailable",
        diagnostics=diagnostics,
    )


def _result_diagnostics(result: CursorInvocationResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "probe": {
            "probe_id": result.probe.probe_id,
            "status": result.probe.status,
            "reason": result.probe.reason,
            "details": result.probe.details,
        },
        "failure_classification": result.failure_classification,
        "recoverable": result.recoverable,
        "attempts": result.attempts,
        "retry_reasons": list(result.retry_reasons),
        "reviewer_runtime": result.reviewer_runtime,
        "reviewer_output_mode": result.reviewer_output_mode,
        "reviewer_assurance": result.reviewer_assurance,
        "model": result.model,
        "status": result.status,
    }
    if result.diagnostics:
        payload["diagnostics"] = result.diagnostics
    return payload


@contextlib.contextmanager
def _cursor_sdk_timeout(timeout_s: int):
    seconds = max(1, int(timeout_s))
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
    ):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _raise_timeout(signum, frame):  # type: ignore[no-untyped-def]
        raise CursorSdkTimeoutError(f"cursor_sdk_timeout after {seconds}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _reviewer_assurance(metadata: dict[str, Any]) -> str | None:
    explicit = str(metadata.get("reviewer_assurance") or "").strip()
    if explicit:
        return explicit
    runtime = str(metadata.get("reviewer_runtime") or metadata.get("reviewer_output_mode") or "").strip()
    if runtime == "cursor_sdk":
        return "tool_backed_primary"
    if runtime == "litellm_structured":
        return "structured_text_only"
    return None


def _metadata_diagnostics(metadata: dict[str, Any]) -> dict[str, Any] | None:
    keys = (
        "prompt_chars",
        "prompt_sha256",
        "finish_reason",
        "prompt_tokens",
        "completion_tokens",
        "infrastructure_retries",
        "worktree_isolation",
    )
    diagnostics = {
        key: metadata.get(key)
        for key in keys
        if metadata.get(key) not in (None, "", [], {})
    }
    return diagnostics or None


def _run_cursor_sdk(request: CursorInvocationRequest) -> tuple[str, dict[str, Any]]:
    from cursor_sdk import Agent, LocalAgentOptions
    try:
        from cursor_sdk.types import SandboxOptions
    except (ImportError, AttributeError):  # pragma: no cover - SDK shape may vary.
        SandboxOptions = None  # type: ignore[assignment]

    api_key = request.api_key or os.environ.get("CURSOR_API_KEY")
    prompt = build_cursor_prompt(request)
    local_options: dict[str, Any] = {
        "cwd": str(Path(request.cwd).expanduser()),
    }
    if request.cursor_sdk_sandbox_enabled:
        local_options["sandbox_options"] = (
            {"enabled": True}
            if SandboxOptions is None
            else SandboxOptions(enabled=True)
        )
    kwargs: dict[str, Any] = {
        "model": select_cursor_model(quality=request.quality, explicit_model=request.model),
        "local": LocalAgentOptions(**local_options),
    }
    if api_key:
        kwargs["api_key"] = api_key

    with Agent.create(**kwargs) as agent:
        run = agent.send(prompt, {"mode": request.mode})
        transcript = run.text()
        metadata = {
            "agent_id": getattr(agent, "agent_id", None),
            "run_id": getattr(run, "id", None),
            "status": getattr(run, "status", None),
            "model": _model_id(getattr(run, "model", None) or getattr(agent, "model", None)),
            "reviewer_runtime": "cursor_sdk",
            "reviewer_output_mode": "cursor_sdk",
            "duration_ms": getattr(run, "duration_ms", None),
            "prompt_chars": len(prompt),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }
    return transcript, metadata


@contextlib.contextmanager
def _cursor_reviewer_runtime_request(request: CursorInvocationRequest):
    if (
        request.reviewer_output_mode != "cursor_sdk"
        or request.reviewer_worktree_isolation == "none"
    ):
        yield request, None
        return

    source = Path(request.cwd).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="cursor-reviewer-") as tmp_dir:
        isolated = Path(tmp_dir) / "worktree"
        shutil.copytree(
            source,
            isolated,
            symlinks=True,
            ignore=_cursor_reviewer_worktree_ignore(source),
        )
        before = _worktree_snapshot(isolated)
        diagnostic: dict[str, Any] = {
            "enabled": True,
            "strategy": "copytree_public_reviewer_worktree",
            "source_cwd": str(source),
            "isolated_cwd": str(isolated),
            "excluded_names": sorted(CURSOR_REVIEWER_WORKTREE_EXCLUDED_NAMES),
            "excluded_markers": list(CURSOR_REVIEWER_WORKTREE_EXCLUDED_MARKERS),
            "before_snapshot_sha256": before["sha256"],
        }
        try:
            yield replace(request, cwd=isolated), diagnostic
        finally:
            after = _worktree_snapshot(isolated) if isolated.exists() else {
                "sha256": "",
                "entries": {},
            }
            changed_paths = _snapshot_changed_paths(before, after)
            diagnostic.update({
                "after_snapshot_sha256": after["sha256"],
                "contained_mutation": before["sha256"] != after["sha256"],
                "changed_paths": changed_paths[:50],
                "changed_path_count": len(changed_paths),
            })


def _cursor_reviewer_worktree_ignore(source_root: Path):
    source_root = source_root.resolve()

    def _ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        directory_path = Path(directory).resolve()
        for name in names:
            try:
                rel_path = directory_path.relative_to(source_root) / name
            except ValueError:
                rel_path = Path(name)
            if _exclude_from_cursor_reviewer_worktree(rel_path):
                ignored.add(name)
        return ignored

    return _ignore


def _exclude_from_cursor_reviewer_worktree(path: Path) -> bool:
    name = path.name
    if name in CURSOR_REVIEWER_WORKTREE_EXCLUDED_NAMES:
        return True
    lowered = path.as_posix().lower()
    return any(marker in lowered for marker in CURSOR_REVIEWER_WORKTREE_EXCLUDED_MARKERS)


def _worktree_snapshot(root: Path) -> dict[str, Any]:
    entries: dict[str, dict[str, int]] = {}
    if not root.exists():
        return {"sha256": "", "entries": entries}
    for directory, dirnames, filenames in os.walk(root):
        directory_path = Path(directory)
        dirnames[:] = [
            dirname for dirname in dirnames
            if not _exclude_from_cursor_reviewer_worktree(
                (directory_path / dirname).relative_to(root)
            )
        ]
        for filename in filenames:
            file_path = directory_path / filename
            rel_path = file_path.relative_to(root)
            if _exclude_from_cursor_reviewer_worktree(rel_path):
                continue
            try:
                stat = file_path.stat()
            except OSError:
                continue
            entries[rel_path.as_posix()] = {
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "entries": entries,
    }


def _snapshot_changed_paths(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    before_entries = before.get("entries") if isinstance(before.get("entries"), dict) else {}
    after_entries = after.get("entries") if isinstance(after.get("entries"), dict) else {}
    changed: list[str] = []
    for path in sorted(set(before_entries) | set(after_entries)):
        if before_entries.get(path) != after_entries.get(path):
            changed.append(path)
    return changed


def _with_worktree_isolation_diagnostics(
    result: CursorInvocationResult,
    diagnostic: dict[str, Any],
) -> CursorInvocationResult:
    return replace(
        result,
        diagnostics={
            **(result.diagnostics or {}),
            "worktree_isolation": diagnostic,
        },
    )


def _run_litellm_structured(request: CursorInvocationRequest) -> tuple[str, dict[str, Any]]:
    from openai import OpenAI

    api_key = request.openai_api_key or os.environ.get("OPENAI_API_KEY")
    base_url = request.openai_base_url or os.environ.get("OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError("missing OPENAI_API_KEY for structured reviewer")

    prompt = build_cursor_prompt(request, compact=True)
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": max(1, int(request.timeout_s)),
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    model = select_reviewer_model(
        quality=request.quality,
        reviewer_output_mode="litellm_structured",
        reviewer_model=request.reviewer_model,
        cursor_model=request.model,
    )
    request_timeout = max(1, int(request.timeout_s))
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an independent reviewer. Return only JSON that "
                    "matches the provided schema. Do not edit files."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "dual_agent_outcome",
                "strict": True,
                "schema": _structured_outcome_json_schema(),
            },
        },
        temperature=0,
        max_tokens=max(1, int(request.reviewer_max_tokens)),
        timeout=request_timeout,
    )
    choice = response.choices[0]
    message = choice.message
    content = getattr(message, "content", None) or ""
    transcript = f"<dual_agent_outcome>{content}</dual_agent_outcome>"
    usage = getattr(response, "usage", None)
    metadata = {
        "agent_id": None,
        "run_id": getattr(response, "id", None),
        "status": "finished",
        "model": model,
        "reviewer_runtime": "litellm_structured",
        "reviewer_output_mode": "litellm_structured",
        "duration_ms": None,
        "finish_reason": getattr(choice, "finish_reason", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    return transcript, metadata


def _structured_outcome_json_schema() -> dict[str, Any]:
    reviewer_context_receipt_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reviewer_id": {"type": "string"},
            "files_reviewed": {"type": "array", "items": {"type": "string"}},
            "criteria_checked": {"type": "array", "items": {"type": "string"}},
            "receipts_considered": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "missing_context": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "reviewer_id",
            "files_reviewed",
            "criteria_checked",
            "receipts_considered",
            "assumptions",
            "missing_context",
        ],
    }
    critical_review_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strongest_objection": {"type": "string"},
            "missing_evidence": {"type": "array", "items": {"type": "string"}},
            "contradictions_checked": {"type": "array", "items": {"type": "string"}},
            "assumptions_to_verify": {"type": "array", "items": {"type": "string"}},
            "what_would_change_my_mind": {"type": "string"},
            "decision": {"type": "string", "enum": ["accept", "revise", "deny"]},
            "severity": {"type": "string"},
            "reviewer_context_receipt": reviewer_context_receipt_schema,
        },
        "required": [
            "strongest_objection",
            "missing_evidence",
            "contradictions_checked",
            "assumptions_to_verify",
            "what_would_change_my_mind",
            "decision",
            "severity",
            "reviewer_context_receipt",
        ],
    }
    specialist_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "decision": {"type": "string", "enum": ["accept", "revise", "deny"]},
            "objection": {"type": "string"},
        },
        "required": ["name", "decision", "objection"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {"type": "string"},
            "summary": {"type": "string"},
            "specialists": {"type": "array", "items": specialist_schema},
            "decisions": {
                "type": "array",
                "items": {"type": "string", "enum": ["accept", "revise", "deny"]},
            },
            "objections": {"type": "array", "items": {"type": "string"}},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "test_status": {"type": "string", "enum": ["passed", "failed", "unknown"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "confidence_rationale": {"type": "string"},
            "confidence_criteria": {"type": "array", "items": {"type": "string"}},
            "claims": {"type": "array", "items": {"type": "string"}},
            "critical_review": critical_review_schema,
        },
        "required": [
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
        ],
    }


def _git_status(cwd: str | Path, *, status_runner: StatusRunner) -> str | None:
    try:
        completed = status_runner(
            ["git", "status", "--porcelain=v1"],
            cwd=str(Path(cwd).expanduser()),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def _model_id(model: Any) -> str | None:
    if model is None:
        return None
    if isinstance(model, str):
        return model
    value = getattr(model, "id", None)
    return str(value) if value is not None else str(model)


def _should_retry_cursor_outcome(probe: ProbeResult) -> bool:
    return (
        probe.reason == "missing dual_agent_outcome block"
        or probe.reason.startswith("invalid dual_agent_outcome block")
        or probe.reason == "outcome_missing_required_fields"
        or probe.reason == "outcome_signal_loss"
        or probe.reason == "structured_reviewer_truncated"
    )


def _evaluate_cursor_contract_completeness(outcome: Outcome) -> ProbeResult:
    missing_fields: list[str] = []
    if not str(outcome.confidence_rationale or "").strip():
        missing_fields.append("confidence_rationale")
    if not outcome.confidence_criteria:
        missing_fields.append("confidence_criteria")
    critical_review = outcome.critical_review if isinstance(outcome.critical_review, dict) else {}
    for field_name in ("missing_evidence", "contradictions_checked", "assumptions_to_verify"):
        if field_name not in critical_review or not isinstance(critical_review.get(field_name), list):
            missing_fields.append(f"critical_review.{field_name}")
    for field_name in (
        "strongest_objection",
        "what_would_change_my_mind",
        "decision",
        "severity",
    ):
        if not str(critical_review.get(field_name) or "").strip():
            missing_fields.append(f"critical_review.{field_name}")
    if missing_fields:
        return ProbeResult(
            "CURSOR",
            "red",
            "outcome_missing_required_fields",
            {"fields": missing_fields},
        )
    return ProbeResult("CURSOR", "green", "cursor_review_ok")


def _contract_corrective_instruction(
    instruction: str,
    *,
    previous_reason: str,
    attempt: int,
    max_attempts: int,
) -> str:
    return "\n\n".join([
        instruction.strip(),
        (
            f"Corrective retry {attempt}/{max_attempts}: the previous Cursor review "
            f"failed the typed outcome contract: {previous_reason}. Return a valid "
            "<dual_agent_outcome>{...compact JSON...}</dual_agent_outcome> block. "
            "Do not omit the block. Do not use null values for specialist decisions. "
            "Repeat required decisions in the top-level decisions array."
        ),
        _outcome_block_contract(),
    ])


def _cursor_infrastructure_result(
    *,
    reason: str,
    attempts: int,
    retry_reasons: tuple[str, ...],
    details: dict[str, Any] | None = None,
    reviewer_output_mode: str | None = None,
) -> CursorInvocationResult:
    diagnostics = {
        "failure": {
            "original_reason": reason,
            "reviewer_output_mode": reviewer_output_mode,
            "attempts": attempts,
            **(details or {}),
        }
    }
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "reviewer_infrastructure_unavailable",
            {
                "original_reason": reason,
                "attempts": attempts,
                "retry_reasons": list(retry_reasons),
                "recoverable": True,
                **(details or {}),
            },
        ),
        outcome=None,
        transcript="",
        failure_classification="reviewer_infrastructure_unavailable",
        recoverable=True,
        attempts=attempts,
        retry_reasons=retry_reasons,
        reviewer_output_mode=reviewer_output_mode,
        reviewer_runtime=reviewer_output_mode,
        reviewer_assurance="unavailable",
        diagnostics=diagnostics,
    )


def _cursor_terminal_empty_result_failure(
    *,
    transcript: str,
    metadata: dict[str, Any],
    attempts: int,
    retry_reasons: tuple[str, ...],
    reviewer_output_mode: str | None,
) -> CursorInvocationResult | None:
    runtime = str(metadata.get("reviewer_runtime") or metadata.get("reviewer_output_mode") or "")
    status = str(metadata.get("status") or "").strip().lower()
    if runtime != "cursor_sdk" or status not in {"error", "cancelled", "expired"}:
        return None
    if transcript.strip():
        return None
    details = {
        "status": status,
        "run_id": metadata.get("run_id"),
        "agent_id": metadata.get("agent_id"),
        "model": metadata.get("model"),
        "duration_ms": metadata.get("duration_ms"),
    }
    return _cursor_infrastructure_result(
        reason="cursor_sdk_terminal_empty_result",
        attempts=attempts,
        retry_reasons=retry_reasons,
        details={key: value for key, value in details.items() if value not in (None, "")},
        reviewer_output_mode=reviewer_output_mode,
    )


def _cursor_access_denied_result(
    *,
    attempts: int,
    retry_reasons: tuple[str, ...],
    details: dict[str, Any],
    reviewer_output_mode: str | None = None,
) -> CursorInvocationResult:
    sanitized_details = {
        "original_reason": "reviewer_access_denied",
        "attempts": attempts,
        "retry_reasons": list(retry_reasons),
        "recoverable": False,
        **details,
    }
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "reviewer_access_denied",
            sanitized_details,
        ),
        outcome=None,
        transcript="",
        failure_classification="reviewer_access_denied",
        recoverable=False,
        attempts=attempts,
        retry_reasons=retry_reasons,
        reviewer_output_mode=reviewer_output_mode,
        reviewer_runtime=reviewer_output_mode,
        reviewer_assurance="unavailable",
        diagnostics={"access_denied": sanitized_details},
    )


def _outcome_block_contract() -> str:
    return (
        "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome>. "
        "The JSON must include: task_id string, summary string, specialists array, "
        "decisions array, objections array, changed_files array, tests array, "
        "test_status one of passed, failed, or unknown, confidence number from 0 to 1, confidence_rationale string, "
        "confidence_criteria array, claims array, and critical_review object. "
        "All values inside decisions, objections, changed_files, tests, confidence_criteria, and claims "
        "must be strings, not objects. "
        "critical_review must include strongest_objection string, missing_evidence array, "
        "contradictions_checked array, assumptions_to_verify array, "
        "what_would_change_my_mind string, decision string, severity string, "
        "and reviewer_context_receipt object with files_reviewed, criteria_checked, "
        "receipts_considered, assumptions, and missing_context arrays. "
        "Every specialist object must include a string name and a string decision."
    )


def _receipt_prompt_payload(receipt: dict[str, Any], *, compact: bool = False) -> str:
    allowed = {
        key: receipt.get(key)
        for key in (
            "receipt_id",
            "id",
            "kind",
            "type",
            "status",
            "result",
            "claims",
            "command",
            "changed_files",
            "remote",
            "branch",
            "commit",
            "path",
            "label",
        )
        if receipt.get(key) not in (None, "", [], {})
    }
    changed_files = allowed.get("changed_files")
    if isinstance(changed_files, list) and len(changed_files) > 20:
        allowed["changed_files"] = changed_files[:20]
        allowed["changed_files_count"] = len(changed_files)
        allowed["changed_files_omitted_count"] = len(changed_files) - 20
    payload = allowed or receipt
    if compact:
        payload = _compact_for_prompt(payload)
    return json.dumps(payload, sort_keys=True, default=str)


def _claude_outcome_prompt_payload(
    claude_outcome: dict[str, Any],
    *,
    compact: bool,
) -> str:
    if not compact:
        return str(claude_outcome)
    allowed = {
        key: claude_outcome.get(key)
        for key in (
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
        )
        if claude_outcome.get(key) not in (None, "", [], {})
    }
    return json.dumps(_compact_for_prompt(allowed or claude_outcome), sort_keys=True, default=str)


def _compact_for_prompt(
    value: Any,
    *,
    max_string_chars: int = 1200,
    max_items: int = 20,
    max_depth: int = 4,
) -> Any:
    if max_depth <= 0:
        return _truncate_prompt_text(str(value), max_string_chars)
    if isinstance(value, str):
        return _truncate_prompt_text(value, max_string_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        items = list(value.items())
        compacted = {
            str(key): _compact_for_prompt(
                item_value,
                max_string_chars=max_string_chars,
                max_items=max_items,
                max_depth=max_depth - 1,
            )
            for key, item_value in items[:max_items]
        }
        if len(items) > max_items:
            compacted["_truncated_keys"] = len(items) - max_items
        return compacted
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        compacted_items = [
            _compact_for_prompt(
                item,
                max_string_chars=max_string_chars,
                max_items=max_items,
                max_depth=max_depth - 1,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            compacted_items.append({"_truncated_items": len(items) - max_items})
        return compacted_items
    return _truncate_prompt_text(str(value), max_string_chars)


def _truncate_prompt_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    omitted = len(value) - max_chars
    return f"{value[:max_chars]}...[truncated {omitted} chars]"


def _reviewer_access_denied_details(
    error: BaseException,
    request: CursorInvocationRequest,
) -> dict[str, Any] | None:
    status_code = _exception_status_code(error)
    message = _truncate_prompt_text(str(error), 1000)
    body_text = _exception_body_text(error)
    haystack = " ".join(
        part.lower()
        for part in (
            type(error).__name__,
            str(status_code or ""),
            message,
            body_text,
        )
        if part
    )
    access_marker = any(
        marker in haystack
        for marker in (
            "access denied",
            "permission denied",
            "unauthorized",
            "forbidden",
            "not authorized",
            "do not have access",
            "don't have access",
        )
    )
    if status_code not in {401, 403} and not (
        access_marker and ("401" in haystack or "403" in haystack)
    ):
        return None

    model = select_reviewer_model(
        quality=request.quality,
        reviewer_output_mode=request.reviewer_output_mode,
        reviewer_model=request.reviewer_model,
        cursor_model=request.model,
    )
    return {
        "status_code": status_code,
        "error_type": type(error).__name__,
        "message": message,
        "body": _truncate_prompt_text(body_text, 1000) if body_text else None,
        "reviewer_output_mode": request.reviewer_output_mode,
        "model": model,
        "base_url_host": _base_url_host(request.openai_base_url or os.environ.get("OPENAI_BASE_URL")),
    }


def _exception_status_code(error: BaseException) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(error, attr, None)
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _exception_body_text(error: BaseException) -> str:
    body = getattr(error, "body", None)
    if body not in (None, "", {}, []):
        return json.dumps(body, sort_keys=True, default=str) if not isinstance(body, str) else body
    response = getattr(error, "response", None)
    text = getattr(response, "text", None)
    if text:
        return str(text)
    return ""


def _base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(str(base_url))
    return parsed.netloc or parsed.path or None
