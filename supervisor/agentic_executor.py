"""Inline supervisor-owned agentic worker production."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agentic_workers import (
    AgenticWorkerSpec,
    cleanup_orphaned_agentic_workers,
    run_agentic_worker_fanout,
)
from .dual_agent_lead import ModelQuality, select_lead_model


Runner = Callable[..., subprocess.CompletedProcess[str]]
FanoutRunner = Callable[[list[AgenticWorkerSpec]], list[dict[str, Any]]]
CleanupRunner = Callable[..., dict[str, Any]]

ROSTER_BLOCK_RE = re.compile(
    r"<agentic_worker_roster>\s*(?P<payload>\{.*?\})\s*</agentic_worker_roster>",
    re.DOTALL,
)
READ_ONLY_PERMISSION_MODES = {"readonly", "read_only", "read-only", "readOnly"}
DEFAULT_WORKER_TOOL_PINS = ("Read", "Grep", "Glob", "Bash(rg *)", "Bash(sed *)", "Bash(nl *)", "Bash(git diff *)")
AGENTIC_WORKER_MAX_SUBAGENTS = 8


@dataclass(frozen=True)
class AgenticWorkerRosterItem:
    worker_id: str
    role: str
    prompt: str
    persona_id: str = ""
    permission_mode: str = "readOnly"
    tool_pins: tuple[str, ...] = field(default_factory=lambda: DEFAULT_WORKER_TOOL_PINS)
    timeout_s: int = 300
    budget_usd: float = 1.0


@dataclass(frozen=True)
class AgenticWorkerProduction:
    status: str
    receipts: list[dict[str, Any]] = field(default_factory=list)
    roster: list[dict[str, Any]] = field(default_factory=list)
    blocking_findings: list[dict[str, Any]] = field(default_factory=list)
    cleanup: dict[str, Any] | None = None
    planner: dict[str, Any] = field(default_factory=dict)

    def to_event_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "agentic-worker-production/v1",
            "status": self.status,
            "receipt_count": len(self.receipts),
            "receipts": self.receipts,
            "roster": self.roster,
            "blocking_findings": self.blocking_findings,
            "cleanup": self.cleanup,
            "planner": self.planner,
        }


def produce_agentic_worker_receipts(
    *,
    cwd: str | Path,
    task_id: str,
    run_id: str,
    intent: str,
    agentic_policy: dict[str, Any],
    existing_receipts: list[dict[str, Any]],
    timeout_s: int,
    budget_usd: float,
    quality: ModelQuality = "best",
    runner: Runner = subprocess.run,
    fanout_runner: FanoutRunner | None = None,
    cleanup_runner: CleanupRunner = cleanup_orphaned_agentic_workers,
    now_s: Callable[[], float] = time.time,
) -> AgenticWorkerProduction:
    """Plan, validate, and run supervisor-owned read-only workers inline."""
    policy = str(agentic_policy.get("agentic_lead_policy") or "off").strip().lower()
    if policy not in {"allowed", "required"}:
        return AgenticWorkerProduction(status="not_applicable")
    if _has_lead_solo_receipt(existing_receipts):
        return AgenticWorkerProduction(
            status="skipped_lead_solo_receipt",
            blocking_findings=[{"reason": "lead_solo_receipt_present"}],
        )

    completed_receipts = _completed_agentic_receipts(existing_receipts)
    min_subagents = max(0, int(agentic_policy.get("min_subagents") or 0))
    required_roles = [
        str(role).strip()
        for role in agentic_policy.get("required_roles") or []
        if str(role).strip()
    ]
    completed_roles = _completed_role_keys(completed_receipts)
    missing_required_roles = [
        role
        for role in required_roles
        if _role_key(role) not in completed_roles
    ]
    missing_min_subagents = max(0, min_subagents - len(completed_receipts))
    if not missing_required_roles and missing_min_subagents == 0:
        return AgenticWorkerProduction(
            status="skipped_existing_receipts",
            receipts=[],
            planner={
                "existing_completed_receipt_count": len(completed_receipts),
                "existing_completed_roles": sorted(completed_roles),
                "reason": "existing_receipts_satisfy_agentic_policy",
            },
        )

    planner_result = plan_agentic_worker_roster(
        cwd=cwd,
        task_id=task_id,
        run_id=run_id,
        intent=intent,
        min_subagents=max(missing_min_subagents, len(missing_required_roles)),
        required_roles=missing_required_roles,
        timeout_s=timeout_s,
        budget_usd=budget_usd,
        quality=quality,
        runner=runner,
    )
    if planner_result.status != "passed":
        return planner_result

    roster_items = [
        AgenticWorkerRosterItem(**item)
        for item in planner_result.roster
    ]
    pending_roster_items = _pending_roster_items(roster_items, completed_receipts)
    validation = validate_agentic_worker_roster(
        pending_roster_items,
        min_subagents=missing_min_subagents,
        required_roles=missing_required_roles,
        timeout_s=timeout_s,
        budget_usd=budget_usd,
    )
    if validation:
        return AgenticWorkerProduction(
            status="blocked",
            roster=[asdict(item) for item in roster_items],
            blocking_findings=validation,
            planner=planner_result.planner,
        )

    specs = [
        _worker_spec(
            item,
            cwd=cwd,
            task_id=task_id,
            quality=quality,
        )
        for item in pending_roster_items
    ]
    receipts = (
        list(fanout_runner(specs))
        if fanout_runner is not None
        else list(run_agentic_worker_fanout(specs, runner=runner))
    )
    for index, receipt in enumerate(receipts):
        worker_id = str(receipt.get("worker_id") or specs[index].worker_id)
        receipt.setdefault("receipt_id", f"agentic-worker-{worker_id}")
        receipt.setdefault("role", specs[index].role)
        receipt.setdefault("persona_id", specs[index].persona_id)
        receipt.setdefault("permission_mode", specs[index].permission_mode)
        receipt.setdefault("tool_pins", list(specs[index].tool_pins))
        receipt.setdefault("agent_runtime", specs[index].agent_runtime)
        receipt.setdefault("agent_id", specs[index].agent_id or worker_id)

    cleanup = None
    if any(str(receipt.get("status") or "").lower() in {"timeout", "failed", "error"} for receipt in receipts):
        cleanup = cleanup_runner(
            cwd=cwd,
            task_id=task_id,
            workers=[
                {
                    "worker_id": receipt.get("worker_id") or specs[index].worker_id,
                    "timeout_s": receipt.get("timeout_s") or specs[index].timeout_s,
                    "budget_usd": receipt.get("budget_usd") or specs[index].budget_usd,
                    "started_at_s": now_s() - (receipt.get("timeout_s") or specs[index].timeout_s) - 1,
                    "log_ref": receipt.get("log_ref"),
                }
                for index, receipt in enumerate(receipts)
            ],
            now_s=now_s(),
        )

    return AgenticWorkerProduction(
        status="passed" if receipts or completed_receipts else "blocked",
        receipts=receipts,
        roster=[asdict(item) for item in roster_items],
        cleanup=cleanup,
        planner={
            **planner_result.planner,
            "existing_completed_receipt_count": len(completed_receipts),
            "existing_completed_roles": sorted(completed_roles),
            "pending_worker_ids": [item.worker_id for item in pending_roster_items],
            "skipped_completed_worker_ids": sorted(_completed_worker_ids(completed_receipts)),
        },
    )


def plan_agentic_worker_roster(
    *,
    cwd: str | Path,
    task_id: str,
    run_id: str,
    intent: str,
    min_subagents: int,
    required_roles: list[str],
    timeout_s: int,
    budget_usd: float,
    quality: ModelQuality,
    runner: Runner,
) -> AgenticWorkerProduction:
    """Ask the lead to return a bounded worker roster, without spawning workers."""
    command = _planner_command(
        task_id=task_id,
        run_id=run_id,
        intent=intent,
        min_subagents=min_subagents,
        required_roles=required_roles,
        timeout_s=timeout_s,
        budget_usd=budget_usd,
        quality=quality,
    )
    try:
        proc = runner(
            command,
            cwd=str(Path(cwd)),
            capture_output=True,
            text=True,
            timeout=min(max(30, int(timeout_s)), 180),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return AgenticWorkerProduction(
            status="blocked",
            blocking_findings=[{"reason": "agentic_roster_planner_timeout"}],
            planner={"command": command, "returncode": None},
        )
    except OSError as e:
        return AgenticWorkerProduction(
            status="blocked",
            blocking_findings=[{"reason": "agentic_roster_planner_failed", "error": str(e)}],
            planner={"command": command, "returncode": None},
        )

    stdout = str(proc.stdout or "")
    stderr = str(proc.stderr or "")
    planner = {
        "command": command,
        "returncode": proc.returncode,
        "stdout_bytes": len(stdout.encode()),
        "stderr_bytes": len(stderr.encode()),
    }
    if proc.returncode != 0:
        return AgenticWorkerProduction(
            status="blocked",
            blocking_findings=[{"reason": "agentic_roster_planner_nonzero", "returncode": proc.returncode}],
            planner=planner,
        )
    roster_payload = _extract_roster_payload(stdout)
    if not roster_payload:
        return AgenticWorkerProduction(
            status="blocked",
            blocking_findings=[{"reason": "agentic_roster_missing"}],
            planner=planner,
        )
    workers = roster_payload.get("workers")
    if not isinstance(workers, list):
        return AgenticWorkerProduction(
            status="blocked",
            blocking_findings=[{"reason": "agentic_roster_workers_not_list"}],
            planner=planner,
        )
    roster: list[dict[str, Any]] = []
    for index, worker in enumerate(workers):
        if not isinstance(worker, dict):
            return AgenticWorkerProduction(
                status="blocked",
                blocking_findings=[{"reason": "agentic_roster_worker_not_object", "index": index}],
                planner=planner,
            )
        roster.append(_normalise_roster_item(worker, index=index))
    return AgenticWorkerProduction(status="passed", roster=roster, planner=planner)


def validate_agentic_worker_roster(
    roster: list[AgenticWorkerRosterItem],
    *,
    min_subagents: int,
    required_roles: list[str],
    timeout_s: int,
    budget_usd: float,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if len(roster) < max(0, int(min_subagents)):
        findings.append({"reason": "insufficient_roster_workers", "observed": len(roster), "required": min_subagents})
    if len(roster) > AGENTIC_WORKER_MAX_SUBAGENTS:
        findings.append({"reason": "too_many_roster_workers", "observed": len(roster), "cap": AGENTIC_WORKER_MAX_SUBAGENTS})

    role_keys = {
        _role_key(item.role)
        for item in roster
        if _role_key(item.role)
    }
    for role in required_roles:
        required = _role_key(role)
        if required and required not in role_keys:
            findings.append({"reason": "missing_required_roster_role", "role": required})

    per_worker_budget_cap = float(budget_usd) if budget_usd > 0 else 0.0
    for item in roster:
        mode = _permission_key(item.permission_mode)
        if mode not in {_permission_key(value) for value in READ_ONLY_PERMISSION_MODES}:
            findings.append({
                "reason": "worker_permission_mode_not_read_only",
                "worker_id": item.worker_id,
                "permission_mode": item.permission_mode,
            })
        if not item.prompt.strip():
            findings.append({"reason": "worker_missing_prompt", "worker_id": item.worker_id})
        if int(item.timeout_s) <= 0 or int(item.timeout_s) > int(timeout_s):
            findings.append({
                "reason": "worker_timeout_out_of_bounds",
                "worker_id": item.worker_id,
                "timeout_s": item.timeout_s,
                "cap": timeout_s,
            })
        if per_worker_budget_cap > 0 and float(item.budget_usd) > per_worker_budget_cap:
            findings.append({
                "reason": "worker_budget_out_of_bounds",
                "worker_id": item.worker_id,
                "budget_usd": item.budget_usd,
                "cap": per_worker_budget_cap,
            })
    return findings


def _planner_command(
    *,
    task_id: str,
    run_id: str,
    intent: str,
    min_subagents: int,
    required_roles: list[str],
    timeout_s: int,
    budget_usd: float,
    quality: ModelQuality,
) -> list[str]:
    model = select_lead_model("execution", quality=quality)
    prompt = (
        f"/lead Agentic worker roster planning. Task id: {task_id}. Run id: {run_id}.\n"
        "Plan read-only helper workers for Codex to spawn. Do not edit files and do not spawn workers yourself.\n"
        f"Intent:\n{intent.strip()}\n\n"
        f"Minimum workers: {max(0, int(min_subagents))}.\n"
        f"Required roles: {', '.join(required_roles) if required_roles else 'none'}.\n"
        f"Maximum per-worker timeout: {int(timeout_s)} seconds. Maximum per-worker budget: {float(budget_usd):.2f} USD.\n"
        "Return only a short note plus <agentic_worker_roster>{...}</agentic_worker_roster> JSON. "
        "The JSON shape is {\"workers\":[{\"worker_id\":\"audit-1\",\"role\":\"codebase_audit\","
        "\"persona_id\":\"reviewer.codebase_audit\",\"permission_mode\":\"readOnly\","
        "\"tool_pins\":[\"rg\",\"sed\"],\"prompt\":\"specific read-only task\","
        "\"timeout_s\":300,\"budget_usd\":1.0}]}."
    )
    return [
        "claude",
        "--no-session-persistence",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-budget-usd",
        _format_budget(min(float(budget_usd), 1.0) if budget_usd > 0 else 0.25),
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Grep,Glob,Bash",
    ]


def _worker_spec(
    item: AgenticWorkerRosterItem,
    *,
    cwd: str | Path,
    task_id: str,
    quality: ModelQuality,
) -> AgenticWorkerSpec:
    return AgenticWorkerSpec(
        task_id=task_id,
        worker_id=item.worker_id,
        role=item.role,
        persona_id=item.persona_id,
        agent_runtime="claude_code",
        agent_id=item.worker_id,
        permission_mode="readOnly",
        tool_pins=item.tool_pins,
        timeout_s=int(item.timeout_s),
        budget_usd=float(item.budget_usd),
        cwd=cwd,
        command=tuple(_worker_command(item, task_id=task_id, quality=quality)),
    )


def _worker_command(item: AgenticWorkerRosterItem, *, task_id: str, quality: ModelQuality) -> list[str]:
    model = select_lead_model("execution", quality=quality)
    prompt = (
        f"Read-only agentic worker for task {task_id}. Role: {item.role}.\n"
        "Do not edit files, write source, or launch subagents. Use read-only inspection only.\n"
        f"Scoped prompt:\n{item.prompt.strip()}\n\n"
        "Return concise findings and mention exact files or commands inspected."
    )
    return [
        "claude",
        "--no-session-persistence",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--max-budget-usd",
        _format_budget(float(item.budget_usd)),
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Grep,Glob,Bash",
        "--disallowedTools",
        "Edit,Write,MultiEdit,NotebookEdit",
    ]


def _extract_roster_payload(stdout: str) -> dict[str, Any] | None:
    text = stdout
    try:
        wrapper = json.loads(stdout)
        if isinstance(wrapper, dict) and isinstance(wrapper.get("workers"), list):
            return wrapper
        if isinstance(wrapper, dict) and isinstance(wrapper.get("result"), str):
            text = wrapper["result"]
    except json.JSONDecodeError:
        pass
    match = ROSTER_BLOCK_RE.search(text)
    if match:
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
    return _extract_embedded_roster_json(text)


def _extract_embedded_roster_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("workers"), list):
            return payload
    return None


def _normalise_roster_item(worker: dict[str, Any], *, index: int) -> dict[str, Any]:
    role = str(worker.get("role") or f"agentic_worker_{index + 1}").strip()
    worker_id = str(worker.get("worker_id") or worker.get("id") or role or f"worker-{index + 1}").strip()
    tool_pins = worker.get("tool_pins") or worker.get("tools") or DEFAULT_WORKER_TOOL_PINS
    if isinstance(tool_pins, str):
        tool_values = tuple(item.strip() for item in tool_pins.split(",") if item.strip())
    elif isinstance(tool_pins, (list, tuple, set)):
        tool_values = tuple(str(item).strip() for item in tool_pins if str(item).strip())
    else:
        tool_values = DEFAULT_WORKER_TOOL_PINS
    return {
        "worker_id": _safe_segment(worker_id),
        "role": role,
        "persona_id": str(worker.get("persona_id") or worker.get("persona") or role).strip(),
        "permission_mode": str(worker.get("permission_mode") or "readOnly").strip(),
        "tool_pins": tool_values or DEFAULT_WORKER_TOOL_PINS,
        "prompt": str(worker.get("prompt") or worker.get("scoped_prompt") or "").strip(),
        "timeout_s": _int_or_default(worker.get("timeout_s"), 300),
        "budget_usd": _float_or_default(worker.get("budget_usd"), 1.0),
    }


def _agentic_subagent_receipts(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subagent_receipts: list[dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        kind = str(receipt.get("kind") or receipt.get("type") or "").strip().lower()
        if kind in {"dynamic_subagent_result", "dynamic_reviewer_result", "subagent_result"}:
            subagent_receipts.append(receipt)
        subagents = receipt.get("subagents")
        if isinstance(subagents, list):
            subagent_receipts.extend(item for item in subagents if isinstance(item, dict))
    return subagent_receipts


def _completed_agentic_receipts(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        receipt
        for receipt in _agentic_subagent_receipts(receipts)
        if _receipt_status(receipt) in {"passed", "pass", "accepted", "accept", "ok", "success", "succeeded"}
    ]


def _completed_role_keys(receipts: list[dict[str, Any]]) -> set[str]:
    role_keys: set[str] = set()
    for receipt in receipts:
        for value in (
            receipt.get("role"),
            receipt.get("persona_id"),
            receipt.get("task_id"),
        ):
            key = _role_key(str(value or ""))
            if key:
                role_keys.add(key)
                role_keys.update(part for part in key.split(".") if part)
    return role_keys


def _completed_worker_ids(receipts: list[dict[str, Any]]) -> set[str]:
    return {
        str(receipt.get("worker_id") or receipt.get("agent_id") or receipt.get("task_id") or "").strip()
        for receipt in receipts
        if str(receipt.get("worker_id") or receipt.get("agent_id") or receipt.get("task_id") or "").strip()
    }


def _pending_roster_items(
    roster: list[AgenticWorkerRosterItem],
    completed_receipts: list[dict[str, Any]],
) -> list[AgenticWorkerRosterItem]:
    completed_workers = _completed_worker_ids(completed_receipts)
    completed_roles = _completed_role_keys(completed_receipts)
    pending: list[AgenticWorkerRosterItem] = []
    for item in roster:
        worker_key = str(item.worker_id).strip()
        role_key = _role_key(item.role)
        persona_key = _role_key(item.persona_id)
        if worker_key and worker_key in completed_workers:
            continue
        if role_key and role_key in completed_roles:
            continue
        if persona_key and persona_key in completed_roles:
            continue
        pending.append(item)
    return pending


def _receipt_status(receipt: dict[str, Any]) -> str:
    return str(receipt.get("status") or receipt.get("result") or receipt.get("decision") or "").strip().lower()


def _has_lead_solo_receipt(receipts: list[dict[str, Any]]) -> bool:
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        kind = str(receipt.get("kind") or receipt.get("type") or "").strip().lower()
        if kind not in {"agentic_lead_execution", "lead_execution"}:
            continue
        mode = str(receipt.get("execution_mode") or receipt.get("mode") or "").strip().lower()
        if mode in {"solo", "self", "single_worker", "lead_direct"}:
            return True
    return False


def _role_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _permission_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value).strip())
    return cleaned.strip(".-") or "worker"


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_budget(value: float) -> str:
    return f"{max(0.01, float(value)):.2f}"
