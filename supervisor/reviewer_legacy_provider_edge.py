"""Explicit legacy Codex CLI reviewer edge.

Production composition should prefer ``RuntimeReviewerAdapter``.  This module
keeps the historical direct subprocess path available for replay fixtures and
backward-compatible callers without leaving provider argv construction in the
provider-neutral reviewer registry.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .agent_mailbox import critical_review_prompt
from .cursor_agent import (
    CursorInvocationRequest,
    CursorInvocationResult,
    _evaluate_cursor_contract_completeness,
    _outcome_block_contract,
)
from .dual_agent import ProbeResult, evaluate_outcome_fidelity
from .redaction import redact


CodexRunner = Callable[..., subprocess.CompletedProcess[str]]


class ReviewerIdentity(Protocol):
    reviewer_id: str
    runtime: str
    model: str | None


@dataclass(frozen=True)
class CodexCliReviewer:
    """Backward-compatible direct Codex subprocess reviewer."""

    spec: ReviewerIdentity
    runner: CodexRunner = subprocess.run
    reasoning_effort: str = "xhigh"
    command: str = "codex"

    def review(self, request: CursorInvocationRequest) -> CursorInvocationResult:
        started = time.monotonic()
        model = self.spec.model or "gpt-5.5"
        prompt = _codex_cli_reviewer_prompt(
            request,
            reviewer_name=self.spec.reviewer_id,
        )
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
        retry_limit = max(0, int(request.reviewer_infra_retry_limit or 0))
        backoff_base_s = max(
            0.0,
            float(request.reviewer_infra_retry_backoff_s or 0.0),
        )
        failed_attempts: list[dict[str, Any]] = []
        retry_backoff_s: list[float] = []
        retry_reasons: list[str] = []
        for attempt_index in range(retry_limit + 1):
            attempt = attempt_index + 1
            try:
                completed = self.runner(
                    argv,
                    cwd=str(Path(request.cwd).expanduser()),
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    timeout=request.timeout_s,
                )
            except Exception as exc:  # pragma: no cover - subprocess errors vary.
                reason = "codex_cli_invocation_failed"
                details = {"error": str(exc)}
                transcript = ""
                failed_attempts.append({
                    "attempt": attempt,
                    "reason": reason,
                    **details,
                })
                retry_reasons.append(reason)
                if attempt_index >= retry_limit:
                    return _codex_cli_infrastructure_result(
                        reason=reason,
                        model=model,
                        transcript=transcript,
                        duration_ms=_duration_ms(started),
                        details={
                            **details,
                            "retry_limit": retry_limit,
                            "attempt_count": attempt,
                        },
                        attempts=attempt,
                        retry_reasons=tuple(retry_reasons),
                        retry_diagnostics=_codex_cli_retry_diagnostics(
                            retry_limit=retry_limit,
                            attempt_count=attempt,
                            exhausted=True,
                            attempts=failed_attempts,
                            backoff_s=retry_backoff_s,
                        ),
                    )
                delay_s = backoff_base_s * (2 ** attempt_index)
                retry_backoff_s.append(delay_s)
                if delay_s:
                    time.sleep(delay_s)
                continue

            raw_stdout = completed.stdout or ""
            raw_stderr = completed.stderr or ""
            safe_stderr = redact(raw_stderr)
            metadata = _parse_codex_cli_jsonl(raw_stdout)
            transcript = "\n\n".join(
                item
                for item in (
                    raw_stdout,
                    f"[stderr]\n{safe_stderr}" if safe_stderr else "",
                    "[agent_messages]\n" + "\n".join(metadata["agent_messages"])
                    if metadata["agent_messages"]
                    else "",
                )
                if item
            )
            if completed.returncode != 0:
                reason = "codex_cli_nonzero_exit"
                details = {
                    "returncode": completed.returncode,
                    "stderr_tail": safe_stderr[-2000:],
                }
                failed_attempts.append({
                    "attempt": attempt,
                    "reason": reason,
                    **details,
                })
                retry_reasons.append(reason)
                if attempt_index >= retry_limit:
                    return _codex_cli_infrastructure_result(
                        reason=reason,
                        model=model,
                        transcript=transcript,
                        duration_ms=_duration_ms(started),
                        details={
                            **details,
                            "retry_limit": retry_limit,
                            "attempt_count": attempt,
                        },
                        attempts=attempt,
                        retry_reasons=tuple(retry_reasons),
                        retry_diagnostics=_codex_cli_retry_diagnostics(
                            retry_limit=retry_limit,
                            attempt_count=attempt,
                            exhausted=True,
                            attempts=failed_attempts,
                            backoff_s=retry_backoff_s,
                        ),
                    )
                delay_s = backoff_base_s * (2 ** attempt_index)
                retry_backoff_s.append(delay_s)
                if delay_s:
                    time.sleep(delay_s)
                continue
            break
        else:  # pragma: no cover - loop always returns or breaks.
            return _codex_cli_infrastructure_result(
                reason="codex_cli_invocation_failed",
                model=model,
                transcript="",
                duration_ms=_duration_ms(started),
                details={"error": "retry_loop_exhausted_without_result"},
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
                reviewer_assurance=(
                    "tool_backed_primary"
                    if metadata["command_executions"]
                    else "self_reported"
                ),
                diagnostics={
                    "codex_cli": {
                        "thread_id": metadata.get("thread_id"),
                        "command_executions": metadata["command_executions"],
                        "command_execution_count": len(
                            metadata["command_executions"]
                        ),
                        "stdout_sha256": hashlib.sha256(
                            raw_stdout.encode("utf-8")
                        ).hexdigest(),
                        "stderr_sha256": hashlib.sha256(
                            raw_stderr.encode("utf-8")
                        ).hexdigest(),
                    },
                    "infrastructure_retries": _codex_cli_retry_diagnostics(
                        retry_limit=retry_limit,
                        attempt_count=attempt,
                        exhausted=False,
                        attempts=failed_attempts,
                        backoff_s=retry_backoff_s,
                    ),
                },
                failure_classification="reviewer_contract_unmet",
                recoverable=True,
                attempts=attempt,
                retry_reasons=(*retry_reasons, probe.reason),
            )

        return CursorInvocationResult(
            probe=ProbeResult(
                "CODEX_REVIEWER",
                "green",
                "codex_cli_review_ok",
                probe.details,
            ),
            outcome=outcome,
            transcript=transcript,
            agent_id=metadata.get("thread_id"),
            run_id=metadata.get("thread_id"),
            status="finished",
            model=model,
            reviewer_runtime="codex_cli",
            reviewer_output_mode="codex_cli",
            duration_ms=_duration_ms(started),
            reviewer_assurance=(
                "tool_backed_primary"
                if metadata["command_executions"]
                else "self_reported"
            ),
            diagnostics={
                "codex_cli": {
                    "thread_id": metadata.get("thread_id"),
                    "command_executions": metadata["command_executions"],
                    "command_execution_count": len(
                        metadata["command_executions"]
                    ),
                    "stdout_sha256": hashlib.sha256(
                        raw_stdout.encode("utf-8")
                    ).hexdigest(),
                    "stderr_sha256": hashlib.sha256(
                        raw_stderr.encode("utf-8")
                    ).hexdigest(),
                },
                "infrastructure_retries": _codex_cli_retry_diagnostics(
                    retry_limit=retry_limit,
                    attempt_count=attempt,
                    exhausted=False,
                    attempts=failed_attempts,
                    backoff_s=retry_backoff_s,
                ),
            },
            attempts=attempt,
            retry_reasons=tuple(retry_reasons),
        )


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
    review_packet = (
        json.dumps(
            request.review_packet,
            sort_keys=True,
            indent=2,
            default=str,
        )
        if request.review_packet
        else "null"
    )
    return "\n".join([
        f"Independent reviewer gate: {request.gate}.",
        f"Task id: {request.task_id}.",
        "",
        (
            f"Role: {reviewer_name} is an independent GPT-family reviewer, "
            "not the implementer."
        ),
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
        json.dumps(request.claude_outcome or {}, sort_keys=True, default=str),
        "",
        (
            f"Return a specialist named {reviewer_name}. Use decision accept "
            "only if the gate should advance."
        ),
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
            payload = (
                event.get("payload")
                if isinstance(event.get("payload"), dict)
                else {}
            )
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
        payload = (
            event.get("payload")
            if isinstance(event.get("payload"), dict)
            else {}
        )
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
            agent_messages.extend(_response_item_message_texts(payload))
        if event_type == "response_item" and payload_type == "function_call":
            command_executions.append(_codex_function_call_summary(payload))
        if (
            event_type == "response_item"
            and payload_type == "function_call_output"
        ):
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
    attempts: int = 1,
    retry_reasons: tuple[str, ...] | None = None,
    retry_diagnostics: dict[str, Any] | None = None,
) -> CursorInvocationResult:
    diagnostics = {"codex_cli": {"reason": reason, **details}}
    if retry_diagnostics is not None:
        diagnostics["infrastructure_retries"] = retry_diagnostics
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
        diagnostics=diagnostics,
        failure_classification="reviewer_infrastructure_unavailable",
        recoverable=True,
        attempts=attempts,
        retry_reasons=retry_reasons or (reason,),
    )


def _codex_cli_retry_diagnostics(
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
        "exhausted": exhausted,
        "attempts": list(attempts),
        "backoff_s": list(backoff_s),
    }


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


__all__ = ["CodexCliReviewer", "CodexRunner"]
