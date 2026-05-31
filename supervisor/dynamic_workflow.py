"""Deterministic dynamic workflow preview policy and receipt synthesis."""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .agent_mailbox import normalize_critical_review
from .dual_agent_lead import DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES


DYNAMIC_WORKFLOW_MANIFEST_SCHEMA_VERSION = "dynamic-workflow-manifest/v1"
DYNAMIC_WORKFLOW_SYNTHESIS_SCHEMA_VERSION = "dynamic-workflow-synthesis/v1"
_PASSING_STATUSES = {"passed", "pass", "accepted", "accept", "ok", "success", "succeeded"}
_BLOCKING_SEVERITIES = {"critical", "important", "blocker"}


@dataclass(frozen=True)
class DynamicWorkflowPreparation:
    manifest: dict[str, Any]
    manifest_ref: str
    manifest_sha256: str
    synthesis: dict[str, Any]
    synthesized_receipts: tuple[dict[str, Any], ...]


_REGISTRY: dict[str, tuple[dict[str, Any], ...]] = {
    "codebase_audit": (
        {
            "suffix": "audit-1",
            "persona_id": "reviewer.codebase_audit",
            "role": "read-only codebase audit",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed", "pytest --collect-only"),
            "timeout_s": 300,
            "budget_usd": 1.5,
            "output_schema": "dynamic-workflow-output/v1",
        },
    ),
    "large_migration": (
        {
            "suffix": "migration-plan",
            "persona_id": "reviewer.migration_plan",
            "role": "migration plan reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed", "git diff", "pytest --collect-only"),
            "timeout_s": 600,
            "budget_usd": 3.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
        {
            "suffix": "migration-tests",
            "persona_id": "reviewer.migration_tests",
            "role": "migration test reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "pytest --collect-only", "git diff"),
            "timeout_s": 600,
            "budget_usd": 3.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
    ),
    "cortex_pod_four_reviewer_fanout": (
        {
            "suffix": "product",
            "persona_id": "reviewer.product",
            "role": "product contract reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed"),
            "timeout_s": 450,
            "budget_usd": 2.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
        {
            "suffix": "architecture",
            "persona_id": "reviewer.architecture",
            "role": "architecture reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed", "git diff"),
            "timeout_s": 450,
            "budget_usd": 2.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
        {
            "suffix": "test",
            "persona_id": "reviewer.test",
            "role": "test strategy reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "pytest --collect-only"),
            "timeout_s": 450,
            "budget_usd": 2.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
        {
            "suffix": "traceability",
            "persona_id": "reviewer.traceability",
            "role": "traceability reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed", "git diff"),
            "timeout_s": 450,
            "budget_usd": 2.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
    ),
    "eval_fixture_population": (
        {
            "suffix": "fixture-audit",
            "persona_id": "reviewer.eval_fixture",
            "role": "eval fixture reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed", "pytest --collect-only"),
            "timeout_s": 300,
            "budget_usd": 1.5,
            "output_schema": "dynamic-workflow-output/v1",
        },
    ),
    "other_fanout": (
        {
            "suffix": "generic-review",
            "persona_id": "reviewer.generic_fanout",
            "role": "generic dynamic fan-out reviewer",
            "permission_mode": "readOnly",
            "tool_pins": ("rg", "sed"),
            "timeout_s": 300,
            "budget_usd": 1.0,
            "output_schema": "dynamic-workflow-output/v1",
        },
    ),
}


def prepare_dynamic_workflow_preview(
    *,
    cwd: str | Path,
    task_id: str,
    run_id: str,
    dynamic_workflow_task_class: str | None,
    budget_usd: float,
    timeout_s: int,
    tool_receipts: list[dict[str, Any]],
    output_dir: str | Path,
) -> DynamicWorkflowPreparation:
    manifest = build_dynamic_workflow_manifest(
        task_id=task_id,
        run_id=run_id,
        dynamic_workflow_task_class=dynamic_workflow_task_class,
        budget_usd=budget_usd,
        timeout_s=timeout_s,
    )
    manifest_path = Path(output_dir) / "replay" / "supervisor-dynamic-workflow-manifest.json"
    manifest_ref, manifest_sha = write_dynamic_workflow_manifest(
        cwd=cwd,
        manifest=manifest,
        path=manifest_path,
    )
    subagent_results = dynamic_subagent_results(tool_receipts)
    synthesis = synthesize_dynamic_workflow_results(
        manifest=manifest,
        subagent_results=subagent_results,
    )
    synthesized_receipts = synthesize_dynamic_workflow_receipts(
        manifest=manifest,
        manifest_ref=manifest_ref,
        manifest_sha256=manifest_sha,
        subagent_results=subagent_results,
    )
    return DynamicWorkflowPreparation(
        manifest=manifest,
        manifest_ref=manifest_ref,
        manifest_sha256=manifest_sha,
        synthesis=synthesis,
        synthesized_receipts=tuple(synthesized_receipts),
    )


def build_dynamic_workflow_manifest(
    *,
    task_id: str,
    run_id: str,
    dynamic_workflow_task_class: str | None,
    budget_usd: float,
    timeout_s: int,
) -> dict[str, Any]:
    task_class = _norm(dynamic_workflow_task_class) or "other_fanout"
    specs = _REGISTRY.get(task_class, _REGISTRY["other_fanout"])
    subagents = []
    for spec in specs:
        subagents.append({
            "task_id": f"{task_id}:{spec['suffix']}",
            "persona_id": spec["persona_id"],
            "role": spec["role"],
            "timeout_s": min(int(timeout_s), int(spec["timeout_s"])),
            "budget_usd": min(float(budget_usd), float(spec["budget_usd"])),
            "permission_mode": spec["permission_mode"],
            "tool_pins": list(spec["tool_pins"]),
            "output_schema": spec["output_schema"],
        })
    return {
        "schema_version": DYNAMIC_WORKFLOW_MANIFEST_SCHEMA_VERSION,
        "task_id": task_id,
        "run_id": run_id,
        "task_class": task_class,
        "supervision_layer": "codex_plus_lead",
        "lead_integrator": "claude_code_lead",
        "preview_required_gates": list(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES),
        "synthesis_policy": {
            "schema_version": DYNAMIC_WORKFLOW_SYNTHESIS_SCHEMA_VERSION,
            "block_on_severities": sorted(_BLOCKING_SEVERITIES),
            "unregistered_result_policy": "block",
            "missing_registered_result_policy": "block",
        },
        "subagents": subagents,
    }


def write_dynamic_workflow_manifest(
    *,
    cwd: str | Path,
    manifest: dict[str, Any],
    path: str | Path,
) -> tuple[str, str]:
    cwd_path = Path(cwd).resolve()
    manifest_path = Path(path)
    if not manifest_path.is_absolute():
        manifest_path = cwd_path / manifest_path
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(manifest_path.resolve().relative_to(cwd_path)), _sha256(manifest_path)


def dynamic_subagent_results(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        kind = _norm(receipt.get("kind") or receipt.get("type"))
        if kind not in {"dynamic_subagent_result", "dynamic_reviewer_result", "subagent_result"}:
            continue
        results.append(dict(receipt))
    return results


def synthesize_dynamic_workflow_results(
    *,
    manifest: dict[str, Any],
    subagent_results: list[dict[str, Any]],
) -> dict[str, Any]:
    if not subagent_results:
        return {
            "schema_version": DYNAMIC_WORKFLOW_SYNTHESIS_SCHEMA_VERSION,
            "status": "not_applicable",
            "result_count": 0,
            "registered_subagent_count": len(manifest.get("subagents") or []),
            "decisions": [],
            "blocking_results": [],
        }
    registered_by_task = {
        str(item.get("task_id") or ""): item
        for item in manifest.get("subagents") or []
        if isinstance(item, dict)
    }
    registered_by_persona = {
        str(item.get("persona_id") or ""): item
        for item in manifest.get("subagents") or []
        if isinstance(item, dict)
    }
    observed_keys: set[str] = set()
    blocking: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for result in subagent_results:
        task_key = str(result.get("task_id") or "")
        persona_key = str(result.get("persona_id") or "")
        registered = registered_by_task.get(task_key) or registered_by_persona.get(persona_key)
        if registered is None:
            blocking.append({
                "reason": "unregistered_dynamic_subagent_result",
                "task_id": task_key,
                "persona_id": persona_key,
            })
            continue
        observed_keys.add(str(registered.get("task_id") or ""))
        decision = _norm(result.get("decision") or result.get("result") or result.get("status"))
        severity = _norm(result.get("severity"))
        status = _norm(result.get("status") or result.get("result"))
        objections = _list_text(result.get("objections") or result.get("objection"))
        critical_review = normalize_critical_review(
            result.get("critical_review"),
            decision=decision,
            objections=tuple(objections),
            evidence_refs=_dynamic_result_evidence_refs(result),
            would_change_if=(
                "Replay verification, supervisor synthesis, or registered "
                "subagent receipts contradict this result."
            ),
        )
        decisions.append({
            "task_id": task_key,
            "persona_id": persona_key,
            "decision": decision,
            "severity": severity,
            "objections": objections,
            "critical_review": critical_review,
        })
        if status and status not in _PASSING_STATUSES:
            blocking.append({
                "reason": "dynamic_subagent_status_not_passing",
                "task_id": task_key,
                "persona_id": persona_key,
                "status": status,
            })
        if decision in {"block", "blocked", "deny", "denied", "revise", "reject", "rejected"}:
            blocking.append({
                "reason": "dynamic_subagent_decision_blocked",
                "task_id": task_key,
                "persona_id": persona_key,
                "decision": decision,
                "severity": severity,
                "objections": objections,
            })
        if severity in _BLOCKING_SEVERITIES and objections:
            blocking.append({
                "reason": "dynamic_subagent_blocking_objection",
                "task_id": task_key,
                "persona_id": persona_key,
                "severity": severity,
                "objections": objections,
            })
    missing = [
        str(item.get("task_id") or "")
        for item in manifest.get("subagents") or []
        if isinstance(item, dict) and str(item.get("task_id") or "") not in observed_keys
    ]
    for task_key in missing:
        blocking.append({
            "reason": "missing_registered_dynamic_subagent_result",
            "task_id": task_key,
        })
    return {
        "schema_version": DYNAMIC_WORKFLOW_SYNTHESIS_SCHEMA_VERSION,
        "status": "blocked" if blocking else "accepted",
        "result_count": len(subagent_results),
        "registered_subagent_count": len(registered_by_task),
        "decisions": decisions,
        "blocking_results": blocking,
    }


def synthesize_dynamic_workflow_receipts(
    *,
    manifest: dict[str, Any],
    manifest_ref: str,
    manifest_sha256: str,
    subagent_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not subagent_results:
        return []
    registered = {
        str(item.get("task_id") or ""): item
        for item in manifest.get("subagents") or []
        if isinstance(item, dict)
    }
    subagents: list[dict[str, Any]] = []
    for result in subagent_results:
        task_key = str(result.get("task_id") or "")
        spec = registered.get(task_key) or {}
        decision = _norm(result.get("decision") or result.get("result") or result.get("status"))
        objections = _list_text(result.get("objections") or result.get("objection"))
        subagents.append({
            "task_id": task_key,
            "persona_id": result.get("persona_id") or spec.get("persona_id"),
            "timeout_s": result.get("timeout_s") or spec.get("timeout_s"),
            "budget_usd": result.get("budget_usd") or spec.get("budget_usd"),
            "agent_runtime": result.get("agent_runtime") or spec.get("agent_runtime"),
            "agent_id": result.get("agent_id"),
            "permission_mode": result.get("permission_mode") or spec.get("permission_mode"),
            "tool_pins": result.get("tool_pins") or result.get("tools") or spec.get("tool_pins"),
            "transcript_ref": result.get("transcript_ref"),
            "transcript_sha256": result.get("transcript_sha256"),
            "output_ref": result.get("output_ref"),
            "output_sha256": result.get("output_sha256"),
            "output_hash": _hash_value(result.get("output_hash") or result.get("output_sha256")),
            "changed_files": result.get("changed_files", []),
            "critical_review": normalize_critical_review(
                result.get("critical_review"),
                decision=decision,
                objections=tuple(objections),
                evidence_refs=_dynamic_result_evidence_refs(result),
                would_change_if=(
                    "Replay verification, supervisor synthesis, or registered "
                    "subagent receipts contradict this result."
                ),
            ),
        })
    output_hash = _combined_output_hash(subagents)
    first = subagent_results[0]
    shared = {
        "kind": "dynamic_workflow_receipt",
        "status": "passed",
        "manifest_ref": manifest_ref,
        "manifest_sha256": manifest_sha256,
        "subagents": subagents,
        "supervision_layer": "codex_plus_lead",
        "lead_integrator": "claude_code_lead",
        "output_schema": "dynamic-workflow-output/v1",
        "output_hash": output_hash,
        "headless": True,
        "no_session_persistence": True,
        "replay_ref": first.get("replay_ref"),
        "replay_sha256": first.get("replay_sha256"),
        "worktree_comparison_ref": first.get("worktree_comparison_ref") or first.get("comparison_ref"),
        "worktree_comparison_sha256": (
            first.get("worktree_comparison_sha256") or first.get("comparison_sha256")
        ),
    }
    return [
        {
            "receipt_id": f"dyn-{manifest['task_id']}-{gate}",
            "gate": gate,
            **shared,
        }
        for gate in DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES
    ]


def _combined_output_hash(subagents: list[dict[str, Any]]) -> str:
    hashes = [
        str(item.get("output_sha256") or item.get("output_hash") or "")
        for item in subagents
        if str(item.get("output_sha256") or item.get("output_hash") or "").strip()
    ]
    if len(hashes) == 1:
        return _hash_value(hashes[0])
    combined = sha256(json.dumps(sorted(hashes), separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{combined}"


def _hash_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _dynamic_result_evidence_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for kind, ref_key, sha_key in [
        ("dynamic_subagent_transcript", "transcript_ref", "transcript_sha256"),
        ("dynamic_subagent_output", "output_ref", "output_sha256"),
        ("dynamic_subagent_replay", "replay_ref", "replay_sha256"),
        (
            "dynamic_subagent_worktree_comparison",
            "worktree_comparison_ref",
            "worktree_comparison_sha256",
        ),
    ]:
        ref = result.get(ref_key)
        if not str(ref or "").strip():
            continue
        item = {"kind": kind, "ref": str(ref)}
        sha = result.get(sha_key)
        if str(sha or "").strip():
            item["sha256"] = str(sha)
        refs.append(item)
    return refs
