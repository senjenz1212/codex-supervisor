from __future__ import annotations

import json
import subprocess
from pathlib import Path

from supervisor.agentic_executor import (
    AgenticWorkerRosterItem,
    _extract_roster_payload,
    produce_agentic_worker_receipts,
    validate_agentic_worker_roster,
)


def test_agentic_roster_validation_rejects_over_budget_or_timeout_before_launch(tmp_path: Path):
    roster = {
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "persona_id": "reviewer.codebase_audit",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Inspect code paths only.",
                "timeout_s": 600,
                "budget_usd": 5.0,
            }
        ]
    }
    launch_calls: list[list[str]] = []

    def fake_planner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>",
            stderr="",
        )

    def fail_if_launched(specs):
        launch_calls.append([spec.worker_id for spec in specs])
        return []

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Require bounded read-only agentic workers.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 1,
            "required_roles": ["codebase_audit"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[],
        timeout_s=60,
        budget_usd=0.25,
        runner=fake_planner,
        fanout_runner=fail_if_launched,
    )

    assert production.status == "blocked"
    assert launch_calls == []
    reasons = {finding["reason"] for finding in production.blocking_findings}
    assert "worker_timeout_out_of_bounds" in reasons
    assert "worker_budget_out_of_bounds" in reasons


def test_agentic_roster_parser_accepts_direct_or_embedded_json_roster():
    direct = _extract_roster_payload(json.dumps({
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "prompt": "Inspect code.",
            }
        ]
    }))
    embedded = _extract_roster_payload(json.dumps({
        "result": (
            "Roster:\n"
            "{\"workers\":[{\"worker_id\":\"audit-2\","
            "\"role\":\"codebase_audit\",\"prompt\":\"Inspect receipts.\"}]}"
        )
    }))

    assert direct is not None
    assert direct["workers"][0]["worker_id"] == "audit-1"
    assert embedded is not None
    assert embedded["workers"][0]["worker_id"] == "audit-2"


def test_agentic_roster_validation_rejects_writable_or_missing_required_roles():
    findings = validate_agentic_worker_roster(
        [
            AgenticWorkerRosterItem(
                worker_id="writer-1",
                role="codebase_audit",
                prompt="Inspect implementation boundaries.",
                permission_mode="bypassPermissions",
                timeout_s=30,
                budget_usd=0.1,
            )
        ],
        min_subagents=1,
        required_roles=["codebase_audit", "independent_reviewer"],
        timeout_s=60,
        budget_usd=0.25,
    )

    reasons = {finding["reason"] for finding in findings}
    assert "worker_permission_mode_not_read_only" in reasons
    assert "missing_required_roster_role" in reasons
    assert any(finding.get("role") == "independent_reviewer" for finding in findings)


def test_agentic_worker_timeout_cleanup_runs_after_fanout_timeout(tmp_path: Path):
    roster = {
        "workers": [
            {
                "worker_id": "review-1",
                "role": "independent_reviewer",
                "persona_id": "reviewer.independent",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Review the fanout receipts.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            }
        ]
    }
    cleanup_calls: list[dict[str, object]] = []

    def fake_planner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>",
            stderr="",
        )

    def timeout_fanout(specs):
        spec = specs[0]
        return [
            {
                "kind": "dynamic_subagent_result",
                "worker_id": spec.worker_id,
                "role": spec.role,
                "status": "timeout",
                "decision": "revise",
                "timeout_s": spec.timeout_s,
                "budget_usd": spec.budget_usd,
                "log_ref": ".handoff/agentic-workers/workflow-1/review-1/worker.log",
            }
        ]

    def cleanup_runner(**kwargs):
        cleanup_calls.append(kwargs)
        return {
            "schema_version": "agentic-worker-cleanup/v1",
            "status": "cleanup_completed",
            "cleaned": [{"worker_id": "review-1", "reason": "timeout_exceeded"}],
            "active": [],
            "skipped": [],
        }

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Require cleanup after timeout.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 1,
            "required_roles": ["independent_reviewer"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[],
        timeout_s=60,
        budget_usd=0.25,
        runner=fake_planner,
        fanout_runner=timeout_fanout,
        cleanup_runner=cleanup_runner,
        now_s=lambda: 100.0,
    )

    assert production.status == "passed"
    assert cleanup_calls
    assert cleanup_calls[0]["workers"][0]["worker_id"] == "review-1"
    assert cleanup_calls[0]["workers"][0]["started_at_s"] < 100.0
    assert production.cleanup is not None
    assert production.cleanup["cleaned"][0]["reason"] == "timeout_exceeded"


def test_produce_agentic_worker_receipts_reuses_completed_workers_and_spawns_missing_roles(tmp_path: Path):
    roster = {
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "persona_id": "reviewer.codebase_audit",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Inspect implementation boundaries.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            },
            {
                "worker_id": "review-1",
                "role": "independent_reviewer",
                "persona_id": "reviewer.independent",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Review hydrated evidence.",
                "timeout_s": 30,
                "budget_usd": 0.1,
            },
        ]
    }
    existing = {
        "kind": "dynamic_subagent_result",
        "receipt_id": "agentic-worker-audit-1",
        "worker_id": "audit-1",
        "role": "codebase_audit",
        "persona_id": "reviewer.codebase_audit",
        "status": "passed",
        "decision": "accept",
        "agent_runtime": "claude_code",
        "agent_id": "audit-1",
        "permission_mode": "readOnly",
        "tool_pins": ["rg", "sed"],
    }
    launched: list[str] = []

    def fake_planner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>",
            stderr="",
        )

    def fanout_runner(specs):
        launched.extend(spec.worker_id for spec in specs)
        return [
            {
                "kind": "dynamic_subagent_result",
                "worker_id": spec.worker_id,
                "role": spec.role,
                "status": "passed",
                "decision": "accept",
            }
            for spec in specs
        ]

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reuse completed workers and run missing roles only.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 2,
            "required_roles": ["codebase_audit", "independent_reviewer"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[existing],
        timeout_s=60,
        budget_usd=0.25,
        runner=fake_planner,
        fanout_runner=fanout_runner,
    )

    assert production.status == "passed"
    assert launched == ["review-1"]
    assert production.planner["existing_completed_receipt_count"] == 1
    assert production.planner["skipped_completed_worker_ids"] == ["audit-1"]


def test_produce_agentic_worker_receipts_skips_when_existing_receipts_satisfy_policy(tmp_path: Path):
    existing = {
        "kind": "dynamic_subagent_result",
        "receipt_id": "agentic-worker-audit-1",
        "worker_id": "audit-1",
        "role": "codebase_audit",
        "status": "passed",
        "decision": "accept",
    }
    planner_calls: list[list[str]] = []

    def fail_if_planned(argv, **kwargs):
        planner_calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    production = produce_agentic_worker_receipts(
        cwd=tmp_path,
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Existing receipt already satisfies required policy.",
        agentic_policy={
            "agentic_lead_policy": "required",
            "min_subagents": 1,
            "required_roles": ["codebase_audit"],
            "required_evidence_grade": "runtime_native",
        },
        existing_receipts=[existing],
        timeout_s=60,
        budget_usd=0.25,
        runner=fail_if_planned,
    )

    assert production.status == "skipped_existing_receipts"
    assert planner_calls == []
