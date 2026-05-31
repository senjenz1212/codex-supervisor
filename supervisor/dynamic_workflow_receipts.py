"""Deterministic receipts for dynamic workflow preview execution."""
from __future__ import annotations

from typing import Any

from .dual_agent import ProbeResult
from .dual_agent_lead import DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES, DynamicWorkflowTaskClass


DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID = "P13"
_PASSING_STATUSES = {"passed", "pass", "accepted", "accept", "ok", "success", "succeeded"}
_DYNAMIC_RECEIPT_KINDS = {
    "dynamic_workflow_receipt",
    "dynamic_workflow_gate",
    "dynamic_workflow_manifest",
}
_ALLOWED_TASK_CLASSES = set(DynamicWorkflowTaskClass.__args__)  # type: ignore[attr-defined]


def verify_dynamic_workflow_receipts(
    *,
    execution_layer_mode: str,
    dynamic_workflow_task_class: str | None,
    tool_receipts: list[dict[str, Any]] | None,
    required_gates: tuple[str, ...] = DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES,
    allowed_task_classes: set[str] | None = None,
) -> ProbeResult:
    """Validate dynamic workflow preview receipts without asking a model."""
    mode = _norm(execution_layer_mode)
    if mode != "dynamic_workflow_preview":
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "green",
            "dynamic_workflow_not_requested",
            {"execution_layer_mode": execution_layer_mode},
        )

    allowed = allowed_task_classes or _ALLOWED_TASK_CLASSES
    if not dynamic_workflow_task_class:
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "missing_dynamic_workflow_task_class",
            {"allowed_task_classes": sorted(allowed)},
        )
    if dynamic_workflow_task_class not in allowed:
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "disallowed_dynamic_workflow_task_class",
            {
                "dynamic_workflow_task_class": dynamic_workflow_task_class,
                "allowed_task_classes": sorted(allowed),
            },
        )

    receipts = _dynamic_receipts(tool_receipts or [])
    by_gate: dict[str, dict[str, Any]] = {}
    invalid: dict[str, list[str]] = {}
    for receipt in receipts:
        for gate, payload in _receipt_gate_payloads(receipt).items():
            if gate not in required_gates:
                continue
            problems = _gate_receipt_problems(gate, payload)
            if problems:
                invalid.setdefault(gate, []).extend(problems)
                continue
            by_gate.setdefault(gate, payload)

    missing = [gate for gate in required_gates if gate not in by_gate]
    details = {
        "execution_layer_mode": execution_layer_mode,
        "dynamic_workflow_task_class": dynamic_workflow_task_class,
        "required_gates": list(required_gates),
        "verified_gates": [gate for gate in required_gates if gate in by_gate],
        "receipt_ids": [
            str(receipt.get("receipt_id") or receipt.get("id") or "")
            for receipt in receipts
            if receipt.get("receipt_id") or receipt.get("id")
        ],
    }
    if missing:
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "missing_dynamic_workflow_receipts",
            {**details, "missing_gates": missing, "invalid_gates": invalid},
        )
    if invalid:
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "invalid_dynamic_workflow_receipts",
            {**details, "invalid_gates": invalid},
        )
    return ProbeResult(
        DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
        "green",
        "dynamic_workflow_receipts_verified",
        details,
    )


def _dynamic_receipts(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        kind = _norm(receipt.get("kind") or receipt.get("type"))
        if kind not in _DYNAMIC_RECEIPT_KINDS:
            continue
        status = _norm(receipt.get("status") or receipt.get("result"))
        if status not in _PASSING_STATUSES:
            continue
        result.append(dict(receipt))
    return result


def _receipt_gate_payloads(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gate = _norm(receipt.get("gate") or receipt.get("preview_gate"))
    if gate:
        return {gate: receipt}
    preview_gates = receipt.get("preview_gates") or receipt.get("gates")
    if not isinstance(preview_gates, dict):
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    for name, payload in preview_gates.items():
        gate_name = _norm(name)
        if not isinstance(payload, dict):
            payloads[gate_name] = {**receipt, "gate": gate_name}
            continue
        payloads[gate_name] = {**receipt, **payload, "gate": gate_name}
    return payloads


def _gate_receipt_problems(gate: str, receipt: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    subagents = receipt.get("subagents")
    if gate in {
        "per_subagent_budget_caps_verified",
        "permission_mode_and_tool_pins_verified",
        "machine_readable_output_verified",
    }:
        if not isinstance(subagents, list) or not subagents:
            problems.append("missing_subagents")
    if gate == "codex_and_lead_remain_supervision_layer":
        if _norm(receipt.get("supervision_layer")) != "codex_plus_lead":
            problems.append("missing_codex_plus_lead_supervision")
        if not _text(receipt.get("lead_integrator") or receipt.get("lead_worker")):
            problems.append("missing_lead_integrator")
    elif gate == "per_subagent_budget_caps_verified":
        for index, subagent in enumerate(subagents if isinstance(subagents, list) else []):
            if not isinstance(subagent, dict):
                problems.append(f"subagent_{index}_not_object")
                continue
            if not _text(subagent.get("task_id")):
                problems.append(f"subagent_{index}_missing_task_id")
            if not _text(subagent.get("persona_id")):
                problems.append(f"subagent_{index}_missing_persona_id")
            if subagent.get("timeout_s") in (None, "", 0) and subagent.get("budget_usd") in (None, "", 0):
                problems.append(f"subagent_{index}_missing_timeout_or_budget")
    elif gate == "permission_mode_and_tool_pins_verified":
        for index, subagent in enumerate(subagents if isinstance(subagents, list) else []):
            if not isinstance(subagent, dict):
                continue
            if not _text(subagent.get("permission_mode") or receipt.get("permission_mode")):
                problems.append(f"subagent_{index}_missing_permission_mode")
            if not _has_list_value(subagent.get("tool_pins") or subagent.get("tools") or receipt.get("tool_pins")):
                problems.append(f"subagent_{index}_missing_tool_pins")
    elif gate == "machine_readable_output_verified":
        if not _text(receipt.get("output_schema") or receipt.get("schema")):
            problems.append("missing_output_schema")
        if not _text(receipt.get("output_hash") or receipt.get("hash")):
            problems.append("missing_output_hash")
        for index, subagent in enumerate(subagents if isinstance(subagents, list) else []):
            if not isinstance(subagent, dict):
                continue
            if not _text(subagent.get("transcript_ref")):
                problems.append(f"subagent_{index}_missing_transcript_ref")
    elif gate == "headless_no_session_persistence_verified":
        if receipt.get("headless") is not True:
            problems.append("headless_not_verified")
        if receipt.get("no_session_persistence") is not True:
            problems.append("no_session_persistence_not_verified")
    elif gate == "replay_or_ci_determinism_verified":
        if not _text(receipt.get("replay_ref") or receipt.get("ci_ref")):
            problems.append("missing_replay_or_ci_ref")
    elif gate == "throwaway_worktree_comparison_recorded":
        if not _text(receipt.get("worktree_comparison_ref") or receipt.get("comparison_ref")):
            problems.append("missing_worktree_comparison_ref")
    return problems


def _has_list_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_text(item) for item in value)
    return value not in (None, "", [], {})


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _text(value: Any) -> str:
    return str(value or "").strip()
