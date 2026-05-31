"""Deterministic receipts for dynamic workflow preview execution."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
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
_DYNAMIC_SUBAGENT_RESULT_KINDS = {
    "dynamic_subagent_result",
    "dynamic_reviewer_result",
    "subagent_result",
}
_ALLOWED_TASK_CLASSES = set(DynamicWorkflowTaskClass.__args__)  # type: ignore[attr-defined]
_EVIDENCE_GRADE_RANK = {
    "self_reported": 0,
    "lead_captured": 1,
    "runtime_native": 2,
}
_SUPERVISOR_OWNED_PREFIXES = (
    ".handoff/agentic-workers/",
    ".handoff/workflow-jobs/",
    "docs/dual-agent/",
)


def verify_dynamic_workflow_receipts(
    *,
    execution_layer_mode: str,
    dynamic_workflow_task_class: str | None,
    tool_receipts: list[dict[str, Any]] | None,
    required_gates: tuple[str, ...] = DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES,
    allowed_task_classes: set[str] | None = None,
    cwd: str | Path | None = None,
    agentic_lead_policy: str = "off",
    min_subagents: int = 0,
    required_roles: list[str] | tuple[str, ...] | None = None,
    solo_exception_for_artifact_only_gates: bool = False,
    required_evidence_grade: str = "self_reported",
) -> ProbeResult:
    """Validate dynamic workflow preview receipts without asking a model."""
    mode = _norm(execution_layer_mode)
    receipt_payloads = tool_receipts or []
    agentic_policy = _evaluate_agentic_lead_policy(
        receipts=receipt_payloads,
        cwd=Path(cwd) if cwd is not None else None,
        policy=agentic_lead_policy,
        min_subagents=min_subagents,
        required_roles=required_roles or (),
        solo_exception_for_artifact_only_gates=solo_exception_for_artifact_only_gates,
        required_evidence_grade=required_evidence_grade,
    )
    if agentic_policy["status"] == "blocked":
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "agentic_lead_policy_blocked",
            {
                "execution_layer_mode": execution_layer_mode,
                "agentic_policy": agentic_policy,
            },
        )

    if mode != "dynamic_workflow_preview":
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "green",
            "dynamic_workflow_not_requested",
            {
                "execution_layer_mode": execution_layer_mode,
                "agentic_policy": agentic_policy,
            },
        )

    allowed = {_norm(item) for item in (allowed_task_classes or _ALLOWED_TASK_CLASSES)}
    task_class = _norm(dynamic_workflow_task_class)
    if not task_class:
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "missing_dynamic_workflow_task_class",
            {"allowed_task_classes": sorted(allowed)},
        )
    if task_class not in allowed:
        return ProbeResult(
            DYNAMIC_WORKFLOW_RECEIPT_PROBE_ID,
            "red",
            "disallowed_dynamic_workflow_task_class",
            {
                "dynamic_workflow_task_class": task_class,
                "requested_dynamic_workflow_task_class": dynamic_workflow_task_class,
                "allowed_task_classes": sorted(allowed),
            },
        )

    receipts = _dynamic_receipts(receipt_payloads)
    by_gate: dict[str, dict[str, Any]] = {}
    invalid: dict[str, list[str]] = {}
    verified_refs: list[dict[str, str]] = []
    for receipt in receipts:
        for gate, payload in _receipt_gate_payloads(receipt).items():
            if gate not in required_gates:
                continue
            gate_verified_refs: list[dict[str, str]] = []
            problems = _gate_receipt_problems(gate, payload)
            if cwd is not None:
                replay_problems, gate_verified_refs = _replay_ref_problems(payload, cwd=Path(cwd))
                problems.extend(replay_problems)
            if problems:
                invalid.setdefault(gate, []).extend(problems)
                continue
            by_gate.setdefault(gate, payload)
            verified_refs.extend(gate_verified_refs)

    missing = [gate for gate in required_gates if gate not in by_gate]
    details = {
        "execution_layer_mode": execution_layer_mode,
        "dynamic_workflow_task_class": task_class,
        "requested_dynamic_workflow_task_class": dynamic_workflow_task_class,
        "required_gates": list(required_gates),
        "verified_gates": [gate for gate in required_gates if gate in by_gate],
        "receipt_ids": [
            str(receipt.get("receipt_id") or receipt.get("id") or "")
            for receipt in receipts
            if receipt.get("receipt_id") or receipt.get("id")
        ],
        "verified_refs": verified_refs,
        "agentic_policy": agentic_policy,
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


def _evaluate_agentic_lead_policy(
    *,
    receipts: list[dict[str, Any]],
    cwd: Path | None,
    policy: str,
    min_subagents: int,
    required_roles: list[str] | tuple[str, ...],
    solo_exception_for_artifact_only_gates: bool,
    required_evidence_grade: str,
) -> dict[str, Any]:
    policy_name = _norm(policy) or "off"
    required_grade = _norm(required_evidence_grade) or "self_reported"
    if required_grade not in _EVIDENCE_GRADE_RANK:
        required_grade = "self_reported"
    required_role_keys = [_norm(role) for role in required_roles if _norm(role)]
    subagents = [
        _agentic_subagent_summary(subagent, cwd=cwd, index=index)
        for index, subagent in enumerate(_agentic_subagents(receipts))
    ]
    role_keys = {
        key
        for subagent in subagents
        for key in _subagent_role_keys(subagent)
        if key
    }
    blocking: list[dict[str, Any]] = []
    lead_solo = _lead_solo_execution(receipts)
    enforce = policy_name == "required"

    if enforce and lead_solo and not solo_exception_for_artifact_only_gates:
        blocking.append({
            "reason": "agentic_lead_solo_execution",
            "receipts": lead_solo,
        })
    if enforce and len(subagents) < max(0, int(min_subagents)):
        blocking.append({
            "reason": "insufficient_subagents",
            "observed": len(subagents),
            "required": max(0, int(min_subagents)),
        })
    for role in required_role_keys:
        if enforce and role not in role_keys:
            blocking.append({
                "reason": "missing_required_role",
                "role": role,
            })
    for subagent in subagents:
        if enforce and subagent["replay_problems"]:
            blocking.append({
                "reason": "subagent_replay_or_ref_invalid",
                "task_id": subagent["task_id"],
                "problems": subagent["replay_problems"],
            })
        missing_runtime = [
            field
            for field in ("agent_runtime", "agent_id", "permission_mode", "tool_pins")
            if not subagent.get(field)
        ]
        if enforce and missing_runtime:
            blocking.append({
                "reason": "subagent_missing_runtime_fields",
                "task_id": subagent["task_id"],
                "fields": missing_runtime,
            })
        if enforce and _EVIDENCE_GRADE_RANK[subagent["evidence_grade"]] < _EVIDENCE_GRADE_RANK[required_grade]:
            blocking.append({
                "reason": "required_evidence_grade_not_met",
                "task_id": subagent["task_id"],
                "observed": subagent["evidence_grade"],
                "required": required_grade,
            })
        if enforce and _is_independent_reviewer(subagent) and _reviewer_blocks(subagent):
            blocking.append({
                "reason": "independent_reviewer_blocked",
                "task_id": subagent["task_id"],
                "decision": subagent["decision"],
                "severity": subagent["severity"],
                "objections": subagent["objections"],
            })

    achieved = "self_reported"
    if subagents:
        achieved = min(
            (subagent["evidence_grade"] for subagent in subagents),
            key=lambda item: _EVIDENCE_GRADE_RANK[item],
        )
    return {
        "schema_version": "agentic-lead-policy/v1",
        "policy": policy_name,
        "status": "blocked" if blocking else "accepted" if policy_name != "off" else "not_applicable",
        "min_subagents": max(0, int(min_subagents)),
        "required_roles": required_role_keys,
        "solo_exception_for_artifact_only_gates": bool(solo_exception_for_artifact_only_gates),
        "required_evidence_grade": required_grade,
        "achieved_evidence_grade": achieved,
        "lead_solo_execution": lead_solo,
        "subagents": subagents,
        "blocking_findings": blocking,
    }


def _agentic_subagents(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subagents: list[dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        kind = _norm(receipt.get("kind") or receipt.get("type"))
        if kind in _DYNAMIC_SUBAGENT_RESULT_KINDS:
            subagents.append(dict(receipt))
        for subagent in receipt.get("subagents") or []:
            if isinstance(subagent, dict):
                subagents.append({**subagent, "parent_receipt_id": receipt.get("receipt_id") or receipt.get("id")})
    return subagents


def _agentic_subagent_summary(subagent: dict[str, Any], *, cwd: Path | None, index: int) -> dict[str, Any]:
    replay_problems, verified_refs = _subagent_ref_problems(subagent, cwd=cwd, index=index)
    evidence_grade = _derive_evidence_grade(subagent, replay_problems=replay_problems, verified_refs=verified_refs)
    return {
        "task_id": _text(subagent.get("task_id")),
        "persona_id": _text(subagent.get("persona_id")),
        "role": _text(subagent.get("role")),
        "decision": _norm(subagent.get("decision") or subagent.get("result") or subagent.get("status")),
        "severity": _norm(subagent.get("severity")),
        "objections": _list_text(subagent.get("objections") or subagent.get("objection")),
        "agent_runtime": _text(subagent.get("agent_runtime")),
        "agent_id": _text(subagent.get("agent_id")),
        "permission_mode": _text(subagent.get("permission_mode")),
        "tool_pins": _list_text(subagent.get("tool_pins") or subagent.get("tools")),
        "timeout_s": subagent.get("timeout_s"),
        "budget_usd": subagent.get("budget_usd"),
        "transcript_ref": _text(subagent.get("transcript_ref")),
        "output_ref": _text(subagent.get("output_ref")),
        "declared_evidence_grade": _text(subagent.get("evidence_grade")),
        "evidence_grade": evidence_grade,
        "verified_refs": verified_refs,
        "replay_problems": replay_problems,
    }


def _subagent_ref_problems(
    subagent: dict[str, Any],
    *,
    cwd: Path | None,
    index: int,
) -> tuple[list[str], list[dict[str, str]]]:
    if cwd is None:
        return ["missing_cwd_for_replay_verification"], []
    problems: list[str] = []
    verified: list[dict[str, str]] = []
    for ref_key, hash_key, label in [
        ("transcript_ref", "transcript_sha256", f"subagent_{index}_transcript"),
        ("output_ref", "output_sha256", f"subagent_{index}_output"),
    ]:
        item_problems, item_verified = _verify_ref_hash(
            subagent,
            ref_key=ref_key,
            hash_key=hash_key,
            label=label,
            cwd=cwd,
        )
        problems.extend(item_problems)
        verified.extend(item_verified)
    return problems, verified


def _derive_evidence_grade(
    subagent: dict[str, Any],
    *,
    replay_problems: list[str],
    verified_refs: list[dict[str, str]],
) -> str:
    if replay_problems or len(verified_refs) < 2:
        return "self_reported"
    refs = [item.get("ref", "") for item in verified_refs]
    if refs and all(_is_supervisor_owned_ref(ref) for ref in refs):
        return "runtime_native"
    return "lead_captured"


def _is_supervisor_owned_ref(ref: str) -> bool:
    normalized = str(ref or "").strip().lstrip("./")
    return any(
        normalized.startswith(prefix.lstrip("./"))
        for prefix in _SUPERVISOR_OWNED_PREFIXES
    )


def _lead_solo_execution(receipts: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        kind = _norm(receipt.get("kind") or receipt.get("type"))
        if kind not in {"agentic_lead_execution", "lead_execution"}:
            continue
        mode = _norm(receipt.get("execution_mode") or receipt.get("mode"))
        if mode in {"solo", "self", "single_worker", "lead_direct"}:
            result.append({
                "receipt_id": _text(receipt.get("receipt_id") or receipt.get("id")),
                "execution_mode": mode,
            })
    return result


def _subagent_role_keys(subagent: dict[str, Any]) -> set[str]:
    values = {
        _norm(subagent.get("role")),
        _norm(subagent.get("persona_id")),
        _norm(subagent.get("task_id")),
    }
    role = _norm(subagent.get("role"))
    if role:
        values.add(role)
    persona = _norm(subagent.get("persona_id"))
    if persona:
        values.update(part for part in persona.split(".") if part)
    return {value for value in values if value}


def _is_independent_reviewer(subagent: dict[str, Any]) -> bool:
    text = " ".join([
        _norm(subagent.get("role")),
        _norm(subagent.get("persona_id")),
    ])
    return "independent" in text and "reviewer" in text


def _reviewer_blocks(subagent: dict[str, Any]) -> bool:
    decision = _norm(subagent.get("decision"))
    severity = _norm(subagent.get("severity"))
    return decision in {"block", "blocked", "deny", "denied", "revise", "reject", "rejected"} or severity in {
        "critical",
        "important",
        "blocker",
    }


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
    if isinstance(subagents, list):
        for index, subagent in enumerate(subagents):
            if isinstance(subagent, dict) and "changed_files" not in subagent:
                problems.append(f"subagent_{index}_missing_changed_files")
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
            if subagent.get("timeout_s") in (None, "", 0):
                problems.append(f"subagent_{index}_missing_timeout_s")
            if subagent.get("budget_usd") in (None, "", 0):
                problems.append(f"subagent_{index}_missing_budget_usd")
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


def _replay_ref_problems(receipt: dict[str, Any], *, cwd: Path) -> tuple[list[str], list[dict[str, str]]]:
    problems: list[str] = []
    verified: list[dict[str, str]] = []
    for ref_key, hash_key, label in [
        ("manifest_ref", "manifest_sha256", "manifest"),
        ("replay_ref", "replay_sha256", "replay"),
        ("worktree_comparison_ref", "worktree_comparison_sha256", "worktree_comparison"),
    ]:
        item_problems, item_verified = _verify_ref_hash(
            receipt,
            ref_key=ref_key,
            hash_key=hash_key,
            label=label,
            cwd=cwd,
        )
        problems.extend(item_problems)
        verified.extend(item_verified)

    subagents = receipt.get("subagents")
    if isinstance(subagents, list):
        for index, subagent in enumerate(subagents):
            if not isinstance(subagent, dict):
                continue
            for ref_key, hash_key, label in [
                ("transcript_ref", "transcript_sha256", f"subagent_{index}_transcript"),
                ("output_ref", "output_sha256", f"subagent_{index}_output"),
            ]:
                item_problems, item_verified = _verify_ref_hash(
                    subagent,
                    ref_key=ref_key,
                    hash_key=hash_key,
                    label=label,
                    cwd=cwd,
                )
                problems.extend(item_problems)
                verified.extend(item_verified)
            output_sha = _normalise_sha(subagent.get("output_sha256"))
            output_hash = _normalise_sha(subagent.get("output_hash") or receipt.get("output_hash"))
            if output_sha and output_hash and output_sha != output_hash:
                problems.append(f"subagent_{index}_output_hash_mismatch")
    return problems, verified


def _verify_ref_hash(
    payload: dict[str, Any],
    *,
    ref_key: str,
    hash_key: str,
    label: str,
    cwd: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    ref = _text(payload.get(ref_key))
    expected = _normalise_sha(payload.get(hash_key))
    problems: list[str] = []
    if not ref:
        problems.append(f"missing_{ref_key}")
        if not expected:
            problems.append(f"missing_{hash_key}")
        return problems, []
    if not expected:
        problems.append(f"missing_{hash_key}")
        return problems, []
    path = _resolve_under_cwd(ref, cwd)
    if path is None:
        return [f"{label}_ref_outside_cwd"], []
    if not path.exists() or not path.is_file():
        return [f"{label}_ref_missing"], []
    actual = sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        return [f"{label}_sha256_mismatch"], []
    return [], [{
        "label": label,
        "ref": ref,
        "sha256": actual,
    }]


def _resolve_under_cwd(ref: str, cwd: Path) -> Path | None:
    cwd_resolved = cwd.resolve()
    path = Path(ref)
    resolved = path.resolve() if path.is_absolute() else (cwd_resolved / path).resolve()
    try:
        resolved.relative_to(cwd_resolved)
    except ValueError:
        return None
    return resolved


def _normalise_sha(value: Any) -> str:
    text = _text(value)
    return text.removeprefix("sha256:")


def _has_list_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_text(item) for item in value)
    return value not in (None, "", [], {})


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _text(value: Any) -> str:
    return str(value or "").strip()
