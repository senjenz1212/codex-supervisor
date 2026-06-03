"""Bridge curated agentic eval cases into recorded three-arm datasets.

The assembler is report-only. It can call an injected workflow runner to record
real arm outcomes, but it never changes the configured default agentic policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from .agentic_eval import DATASET_SCHEMA_VERSION, REQUIRED_MODES


MODE_TO_POLICY = {
    "lead_direct": "off",
    "agentic_allowed": "allowed",
    "agentic_required": "required",
}
BRIDGE_SCHEMA_VERSION = "agentic-lead-eval-bridge/v1"
DEFAULT_MIN_SUBAGENTS = 1
WorkflowRunner = Callable[..., dict[str, Any]]


def compute_arm_budget_split(
    *,
    mode: str,
    total_tokens: int | float,
    total_usd: int | float,
    min_subagents: int = DEFAULT_MIN_SUBAGENTS,
) -> dict[str, Any]:
    """Return a fair per-arm budget split without adding worker budget."""
    policy = _policy_for_mode(mode)
    tokens = int(total_tokens)
    usd = float(total_usd)
    if tokens <= 0 or usd <= 0:
        raise ValueError("agentic eval bridge budget must be positive")
    worker_count = max(0, int(min_subagents)) if policy in {"allowed", "required"} else 0
    participant_count = 1 + worker_count
    lead_tokens = tokens if worker_count == 0 else max(1, tokens // participant_count)
    worker_tokens_each = 0 if worker_count == 0 else max(1, (tokens - lead_tokens) // worker_count)
    worker_tokens_total = worker_tokens_each * worker_count
    if lead_tokens + worker_tokens_total > tokens:
        worker_tokens_total = max(0, tokens - lead_tokens)
        worker_tokens_each = worker_tokens_total // max(1, worker_count)

    lead_budget_usd = usd if worker_count == 0 else round(usd / participant_count, 6)
    worker_budget_usd_each = 0.0 if worker_count == 0 else round((usd - lead_budget_usd) / worker_count, 6)
    worker_budget_usd_total = round(worker_budget_usd_each * worker_count, 6)
    if lead_budget_usd + worker_budget_usd_total > usd:
        worker_budget_usd_total = round(max(0.0, usd - lead_budget_usd), 6)
        worker_budget_usd_each = round(worker_budget_usd_total / max(1, worker_count), 6)

    return {
        "schema_version": "agentic-lead-eval-budget-split/v1",
        "mode": mode,
        "agentic_lead_policy": policy,
        "total_tokens": tokens,
        "total_usd": usd,
        "lead_tokens": lead_tokens,
        "lead_budget_usd": lead_budget_usd,
        "worker_count": worker_count,
        "worker_tokens_each": worker_tokens_each,
        "worker_tokens_total": worker_tokens_total,
        "worker_budget_usd_each": worker_budget_usd_each,
        "worker_budget_usd_total": worker_budget_usd_total,
        "workflow_budget_usd": lead_budget_usd,
        "budget_sum_usd": min(usd, round(lead_budget_usd + worker_budget_usd_total, 6)),
        "budget_sum_tokens": lead_tokens + worker_tokens_total,
    }


def assemble_agentic_eval_task(
    *,
    case: dict[str, Any],
    workflow_runner: WorkflowRunner,
    cassette_dir: str | Path,
    repo_root: str | Path | None = None,
    run_id_prefix: str = "agentic-eval-bridge",
    min_subagents: int = DEFAULT_MIN_SUBAGENTS,
) -> dict[str, Any]:
    """Run one labeled case through all required modes and return a dataset task."""
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    cassette_root = Path(cassette_dir)
    task_id = str(case.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("agentic eval bridge case is missing task_id")
    budget = case.get("budget") if isinstance(case.get("budget"), dict) else {}
    total_tokens = int(budget.get("total_tokens") or 0)
    total_usd = float(budget.get("total_usd") or 0)
    required_verdicts = _dataset_required_verdicts(case)
    task: dict[str, Any] = {
        "schema_version": "agentic-lead-eval-task/v1",
        "task_id": task_id,
        "intent": str(case.get("intent") or ""),
        "required_verdicts": required_verdicts,
        "arms": {},
        "source_case": {
            "repo": (case.get("case") or {}).get("repo"),
            "commit": (case.get("case") or {}).get("commit"),
            "expected_outcome": (case.get("case") or {}).get("expected_outcome"),
            "provenance": case.get("provenance") or {},
        },
    }

    for mode in REQUIRED_MODES:
        split = compute_arm_budget_split(
            mode=mode,
            total_tokens=total_tokens,
            total_usd=total_usd,
            min_subagents=min_subagents,
        )
        arm: dict[str, Any] = {
            "budget": {
                "token_budget": total_tokens,
                "budget_usd_limit": total_usd,
            },
            "budget_split": split,
            "workflow_request": _workflow_request_for_arm(
                case=case,
                mode=mode,
                run_id_prefix=run_id_prefix,
                split=split,
                cwd=root,
            ),
        }
        started = time.monotonic()
        workflow_result = workflow_runner(task=task, mode=mode, arm=arm)
        if not isinstance(workflow_result, dict):
            raise ValueError("agentic eval bridge workflow_runner must return a dict")
        elapsed = time.monotonic() - started
        cassette_ref = _write_arm_cassette(
            workflow_result,
            cassette_dir=cassette_root,
            task_id=task_id,
            mode=mode,
            repo_root=root,
        )
        arm["workflow_result"] = workflow_result
        arm["metrics"] = _metrics_from_workflow_result(workflow_result, elapsed_s=elapsed)
        arm["verdict_evidence"] = _verdict_evidence_from_case(
            case=case,
            workflow_result=workflow_result,
            cassette_ref=cassette_ref,
        )
        arm["cassette_ref"] = cassette_ref
        arm["recording"] = {
            "schema_version": "agentic-lead-eval-arm-recording/v1",
            "source": "workflow_runner",
            "hand_authored": False,
        }
        task["arms"][mode] = arm

    assert_equal_arm_total_budgets(task)
    return task


def assemble_agentic_eval_dataset(
    *,
    cases: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    workflow_runner: WorkflowRunner,
    cassette_dir: str | Path,
    repo_root: str | Path | None = None,
    run_id_prefix: str = "agentic-eval-bridge",
    min_subagents: int = DEFAULT_MIN_SUBAGENTS,
) -> dict[str, Any]:
    """Assemble a replayable three-arm dataset from labeled cases."""
    tasks = [
        assemble_agentic_eval_task(
            case=case,
            workflow_runner=workflow_runner,
            cassette_dir=cassette_dir,
            repo_root=repo_root,
            run_id_prefix=run_id_prefix,
            min_subagents=min_subagents,
        )
        for case in cases
    ]
    dataset = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "bridge": {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "hand_authored_arms": False,
            "required_modes": list(REQUIRED_MODES),
            "default_change_allowed": False,
            "agentic_lead_policy_default_mutated": False,
        },
        "tasks": tasks,
    }
    for task in tasks:
        assert_equal_arm_total_budgets(task)
    dataset["sha256"] = _sha256_json({k: v for k, v in dataset.items() if k != "sha256"})
    return dataset


def assert_equal_arm_total_budgets(task: dict[str, Any]) -> None:
    arms = task.get("arms")
    if not isinstance(arms, dict):
        raise ValueError("agentic eval bridge task requires arms")
    budgets = {}
    for mode in REQUIRED_MODES:
        arm = arms.get(mode)
        if not isinstance(arm, dict):
            raise ValueError(f"agentic eval bridge task {task.get('task_id')} missing mode {mode}")
        budget = arm.get("budget") if isinstance(arm.get("budget"), dict) else {}
        budgets[mode] = {
            "token_budget": int(budget.get("token_budget") or 0),
            "budget_usd_limit": float(budget.get("budget_usd_limit") or 0),
        }
        split = arm.get("budget_split") if isinstance(arm.get("budget_split"), dict) else {}
        if split:
            if int(split.get("budget_sum_tokens") or 0) > budgets[mode]["token_budget"]:
                raise ValueError(f"agentic eval bridge task {task.get('task_id')} budget split exceeds token total")
            if float(split.get("budget_sum_usd") or 0.0) > budgets[mode]["budget_usd_limit"] + 0.000001:
                raise ValueError(f"agentic eval bridge task {task.get('task_id')} budget split exceeds dollar total")
    unique = {json.dumps(value, sort_keys=True) for value in budgets.values()}
    if len(unique) != 1:
        raise ValueError(f"agentic eval bridge task {task.get('task_id')} has unequal arm budgets: {budgets}")


def write_agentic_eval_dataset(dataset: dict[str, Any], path: str | Path) -> Path:
    output = Path(path)
    for task in dataset.get("tasks") or []:
        if isinstance(task, dict):
            assert_equal_arm_total_budgets(task)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(dataset, sort_keys=False), encoding="utf-8")
    return output


def select_labeled_cases(
    labeled_set: dict[str, Any],
    *,
    task_ids: list[str] | tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    tasks = list(labeled_set.get("tasks") or [])
    if task_ids:
        wanted = list(task_ids)
        by_id = {str(task.get("task_id")): task for task in tasks if isinstance(task, dict)}
        missing = [task_id for task_id in wanted if task_id not in by_id]
        if missing:
            raise ValueError(f"unknown labeled eval task ids: {missing}")
        tasks = [by_id[task_id] for task_id in wanted]
    if limit is not None:
        tasks = tasks[: max(0, int(limit))]
    return tasks


class CliDualAgentWorkflowRunner:
    """Callable adapter that records real arm results via the workflow CLI."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        output_dir: str | Path,
        quality: str = "best",
        timeout_s: int = 900,
        cursor_review_profile: str = "rigorous",
        runner: Any = subprocess.run,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.output_dir = Path(output_dir)
        self.quality = quality
        self.timeout_s = int(timeout_s)
        self.cursor_review_profile = cursor_review_profile
        self.runner = runner

    def __call__(self, *, task: dict[str, Any], mode: str, arm: dict[str, Any]) -> dict[str, Any]:
        request = dict(arm["workflow_request"])
        request.update({
            "quality": self.quality,
            "timeout_s": self.timeout_s,
            "cursor_review": True,
            "cursor_review_profile": self.cursor_review_profile,
            "require_skill_receipts": False,
        })
        request_dir = self.output_dir / "requests"
        request_dir.mkdir(parents=True, exist_ok=True)
        request_path = request_dir / f"{task['task_id']}--{mode}.request.json"
        output_path = request_dir / f"{task['task_id']}--{mode}.workflow-result.json"
        request_path.write_text(json.dumps(request, sort_keys=True, indent=2) + "\n", encoding="utf-8")

        command = [
            "uv",
            "run",
            "python",
            "-m",
            "mcp_tools.codex_supervisor_workflow_cli",
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ]
        started = time.monotonic()
        proc = self.runner(
            command,
            cwd=str(self.repo_root),
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_s + 120,
        )
        elapsed = time.monotonic() - started
        if not output_path.exists():
            raise RuntimeError(
                f"workflow CLI did not write {output_path}: returncode={proc.returncode} stderr={proc.stderr}"
            )
        result = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError(f"workflow CLI result for {task['task_id']}/{mode} must be an object")
        result.setdefault("metrics", {})
        if isinstance(result["metrics"], dict):
            result["metrics"].setdefault("wall_clock_s", round(elapsed, 3))
        result["agentic_eval_bridge"] = {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "mode": mode,
            "request_path": str(request_path),
            "raw_output_path": str(output_path),
            "returncode": proc.returncode,
        }
        return result


def _workflow_request_for_arm(
    *,
    case: dict[str, Any],
    mode: str,
    run_id_prefix: str,
    split: dict[str, Any],
    cwd: Path,
) -> dict[str, Any]:
    task_id = str(case.get("task_id") or "case")
    policy = _policy_for_mode(mode)
    return {
        "cwd": str(cwd),
        "task_id": f"{run_id_prefix}-{task_id}-{mode}",
        "run_id": f"{run_id_prefix}-{task_id}-{mode}-{_short_hash(task_id + mode)}",
        "intent": str(case.get("intent") or ""),
        "execution_layer_mode": "lead_direct",
        "agentic_lead_policy": policy,
        "budget_usd": float(split["workflow_budget_usd"]),
        "min_subagents": int(split["worker_count"]),
        "required_roles": [],
        "user_facing": False,
        "task_complexity": "large",
    }


def _dataset_required_verdicts(case: dict[str, Any]) -> list[dict[str, str]]:
    verdicts = []
    for item in case.get("required_verdicts") or []:
        if not isinstance(item, dict):
            continue
        verdict_id = str(item.get("id") or "").strip()
        if not verdict_id:
            continue
        verdicts.append({
            "id": verdict_id,
            "description": str(item.get("description") or verdict_id),
        })
    if not verdicts:
        raise ValueError(f"agentic eval bridge case {case.get('task_id')} has no required verdicts")
    return verdicts


def _write_arm_cassette(
    workflow_result: dict[str, Any],
    *,
    cassette_dir: Path,
    task_id: str,
    mode: str,
    repo_root: Path,
) -> str:
    cassette_dir.mkdir(parents=True, exist_ok=True)
    path = cassette_dir / f"{task_id}--{mode}.json"
    path.write_text(json.dumps(workflow_result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _verdict_evidence_from_case(
    *,
    case: dict[str, Any],
    workflow_result: dict[str, Any],
    cassette_ref: str,
) -> dict[str, list[dict[str, str]]]:
    status = "passed" if _workflow_matches_expected(case=case, workflow_result=workflow_result) else "failed"
    evidence: dict[str, list[dict[str, str]]] = {}
    for item in case.get("required_verdicts") or []:
        if not isinstance(item, dict):
            continue
        verdict_id = str(item.get("id") or "").strip()
        kind = str(item.get("evidence_kind") or "artifact_path").strip()
        ref = str(item.get("evidence_ref") or "").strip()
        if not verdict_id:
            continue
        records = []
        if kind and ref:
            records.append({"kind": kind, "ref": ref, "status": status})
        records.append({"kind": "artifact_path", "ref": cassette_ref, "status": status})
        evidence[verdict_id] = records
    return evidence


def _workflow_matches_expected(*, case: dict[str, Any], workflow_result: dict[str, Any]) -> bool:
    expected = str((case.get("case") or {}).get("expected_outcome") or "").strip().lower()
    if expected not in {"accept", "revise", "deny"}:
        expected = str((case.get("provenance") or {}).get("source_gate_result") or "").strip().lower()
    final = workflow_result.get("final_gate_result")
    final = final if isinstance(final, dict) else {}
    terminal_values = {
        str(workflow_result.get("status") or "").strip().lower(),
        str(final.get("status") or "").strip().lower(),
        str(final.get("supervisor_final_status") or "").strip().lower(),
    }
    decision_values = {
        str(final.get("codex_decision") or "").strip().lower(),
        str(final.get("claude_decision") or "").strip().lower(),
        str(final.get("cursor_decision") or "").strip().lower(),
    }
    if expected == "accept":
        return bool(terminal_values & {"accept", "accepted"})
    if expected in {"revise", "deny"}:
        return expected in decision_values or bool(terminal_values & {"blocked", "denied"})
    return False


def _metrics_from_workflow_result(workflow_result: dict[str, Any], *, elapsed_s: float) -> dict[str, Any]:
    source_metrics = workflow_result.get("metrics") if isinstance(workflow_result.get("metrics"), dict) else {}
    return {
        "wall_clock_s": _number(source_metrics.get("wall_clock_s"), default=round(elapsed_s, 3)),
        "cost_usd": _number(source_metrics.get("cost_usd"), default=_sum_key(workflow_result, "cost_usd")),
        "retries": _number(source_metrics.get("retries"), default=_retry_count(workflow_result)),
        "rejected_gates": _number(source_metrics.get("rejected_gates"), default=_rejected_gate_count(workflow_result)),
        "missed_issues": _number(source_metrics.get("missed_issues"), default=0),
        "operator_interventions": _number(source_metrics.get("operator_interventions"), default=0),
    }


def _retry_count(workflow_result: dict[str, Any]) -> int:
    retries = 0
    for step in workflow_result.get("steps") or []:
        if isinstance(step, dict):
            retries += max(0, int(_number(step.get("attempt_count") or step.get("attempts"), default=1)) - 1)
    return retries


def _rejected_gate_count(workflow_result: dict[str, Any]) -> int:
    rejected = 0
    for step in workflow_result.get("steps") or []:
        if isinstance(step, dict) and str(step.get("status") or "").strip().lower() not in {"accept", "accepted"}:
            rejected += 1
    final = workflow_result.get("final_gate_result")
    if isinstance(final, dict):
        for payload in (final.get("probes") or {}).values():
            if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() not in {
                "green",
                "pass",
                "passed",
                "ok",
                "accept",
                "accepted",
            }:
                rejected += 1
    return rejected


def _sum_key(value: Any, key: str) -> float:
    total = 0.0
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key:
                total += float(_number(item_value))
            else:
                total += _sum_key(item_value, key)
    elif isinstance(value, list):
        for item in value:
            total += _sum_key(item, key)
    return round(total, 6)


def _policy_for_mode(mode: str) -> str:
    if mode not in MODE_TO_POLICY:
        raise ValueError(f"unknown agentic eval mode: {mode}")
    return MODE_TO_POLICY[mode]


def _number(value: Any, *, default: int | float = 0) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return int(parsed) if parsed.is_integer() else parsed


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
