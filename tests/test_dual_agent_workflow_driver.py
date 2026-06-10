from __future__ import annotations

import asyncio
import inspect
import json
import multiprocessing
import os
import re
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.config import Config
from supervisor.cursor_agent import CursorInvocationRequest, CursorInvocationResult
from supervisor.dual_agent import Outcome, ProbeResult
from supervisor.dual_agent_workflow import (
    cursor_review_gates_for_workflow,
    select_workflow_route,
    workflow_visual_evidence_policy,
)
from supervisor.dual_agent_lead import DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES
from supervisor.state import State


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


def _reserve_same_workflow_token_process(
    db_path: str,
    barrier: multiprocessing.synchronize.Barrier,
    queue: multiprocessing.Queue,
    index: int,
) -> None:
    state = State(db_path)
    barrier.wait()
    row, created = state.reserve_dual_agent_workflow_job(
        job_id=f"job-{index}",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd="/tmp",
        status="submitted",
        request_path=f"/tmp/request-{index}.json",
        result_path=f"/tmp/result-{index}.json",
        log_path=f"/tmp/log-{index}.txt",
        idempotency_token="same-process-token",
        request_payload_json=json.dumps({
            "cwd": "/tmp",
            "task_id": "workflow-1",
            "run_id": "workflow-run",
            "intent": "race",
        }),
    )
    queue.put((index, bool(created), row["job_id"], row["recovery_point"]))


class _FakeMCP:
    def __init__(self, name: str):
        self.name = name
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


class _Notifier:
    def __init__(self):
        self.messages: list[str] = []
        self.prompts: list[dict] = []

    async def send_message(self, text: str, **kwargs):
        self.messages.append(text)
        return {"ok": True}

    async def send_approval_prompt(self, **kwargs):
        self.prompts.append(kwargs)
        return {"ok": True}


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _cfg(tmp_path: Path) -> Config:
    return Config(**{
        "target": {
            "kind": "codex",
            "codex": {
                "sessions_root": str(tmp_path / "sessions"),
                "cli_command": "codex",
            },
        },
        "orchestrator": {"run_registry_dir": str(tmp_path / "runs")},
        "supervisor": {"state_db": str(tmp_path / "state.db")},
        "models": {
            "realtime_critique_model": "claude-haiku-4-5",
            "drift_l3_model": "claude-haiku-4-5",
            "drift_l4_model": "claude-sonnet-4-6",
            "post_run_eval_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
        },
        "telegram": {"bot_token": "fake", "chat_id": "42"},
    })


def _outcome_block(
    task_id: str,
    *,
    decision: str = "accept",
    claims: list[str] | None = None,
    changed_files: list[str] | None = None,
    tests: list[str] | None = None,
    test_status: str = "passed",
) -> str:
    payload = {
        "task_id": task_id,
        "summary": "Workflow response complete.",
        "specialists": [{"name": "Planner", "decision": decision}],
        "decisions": [decision],
        "objections": [] if decision == "accept" else ["Needs another revision."],
        "changed_files": changed_files
        if changed_files is not None else [
            "supervisor/dual_agent_workflow.py",
            "tests/test_dual_agent_workflow_driver.py",
        ],
        "tests": tests
        if tests is not None else [f"{sys.executable} -c \"pass\""],
        "test_status": test_status,
        "confidence": 0.94,
        "claims": claims or ["tests passed", "implemented"],
    }
    return f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"


def _tiny_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde"
    )


def _tool_receipts(*, include_push: bool = False) -> list[dict]:
    receipts = [
        *_skill_receipts(),
        {
            "receipt_id": "pytest-focused",
            "kind": "test",
            "status": "passed",
            "command": f"{sys.executable} -c \"pass\"",
            "claims": ["tests passed"],
        },
        {
            "receipt_id": "git-diff",
            "kind": "git_diff",
            "status": "present",
            "changed_files": [
                "supervisor/dual_agent_workflow.py",
                "tests/test_dual_agent_workflow_driver.py",
            ],
            "claims": ["implemented"],
        },
    ]
    if include_push:
        receipts.append({
            "receipt_id": "unity-push",
            "kind": "git_remote",
            "status": "pushed",
            "remote": "unity",
            "claims": ["pushed"],
        })
    return receipts


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_dynamic_replay_files(tmp_path: Path, *, suffix: str = "audit-1") -> dict[str, str]:
    files = {
        "manifest_ref": tmp_path / "docs" / "dual-agent" / "workflow-1" / "replay" / "dynamic-workflow-manifest.json",
        "transcript_ref": tmp_path / "artifacts" / "dynamic" / suffix / "transcript.jsonl",
        "output_ref": tmp_path / "artifacts" / "dynamic" / suffix / "output.json",
        "replay_ref": tmp_path / "docs" / "dual-agent" / "workflow-1" / "replay" / f"{suffix}-manifest.json",
        "worktree_comparison_ref": tmp_path / "docs" / "dual-agent" / "workflow-1" / f"dynamic-comparison-{suffix}.md",
    }
    contents = {
        "manifest_ref": '{"schema_version":"dynamic-workflow-manifest/v1"}\n',
        "transcript_ref": '{"event":"review","decision":"accept"}\n',
        "output_ref": '{"decision":"accept","changed_files":[]}\n',
        "replay_ref": '{"schema_version":"dual-agent-replay/v1"}\n',
        "worktree_comparison_ref": "# Dynamic Comparison\n\naccepted\n",
    }
    result: dict[str, str] = {}
    for key, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents[key], encoding="utf-8")
        result[key] = str(path.relative_to(tmp_path))
        result[f"{key.removesuffix('_ref')}_sha256"] = _hash_file(path)
    return result


def _dynamic_workflow_receipts(tmp_path: Path | None = None) -> list[dict]:
    refs = _write_dynamic_replay_files(tmp_path) if tmp_path is not None else {}
    shared = {
        "subagents": [
            {
                "task_id": "workflow-1:audit-1",
                "persona_id": "reviewer.codebase_audit",
                "agent_runtime": "supervisor_subprocess",
                "agent_id": "agent-audit-1",
                "timeout_s": 300,
                "budget_usd": 1.5,
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed", "pytest --collect-only"],
                "transcript_ref": refs.get("transcript_ref", "artifacts/dynamic/audit-1/transcript.jsonl"),
                "transcript_sha256": refs.get("transcript_sha256"),
                "output_ref": refs.get("output_ref"),
                "output_sha256": refs.get("output_sha256"),
                "output_hash": f"sha256:{refs['output_sha256']}" if refs else "sha256:abc123",
                "changed_files": [],
            }
        ],
        "manifest_ref": refs.get("manifest_ref"),
        "manifest_sha256": refs.get("manifest_sha256"),
        "supervision_layer": "codex_plus_lead",
        "lead_integrator": "claude_code_lead",
        "output_schema": "dynamic-workflow-output/v1",
        "output_hash": f"sha256:{refs['output_sha256']}" if refs else "sha256:abc123",
        "headless": True,
        "no_session_persistence": True,
        "replay_ref": refs.get("replay_ref", "docs/dual-agent/workflow-1/replay/manifest.json"),
        "replay_sha256": refs.get("replay_sha256"),
        "worktree_comparison_ref": refs.get("worktree_comparison_ref", "docs/dual-agent/workflow-1/dynamic-comparison.md"),
        "worktree_comparison_sha256": refs.get("worktree_comparison_sha256"),
    }
    return [
        {
            "receipt_id": f"dyn-{gate}",
            "kind": "dynamic_workflow_receipt",
            "status": "passed",
            "gate": gate,
            **shared,
        }
        for gate in DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES
    ]


def _dynamic_subagent_result_receipts(
    tmp_path: Path,
    *,
    decision: str = "accept",
    severity: str = "none",
) -> list[dict]:
    refs = _write_dynamic_replay_files(tmp_path)
    return [{
        "receipt_id": "subagent-audit-1",
        "kind": "dynamic_subagent_result",
        "status": "passed",
        "task_id": "workflow-1:audit-1",
        "persona_id": "reviewer.codebase_audit",
        "agent_runtime": "supervisor_subprocess",
        "agent_id": "agent-audit-1",
        "decision": decision,
        "severity": severity,
        "objections": [] if decision == "accept" else ["critical unresolved mismatch"],
        "critical_review": {
            "schema_version": "critical-review/v1",
            "strongest_objection": "none" if decision == "accept" else "critical unresolved mismatch",
            "missing_evidence": [],
            "contradictions_checked": ["manifest registration", "subagent output receipt"],
            "assumptions_to_verify": ["subagent read-only claim matches transcript"],
            "what_would_change_my_mind": "Replay verification contradicts the subagent output.",
            "decision": decision,
            "severity": severity,
        },
        "timeout_s": 300,
        "budget_usd": 1.5,
        "permission_mode": "readOnly",
        "tool_pins": ["rg", "sed", "pytest --collect-only"],
        "output_schema": "dynamic-workflow-output/v1",
        "changed_files": [],
        "headless": True,
        "no_session_persistence": True,
        **refs,
    }]


def _skill_receipts() -> list[dict]:
    return [
        {
            "receipt_id": f"skill-{stage}",
            "kind": "skill_run",
            "status": "passed",
            "skill": skill,
            "stage": stage,
            "claims": ["prd-tdd skill executed"],
        }
        for stage, skill in [
            ("to_prd", "to-prd"),
            ("prd_grill", "grill-with-docs"),
            ("to_issues", "to-issues"),
            ("tdd", "tdd"),
            ("tdd_grill", "grill-with-docs"),
        ]
    ]


def test_workflow_kwargs_from_payload_preserves_dynamic_workflow_preview_fields():
    from mcp_tools.codex_supervisor_workflow_cli import workflow_kwargs_from_payload

    kwargs = workflow_kwargs_from_payload({
        "cwd": "/tmp/workspace",
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run a fan-out task under the supervisor boundary.",
        "execution_layer_mode": "dynamic_workflow_preview",
        "dynamic_workflow_task_class": "codebase_audit",
        "agentic_lead_policy": "required",
        "min_subagents": 2,
        "required_roles": ["codebase_audit", "independent_reviewer"],
        "solo_exception_for_artifact_only_gates": True,
        "required_evidence_grade": "runtime_native",
        "reviewer_unavailable_policy": "proceed_degraded",
        "reviewer_model": "gemini-3.1-pro-preview",
        "reviewer_output_mode": "litellm_structured",
        "reviewer_max_tokens": 4096,
        "reviewer_infra_retry_limit": 4,
        "reviewer_infra_retry_backoff_s": 0.25,
        "reviewer_low_confidence_threshold": 0.4,
        "reviewer_panel_calibration_path": "docs/dual-agent/calibration.json",
        "irrelevant_field": "must not leak into workflow kwargs",
    })

    assert kwargs["execution_layer_mode"] == "dynamic_workflow_preview"
    assert kwargs["dynamic_workflow_task_class"] == "codebase_audit"
    assert kwargs["agentic_lead_policy"] == "required"
    assert kwargs["min_subagents"] == 2
    assert kwargs["required_roles"] == ["codebase_audit", "independent_reviewer"]
    assert kwargs["solo_exception_for_artifact_only_gates"] is True
    assert kwargs["required_evidence_grade"] == "runtime_native"
    assert kwargs["reviewer_unavailable_policy"] == "proceed_degraded"
    assert kwargs["reviewer_model"] == "gemini-3.1-pro-preview"
    assert kwargs["reviewer_output_mode"] == "litellm_structured"
    assert kwargs["reviewer_max_tokens"] == 4096
    assert kwargs["reviewer_infra_retry_limit"] == 4
    assert kwargs["reviewer_infra_retry_backoff_s"] == 0.25
    assert kwargs["reviewer_low_confidence_threshold"] == 0.4
    assert kwargs["reviewer_panel_calibration_path"] == "docs/dual-agent/calibration.json"
    assert "irrelevant_field" not in kwargs


def test_workflow_kwargs_from_payload_preserves_reviewer_infra_retry_fields():
    from mcp_tools.codex_supervisor_workflow_cli import workflow_kwargs_from_payload

    kwargs = workflow_kwargs_from_payload({
        "cwd": "/tmp/workspace",
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run Cursor reviewer with bounded infra retries.",
        "reviewer_infra_retry_limit": 4,
        "reviewer_infra_retry_backoff_s": 0.25,
        "no_mistakes_policy": "advisory",
        "no_mistakes_skip_steps": ["push", "pr", "ci"],
        "no_mistakes_timeout_s": 120,
        "irrelevant_field": "must not leak into workflow kwargs",
    })

    assert kwargs["reviewer_infra_retry_limit"] == 4
    assert kwargs["reviewer_infra_retry_backoff_s"] == 0.25
    assert kwargs["no_mistakes_policy"] == "advisory"
    assert kwargs["no_mistakes_skip_steps"] == ["push", "pr", "ci"]
    assert kwargs["no_mistakes_timeout_s"] == 120
    assert "irrelevant_field" not in kwargs


def _server(
    tmp_path: Path,
    *,
    decision: str = "accept",
    claims: list[str] | None = None,
    changed_files: list[str] | None = None,
    tests: list[str] | None = None,
    test_status: str = "passed",
    gate_outcomes: dict[str, dict] | None = None,
    notifier=None,
    cursor_runner=None,
    codex_runner=None,
    no_mistakes_runner=None,
    runtime_materialize: bool = True,
):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        gate = _gate_from_argv(argv)
        overrides = dict(gate_outcomes.get(gate, {})) if gate_outcomes else {}
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block(
                    "workflow-1",
                    decision=overrides.pop("decision", decision),
                    claims=overrides.pop("claims", claims),
                    changed_files=overrides.pop("changed_files", changed_files),
                    tests=overrides.pop("tests", tests),
                    test_status=overrides.pop("test_status", test_status),
                ),
            ),
            stderr="",
        )

    cfg = _cfg(tmp_path)
    if no_mistakes_runner is not None:
        cfg.no_mistakes.require_clean_committed_branch = False
    if runtime_materialize:
        _materialize_runtime_evidence_fixture(
            tmp_path,
            changed_files=changed_files,
            tests=tests,
            gate_outcomes=gate_outcomes,
        )
    server = build_codex_supervisor_mcp_server(
        cfg,
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=codex_runner or _accepting_codex_reviewer_runner,
        cursor_runner=cursor_runner or _accepting_cursor_runner,
        no_mistakes_runner=no_mistakes_runner,
        notifier=notifier,
    )
    return server, state


def _materialize_runtime_evidence_fixture(
    root: Path,
    *,
    changed_files: list[str] | None,
    tests: list[str] | None,
    gate_outcomes: dict[str, dict] | None,
) -> None:
    if not (root / ".git").exists():
        _init_runtime_git_repo(root)

    file_set: set[str] = set(
        changed_files
        if changed_files is not None else [
            "supervisor/dual_agent_workflow.py",
            "tests/test_dual_agent_workflow_driver.py",
        ]
    )
    test_items: list[str] = list(tests if tests is not None else [f"{sys.executable} -c \"pass\""])
    for overrides in (gate_outcomes or {}).values():
        if "changed_files" in overrides:
            file_set.update(str(item) for item in overrides.get("changed_files") or [])
        if "tests" in overrides:
            test_items.extend(str(item) for item in overrides.get("tests") or [])

    for relative in sorted(path for path in file_set if path):
        _write_runtime_file(root, relative, _runtime_fixture_content(relative))
    for item in test_items:
        if _runtime_test_item_is_path(item):
            _write_runtime_file(root, item, _runtime_fixture_content(item))


def _runtime_test_item_is_path(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and " " not in text and text.endswith(".py")


def _runtime_fixture_content(relative: str) -> str:
    path = str(relative)
    if path.endswith(".py") and "/test" in path:
        return "def test_runtime_fixture_materialized():\n    assert True\n"
    if path.endswith(".py"):
        return "RUNTIME_FIXTURE = True\n"
    if path.endswith(".json"):
        return "{}\n"
    return "runtime fixture artifact\n"


def _gate_from_argv(argv) -> str:
    text = " ".join(str(item) for item in argv)
    match = re.search(r"Gate mode:\s*([A-Za-z_]+)", text)
    return match.group(1) if match else ""


def _write_good_workflow_source_artifacts(tmp_path: Path, task_id: str = "workflow-1") -> None:
    source_dir = tmp_path / "docs" / "dual-agent" / task_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "prd": "prd.md",
        "grill_findings": "grill-findings.md",
        "issues": "issues.md",
        "tdd_plan": "tdd.md",
        "tdd_grill_findings": "grill-findings-tdd.md",
        "implementation_plan": "implementation-plan.md",
    }
    for key, filename in mapping.items():
        kind = "grill_findings" if key == "tdd_grill_findings" else key
        target = source_dir / filename
        target.write_text(
            (FIXTURE_ROOT / kind / "good.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _init_runtime_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_runtime_file(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_reviewer_panel_calibration(
    tmp_path: Path,
    *,
    correlated: bool,
) -> Path:
    from supervisor.reviewer_panel_eval import reviewer_panel_eval_runner
    from supervisor.reviewer_registry import ReviewerSpec

    reviewers = [
        ReviewerSpec(
            reviewer_id="independent-reviewer-0",
            runtime="cursor_sdk",
            model="composer-2.5",
            provider_family="cursor",
            lineage=("cursor", "cursor_sdk", "composer-2.5"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
        ReviewerSpec(
            reviewer_id="independent-reviewer-1",
            runtime="codex_cli",
            model="gpt-5.5",
            provider_family="openai",
            lineage=("openai", "codex_cli", "gpt-5.5"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
    ]
    tasks = [
        {
            "task_id": "calibration-accept",
            "gate": "outcome_review",
            "label": "accept_allowed",
            "input_sha256": "input-calibration-accept",
        },
        {
            "task_id": "calibration-block",
            "gate": "outcome_review",
            "label": "block_required",
            "input_sha256": "input-calibration-block",
        },
    ]

    def cassette(task_id: str, reviewer_id: str, decision: str) -> dict:
        payload_seed = f"{task_id}:{reviewer_id}:{decision}".encode("utf-8")
        payload_sha = sha256(payload_seed).hexdigest()
        transcript_sha = sha256(b"transcript:" + payload_seed).hexdigest()
        output_sha = sha256(b"output:" + payload_seed).hexdigest()
        return {
            "task_id": task_id,
            "gate": "outcome_review",
            "reviewer_id": reviewer_id,
            "cassette_id": f"cassette-{task_id}-{reviewer_id}-{decision}",
            "decision": decision,
            "severity": "important" if decision == "revise" else "none",
            "confidence": 0.9,
            "verdict_present": True,
            "runtime": "cursor_sdk" if reviewer_id.endswith("-0") else "codex_cli",
            "model": "composer-2.5" if reviewer_id.endswith("-0") else "gpt-5.5",
            "provider_family": "cursor" if reviewer_id.endswith("-0") else "openai",
            "lineage": ["cursor", "cursor_sdk", "composer-2.5"]
            if reviewer_id.endswith("-0") else ["openai", "codex_cli", "gpt-5.5"],
            "tool_access": "codebase_tools",
            "assurance_grade": "agentic",
            "source_kind": "workflow_transcript_event",
            "source_trace_path": f"docs/dual-agent/test/{task_id}/transcript.jsonl",
            "source_event_id": f"{task_id}-{reviewer_id}",
            "source_payload_sha256": payload_sha,
            "transcript_sha256": transcript_sha,
            "transcript_refs": [
                {
                    "kind": "workflow_transcript_event",
                    "ref": f"docs/dual-agent/test/{task_id}/transcript.jsonl#{task_id}-{reviewer_id}",
                }
            ],
            "output_sha256": output_sha,
            "cost_usd": 0.1,
            "latency_ms": 100,
        }

    if correlated:
        cassettes = [
            cassette("calibration-accept", "independent-reviewer-0", "accept"),
            cassette("calibration-accept", "independent-reviewer-1", "accept"),
            cassette("calibration-block", "independent-reviewer-0", "accept"),
            cassette("calibration-block", "independent-reviewer-1", "accept"),
        ]
    else:
        cassettes = [
            cassette("calibration-accept", "independent-reviewer-0", "accept"),
            cassette("calibration-accept", "independent-reviewer-1", "accept"),
            cassette("calibration-block", "independent-reviewer-0", "accept"),
            cassette("calibration-block", "independent-reviewer-1", "revise"),
        ]
    output_dir = tmp_path / "docs" / "dual-agent" / "calibration-eval"
    report = reviewer_panel_eval_runner(
        labeled_tasks=tasks,
        reviewers=reviewers,
        cassettes=cassettes,
        output_dir=output_dir,
        emit_calibration=True,
    )
    return Path(report["exports"]["calibration_json"])


def test_workflow_visual_evidence_policy_does_not_require_evidence_for_product_name_only():
    policy = workflow_visual_evidence_policy(
        intent="Fix Vela colleague behavior without claiming live success prematurely.",
        task_id="workflow-1",
        user_facing=False,
        planning_artifacts=[],
    )

    assert policy["required"] is False
    assert policy["source"] == "not_user_facing"
    assert policy["matched_terms"] == []


def test_workflow_visual_evidence_policy_scans_planning_artifacts(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text(
        "This PRD touches Slack-visible output and requires screenshots.",
        encoding="utf-8",
    )

    policy = workflow_visual_evidence_policy(
        intent="Refactor internal helper.",
        task_id="workflow-1",
        user_facing=False,
        planning_artifacts=[{
            "path": str(prd),
            "kind": "prd",
            "mutable_by_worker": False,
        }],
    )

    assert policy["required"] is True
    assert {"slack", "screenshot"} <= set(policy["matched_terms"])
    assert policy["artifact_matches"][0]["kind"] == "prd"


def test_select_workflow_route_uses_explicit_trivial_depth():
    route = select_workflow_route(
        intent="One-line config tweak.",
        user_facing=False,
        task_complexity="trivial",
    )

    assert route["schema_version"] == "dual-agent-workflow-route/v1"
    assert route["task_complexity"] == "trivial"
    assert route["gates"] == ["execution", "outcome_review"]
    assert route["required_skill_stages"] == []
    assert route["requires_skill_receipts"] is False
    assert route["force_cursor_review"] is False


def test_cursor_review_gate_profiles_are_policy_not_prompt():
    route_gates = (
        "prd_review",
        "issues_review",
        "tdd_review",
        "implementation_plan",
        "execution",
        "outcome_review",
    )

    assert cursor_review_gates_for_workflow(
        route_gates=route_gates,
        task_complexity="large",
        cursor_review=True,
        cursor_review_profile="default",
    ) == ("outcome_review",)
    assert cursor_review_gates_for_workflow(
        route_gates=route_gates,
        task_complexity="large",
        cursor_review=True,
        cursor_review_profile="rigorous",
    ) == ("tdd_review", "implementation_plan", "outcome_review")
    assert cursor_review_gates_for_workflow(
        route_gates=route_gates,
        task_complexity="vague",
        cursor_review=False,
        cursor_review_profile="default",
    ) == ("prd_review", "tdd_review", "implementation_plan", "outcome_review")
    assert cursor_review_gates_for_workflow(
        route_gates=route_gates,
        task_complexity="large",
        cursor_review=True,
        cursor_review_gates=["issues_review", "outcome_review", "not_a_gate"],
    ) == ("issues_review", "outcome_review")


@pytest.mark.asyncio
async def test_execution_gate_blocks_accept_without_deliverable_changes(tmp_path):
    server, _state = _server(
        tmp_path,
        claims=["reviewed current artifacts"],
        changed_files=[],
        tests=[],
        test_status="unknown",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Implement the accepted code change.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "execution"
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert p11["status"] == "red"
    assert p11["reason"] == "deliverable_evidence_failed"
    assert "accepted_gate_without_changed_files" in p11["details"]["failures"]


@pytest.mark.asyncio
async def test_execution_gate_accepts_code_change_with_supervisor_runtime_diff_receipt(tmp_path):
    server, _state = _server(
        tmp_path,
        claims=["implemented"],
        changed_files=["supervisor/dual_agent_workflow.py"],
        tests=[],
        test_status="unknown",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Implement the accepted code change.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[],
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["gate"] == "outcome_review"
    runtime_receipts = result["final_gate_result"]["runtime_evidence"]["receipts"]
    assert any(
        receipt["kind"] == "git_diff"
        and receipt["source"] == "supervisor"
        and receipt["evidence_grade"] == "runtime_native"
        and receipt["status"] == "present"
        for receipt in runtime_receipts
    )


@pytest.mark.asyncio
async def test_execution_gate_blocks_accept_with_only_incidental_workflow_files(tmp_path):
    server, _state = _server(
        tmp_path,
        claims=["updated workflow artifacts"],
        changed_files=[
            "docs/dual-agent/workflow-1/source/prd.md",
            "docs/dual-agent/workflow-1/transcript.jsonl",
            ".handoff/workflow-1.json",
        ],
        tests=[],
        test_status="unknown",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Implement the accepted code change.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "execution"
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert p11["status"] == "red"
    assert p11["reason"] == "deliverable_evidence_failed"
    assert p11["details"]["deliverable_files"] == []
    assert "accepted_gate_only_incidental_files" in p11["details"]["failures"]


@pytest.mark.asyncio
async def test_execution_gate_blocks_docs_only_change_without_explicit_report_scope(tmp_path):
    docs_path = "docs/operator-notes.md"
    server, _state = _server(
        tmp_path,
        claims=["updated documentation"],
        changed_files=[docs_path],
        tests=[],
        test_status="unknown",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Implement the accepted code change.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[{
            "receipt_id": "docs-diff",
            "kind": "git_diff",
            "status": "present",
            "changed_files": [docs_path],
            "claims": ["updated documentation"],
        }],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "execution"
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert p11["status"] == "red"
    assert p11["reason"] == "deliverable_evidence_failed"
    assert p11["details"]["doc_report_files"] == [docs_path]
    assert "docs_report_deliverable_without_explicit_scope" in p11["details"]["failures"]


@pytest.mark.asyncio
async def test_execution_gate_allows_explicit_report_only_artifact_with_receipt(tmp_path):
    report_path = "docs/dual-agent/workflow-1/pilot/report.json"
    server, _state = _server(
        tmp_path,
        claims=["report-only artifact updated"],
        changed_files=[report_path],
        tests=[],
        test_status="not_run",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Produce a report-only benchmark artifact.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[{
            "receipt_id": "report-export",
            "kind": "artifact_export",
            "status": "passed",
            "changed_files": [report_path],
            "claims": ["report-only deliverable"],
        }],
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["gate"] == "outcome_review"
    assert result["final_gate_result"]["probes"]["P11"]["status"] == "green"


@pytest.mark.asyncio
async def test_execution_gate_allows_vela_style_report_only_artifact_with_receipt(tmp_path):
    report_path = "docs/dual-agent/workflow-1/autoresearch-report.md"
    server, _state = _server(
        tmp_path,
        claims=["report-only artifact updated"],
        changed_files=[report_path],
        tests=[
            "tests/unit/test_vela2_rewind_packet.py",
            "tests/unit/test_vela2_replay_regrade.py",
        ],
        test_status="passed",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a report-only Vela AutoResearch trial and update the report artifact.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[{
            "receipt_id": "report-export",
            "kind": "report",
            "status": "passed",
            "changed_files": [report_path],
            "claims": ["report-only artifact"],
        }],
    ))

    assert result["status"] == "accepted"
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert p11["status"] == "green"
    assert p11["details"]["deliverable_files"] == [report_path]
    assert p11["details"]["doc_report_files"] == [report_path]


@pytest.mark.asyncio
async def test_execution_gate_blocks_vela_style_report_receipt_without_changed_file(tmp_path):
    report_path = "docs/dual-agent/workflow-1/autoresearch-report.md"
    server, _state = _server(
        tmp_path,
        claims=["report-only artifact verified"],
        changed_files=[],
        tests=[
            "tests/unit/test_vela2_rewind_packet.py",
            "tests/unit/test_vela2_replay_regrade.py",
        ],
        test_status="passed",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a report-only Vela AutoResearch trial and update the report artifact.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[{
            "receipt_id": "report-export",
            "kind": "report",
            "status": "passed",
            "changed_files": [report_path],
            "claims": ["report-only artifact"],
        }],
    ))

    assert result["status"] == "blocked"
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert p11["reason"] == "deliverable_evidence_failed"
    assert "accepted_gate_without_changed_files" in p11["details"]["failures"]


@pytest.mark.asyncio
async def test_outcome_review_blocks_deliverable_failure_even_when_claims_verify(tmp_path):
    source_file = "supervisor/dual_agent_workflow.py"
    server, _state = _server(
        tmp_path,
        claims=["reviewed evidence"],
        changed_files=[source_file],
        gate_outcomes={
            "outcome_review": {
                "claims": ["reviewed evidence"],
                "changed_files": [],
                "tests": [],
                "test_status": "passed",
            },
        },
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Implement the accepted code change.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[{
            "receipt_id": "diff",
            "kind": "git_diff",
            "status": "present",
            "changed_files": [source_file],
            "claims": ["implemented"],
        }],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["claim_verification"]["status"] == "green"
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert p11["status"] == "red"
    assert p11["reason"] == "deliverable_evidence_failed"
    assert "accepted_gate_without_changed_files" in p11["details"]["failures"]
    assert result["final_gate_result"]["codex_decision"] == "revise"


@pytest.mark.asyncio
async def test_execution_gate_rejects_fabricated_runtime_receipts_for_missing_file(tmp_path):
    fake_path = "supervisor/fake.py"
    server, _state = _server(
        tmp_path,
        claims=["implemented"],
        changed_files=[fake_path],
        tests=[],
        test_status="unknown",
        runtime_materialize=False,
    )
    _init_runtime_git_repo(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject fabricated runtime evidence for a missing file.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[
            *_skill_receipts(),
            {
                "receipt_id": "agent-fabricated-diff",
                "kind": "git_diff",
                "status": "present",
                "source": "supervisor",
                "evidence_grade": "runtime_native",
                "changed_files": [fake_path],
                "claims": ["implemented"],
            },
            {
                "receipt_id": "agent-fabricated-test",
                "kind": "test",
                "status": "passed",
                "source": "supervisor",
                "evidence_grade": "runtime_native",
                "command": "python -m pytest tests/test_fake.py",
                "claims": ["tests passed"],
            },
        ],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "execution"
    runtime_probe = result["final_gate_result"]["runtime_evidence"]["probe"]
    assert runtime_probe["status"] == "red"
    assert "runtime_changed_files_missing_from_diff" in runtime_probe["details"]["failures"]
    assert "runtime_deliverable_missing" in runtime_probe["details"]["failures"]
    p11 = result["final_gate_result"]["probes"]["P11"]
    assert "accepted_gate_without_supervisor_runtime_deliverable_receipt" in p11["details"]["failures"]


@pytest.mark.asyncio
async def test_outcome_review_requires_supervisor_rerun_for_tests_passed_claim(tmp_path):
    source_path = "supervisor/runtime_target.py"
    server, _state = _server(
        tmp_path,
        gate_outcomes={
            "execution": {
                "claims": ["implemented"],
                "changed_files": [source_path],
                "tests": [],
                "test_status": "unknown",
            },
            "outcome_review": {
                "claims": ["tests passed", "implemented"],
                "changed_files": [source_path],
                "tests": ['python -c "raise SystemExit(1)"'],
                "test_status": "passed",
            },
        },
        runtime_materialize=False,
    )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, source_path, "VALUE = 1\n")

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject self-reported tests when supervisor rerun fails.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[
            *_skill_receipts(),
            {
                "receipt_id": "agent-claimed-tests",
                "kind": "test",
                "status": "passed",
                "command": 'python -c "raise SystemExit(1)"',
                "claims": ["tests passed"],
            },
        ],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    runtime_probe = result["final_gate_result"]["runtime_evidence"]["probe"]
    assert "runtime_tests_failed" in runtime_probe["details"]["failures"]
    claim_probe = result["final_gate_result"]["claim_verification"]
    assert claim_probe["status"] == "red"
    assert "tests_passed_without_supervisor_runtime_test_receipt" in claim_probe["details"]["failures"]


@pytest.mark.asyncio
async def test_agent_supplied_tests_passed_receipt_without_supervisor_rerun_fails(tmp_path):
    source_path = "supervisor/runtime_target.py"
    server, _state = _server(
        tmp_path,
        gate_outcomes={
            "execution": {
                "claims": ["implemented"],
                "changed_files": [source_path],
                "tests": [],
                "test_status": "unknown",
            },
            "outcome_review": {
                "claims": ["tests passed", "implemented"],
                "changed_files": [source_path],
                "tests": [],
                "test_status": "passed",
            },
        },
        runtime_materialize=False,
    )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, source_path, "VALUE = 1\n")

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject tests-passed claims without a command the supervisor can rerun.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=[
            *_skill_receipts(),
            {
                "receipt_id": "agent-claimed-tests",
                "kind": "test",
                "status": "passed",
                "claims": ["tests passed"],
            },
        ],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    runtime_probe = result["final_gate_result"]["runtime_evidence"]["probe"]
    assert "runtime_test_command_missing" in runtime_probe["details"]["failures"]
    claim_probe = result["final_gate_result"]["claim_verification"]
    assert claim_probe["status"] == "red"
    assert "tests_passed_without_supervisor_runtime_test_receipt" in claim_probe["details"]["failures"]


@pytest.mark.asyncio
async def test_execution_gate_accepts_supervisor_runtime_native_receipts(tmp_path):
    source_path = "supervisor/runtime_target.py"
    test_path = "tests/test_runtime_target.py"
    test_command = f"{sys.executable} -m pytest {test_path}::test_runtime_target_value:999 -q"
    server, state = _server(
        tmp_path,
        changed_files=[source_path, test_path],
        tests=[test_command],
        test_status="passed",
        runtime_materialize=False,
    )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, source_path, "VALUE = 42\n")
    _write_runtime_file(
        tmp_path,
        test_path,
        "from supervisor.runtime_target import VALUE\n\n"
        "def test_runtime_target_value():\n"
        "    assert VALUE == 42\n",
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Implement a runtime evidence verified code change.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["claim_verification"]["status"] == "green"
    runtime_receipts = result["final_gate_result"]["runtime_evidence"]["receipts"]
    assert any(
        receipt["kind"] == "test"
        and receipt["source"] == "supervisor"
        and receipt["evidence_grade"] == "runtime_native"
        and receipt["status"] == "passed"
        for receipt in runtime_receipts
    )
    test_receipt = next(receipt for receipt in runtime_receipts if receipt["kind"] == "test")
    assert test_receipt["commands"] == [
        f"{sys.executable} -m pytest {test_path}::test_runtime_target_value -q"
    ]
    events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_runtime_evidence"
    ]
    assert events


@pytest.mark.asyncio
async def test_read_gate_transcript_includes_runtime_evidence_events(tmp_path):
    source_path = "supervisor/runtime_target.py"
    server, _state = _server(
        tmp_path,
        claims=["implemented"],
        changed_files=[source_path],
        tests=[],
        test_status="unknown",
        runtime_materialize=False,
    )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, source_path, "VALUE = 2\n")

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Expose runtime evidence in the gate transcript.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=False,
        tool_receipts=_skill_receipts(),
    ))
    assert result["status"] == "accepted"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["runtime_evidence"]
    assert transcript["runtime_evidence"][0]["receipts"][0]["source"] == "supervisor"


@pytest.mark.asyncio
async def test_runtime_evidence_is_exported_before_cursor_review(tmp_path):
    source_path = "supervisor/runtime_target.py"
    observed = {"review_called": False}

    def checking_cursor_runner(request) -> CursorInvocationResult:
        observed["review_called"] = True
        transcript_path = tmp_path / "docs" / "dual-agent" / "workflow-1" / "transcript.jsonl"
        assert transcript_path.exists()
        assert "dual_agent_runtime_evidence" in transcript_path.read_text(encoding="utf-8")
        return _accepting_cursor_runner(request)

    server, _state = _server(
        tmp_path,
        claims=["implemented"],
        changed_files=[source_path],
        tests=[],
        test_status="unknown",
        cursor_runner=checking_cursor_runner,
        runtime_materialize=False,
    )
    _init_runtime_git_repo(tmp_path)
    _write_runtime_file(tmp_path, source_path, "VALUE = 3\n")

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Expose runtime evidence before reviewer inspection.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        cursor_review=True,
        cursor_review_gates=["execution"],
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "accepted"
    assert observed["review_called"] is True


def test_workflow_cli_loads_codex_mcp_env_without_overriding_existing(tmp_path, monkeypatch):
    from mcp_tools.codex_supervisor_workflow_cli import load_codex_mcp_env

    monkeypatch.setenv("CURSOR_API_KEY", "existing")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    codex_config = tmp_path / "config.toml"
    codex_config.write_text(
        "\n".join([
            "[mcp_servers.codex_supervisor]",
            'command = "/tmp/codex-supervisor-mcp"',
            "",
            "[mcp_servers.codex_supervisor.env]",
            'CURSOR_API_KEY = "from-config"',
            'ANTHROPIC_API_KEY = "from-config-anthropic"',
            "",
            "[mcp_servers.other.env]",
            'SHOULD_NOT_LOAD = "nope"',
        ]),
        encoding="utf-8",
    )

    loaded = load_codex_mcp_env(codex_config)

    assert os.environ["CURSOR_API_KEY"] == "existing"
    assert os.environ["ANTHROPIC_API_KEY"] == "from-config-anthropic"
    assert loaded == {"ANTHROPIC_API_KEY": "from-config-anthropic"}
    assert "SHOULD_NOT_LOAD" not in os.environ


def _cursor_review_result(
    task_id: str,
    *,
    decision: str = "accept",
    severity: str | None = None,
    confidence: float = 0.91,
    strongest_objection: str | None = None,
    evidence_ref: str | None = None,
) -> CursorInvocationResult:
    objection = (
        strongest_objection
        if strongest_objection is not None
        else "none" if decision == "accept" else "unresolved concern"
    )
    outcome_objections = [] if decision == "accept" else [
        strongest_objection or "Cursor found an unresolved concern."
    ]
    outcome = Outcome(
        task_id=task_id,
        summary="Cursor independently reviewed the gate.",
        specialists=[{"name": "Cursor Reviewer", "decision": decision}],
        decisions=[decision],
        objections=outcome_objections,
        changed_files=[],
        tests=[evidence_ref] if evidence_ref else [],
        test_status="unknown",
        confidence=confidence,
        confidence_rationale="Cursor reviewed the gate evidence independently.",
        confidence_criteria=["typed outcome complete", "review decision present"],
        claims=[],
        critical_review={
            "strongest_objection": objection,
            "missing_evidence": [],
            "contradictions_checked": ["gate evidence"],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "New contradictory evidence.",
            "decision": decision,
            "severity": severity or ("none" if decision == "accept" else "important"),
            "evidence_refs": [evidence_ref] if evidence_ref else [],
        },
    )
    return CursorInvocationResult(
        probe=ProbeResult("CURSOR", "green", "cursor_review_ok"),
        outcome=outcome,
        transcript=f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>",
        agent_id="agent-test",
        run_id="run-cursor",
        status="finished",
        model="composer-2.5",
        reviewer_runtime="cursor_sdk",
        reviewer_output_mode="cursor_sdk",
        reviewer_assurance="tool_backed_primary",
        duration_ms=10,
    )


def _codex_reviewer_jsonl(
    task_id: str,
    *,
    decision: str = "accept",
    severity: str | None = None,
    confidence: float = 0.93,
    include_command: bool = True,
    strongest_objection: str | None = None,
    evidence_ref: str | None = None,
) -> str:
    objection = (
        strongest_objection
        if strongest_objection is not None
        else "none" if decision == "accept" else "unresolved concern"
    )
    outcome = Outcome(
        task_id=task_id,
        summary="Codex CLI independently reviewed the gate.",
        specialists=[{"name": "independent-reviewer-1", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else [objection],
        changed_files=[],
        tests=[evidence_ref] if evidence_ref else [],
        test_status="unknown",
        confidence=confidence,
        confidence_rationale="Codex CLI reviewed the gate evidence with read-only tools.",
        confidence_criteria=["typed outcome complete", "read-only command evidence observed"],
        claims=[],
        critical_review={
            "strongest_objection": objection,
            "missing_evidence": [],
            "contradictions_checked": ["gate evidence"],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "New contradictory evidence.",
            "decision": decision,
            "severity": severity or ("none" if decision == "accept" else "important"),
            "evidence_refs": [evidence_ref] if evidence_ref else [],
        },
    )
    events = [
        json.dumps({"type": "thread.started", "thread_id": "thread-codex-reviewer"}),
    ]
    if include_command:
        events.append(json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/zsh -lc 'rg -n reviewer supervisor'",
                "aggregated_output": "review evidence\n",
                "exit_code": 0,
                "status": "completed",
            },
        }))
    events.extend([
        json.dumps({
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>",
            },
        }),
        "",
    ])
    return "\n".join(events)


def _codex_reviewer_session_jsonl(
    task_id: str,
    *,
    decision: str = "accept",
    severity: str | None = None,
    confidence: float = 0.93,
) -> str:
    outcome = Outcome(
        task_id=task_id,
        summary="Codex CLI independently reviewed the gate.",
        specialists=[{"name": "independent-reviewer-1", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else ["Codex CLI found an unresolved concern."],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=confidence,
        confidence_rationale="Codex CLI reviewed the gate evidence with read-only tools.",
        confidence_criteria=["typed outcome complete", "read-only command evidence observed"],
        claims=[],
        critical_review={
            "strongest_objection": "none" if decision == "accept" else "unresolved concern",
            "missing_evidence": [],
            "contradictions_checked": ["gate evidence"],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "New contradictory evidence.",
            "decision": decision,
            "severity": severity or ("none" if decision == "accept" else "important"),
        },
    )
    return "\n".join([
        json.dumps({
            "type": "session_meta",
            "payload": {"id": "thread-codex-reviewer-session"},
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Always end with <dual_agent_outcome>{...valid compact JSON...}</dual_agent_outcome>.",
                    }
                ],
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_read_repo",
                "arguments": json.dumps({"cmd": "rg -n reviewer supervisor"}),
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_read_repo",
                "output": "review evidence\n",
            },
        }),
        json.dumps({
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": f"<dual_agent_outcome>{outcome.model_dump_json()}</dual_agent_outcome>",
            },
        }),
        "",
    ])


def _task_id_from_codex_argv(argv) -> str:
    prompt = str(argv[-1]) if argv else ""
    match = re.search(r"Task id:\s*([A-Za-z0-9_.:-]+)", prompt)
    return match.group(1).rstrip(".") if match else "workflow-1"


def _accepting_codex_reviewer_runner(argv, **kwargs):
    task_id = _task_id_from_codex_argv(argv)
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout=_codex_reviewer_jsonl(task_id),
        stderr="",
    )


def _revising_codex_reviewer_runner(argv, **kwargs):
    task_id = _task_id_from_codex_argv(argv)
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout=_codex_reviewer_jsonl(task_id, decision="revise", severity="important"),
        stderr="",
    )


def _revising_codex_reviewer_with_evidence_runner(argv, **kwargs):
    task_id = _task_id_from_codex_argv(argv)
    cwd = Path(kwargs.get("cwd") or ".")
    evidence = cwd / "docs" / "dual-agent" / "workflow-1" / "adjudication-evidence.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("minority objection evidence\n", encoding="utf-8")
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout=_codex_reviewer_jsonl(
            task_id,
            decision="revise",
            severity="important",
            strongest_objection="cited receipt contradicts the accept path",
            evidence_ref=str(evidence.relative_to(cwd)),
        ),
        stderr="",
    )


def _accepting_codex_reviewer_with_objection_runner(argv, **kwargs):
    task_id = _task_id_from_codex_argv(argv)
    cwd = Path(kwargs.get("cwd") or ".")
    evidence = cwd / "docs" / "dual-agent" / "workflow-1" / "strong-objection.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("strong accept-shaped objection evidence\n", encoding="utf-8")
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout=_codex_reviewer_jsonl(
            task_id,
            decision="accept",
            severity="important",
            confidence=0.88,
            strongest_objection="accept path lacks the cited receipt replay",
            evidence_ref=str(evidence.relative_to(cwd)),
        ),
        stderr="",
    )


def _unavailable_codex_reviewer_runner(argv, **kwargs):
    return subprocess.CompletedProcess(
        argv,
        1,
        stdout="",
        stderr="codex reviewer unavailable",
    )


def _cursor_missing_verdict_runner(request) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "cursor_review_missing_verdict",
            {"recoverable": False},
        ),
        outcome=None,
        transcript="reviewer returned no typed outcome",
        agent_id="agent-missing",
        run_id="run-missing",
        status="finished",
        model="gemini-3.1-pro-preview",
        reviewer_runtime="litellm_structured",
        reviewer_output_mode="litellm_structured",
        reviewer_assurance="structured_text_only",
        failure_classification=None,
        recoverable=False,
        duration_ms=10,
    )


def _accepting_cursor_runner(request) -> CursorInvocationResult:
    return _cursor_review_result(request.task_id)


def _cursor_contract_unmet_runner(request) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "reviewer_contract_unmet",
            {
                "original_reason": "missing dual_agent_outcome block",
                "recoverable": True,
                "attempts": 4,
            },
        ),
        outcome=None,
        transcript="",
        agent_id="agent-contract",
        run_id="run-contract",
        status="finished",
        model="composer-2.5",
        duration_ms=30,
        failure_classification="reviewer_contract_unmet",
        recoverable=True,
        attempts=4,
        retry_reasons=("missing dual_agent_outcome block",) * 4,
    )


def _cursor_exhausted_infra_runner(request) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "reviewer_infrastructure_unavailable",
            {
                "original_reason": "reviewer_invocation_failed",
                "recoverable": True,
                "attempts": 3,
                "retry_limit": 2,
                "infrastructure_retries": {
                    "attempt_count": 3,
                    "failed_attempt_count": 3,
                    "retry_limit": 2,
                    "attempts": [
                        {
                            "attempt": 1,
                            "reason": "reviewer_invocation_failed",
                            "error": "internal: internal error 1",
                        },
                        {
                            "attempt": 2,
                            "reason": "reviewer_invocation_failed",
                            "error": "internal: internal error 2",
                        },
                        {
                            "attempt": 3,
                            "reason": "reviewer_invocation_failed",
                            "error": "internal: internal error 3",
                        },
                    ],
                    "backoff_s": [0.0, 0.0],
                    "exhausted": True,
                },
            },
        ),
        outcome=None,
        transcript="",
        agent_id="agent-infra",
        run_id="run-infra",
        status="failed",
        model="composer-2.5",
        duration_ms=90,
        reviewer_runtime="cursor_sdk",
        reviewer_output_mode="cursor_sdk",
        reviewer_assurance="unavailable",
        failure_classification="reviewer_infrastructure_unavailable",
        recoverable=True,
        attempts=3,
        diagnostics={
            "infrastructure_retries": {
                "attempt_count": 3,
                "failed_attempt_count": 3,
                "retry_limit": 2,
                "attempts": [
                    {
                        "attempt": 1,
                        "reason": "reviewer_invocation_failed",
                        "error": "internal: internal error 1",
                    },
                    {
                        "attempt": 2,
                        "reason": "reviewer_invocation_failed",
                        "error": "internal: internal error 2",
                    },
                    {
                        "attempt": 3,
                        "reason": "reviewer_invocation_failed",
                        "error": "internal: internal error 3",
                    },
                ],
                "backoff_s": [0.0, 0.0],
                "exhausted": True,
            },
            "fallback": {
                "attempted": False,
                "reason": "missing_openai_api_key",
            },
        },
    )


def _cursor_access_denied_runner(request) -> CursorInvocationResult:
    return CursorInvocationResult(
        probe=ProbeResult(
            "CURSOR",
            "red",
            "reviewer_access_denied",
            {
                "status_code": 403,
                "recoverable": False,
                "message": "Access denied for configured reviewer route.",
            },
        ),
        outcome=None,
        transcript="",
        agent_id="agent-access",
        run_id="run-access",
        status="failed",
        model="gemini-3.1-pro-preview",
        reviewer_runtime="litellm_structured",
        reviewer_output_mode="litellm_structured",
        reviewer_assurance="unavailable",
        failure_classification="reviewer_access_denied",
        recoverable=False,
        attempts=1,
        diagnostics={
            "access_denied": {
                "status_code": 403,
                "base_url_host": "uai-litellm.internal.unity.com",
                "model": "gemini-3.1-pro-preview",
            },
        },
    )


@pytest.mark.asyncio
async def test_workflow_invokes_cursor_sdk_reviewer_after_claude_accept_by_default(
    tmp_path,
):
    requests = []

    def fake_cursor_runner(request):
        requests.append(request)
        return CursorInvocationResult(
            probe=ProbeResult("CURSOR", "green", "cursor_review_ok"),
            outcome=_cursor_review_result(request.task_id).outcome,
            transcript="",
            run_id="run-cursor",
            status="finished",
            model=request.reviewer_model,
            reviewer_runtime="cursor_sdk",
            reviewer_output_mode=request.reviewer_output_mode,
            reviewer_assurance="tool_backed_primary",
        )

    server, _state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Default reviewer should be Cursor SDK and downstream of Claude.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["reviewer_model"] == "composer-2.5"
    assert result["workflow_route"]["reviewer_output_mode"] == "cursor_sdk"
    assert result["workflow_route"]["reviewer_max_tokens"] == 4096
    assert requests
    assert all(request.reviewer_model == "composer-2.5" for request in requests)
    assert all(request.reviewer_output_mode == "cursor_sdk" for request in requests)
    assert all(request.claude_outcome is not None for request in requests)
    cursor_payload = result["final_gate_result"]["cursor_review"]
    assert cursor_payload["reviewer_runtime"] == "cursor_sdk"
    assert cursor_payload["reviewer_output_mode"] == "cursor_sdk"
    assert cursor_payload["reviewer_assurance"] == "tool_backed_primary"
    assert result["final_gate_result"]["independent_reviewer"] == cursor_payload


@pytest.mark.asyncio
async def test_workflow_passes_reviewer_infra_retry_policy_to_cursor_request(tmp_path):
    requests = []

    def fake_cursor_runner(request):
        requests.append(request)
        return _cursor_review_result(request.task_id)

    server, _state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Cursor infrastructure retry policy should be threaded to the reviewer.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_infra_retry_limit=4,
        reviewer_infra_retry_backoff_s=0.25,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["reviewer_infra_retry_limit"] == 4
    assert result["workflow_route"]["reviewer_infra_retry_backoff_s"] == 0.25
    assert requests
    assert all(request.reviewer_infra_retry_limit == 4 for request in requests)
    assert all(request.reviewer_infra_retry_backoff_s == 0.25 for request in requests)


@pytest.mark.asyncio
async def test_cursor_sdk_output_mode_routes_legacy_model_to_reviewer_request(tmp_path):
    requests = []

    def fake_cursor_runner(request):
        requests.append(request)
        return _cursor_review_result(request.task_id)

    server, _state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Explicit Cursor SDK route should remain available.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_output_mode="cursor_sdk",
        cursor_model="composer-custom",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["reviewer_model"] == "composer-custom"
    assert result["workflow_route"]["reviewer_output_mode"] == "cursor_sdk"
    assert requests
    assert all(request.reviewer_output_mode == "cursor_sdk" for request in requests)
    assert all(request.model == "composer-custom" for request in requests)


@pytest.mark.asyncio
async def test_explicit_litellm_reviewer_is_not_labeled_as_cursor_runtime(tmp_path):
    requests = []

    def fake_cursor_runner(request):
        requests.append(request)
        result = _cursor_review_result(request.task_id)
        return CursorInvocationResult(
            probe=result.probe,
            outcome=result.outcome,
            transcript=result.transcript,
            run_id="chatcmpl-litellm",
            status="finished",
            model=request.reviewer_model,
            reviewer_runtime="litellm_structured",
            reviewer_output_mode="litellm_structured",
            reviewer_assurance="structured_text_only",
        )

    server, _state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Explicit LiteLLM reviewer should stay lower assurance.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_output_mode="litellm_structured",
        reviewer_model="gemini-test",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert requests
    assert all(request.reviewer_output_mode == "litellm_structured" for request in requests)
    payload = result["final_gate_result"]["independent_reviewer"]
    assert payload == result["final_gate_result"]["cursor_review"]
    assert payload["reviewer_runtime"] == "litellm_structured"
    assert payload["reviewer_output_mode"] == "litellm_structured"
    assert payload["reviewer_assurance"] == "structured_text_only"
    assert payload["model"] == "gemini-test"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_happy_path_owns_full_lifecycle(tmp_path):
    notifier = _Notifier()
    server, state = _server(tmp_path, notifier=notifier)
    _write_good_workflow_source_artifacts(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["task_complexity"] == "large"
    assert [step["gate"] for step in result["steps"]] == [
        "prd_review",
        "issues_review",
        "tdd_review",
        "implementation_plan",
        "execution",
        "outcome_review",
    ]
    assert all(step["status"] == "accepted" for step in result["steps"])
    assert result["mandatory_artifacts"]["status"] == "ok"
    for relative in [
        "source/prd.md",
        "source/grill-findings.md",
        "source/issues.md",
        "source/tdd.md",
        "source/implementation-plan.md",
        "interactions.md",
        "outcome-review.md",
        "transcript.md",
    ]:
        assert (tmp_path / "docs" / "dual-agent" / "workflow-1" / relative).exists()

    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "accepted"
    steps = state.list_dual_agent_workflow_steps(run_id="workflow-run", task_id="workflow-1")
    assert [step["gate"] for step in steps] == [step["gate"] for step in result["steps"]]
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert all(round_payload["objection"] == "both agents accepted" for round_payload in rounds)
    assert any("dual-agent workflow started" in message for message in notifier.messages)
    assert any("dual-agent workflow accepted" in message for message in notifier.messages)


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_records_advisory_no_mistakes_after_outcome_review(tmp_path):
    no_mistakes_calls: list[list[str]] = []

    def fake_no_mistakes_runner(argv, **kwargs):
        no_mistakes_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="outcome: checks-passed\n", stderr="")

    server, state = _server(
        tmp_path,
        no_mistakes_runner=fake_no_mistakes_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
        tool_receipts=_tool_receipts(),
        no_mistakes_policy="advisory",
    ))

    assert result["status"] == "accepted"
    assert no_mistakes_calls
    assert no_mistakes_calls[0][-1] == "--skip=push,pr,ci"
    assert "--yes" not in no_mistakes_calls[0]
    assert result["no_mistakes_validation"]["verdict"] == "accepted"
    assert result["no_mistakes_validation"]["receipt"]["kind"] == "no_mistakes_validation_receipt"

    events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"].startswith("no_mistakes_")
    ]
    assert [event["kind"] for event in events] == [
        "no_mistakes_validation_started",
        "no_mistakes_validation_completed",
    ]
    outcome_index = next(
        index
        for index, row in enumerate(state.read_dual_agent_gate_events("workflow-run"))
        if row["kind"] == "dual_agent_gate_result"
        and json.loads(row["payload_json"]).get("gate") == "outcome_review"
    )
    no_mistakes_index = next(
        index
        for index, row in enumerate(state.read_dual_agent_gate_events("workflow-run"))
        if row["kind"] == "no_mistakes_validation_started"
    )
    assert no_mistakes_index > outcome_index


@pytest.mark.asyncio
async def test_required_no_mistakes_unavailable_blocks_without_rewriting_gate_acceptance(tmp_path):
    def missing_no_mistakes_runner(argv, **kwargs):
        raise FileNotFoundError("no-mistakes")

    server, state = _server(
        tmp_path,
        no_mistakes_runner=missing_no_mistakes_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
        tool_receipts=_tool_receipts(),
        no_mistakes_policy="required",
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "no_mistakes_validation"
    assert result["no_mistakes_validation"]["verdict"] == "required_blocked"
    assert result["final_gate_result"]["gate"] == "no_mistakes_validation"

    gate_statuses = [
        json.loads(row["payload_json"]).get("status")
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_result"
        and json.loads(row["payload_json"]).get("gate") == "outcome_review"
    ]
    assert gate_statuses == ["accepted"]


@pytest.mark.asyncio
async def test_workflow_cli_payload_runs_same_supervisor_api(tmp_path):
    from mcp_tools.codex_supervisor_workflow_cli import run_workflow_payload
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    runner_calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    result = await run_workflow_payload(
        {
            "cwd": str(tmp_path),
            "task_id": "workflow-1",
            "run_id": "workflow-run",
            "intent": "Run through the non-MCP fallback transport.",
            "max_rounds_per_gate": 1,
            "execution_layer_mode": "dynamic_workflow_preview",
            "dynamic_workflow_task_class": "codebase_audit",
            "tool_receipts": [*_tool_receipts(), *_dynamic_workflow_receipts(tmp_path)],
        },
        cfg=_cfg(tmp_path),
        state=state,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    assert result["status"] == "accepted"
    assert result["workflow_route"]["execution_layer_mode"] == "dynamic_workflow_preview"
    assert result["workflow_route"]["dynamic_workflow_task_class"] == "codebase_audit"
    assert runner_calls
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "accepted"
    assert (tmp_path / "docs" / "dual-agent" / "workflow-1" / "outcome-review.md").exists()


@pytest.mark.asyncio
async def test_submit_dual_agent_workflow_job_reserves_then_poll_spawns_worker(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[dict] = []
    phase_at_spawn: list[str] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            row = state._conn.execute(
                "SELECT recovery_point FROM dual_agent_workflow_jobs"
            ).fetchone()
            phase_at_spawn.append(row["recovery_point"] if row else "missing")
            popen_calls.append({"argv": list(argv), "kwargs": kwargs})

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    result = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run long workflow out of band.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic-workflow-preview",
        dynamic_workflow_task_class="codebase-audit",
        tool_receipts=_tool_receipts(),
        config_path=str(tmp_path / "config.yaml"),
    ))

    assert result["status"] == "submitted"
    assert result["recovery_point"] == "reserved"
    assert result["poll_tool"] == "poll_dual_agent_workflow_job"
    assert popen_calls == []
    assert not Path(result["request_path"]).exists()

    job = state.get_dual_agent_workflow_job(job_id=result["job_id"])
    assert job is not None
    assert job["status"] == "submitted"
    assert job["pid"] is None
    assert job["recovery_point"] == "reserved"

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=result["job_id"]))

    assert poll["status"] == "running"
    assert poll["recovery_point"] == "spawned"
    assert phase_at_spawn == ["request_written"]
    assert popen_calls
    assert popen_calls[0]["argv"][1:3] == ["-m", "mcp_tools.codex_supervisor_workflow_cli"]
    assert popen_calls[0]["kwargs"]["start_new_session"] is True

    request = json.loads(Path(result["request_path"]).read_text(encoding="utf-8"))
    assert request["execution_layer_mode"] == "dynamic_workflow_preview"
    assert request["dynamic_workflow_task_class"] == "codebase_audit"
    assert request["job_id"] == result["job_id"]

    job = state.get_dual_agent_workflow_job(job_id=result["job_id"])
    assert job is not None
    assert job["status"] == "running"
    assert job["pid"] == 43210
    assert job["recovery_point"] == "spawned"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["workflow_jobs"][-1]["status"] == "running"


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_restarts_from_request_written(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)
    payload = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
    }
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-request-written"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text(json.dumps({**payload, "job_id": "job-request-written"}), encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-request-written",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
        idempotency_token="token-request-written",
        recovery_point="request_written",
        request_payload_json=json.dumps(payload),
    )

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id="job-request-written"))

    assert poll["status"] == "running"
    assert poll["recovery_point"] == "spawned"
    assert len(popen_calls) == 1
    job = state.get_dual_agent_workflow_job(job_id="job-request-written")
    assert job["pid"] == 43210
    assert job["recovery_point"] == "spawned"


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_concurrent_request_written_spawns_once(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []
    popen_lock = threading.Lock()

    class FakePopen:
        def __init__(self, argv, **kwargs):
            with popen_lock:
                popen_calls.append(list(argv))
                self.pid = 43210 + len(popen_calls)
            time.sleep(0.05)

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    payload = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
    }
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-request-written-race"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text(json.dumps({**payload, "job_id": "job-request-written-race"}), encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-request-written-race",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
        idempotency_token="token-request-written-race",
        recovery_point="request_written",
        request_payload_json=json.dumps(payload),
    )

    async def poll_once():
        def poll_with_fresh_connection():
            local_server, _local_state = _server(tmp_path)
            return local_server.tools["poll_dual_agent_workflow_job"](
                job_id="job-request-written-race",
            )

        return await asyncio.to_thread(
            poll_with_fresh_connection,
        )

    results = await asyncio.gather(*(poll_once() for _ in range(8)))

    assert len(popen_calls) == 1
    assert any(result["status"] == "running" for result in results)
    job = state.get_dual_agent_workflow_job(job_id="job-request-written-race")
    assert job["pid"] == 43211
    assert job["recovery_point"] == "spawned"
    assert job["recovery_claim_token"] is None
    assert job["recovery_claimed_at"] is None


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_recovers_stale_recovery_claim(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    payload = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
    }
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-stale-claim"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text(json.dumps({**payload, "job_id": "job-stale-claim"}), encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-stale-claim",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
        idempotency_token="token-stale-claim",
        recovery_point="request_written",
        request_payload_json=json.dumps(payload),
    )
    state._conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET recovery_claim_token='dead-poller',
                  recovery_claimed_at=0
            WHERE job_id='job-stale-claim'"""
    )
    state._conn.commit()

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](
        job_id="job-stale-claim",
    ))

    assert poll["status"] == "running"
    assert poll["recovery_point"] == "spawned"
    assert len(popen_calls) == 1
    job = state.get_dual_agent_workflow_job(job_id="job-stale-claim")
    assert job["recovery_claim_token"] is None
    assert job["recovery_claimed_at"] is None


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_stale_spawn_claim_fails_without_respawn(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    payload = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
    }
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-stale-spawn-claim"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text(
        json.dumps({**payload, "job_id": "job-stale-spawn-claim"}),
        encoding="utf-8",
    )
    state.upsert_dual_agent_workflow_job(
        job_id="job-stale-spawn-claim",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
        idempotency_token="token-stale-spawn-claim",
        recovery_point="request_written",
        request_payload_json=json.dumps(payload),
    )
    state._conn.execute(
        """UPDATE dual_agent_workflow_jobs
              SET recovery_claim_token='spawn:dead-poller',
                  recovery_claimed_at=0
            WHERE job_id='job-stale-spawn-claim'"""
    )
    state._conn.commit()

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](
        job_id="job-stale-spawn-claim",
    ))

    assert poll["status"] == "parked"
    assert poll["recovery_point"] == "request_written"
    assert poll["error"] == "stale_spawn_claim_without_persisted_pid"
    assert popen_calls == []
    job = state.get_dual_agent_workflow_job(job_id="job-stale-spawn-claim")
    assert job["status"] == "parked"
    assert job["parked_reason"] == "stale_spawn_claim_without_persisted_pid"
    assert job["recovery_point"] == "request_written"
    assert job["recovery_claim_token"] is None
    assert job["recovery_claimed_at"] is None


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_kills_worker_when_spawn_persist_fails(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_instances = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            self.argv = list(argv)
            self.terminated = False
            self.killed = False
            popen_instances.append(self)

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    original_upsert = state.upsert_dual_agent_workflow_job

    def fail_spawned_upsert(**kwargs):
        if kwargs.get("recovery_point") == "spawned":
            raise RuntimeError("persist denied")
        return original_upsert(**kwargs)

    monkeypatch.setattr(state, "upsert_dual_agent_workflow_job", fail_spawned_upsert)

    submit = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run long workflow out of band.",
        tool_receipts=_tool_receipts(),
        config_path=str(tmp_path / "config.yaml"),
    ))

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=submit["job_id"]))
    second_poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](
        job_id=submit["job_id"],
    ))

    assert poll["status"] == "parked"
    assert poll["recovery_point"] == "request_written"
    assert "failed_to_persist_spawned_worker: persist denied" == poll["error"]
    assert len(popen_instances) == 1
    assert popen_instances[0].terminated is True
    assert popen_instances[0].killed is False
    assert second_poll["status"] == "parked"
    assert len(popen_instances) == 1
    job = state.get_dual_agent_workflow_job(job_id=submit["job_id"])
    assert job["status"] == "parked"
    assert job["recovery_point"] == "request_written"
    assert job["parked_reason"] == "failed_to_persist_spawned_worker: persist denied"
    assert job["pid"] is None
    assert job["recovery_claim_token"] is None
    assert job["recovery_claimed_at"] is None


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_schedules_retry_when_spawn_fails(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)

    def fail_popen(*args, **kwargs):
        raise OSError("spawn denied")

    monkeypatch.setattr(stdio.subprocess, "Popen", fail_popen)
    submit = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run long workflow out of band.",
        tool_receipts=_tool_receipts(),
        config_path=str(tmp_path / "config.yaml"),
    ))

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=submit["job_id"]))

    assert poll["status"] == "submitted"
    assert poll["recovery_point"] == "request_written"
    assert poll["error"] == "spawn denied"
    job = state.get_dual_agent_workflow_job(job_id=submit["job_id"])
    assert job["status"] == "submitted"
    assert job["recovery_point"] == "request_written"
    assert job["dispatch_attempts"] == 1
    assert job["next_dispatch_at"] is not None
    assert job["terminal_outcome_json"] is None


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_uses_dispatcher_bridge(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    calls: list[tuple[str, str | None]] = []

    class FakeDispatcher:
        def __init__(self, state_arg, **kwargs):
            self.state = state_arg
            calls.append(("init", kwargs["dispatcher_id"]))

        def reap_stale_leases(self):
            calls.append(("reap", None))
            return {"reclaimed": [], "failed": [], "completed": []}

        def run_once(self, *, job_id=None):
            calls.append(("run_once", job_id))
            self.state.update_dual_agent_workflow_job(
                job_id=job_id,
                status="running",
                pid=54321,
                recovery_point="spawned",
            )
            return {"status": "spawned", "job_id": job_id}

    monkeypatch.setattr(stdio, "WorkflowJobDispatcher", FakeDispatcher)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)
    submit = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run long workflow out of band.",
        tool_receipts=_tool_receipts(),
        config_path=str(tmp_path / "config.yaml"),
    ))

    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=submit["job_id"]))

    assert poll["status"] == "running"
    assert poll["recovery_point"] == "spawned"
    assert ("reap", None) in calls
    assert ("run_once", submit["job_id"]) in calls
    assert calls[0][0] == "init"


def _reserve_dispatcher_test_job(
    state: State,
    tmp_path: Path,
    *,
    job_id: str = "job-dispatcher",
    token: str = "token-dispatcher",
    recovery_point: str = "reserved",
    request_payload_json: str | None = None,
):
    payload = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
    }
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / job_id
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    row, _created = state.reserve_dual_agent_workflow_job(
        job_id=job_id,
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="submitted",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
        idempotency_token=token,
        request_payload_json=(
            request_payload_json
            if request_payload_json is not None
            else json.dumps(payload, sort_keys=True)
        ),
        config_path=str(tmp_path / "config.yaml"),
    )
    if recovery_point != "reserved":
        job_dir.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps({**payload, "job_id": job_id}, sort_keys=True),
            encoding="utf-8",
        )
        state.update_dual_agent_workflow_job(
            job_id=job_id,
            recovery_point=recovery_point,
            status="submitted",
        )
        refreshed = state.get_dual_agent_workflow_job(job_id=job_id)
        assert refreshed is not None
        row = refreshed
    return row


def test_dispatcher_claims_reserved_job_and_spawns_worker(monkeypatch, tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=FakePopen,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.run_once()

    assert result["status"] == "spawned"
    assert result["job_id"] == "job-dispatcher"
    assert len(popen_calls) == 1
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["status"] == "running"
    assert job["pid"] == 43210
    assert job["recovery_point"] == "spawned"
    assert job["leased_by"] == "worker:43210"
    assert job["lease_expires_at"] == 1060
    assert Path(job["request_path"]).exists()


def test_dispatcher_restarts_from_request_written(monkeypatch, tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(
        state,
        tmp_path,
        job_id="job-request-written-dispatcher",
        token="token-request-written-dispatcher",
        recovery_point="request_written",
    )
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43211

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=FakePopen,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.run_once()

    assert result["status"] == "spawned"
    assert result["job_id"] == "job-request-written-dispatcher"
    assert len(popen_calls) == 1
    job = state.get_dual_agent_workflow_job(job_id="job-request-written-dispatcher")
    assert job["recovery_point"] == "spawned"
    assert job["pid"] == 43211


def test_heartbeat_extends_lease_for_matching_worker(tmp_path):
    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    state.update_dual_agent_workflow_job(
        job_id="job-dispatcher",
        status="running",
        pid=43210,
        recovery_point="spawned",
        leased_by="worker:43210",
        lease_expires_at=1010,
    )

    assert state.heartbeat_dual_agent_workflow_job(
        job_id="job-dispatcher",
        leased_by="worker:wrong",
        lease_ttl_s=60,
        now=1005,
    ) is False
    assert state.heartbeat_dual_agent_workflow_job(
        job_id="job-dispatcher",
        leased_by="worker:43210",
        lease_ttl_s=60,
        now=1005,
    ) is True
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["lease_expires_at"] == 1065
    assert job["heartbeat_at"] == 1005


def test_workflow_job_lease_heartbeat_runs_until_worker_lease_rejected():
    from supervisor.workflow_job_dispatcher import WorkflowJobLeaseHeartbeat

    calls: list[dict[str, object]] = []
    second_heartbeat = threading.Event()

    class FakeState:
        def heartbeat_dual_agent_workflow_job(self, **kwargs):
            calls.append(kwargs)
            if len(calls) >= 2:
                second_heartbeat.set()
                return False
            return True

    heartbeat = WorkflowJobLeaseHeartbeat(
        FakeState(),
        job_id="job-dispatcher",
        leased_by="worker:43210",
        lease_ttl_s=9,
        interval_s=0.01,
    )

    heartbeat.start()
    assert second_heartbeat.wait(timeout=1.0)
    heartbeat.stop()

    assert calls[0]["job_id"] == "job-dispatcher"
    assert calls[0]["leased_by"] == "worker:43210"
    assert calls[0]["lease_ttl_s"] == 9
    assert len(calls) >= 2
    assert not heartbeat._thread.is_alive()


def test_dispatcher_reaper_reclaims_expired_pre_spawn_lease(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    state.update_dual_agent_workflow_job(
        job_id="job-dispatcher",
        leased_by="dispatcher-old",
        lease_expires_at=900,
    )
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43212

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=FakePopen,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
    )

    result = dispatcher.reap_stale_leases()

    assert result["reclaimed"] == ["job-dispatcher"]
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["leased_by"] is None
    assert job["lease_expires_at"] is None

    redrive = dispatcher.run_once()

    assert redrive["status"] == "spawned"
    assert popen_calls
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["pid"] == 43212
    assert job["recovery_point"] == "spawned"


def test_dispatcher_reaper_fails_dead_spawned_worker(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    state.update_dual_agent_workflow_job(
        job_id="job-dispatcher",
        status="running",
        pid=43210,
        recovery_point="spawned",
        leased_by="worker:43210",
        lease_expires_at=2000,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=lambda *args, **kwargs: None,
        pid_alive=lambda pid: False,
        now=lambda: 1000,
    )

    result = dispatcher.reap_stale_leases()

    assert result["failed"] == ["job-dispatcher"]
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["status"] == "failed"
    assert job["recovery_point"] == "terminal"
    assert json.loads(job["terminal_outcome_json"])["error"] == "worker_lease_stale_or_dead"


def test_dispatcher_admission_cap_prevents_claim_when_full(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path, job_id="job-waiting", token="token-waiting")
    _reserve_dispatcher_test_job(state, tmp_path, job_id="job-running", token="token-running")
    state.update_dual_agent_workflow_job(
        job_id="job-running",
        status="running",
        pid=43210,
        recovery_point="spawned",
        leased_by="worker:43210",
        lease_expires_at=2000,
    )
    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        max_concurrent_spawns=1,
        popen=lambda *args, **kwargs: None,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
    )

    result = dispatcher.run_once()

    assert result["status"] == "backpressure"
    waiting = state.get_dual_agent_workflow_job(job_id="job-waiting")
    assert waiting["leased_by"] is None
    assert waiting["recovery_point"] == "reserved"


def test_dispatcher_retryable_spawn_failure_uses_capped_backoff(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)

    def fail_popen(*args, **kwargs):
        raise OSError("temporary spawn failure")

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=fail_popen,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
        base_backoff_s=10,
        max_backoff_s=60,
        max_dispatch_attempts=3,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.run_once()
    immediate = dispatcher.run_once()

    assert result["status"] == "retry_scheduled"
    assert immediate["status"] == "idle"
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["status"] == "submitted"
    assert job["recovery_point"] == "request_written"
    assert job["dispatch_attempts"] == 1
    assert job["next_dispatch_at"] == 1010
    assert job["leased_by"] is None


def test_dispatcher_retryable_spawn_failure_caps_backoff_after_jitter(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    now = {"value": 1000}

    def fail_popen(*args, **kwargs):
        raise OSError("temporary spawn failure")

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=fail_popen,
        pid_alive=lambda pid: True,
        now=lambda: now["value"],
        base_backoff_s=10,
        max_backoff_s=15,
        max_dispatch_attempts=4,
        jitter=lambda _delay: 999,
    )

    first = dispatcher.run_once()
    now["value"] = int(first["next_dispatch_at"])
    second = dispatcher.run_once()

    assert first["status"] == "retry_scheduled"
    assert first["next_dispatch_at"] == 1015
    assert second["status"] == "retry_scheduled"
    assert second["next_dispatch_at"] == 1030
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["dispatch_attempts"] == 2


def test_dispatcher_retryable_spawn_failure_parks_at_attempt_cap(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    now = {"value": 1000}

    def fail_popen(*args, **kwargs):
        raise OSError("temporary spawn failure")

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=fail_popen,
        pid_alive=lambda pid: True,
        now=lambda: now["value"],
        base_backoff_s=10,
        max_backoff_s=60,
        max_dispatch_attempts=2,
        jitter=lambda _delay: 0,
    )

    first = dispatcher.run_once()
    now["value"] = 1010
    second = dispatcher.run_once()
    third = dispatcher.run_once()

    assert first["status"] == "retry_scheduled"
    assert second["status"] == "parked"
    assert second["parked_reason"] == "max_dispatch_attempts_exceeded: temporary spawn failure"
    assert third["status"] == "idle"
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["status"] == "parked"
    assert job["dispatch_attempts"] == 2
    assert job["leased_by"] is None
    assert job["parked_reason"] == "max_dispatch_attempts_exceeded: temporary spawn failure"


def test_dispatcher_budget_hook_parks_before_spawn(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(state, tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        budget_hook=lambda _row: False,
        popen=FakePopen,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
    )

    result = dispatcher.run_once()
    second = dispatcher.run_once()

    assert result["status"] == "parked"
    assert result["reason"] == "budget_cap_exceeded"
    assert second["status"] == "idle"
    assert popen_calls == []
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["status"] == "parked"
    assert job["parked_reason"] == "budget_cap_exceeded"
    assert job["leased_by"] is None


def test_dispatcher_poison_job_parks_without_retry_loop(tmp_path):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    _server(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _reserve_dispatcher_test_job(
        state,
        tmp_path,
        request_payload_json="{not valid json",
    )
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    dispatcher = WorkflowJobDispatcher(
        state,
        dispatcher_id="dispatcher-test",
        popen=FakePopen,
        pid_alive=lambda pid: True,
        now=lambda: 1000,
        jitter=lambda _delay: 0,
    )

    result = dispatcher.run_once()
    second = dispatcher.run_once()

    assert result["status"] == "parked"
    assert second["status"] == "idle"
    assert popen_calls == []
    job = state.get_dual_agent_workflow_job(job_id="job-dispatcher")
    assert job["status"] == "parked"
    assert job["parked_reason"].startswith("invalid_request_payload_json")
    assert job["leased_by"] is None


def test_dispatcher_run_forever_repeats_reaper_and_dispatch_until_stopped(monkeypatch):
    from supervisor.workflow_job_dispatcher import WorkflowJobDispatcher

    dispatcher = WorkflowJobDispatcher(
        object(),
        dispatcher_id="dispatcher-test",
        now=lambda: 1000,
    )
    stop_event = threading.Event()
    calls: list[str] = []

    def fake_reap():
        calls.append("reap")
        return {"reclaimed": [], "failed": [], "completed": []}

    def fake_run_once():
        calls.append("dispatch")
        if calls.count("dispatch") == 2:
            stop_event.set()
        return {"status": "idle"}

    monkeypatch.setattr(dispatcher, "reap_stale_leases", fake_reap)
    monkeypatch.setattr(dispatcher, "run_once", fake_run_once)

    dispatcher.run_forever(interval_s=0.01, stop_event=stop_event)

    assert calls == ["reap", "dispatch", "reap", "dispatch"]


def test_dispatcher_cli_once_runs_reaper_and_dispatch(monkeypatch, capsys, tmp_path):
    import supervisor.workflow_job_dispatcher as dispatcher_module

    constructed: dict[str, object] = {}

    class FakeSupervisor:
        state_db = str(tmp_path / "state.db")

    class FakeConfig:
        supervisor = FakeSupervisor()

    class FakeState:
        def __init__(self, db_path):
            constructed["state_db"] = db_path

    class FakeDispatcher:
        def __init__(self, state, **kwargs):
            constructed["state_type"] = type(state).__name__
            constructed.update(kwargs)

        def reap_stale_leases(self):
            return {"reclaimed": ["job-old"], "failed": [], "completed": []}

        def run_once(self):
            return {"status": "spawned", "job_id": "job-new"}

    monkeypatch.setattr(dispatcher_module.Config, "load", lambda path: FakeConfig())
    monkeypatch.setattr(dispatcher_module, "State", FakeState)
    monkeypatch.setattr(dispatcher_module, "WorkflowJobDispatcher", FakeDispatcher)

    exit_code = dispatcher_module.main([
        "--config",
        str(tmp_path / "config.yaml"),
        "--once",
        "--dispatcher-id",
        "dispatcher-cli-test",
        "--max-concurrent-spawns",
        "2",
        "--lease-ttl-s",
        "7",
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output == {
        "dispatch": {"job_id": "job-new", "status": "spawned"},
        "reaper": {"completed": [], "failed": [], "reclaimed": ["job-old"]},
    }
    assert constructed["state_db"] == str(tmp_path / "state.db")
    assert constructed["dispatcher_id"] == "dispatcher-cli-test"
    assert constructed["max_concurrent_spawns"] == 2
    assert constructed["lease_ttl_s"] == 7


def test_dispatcher_cli_without_once_runs_long_lived_loop(monkeypatch, capsys, tmp_path):
    import supervisor.workflow_job_dispatcher as dispatcher_module

    constructed: dict[str, object] = {}

    class FakeSupervisor:
        state_db = str(tmp_path / "state.db")

    class FakeConfig:
        supervisor = FakeSupervisor()

    class FakeState:
        def __init__(self, db_path):
            constructed["state_db"] = db_path

    class FakeDispatcher:
        def __init__(self, state, **kwargs):
            constructed["state_type"] = type(state).__name__
            constructed.update(kwargs)

        def run_forever(self, *, interval_s):
            constructed["run_forever_interval_s"] = interval_s

    monkeypatch.setattr(dispatcher_module.Config, "load", lambda path: FakeConfig())
    monkeypatch.setattr(dispatcher_module, "State", FakeState)
    monkeypatch.setattr(dispatcher_module, "WorkflowJobDispatcher", FakeDispatcher)

    exit_code = dispatcher_module.main([
        "--config",
        str(tmp_path / "config.yaml"),
        "--dispatcher-id",
        "dispatcher-cli-test",
        "--max-concurrent-spawns",
        "2",
        "--lease-ttl-s",
        "7",
        "--interval-s",
        "0.25",
    ])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    assert constructed["state_db"] == str(tmp_path / "state.db")
    assert constructed["dispatcher_id"] == "dispatcher-cli-test"
    assert constructed["max_concurrent_spawns"] == 2
    assert constructed["lease_ttl_s"] == 7
    assert constructed["run_forever_interval_s"] == 0.25


@pytest.mark.asyncio
async def test_submit_workflow_job_payload_round_trips_agentic_policy_fields(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio
    from mcp_tools.codex_supervisor_workflow_cli import workflow_kwargs_from_payload

    server, _state = _server(tmp_path)

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            pass

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    result = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run long workflow out of band with agentic policy.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
        agentic_lead_policy="required",
        min_subagents=2,
        required_roles=["codebase_audit", "independent_reviewer"],
        solo_exception_for_artifact_only_gates=True,
        required_evidence_grade="runtime_native",
        config_path=str(tmp_path / "config.yaml"),
    ))
    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=result["job_id"]))

    request = json.loads(Path(result["request_path"]).read_text(encoding="utf-8"))
    kwargs = workflow_kwargs_from_payload(request)

    assert result["status"] == "submitted"
    assert poll["status"] == "running"
    assert kwargs["agentic_lead_policy"] == "required"
    assert kwargs["min_subagents"] == 2
    assert kwargs["required_roles"] == ["codebase_audit", "independent_reviewer"]
    assert kwargs["solo_exception_for_artifact_only_gates"] is True
    assert kwargs["required_evidence_grade"] == "runtime_native"
    assert poll["job_id"] == result["job_id"]


@pytest.mark.asyncio
async def test_submit_workflow_job_payload_round_trips_reviewer_infra_retry_policy(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio
    from mcp_tools.codex_supervisor_workflow_cli import workflow_kwargs_from_payload

    server, _state = _server(tmp_path)

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            pass

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    result = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run long workflow out of band with reviewer infra retry policy.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
        reviewer_infra_retry_limit=4,
        reviewer_infra_retry_backoff_s=0.25,
        config_path=str(tmp_path / "config.yaml"),
    ))
    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=result["job_id"]))

    request = json.loads(Path(result["request_path"]).read_text(encoding="utf-8"))
    kwargs = workflow_kwargs_from_payload(request)

    assert result["status"] == "submitted"
    assert poll["status"] == "running"
    assert request["reviewer_infra_retry_limit"] == 4
    assert request["reviewer_infra_retry_backoff_s"] == 0.25
    assert kwargs["reviewer_infra_retry_limit"] == 4
    assert kwargs["reviewer_infra_retry_backoff_s"] == 0.25


@pytest.mark.asyncio
async def test_submit_dual_agent_workflow_job_dedupes_same_client_token(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(stdio, "_pid_alive", lambda pid: True)

    kwargs = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
        "config_path": str(tmp_path / "config.yaml"),
        "client_token": " retry-token ",
    }

    first = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](**kwargs))
    second = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](**kwargs))

    assert first["status"] == "submitted"
    assert second["job_id"] == first["job_id"]
    assert second["status"] == "submitted"
    assert second["reattached"] is True
    assert len(popen_calls) == 0
    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id=first["job_id"]))
    assert poll["status"] == "running"
    assert len(popen_calls) == 1
    rows = state._conn.execute("SELECT COUNT(*) FROM dual_agent_workflow_jobs").fetchone()
    assert rows[0] == 1


@pytest.mark.asyncio
async def test_submit_dual_agent_workflow_job_derives_idempotency_for_legacy_callers(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, _state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)

    kwargs = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
        "config_path": str(tmp_path / "config.yaml"),
    }

    first = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](**kwargs))
    second = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](**kwargs))

    assert second["job_id"] == first["job_id"]
    assert second["reattached"] is True
    assert len(popen_calls) == 0


@pytest.mark.asyncio
async def test_submit_dual_agent_workflow_job_derived_tokens_differ_for_different_payloads(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, _state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)

    base = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "tool_receipts": _tool_receipts(),
        "config_path": str(tmp_path / "config.yaml"),
    }

    first = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        **base,
        intent="Run long workflow out of band.",
    ))
    second = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        **base,
        intent="Run a different logical workflow.",
    ))

    assert second["job_id"] != first["job_id"]
    assert "reattached" not in second
    assert len(popen_calls) == 0


@pytest.mark.asyncio
async def test_submit_dual_agent_workflow_job_keeps_different_tokens_independent(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, _state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)

    base = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
        "config_path": str(tmp_path / "config.yaml"),
    }

    first = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        **base,
        client_token="token-a",
    ))
    second = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](
        **base,
        client_token="token-b",
    ))

    assert second["job_id"] != first["job_id"]
    assert "reattached" not in second
    assert len(popen_calls) == 0


@pytest.mark.asyncio
async def test_submit_dual_agent_workflow_job_concurrent_same_token_launches_once(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            time.sleep(0.01)
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    kwargs = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
        "config_path": str(tmp_path / "config.yaml"),
        "client_token": "same-concurrent-token",
    }

    async def submit_once():
        return await asyncio.to_thread(
            server.tools["submit_dual_agent_workflow_job"],
            **kwargs,
        )

    results = await asyncio.gather(*(submit_once() for _ in range(8)))

    job_ids = {result["job_id"] for result in results}
    assert len(job_ids) == 1
    assert len(popen_calls) == 0
    rows = state._conn.execute("SELECT COUNT(*) FROM dual_agent_workflow_jobs").fetchone()
    assert rows[0] == 1


def test_reserve_dual_agent_workflow_job_eight_process_race_reserves_once(tmp_path):
    db_path = str(tmp_path / "state.db")
    State(db_path)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(8)
    queue = context.Queue()
    processes = [
        context.Process(
            target=_reserve_same_workflow_token_process,
            args=(db_path, barrier, queue, index),
        )
        for index in range(8)
    ]

    for process in processes:
        process.start()
    results = [queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    created = [result for result in results if result[1] is True]
    assert len(created) == 1
    job_ids = {result[2] for result in results}
    assert len(job_ids) == 1
    assert {result[3] for result in results} == {"reserved"}

    state = State(db_path)
    rows = state._conn.execute("SELECT COUNT(*) FROM dual_agent_workflow_jobs").fetchone()
    assert rows[0] == 1


@pytest.mark.asyncio
async def test_catch_up_dual_agent_workflow_returns_cursor_page(tmp_path):
    server, state = _server(tmp_path)
    first = state.write_event(
        run_id="workflow-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={"task_id": "workflow-1", "status": "running", "seq": 1},
    )
    state.write_event(
        run_id="other-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={"task_id": "other", "status": "noise"},
    )
    second = state.write_event(
        run_id="workflow-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={"task_id": "workflow-1", "status": "progress", "seq": 2},
    )
    state.write_event(
        run_id="other-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={"task_id": "other", "status": "noise-2"},
    )
    third = state.write_event(
        run_id="workflow-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={"task_id": "workflow-1", "status": "progress", "seq": 3},
    )
    before_max = state._conn.execute("SELECT MAX(event_id) FROM events").fetchone()[0]

    page = await _maybe_await(server.tools["catch_up_dual_agent_workflow"](
        run_id="workflow-run",
        last_event_id=first,
        limit=2,
    ))

    assert [event["event_id"] for event in page["events"]] == [second, third]
    assert third - first > 2
    assert page["next_event_id"] == third
    assert page["count"] == 2
    assert page["has_more"] is True
    assert state._conn.execute("SELECT MAX(event_id) FROM events").fetchone()[0] == before_max

    empty = await _maybe_await(server.tools["catch_up_dual_agent_workflow"](
        run_id="workflow-run",
        last_event_id=page["next_event_id"],
        limit=100,
    ))

    assert empty["events"] == []
    assert empty["next_event_id"] == third
    assert empty["has_more"] is False


@pytest.mark.asyncio
async def test_resumable_transport_drop_reconnect_catches_up_and_polls_terminal_outcome(
    monkeypatch,
    tmp_path,
):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[list[str]] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append(list(argv))

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)
    kwargs = {
        "cwd": str(tmp_path),
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "intent": "Run long workflow out of band.",
        "tool_receipts": _tool_receipts(),
        "config_path": str(tmp_path / "config.yaml"),
        "client_token": "stable-reconnect-token",
    }

    first_submit = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](**kwargs))
    last_seen_event_id = state.latest_event_id("workflow-run")
    missed_progress = state.write_event(
        run_id="workflow-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={
            "task_id": "workflow-1",
            "job_id": first_submit["job_id"],
            "status": "progress",
            "seq": 1,
        },
    )
    state.write_event(
        run_id="other-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={"task_id": "other", "status": "gap"},
    )
    missed_progress_2 = state.write_event(
        run_id="workflow-run",
        source="dual_agent",
        kind="dual_agent_workflow_job",
        payload={
            "task_id": "workflow-1",
            "job_id": first_submit["job_id"],
            "status": "progress",
            "seq": 2,
        },
    )
    terminal_outcome = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-1",
        "steps": [{"gate": "outcome_review", "status": "accepted"}],
    }
    state.complete_dual_agent_workflow_job(
        job_id=first_submit["job_id"],
        status="accepted",
        terminal_outcome=terminal_outcome,
    )
    result_path = Path(first_submit["result_path"])
    if result_path.exists():
        result_path.unlink()

    second_submit = await _maybe_await(server.tools["submit_dual_agent_workflow_job"](**kwargs))
    catch_up = await _maybe_await(server.tools["catch_up_dual_agent_workflow"](
        run_id="workflow-run",
        last_event_id=last_seen_event_id,
        limit=20,
    ))
    poll = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](
        job_id=first_submit["job_id"],
    ))
    empty = await _maybe_await(server.tools["catch_up_dual_agent_workflow"](
        run_id="workflow-run",
        last_event_id=catch_up["next_event_id"],
        limit=20,
    ))

    assert second_submit["job_id"] == first_submit["job_id"]
    assert second_submit["reattached"] is True
    assert second_submit["status"] == "accepted"
    assert second_submit["result"] == terminal_outcome
    assert len(popen_calls) == 0
    caught_ids = [event["event_id"] for event in catch_up["events"]]
    assert caught_ids == sorted(set(caught_ids))
    assert missed_progress in caught_ids
    assert missed_progress_2 in caught_ids
    assert any(
        event["kind"] == "dual_agent_workflow_terminal_outcome"
        for event in catch_up["events"]
    )
    assert empty["events"] == []
    assert poll["status"] == "accepted"
    assert poll["result"] == terminal_outcome


def test_reconnect_protocol_doc_is_present():
    protocol = Path(
        "docs/dual-agent/durable-substrate-s5-resumable-transport-20260531/"
        "reconnect-protocol.md"
    ).read_text(encoding="utf-8")

    assert "client_token" in protocol
    assert "last_event_id" in protocol
    assert "submit_dual_agent_workflow_job" in protocol
    assert "catch_up_dual_agent_workflow" in protocol
    assert "poll_dual_agent_workflow_job" in protocol
    assert "monotonic but not contiguous" in protocol
    assert "read-only" in protocol


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_reads_durable_result_after_transport_loss(tmp_path):
    server, state = _server(tmp_path)
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-1"
    job_dir.mkdir(parents=True)
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    request_path.write_text("{}", encoding="utf-8")
    result_path.write_text(
        json.dumps({
            "status": "accepted",
            "run_id": "workflow-run",
            "task_id": "workflow-1",
            "steps": [{"gate": "outcome_review", "status": "accepted"}],
        }),
        encoding="utf-8",
    )
    log_path.write_text("worker completed\n", encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-1",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        pid=987654,
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
    )

    result = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id="job-1"))

    assert result["status"] == "accepted"
    assert result["recovery_point"] == "terminal"
    assert result["result"]["steps"] == [{"gate": "outcome_review", "status": "accepted"}]
    job = state.get_dual_agent_workflow_job(job_id="job-1")
    assert job["status"] == "accepted"
    assert job["terminal_status"] == "accepted"
    assert json.loads(job["terminal_outcome_json"])["steps"] == [
        {"gate": "outcome_review", "status": "accepted"}
    ]

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["workflow_jobs"][0]["status"] == "accepted"


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_reads_ledger_result_when_result_file_deleted(tmp_path):
    server, state = _server(tmp_path)
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-ledger"
    job_dir.mkdir(parents=True)
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    request_path.write_text("{}", encoding="utf-8")
    result_path.write_text("{}", encoding="utf-8")
    log_path.write_text("worker completed\n", encoding="utf-8")
    terminal_outcome = {
        "status": "accepted",
        "run_id": "workflow-run",
        "task_id": "workflow-1",
        "steps": [{"gate": "outcome_review", "status": "accepted"}],
    }
    state.upsert_dual_agent_workflow_job(
        job_id="job-ledger",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        pid=987654,
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
    )
    state.complete_dual_agent_workflow_job(
        job_id="job-ledger",
        status="accepted",
        terminal_outcome=terminal_outcome,
    )
    result_path.unlink()

    result = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id="job-ledger"))

    assert result["status"] == "accepted"
    assert result["error"] in (None, "")
    assert result["result"] == terminal_outcome
    assert state.get_dual_agent_workflow_job(job_id="job-ledger")["terminal_status"] == "accepted"


def test_workflow_cli_records_terminal_outcome_in_ledger(tmp_path):
    from mcp_tools.codex_supervisor_workflow_cli import persist_detached_workflow_terminal_outcome

    state = State(str(tmp_path / "state.db"))
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-cli"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text("{}", encoding="utf-8")
    result_path.write_text("{}", encoding="utf-8")
    log_path.write_text("", encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-cli",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
    )
    terminal_outcome = {"status": "accepted", "run_id": "workflow-run", "task_id": "workflow-1"}

    assert persist_detached_workflow_terminal_outcome(
        request_payload={"job_id": "job-cli"},
        result=terminal_outcome,
        state=state,
        output_path=result_path,
        returncode=0,
    ) is True

    job = state.get_dual_agent_workflow_job(job_id="job-cli")
    assert job["status"] == "accepted"
    assert json.loads(job["terminal_outcome_json"]) == terminal_outcome
    terminal_events = [
        event
        for event in state.read_events_since("workflow-run", after_event_id=0, limit=20)
        if event["kind"] == "dual_agent_workflow_terminal_outcome"
    ]
    assert terminal_events
    assert terminal_events[-1]["payload"]["job_id"] == "job-cli"


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_ledger_wins_over_result_file_cache(tmp_path):
    server, state = _server(tmp_path)
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-mismatch"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text("{}", encoding="utf-8")
    log_path.write_text("", encoding="utf-8")
    ledger_outcome = {"status": "accepted", "run_id": "workflow-run", "task_id": "workflow-1"}
    file_outcome = {"status": "blocked", "run_id": "workflow-run", "task_id": "workflow-1"}
    result_path.write_text(json.dumps(file_outcome), encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-mismatch",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
    )
    state.complete_dual_agent_workflow_job(
        job_id="job-mismatch",
        status="accepted",
        terminal_outcome=ledger_outcome,
    )

    result = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id="job-mismatch"))

    assert result["status"] == "accepted"
    assert result["result"] == ledger_outcome
    discrepancy_events = [
        event
        for event in state.read_events_since("workflow-run", after_event_id=0, limit=20)
        if event["kind"] == "dual_agent_workflow_terminal_discrepancy"
    ]
    assert discrepancy_events
    assert discrepancy_events[-1]["payload"]["job_id"] == "job-mismatch"


@pytest.mark.asyncio
async def test_poll_dual_agent_workflow_job_does_not_emit_discrepancy_for_matching_cache(tmp_path):
    server, state = _server(tmp_path)
    job_dir = tmp_path / ".handoff" / "workflow-jobs" / "job-match"
    request_path = job_dir / "request.json"
    result_path = job_dir / "result.json"
    log_path = job_dir / "worker.log"
    job_dir.mkdir(parents=True)
    request_path.write_text("{}", encoding="utf-8")
    log_path.write_text("", encoding="utf-8")
    outcome = {
        "task_id": "workflow-1",
        "run_id": "workflow-run",
        "status": "accepted",
        "steps": [{"status": "accepted", "gate": "outcome_review"}],
    }
    result_path.write_text(json.dumps({
        "steps": [{"gate": "outcome_review", "status": "accepted"}],
        "status": "accepted",
        "task_id": "workflow-1",
        "run_id": "workflow-run",
    }, indent=2), encoding="utf-8")
    state.upsert_dual_agent_workflow_job(
        job_id="job-match",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        request_path=str(request_path),
        result_path=str(result_path),
        log_path=str(log_path),
    )
    state.complete_dual_agent_workflow_job(
        job_id="job-match",
        status="accepted",
        terminal_outcome=outcome,
    )
    cursor = state.latest_event_id("workflow-run")

    result = await _maybe_await(server.tools["poll_dual_agent_workflow_job"](job_id="job-match"))

    assert result["result"] == outcome
    new_events = state.read_events_since("workflow-run", after_event_id=cursor, limit=20)
    assert [event for event in new_events if event["kind"] == "dual_agent_workflow_terminal_discrepancy"] == []


def test_complete_dual_agent_workflow_job_requires_terminal_outcome(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-atomic",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        request_path="request.json",
        result_path="result.json",
        log_path="worker.log",
    )

    with pytest.raises(ValueError, match="terminal_outcome"):
        state.complete_dual_agent_workflow_job(
            job_id="job-atomic",
            status="accepted",
            terminal_outcome=None,  # type: ignore[arg-type]
        )

    job = state.get_dual_agent_workflow_job(job_id="job-atomic")
    assert job["status"] == "running"
    assert job["terminal_outcome_json"] is None


def test_complete_dual_agent_workflow_job_rolls_back_status_when_terminal_event_fails(monkeypatch, tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-rollback",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        request_path="request.json",
        result_path="result.json",
        log_path="worker.log",
    )

    def fail_insert_event(**kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(state, "_insert_event_unlocked", fail_insert_event)

    with pytest.raises(RuntimeError, match="injected event failure"):
        state.complete_dual_agent_workflow_job(
            job_id="job-rollback",
            status="accepted",
            terminal_outcome={"status": "accepted", "api_key": "sk-proj-secretvalue"},
        )

    job = state.get_dual_agent_workflow_job(job_id="job-rollback")
    assert job["status"] == "running"
    assert job["terminal_status"] is None
    assert job["terminal_outcome_json"] is None


def test_complete_dual_agent_workflow_job_redacts_terminal_outcome_at_rest(tmp_path):
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow_job(
        job_id="job-redact",
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        status="running",
        request_path="request.json",
        result_path="result.json",
        log_path="worker.log",
    )

    state.complete_dual_agent_workflow_job(
        job_id="job-redact",
        status="accepted",
        terminal_outcome={"status": "accepted", "token": "sk-proj-secretvalue"},
    )

    stored = state.get_dual_agent_workflow_job(job_id="job-redact")["terminal_outcome_json"]
    assert "sk-proj-secretvalue" not in stored
    assert "[REDACTED_API_KEY]" in stored


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_records_trivial_route_and_skips_planning_gates(tmp_path):
    server, state = _server(tmp_path, claims=["tests passed", "implemented"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="One-line config tweak.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["task_complexity"] == "trivial"
    assert [step["gate"] for step in result["steps"]] == ["execution", "outcome_review"]
    route_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_workflow_route"
    ]
    assert route_events
    assert route_events[0]["task_complexity"] == "trivial"
    skill_events = [
        row for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_skill_receipt_validation"
    ]
    assert skill_events == []
    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["workflow_routes"][0]["task_complexity"] == "trivial"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_small_route_requires_only_prd_skill_receipts(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    receipts = [
        receipt for receipt in _tool_receipts()
        if receipt.get("kind") != "skill_run"
        or receipt.get("stage") in {"to_prd", "prd_grill"}
    ]

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Small single-file helper change.",
        max_rounds_per_gate=1,
        task_complexity="small",
        tool_receipts=receipts,
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["task_complexity"] == "small"
    assert result["workflow_route"]["required_skill_stages"] == ["to_prd", "prd_grill"]
    assert [step["gate"] for step in result["steps"]] == [
        "prd_review",
        "execution",
        "outcome_review",
    ]


def test_select_workflow_route_infers_modes_and_aliases():
    assert select_workflow_route(
        intent="Fix a typo.",
        user_facing=False,
    )["task_complexity"] == "trivial"
    assert select_workflow_route(
        intent="Small single-file helper change.",
        user_facing=False,
    )["task_complexity"] == "small"
    assert select_workflow_route(
        intent="Ambiguous brainstorm request.",
        user_facing=False,
    )["task_complexity"] == "vague"
    assert select_workflow_route(
        intent="Unknown work.",
        user_facing=False,
        task_complexity="standard",
    )["task_complexity"] == "large"
    assert select_workflow_route(
        intent="General backend work.",
        user_facing=False,
        task_complexity="nonsense",
    )["task_complexity"] == "large"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_passes_budget_to_each_lead_gate(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    runner_calls: list[list[str]] = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run workflow with a non-default live probe budget.",
        max_rounds_per_gate=1,
        agentic_lead_policy="off",
        budget_usd=42.5,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert len(runner_calls) == 6
    for argv in runner_calls:
        budget_flag_index = argv.index("--max-budget-usd")
        assert argv[budget_flag_index + 1] == "42.5"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_can_pass_dynamic_workflow_preview_policy(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a codebase-audit fan-out task behind the lead boundary.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[*_tool_receipts(), *_dynamic_workflow_receipts(tmp_path)],
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["execution_layer_mode"] == "dynamic_workflow_preview"
    assert result["workflow_route"]["dynamic_workflow_task_class"] == "codebase_audit"
    packet = json.loads((tmp_path / ".handoff" / "workflow-1.json").read_text())
    policy = packet["execution_layer_policy"]
    assert policy["supervision_layer"] == "codex_plus_lead"
    assert policy["lead_execution_layer"] == "lead_worker_may_use_dynamic_workflow"
    assert policy["codex_supervises_final_artifact"] is True
    assert "per_subagent_budget_caps_verified" in policy["preview_required_gates"]
    assert "throwaway_worktree_comparison_recorded" in policy["preview_required_gates"]


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_dynamic_preview_with_forged_replay_refs(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a forged dynamic preview receipt.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
        agentic_lead_policy="off",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[*_tool_receipts(), *_dynamic_workflow_receipts()],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert result["final_gate_result"]["probes"]["P13"]["reason"] == "missing_dynamic_workflow_receipts"
    assert "missing_manifest_sha256" in str(result["final_gate_result"]["probes"]["P13"]["details"])
    assert runner_calls == []


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_generates_dynamic_manifest_and_auto_receipts(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a codebase-audit fan-out task behind the lead boundary.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[*_tool_receipts(), *_dynamic_subagent_result_receipts(tmp_path)],
    ))

    assert result["status"] == "accepted"
    manifest = result["workflow_route"]["dynamic_workflow_manifest"]
    assert manifest["task_class"] == "codebase_audit"
    assert manifest["subagents"][0]["persona_id"] == "reviewer.codebase_audit"
    assert result["workflow_route"]["dynamic_workflow_synthesis"]["status"] == "accepted"
    decision = result["workflow_route"]["dynamic_workflow_synthesis"]["decisions"][0]
    assert decision["critical_review"]["schema_version"] == "critical-review/v1"
    assert decision["critical_review"]["contradictions_checked"] == [
        "manifest registration",
        "subagent output receipt",
    ]

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    validations = transcript["dynamic_workflow_receipt_validations"]
    assert validations[0]["probe"]["status"] == "green"
    assert any(
        receipt.get("kind") == "dynamic_workflow_receipt"
        for receipt in validations[0]["tool_receipts"]
    )
    manifest_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_dynamic_workflow_manifest"
    ]
    assert manifest_events
    assert manifest_events[0]["manifest_sha256"]


@pytest.mark.asyncio
async def test_agentic_required_blocks_solo_execution_before_lead(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Should not run.\n" + _outcome_block("workflow-1")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Require agentic provenance before lead synthesis.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=[
            *_tool_receipts(),
            {
                "receipt_id": "lead-solo",
                "kind": "agentic_lead_execution",
                "status": "passed",
                "execution_mode": "solo",
            },
        ],
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert result["final_gate_result"]["probes"]["P13"]["reason"] == "agentic_lead_policy_blocked"
    assert "agentic_lead_solo_execution" in str(result["final_gate_result"]["probes"]["P13"]["details"])
    assert runner_calls == []


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_required_policy_still_blocks_without_executor_receipts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    planner_calls = []
    lead_calls = []

    def fake_runner(argv, **kwargs):
        prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
        if "Agentic worker roster planning" in prompt:
            planner_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="No roster available.\n", stderr="")
        lead_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Should not run.\n" + _outcome_block("workflow-1")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Required policy must fail closed when Codex cannot produce executor receipts.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=_tool_receipts(),
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert planner_calls
    assert lead_calls == []
    p13 = result["final_gate_result"]["probes"]["P13"]
    assert p13["status"] == "red"
    assert p13["reason"] == "agentic_lead_policy_blocked"
    assert "insufficient_subagents" in str(p13["details"])
    productions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_agentic_worker_production"
    ]
    assert productions
    assert productions[-1]["status"] == "blocked"
    assert productions[-1]["receipt_count"] == 0


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    planner_calls = []
    worker_calls = []
    lead_calls = []

    roster = {
        "workers": [
            {
                "worker_id": "audit-1",
                "role": "codebase_audit",
                "persona_id": "reviewer.codebase_audit",
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed"],
                "prompt": "Inspect the agentic executor wiring and report risks only.",
                "timeout_s": 60,
                "budget_usd": 0.25,
            }
        ]
    }

    def fake_runner(argv, **kwargs):
        prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
        if "Agentic worker roster planning" in prompt:
            planner_calls.append(argv)
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=build_lead_replay_stdout(
                    "Roster planned.\n"
                    f"<agentic_worker_roster>{json.dumps(roster)}</agentic_worker_roster>"
                ),
                stderr="",
            )
        if "Read-only agentic worker" in prompt:
            worker_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="worker accepted\n", stderr="")
        lead_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Require agentic provenance and run read-only workers inline.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=_tool_receipts(),
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    ))

    assert result["status"] == "accepted"
    assert planner_calls
    assert worker_calls
    assert lead_calls
    worker_dir = tmp_path / ".handoff" / "agentic-workers" / "workflow-1" / "audit-1"
    assert (worker_dir / "output.json").is_file()
    assert (worker_dir / "transcript.jsonl").is_file()

    productions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_agentic_worker_production"
    ]
    assert productions
    assert productions[-1]["status"] == "passed"
    assert productions[-1]["receipts"][0]["transcript_ref"].startswith(".handoff/agentic-workers/")

    validations = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_dynamic_workflow_receipt_validation"
    ]
    assert validations
    policy = validations[0]["probe"]["details"]["agentic_policy"]
    assert policy["status"] == "accepted"
    assert policy["subagents"][0]["evidence_grade"] == "runtime_native"
    progress_events = [
        event for event in state.read_events_since("workflow-run", after_event_id=0, limit=100)
        if event["kind"] == "dual_agent_agentic_worker_progress"
    ]
    assert progress_events
    assert progress_events[-1]["payload"]["worker_id"] == "audit-1"
    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["agentic_worker_progress"]


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_hydrates_durable_agentic_worker_receipts_before_producer(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.agentic_workers import AgenticWorkerSpec, run_agentic_worker
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    receipt = run_agentic_worker(
        AgenticWorkerSpec(
            task_id="workflow-1",
            worker_id="audit-1",
            role="codebase_audit",
            command=("worker", "audit"),
            cwd=tmp_path,
            agent_id="agent-audit-1",
            permission_mode="readOnly",
            tool_pins=("rg", "sed"),
            timeout_s=30,
            budget_usd=0.1,
        ),
        runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="audit ok\n", stderr=""),
    )
    planner_calls = []
    lead_calls = []

    def fake_runner(argv, **kwargs):
        prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
        if "Agentic worker roster planning" in prompt:
            planner_calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="should not plan")
        lead_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Workflow response.\n" + _outcome_block("workflow-1")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Required policy should hydrate existing worker receipts before producer planning.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=_tool_receipts(),
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    ))

    assert result["status"] == "accepted"
    assert planner_calls == []
    assert lead_calls
    route = result["workflow_route"]
    assert route["agentic_worker_hydration"]["receipt_count"] == 1
    assert route["agentic_worker_production"]["status"] == "skipped_existing_receipts"
    productions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_agentic_worker_production"
    ]
    assert productions[-1]["status"] == "skipped_existing_receipts"
    validations = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_dynamic_workflow_receipt_validation"
    ]
    subagent = validations[0]["probe"]["details"]["agentic_policy"]["subagents"][0]
    assert subagent["evidence_grade"] == "runtime_native"
    assert subagent["transcript_ref"] == receipt["transcript_ref"]


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_allowed_policy_runs_producer_without_blocking_on_missing_receipts(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    planner_calls = []
    lead_calls = []

    def fake_runner(argv, **kwargs):
        prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
        if "Agentic worker roster planning" in prompt:
            planner_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="No roster available.\n", stderr="")
        lead_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Allow agentic provenance without making missing worker receipts fatal.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=_tool_receipts(),
        agentic_lead_policy="allowed",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    ))

    assert result["status"] == "accepted"
    assert planner_calls
    assert lead_calls

    productions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_agentic_worker_production"
    ]
    assert productions
    assert productions[-1]["status"] == "blocked"
    assert productions[-1]["receipt_count"] == 0

    validations = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_dynamic_workflow_receipt_validation"
    ]
    assert validations
    policy = validations[0]["probe"]["details"]["agentic_policy"]
    assert policy["policy"] == "allowed"
    assert policy["status"] == "accepted"
    assert policy["blocking_findings"] == []


@pytest.mark.asyncio
async def test_dynamic_reviewer_synthesis_blocks_on_critical_disagreement(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a fan-out task with a critical reviewer disagreement.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
        agentic_lead_policy="off",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[
            *_tool_receipts(),
            *_dynamic_subagent_result_receipts(tmp_path, decision="block", severity="critical"),
        ],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert result["final_gate_result"]["escalation"]["reason"] == "dynamic_workflow_synthesis_blocked"
    assert runner_calls == []


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_dynamic_preview_without_p13_receipts(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a codebase-audit fan-out task behind the lead boundary.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic-workflow-preview",
        dynamic_workflow_task_class="codebase-audit",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert result["workflow_route"]["execution_layer_mode"] == "dynamic_workflow_preview"
    assert result["workflow_route"]["dynamic_workflow_task_class"] == "codebase_audit"
    assert result["final_gate_result"]["probes"]["P13"]["reason"] == "missing_dynamic_workflow_receipts"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"


@pytest.mark.asyncio
async def test_read_gate_transcript_includes_dynamic_workflow_receipt_validation(tmp_path):
    server, _state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a codebase-audit fan-out task behind the lead boundary.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[*_tool_receipts(), *_dynamic_workflow_receipts(tmp_path)],
    ))
    assert result["status"] == "accepted"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))

    validations = transcript["dynamic_workflow_receipt_validations"]
    assert validations
    assert validations[0]["probe"]["probe_id"] == "P13"
    assert validations[0]["probe"]["status"] == "green"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_auto_seeded_planning_stubs(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    state = State(str(tmp_path / "state.db"))
    runner_calls = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout("Should not run.\n" + _outcome_block("workflow-1")),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
        agentic_lead_policy="off",
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["final_gate_result"]["probes"]["P_planning"]["reason"] == "planning_validation_failed"
    assert runner_calls == []


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_requires_prd_tdd_skill_receipts(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Attempt workflow without PRD/TDD skill receipts.",
        max_rounds_per_gate=1,
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "workflow_start"
    assert result["final_gate_result"]["probes"]["P12"]["reason"] == "missing_prd_tdd_skill_receipts"
    assert result["final_gate_result"]["trace_envelope"]["failure_taxonomy"]["category"] == "governance"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_records_skill_receipt_validation(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run workflow with PRD/TDD skill receipts.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_skill_receipt_validation"
    ]
    assert events
    assert events[0]["probe"]["status"] == "green"
    assert events[0]["trace_envelope"]["policy_verdict"] == "accepted"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_runs_cursor_review_by_default(tmp_path):
    cursor_calls = []

    def fake_cursor_runner(request):
        cursor_calls.append(request)
        return _cursor_review_result(request.task_id)

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Use Cursor as the third reviewer.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert len(cursor_calls) == 1
    assert cursor_calls[0].gate == "outcome_review"
    assert result["workflow_route"]["requested_cursor_review"] is True
    assert result["workflow_route"]["effective_cursor_review"] is True
    assert result["workflow_route"]["cursor_review_gates"] == ["outcome_review"]
    cursor_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "tri_agent_cursor_review"
    ]
    assert len(cursor_events) == 1
    assert cursor_events[0]["gate"] == "outcome_review"
    assert all(event["cursor_review"]["accepted"] for event in cursor_events)
    assert cursor_events[0]["cursor_review"]["critical_review"]["schema_version"] == "critical-review/v1"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert len(transcript["cursor_reviews"]) == 1
    interactions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_interaction_message"
    ]
    review_request = next(event for event in interactions if event["message_type"] == "review_request")
    review_response = next(event for event in interactions if event["message_type"] == "review_response")
    gate_decision = next(
        event
        for event in interactions
        if event["message_type"] == "gate_decision" and event["gate"] == "outcome_review"
    )
    assert "Critical review:" in review_request["content"]
    assert review_request["critical_review"]["schema_version"] == "critical-review/v1"
    assert review_response["critical_review"]["schema_version"] == "critical-review/v1"
    assert gate_decision["critical_review"]["schema_version"] == "critical-review/v1"


@pytest.mark.asyncio
async def test_workflow_exposes_independent_reviewer_results_and_dual_writes_events(tmp_path):
    cursor_calls = []

    def fake_cursor_runner(request):
        cursor_calls.append(request)
        result = _cursor_review_result(request.task_id)
        return CursorInvocationResult(
            probe=result.probe,
            outcome=result.outcome,
            transcript=result.transcript,
            run_id="chatcmpl-gemini",
            status="finished",
            model="gemini-3.1-pro-preview",
            reviewer_runtime="litellm_structured",
            reviewer_output_mode="litellm_structured",
            reviewer_assurance="structured_text_only",
        )

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Expose the independent reviewer panel shape.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_output_mode="litellm_structured",
        reviewer_model="gemini-3.1-pro-preview",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    panel = result["final_gate_result"]["independent_reviewer_results"]
    assert len(panel) == 2
    reviewer = panel[0]
    assert reviewer["reviewer_id"] == "independent-reviewer-0"
    assert reviewer["runtime"] == "litellm_structured"
    assert reviewer["model"] == "gemini-3.1-pro-preview"
    assert reviewer["provider_family"] == "google"
    assert reviewer["tool_access"] == "text_only"
    assert reviewer["assurance_grade"] == "text_only"
    assert reviewer["decision"] == "accept"
    assert reviewer["severity"] == "none"
    assert reviewer["confidence"] == 0.91
    assert reviewer["transcript_refs"]
    assert reviewer["transcript_sha256"]
    assert reviewer["output_sha256"]
    codex_reviewer = panel[1]
    assert codex_reviewer["reviewer_id"] == "independent-reviewer-1"
    assert codex_reviewer["runtime"] == "codex_cli"
    assert codex_reviewer["model"] == "gpt-5.5"
    assert codex_reviewer["provider_family"] == "openai"
    assert codex_reviewer["tool_access"] == "codebase_tools"
    assert codex_reviewer["assurance_grade"] == "agentic"
    assert codex_reviewer["decision"] == "accept"
    assert codex_reviewer["transcript_sha256"]
    assert codex_reviewer["output_sha256"]

    events = [
        (row["kind"], json.loads(row["payload_json"]))
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] in {"independent_reviewer_review", "tri_agent_cursor_review"}
    ]
    assert [kind for kind, _payload in events] == [
        "independent_reviewer_review",
        "tri_agent_cursor_review",
    ]
    assert [item["provider_family"] for item in events[0][1]["independent_reviewer_results"]] == [
        "google",
        "openai",
    ]
    assert events[1][1]["cursor_review"]["independent_reviewer_results"][0]["provider_family"] == "google"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert len(transcript["independent_reviewer_reviews"]) == 1
    assert len(transcript["cursor_reviews"]) == 1
    assert [
        item["decision"]
        for item in transcript["independent_reviewer_reviews"][0]["independent_reviewer_results"]
    ] == ["accept", "accept"]


@pytest.mark.asyncio
async def test_read_gate_transcript_backfills_panel_results_from_legacy_cursor_event(tmp_path):
    server, state = _server(tmp_path)
    outcome = _cursor_review_result("workflow-1").outcome
    assert outcome is not None
    state.write_event(
        run_id="legacy-run",
        source="dual_agent",
        kind="tri_agent_cursor_review",
        payload={
            "task_id": "workflow-1",
            "gate": "outcome_review",
            "cursor_review": {
                "accepted": True,
                "outcome": outcome.model_dump(),
                "model": "gemini-3.1-pro-preview",
                "reviewer_runtime": "litellm_structured",
                "reviewer_output_mode": "litellm_structured",
                "reviewer_assurance": "structured_text_only",
                "transcript_tail": "<dual_agent_outcome>{}</dual_agent_outcome>",
            },
        },
    )

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="legacy-run",
        task_id="workflow-1",
    ))

    assert transcript["status"] == "ok"
    assert len(transcript["cursor_reviews"]) == 1
    assert transcript["independent_reviewer_reviews"] == []
    panel = transcript["cursor_reviews"][0]["independent_reviewer_results"]
    assert panel[0]["reviewer_id"] == "independent-reviewer-0"
    assert panel[0]["decision"] == "accept"
    assert panel[0]["provider_family"] == "google"
    assert panel[0]["assurance_grade"] == "text_only"


def test_reviewer_registry_supports_mock_panel_and_configured_structured_reviewer(tmp_path):
    from supervisor.reviewer_registry import MockReviewer, ReviewerSpec, configured_reviewers

    result = _cursor_review_result("workflow-1")
    mock = MockReviewer(
        spec=ReviewerSpec(
            reviewer_id="mock-reviewer",
            runtime="mock",
            provider_family="test",
            lineage=("test", "mock"),
            tool_access="none",
            assurance_grade="self_reported",
        ),
        result=result,
    )

    request = None

    def fake_runner(incoming_request):
        nonlocal request
        request = incoming_request
        return result

    reviewers = configured_reviewers(
        reviewer_output_mode="litellm_structured",
        reviewer_model="gemini-3.1-pro-preview",
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
    )

    assert mock.review(CursorInvocationRequest(
        task_id="workflow-1",
        gate="outcome_review",
        instruction="Review.",
        cwd=tmp_path,
    )).outcome == result.outcome
    assert len(reviewers) == 2
    assert reviewers[0].spec.provider_family == "google"
    assert reviewers[0].spec.assurance_grade == "text_only"
    assert reviewers[1].spec.reviewer_id == "independent-reviewer-1"
    assert reviewers[1].spec.runtime == "codex_cli"
    assert reviewers[1].spec.provider_family == "openai"
    assert reviewers[1].spec.tool_access == "codebase_tools"
    assert reviewers[1].spec.assurance_grade == "agentic"

    reviewers[0].review(CursorInvocationRequest(
        task_id="workflow-1",
        gate="outcome_review",
        instruction="Review.",
        cwd=tmp_path,
        reviewer_output_mode="litellm_structured",
        reviewer_model="gemini-3.1-pro-preview",
    ))
    assert request is not None
    assert request.reviewer_output_mode == "litellm_structured"


def test_reviewer_registry_returns_codex_cli_second_reviewer():
    from supervisor.reviewer_registry import configured_reviewers

    reviewers = configured_reviewers(
        reviewer_output_mode="litellm_structured",
        reviewer_model="gemini-3.1-pro-preview",
        runner=_accepting_cursor_runner,
        codex_runner=_accepting_codex_reviewer_runner,
    )

    assert [reviewer.spec.reviewer_id for reviewer in reviewers] == [
        "independent-reviewer-0",
        "independent-reviewer-1",
    ]
    assert reviewers[0].spec.provider_family == "google"
    assert reviewers[1].spec.runtime == "codex_cli"
    assert reviewers[1].spec.provider_family == "openai"
    assert reviewers[1].spec.lineage == ("openai", "codex_cli", "gpt-5.5")


def test_codex_cli_reviewer_parses_typed_outcome_with_hashes(tmp_path):
    from supervisor.reviewer_registry import CodexCliReviewer, ReviewerSpec

    calls: list[tuple[list[str], dict]] = []

    def fake_codex_runner(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=_codex_reviewer_jsonl("workflow-1"),
            stderr="",
        )

    reviewer = CodexCliReviewer(
        spec=ReviewerSpec(
            reviewer_id="independent-reviewer-1",
            runtime="codex_cli",
            model="gpt-5.5",
            provider_family="openai",
            lineage=("openai", "codex_cli", "gpt-5.5"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
        runner=fake_codex_runner,
    )

    result = reviewer.review(CursorInvocationRequest(
        task_id="workflow-1",
        gate="outcome_review",
        instruction="Review.",
        cwd=tmp_path,
    ))

    assert result.probe.ok
    assert result.outcome is not None
    assert result.outcome.decisions == ["accept"]
    assert result.reviewer_runtime == "codex_cli"
    assert result.reviewer_output_mode == "codex_cli"
    assert result.reviewer_assurance == "tool_backed_primary"
    assert result.diagnostics is not None
    assert result.diagnostics["codex_cli"]["command_execution_count"] == 1
    assert result.diagnostics["codex_cli"]["stdout_sha256"]
    assert calls
    argv, kwargs = calls[0]
    assert "--sandbox" in argv
    assert "read-only" in argv
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_codex_cli_reviewer_without_command_evidence_is_not_agentic(tmp_path):
    from supervisor.reviewer_registry import (
        CodexCliReviewer,
        ReviewerSpec,
        independent_reviewer_result_from_cursor_result,
    )

    def fake_codex_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=_codex_reviewer_jsonl("workflow-1", include_command=False),
            stderr="",
        )

    reviewer = CodexCliReviewer(
        spec=ReviewerSpec(
            reviewer_id="independent-reviewer-1",
            runtime="codex_cli",
            model="gpt-5.5",
            provider_family="openai",
            lineage=("openai", "codex_cli", "gpt-5.5"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
        runner=fake_codex_runner,
    )

    result = reviewer.review(CursorInvocationRequest(
        task_id="workflow-1",
        gate="outcome_review",
        instruction="Review.",
        cwd=tmp_path,
    ))

    assert result.probe.ok
    assert result.outcome is not None
    assert result.reviewer_assurance == "self_reported"
    assert result.diagnostics is not None
    assert result.diagnostics["codex_cli"]["command_execution_count"] == 0

    reviewer_result = independent_reviewer_result_from_cursor_result(
        result,
        task_id="workflow-1",
        gate="outcome_review",
        round_index=0,
        reviewer_id="independent-reviewer-1",
    )

    assert reviewer_result["reviewer_assurance"] == "self_reported"
    assert reviewer_result["assurance_grade"] == "self_reported"


def test_codex_cli_reviewer_parses_session_event_jsonl(tmp_path):
    from supervisor.reviewer_registry import CodexCliReviewer, ReviewerSpec

    def fake_codex_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=_codex_reviewer_session_jsonl("workflow-1"),
            stderr="",
        )

    reviewer = CodexCliReviewer(
        spec=ReviewerSpec(
            reviewer_id="independent-reviewer-1",
            runtime="codex_cli",
            model="gpt-5.5",
            provider_family="openai",
            lineage=("openai", "codex_cli", "gpt-5.5"),
            tool_access="codebase_tools",
            assurance_grade="agentic",
        ),
        runner=fake_codex_runner,
    )

    result = reviewer.review(CursorInvocationRequest(
        task_id="workflow-1",
        gate="outcome_review",
        instruction="Review.",
        cwd=tmp_path,
    ))

    assert result.probe.ok
    assert result.outcome is not None
    assert result.outcome.decisions == ["accept"]
    assert result.agent_id == "thread-codex-reviewer-session"
    assert result.reviewer_assurance == "tool_backed_primary"
    assert result.diagnostics is not None
    assert result.diagnostics["codex_cli"]["command_execution_count"] == 2
    assert result.diagnostics["codex_cli"]["command_executions"][0]["command"] == "rg -n reviewer supervisor"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_rigorous_cursor_profile_reviews_quality_gates(tmp_path):
    cursor_calls = []

    def fake_cursor_runner(request):
        cursor_calls.append(request)
        return _cursor_review_result(request.task_id)

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a rigorous backend eval uplift review.",
        max_rounds_per_gate=1,
        cursor_review_profile="rigorous",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert [request.gate for request in cursor_calls] == [
        "tdd_review",
        "implementation_plan",
        "outcome_review",
    ]
    assert result["workflow_route"]["cursor_review_gates"] == [
        "tdd_review",
        "implementation_plan",
        "outcome_review",
    ]
    cursor_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "tri_agent_cursor_review"
    ]
    assert [event["gate"] for event in cursor_events] == [
        "tdd_review",
        "implementation_plan",
        "outcome_review",
    ]


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_vague_route_forces_cursor_review(tmp_path):
    cursor_calls = []

    def fake_cursor_runner(request):
        cursor_calls.append(request)
        return _cursor_review_result(request.task_id)

    server, _state = _server(tmp_path, cursor_runner=fake_cursor_runner)
    screenshot = tmp_path / "result.png"
    screenshot.write_bytes(_tiny_png())

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Explore a vague workflow before deciding the implementation shape.",
        screenshots=[{"path": str(screenshot), "source": "computer_use", "validation": {"status": "passed"}}],
        task_complexity="vague",
        max_rounds_per_gate=1,
        cursor_review=False,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["force_cursor_review"] is True
    assert [request.gate for request in cursor_calls] == [
        "prd_review",
        "tdd_review",
        "implementation_plan",
        "outcome_review",
    ]


@pytest.mark.parametrize("decision", ["revise", "deny"])
@pytest.mark.asyncio
async def test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection(
    tmp_path,
    decision,
):
    def fake_cursor_runner(request):
        return _cursor_review_result(request.task_id, decision=decision)

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Cursor should block unresolved concerns.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_infra_retry_limit=3,
        reviewer_infra_retry_backoff_s=0,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["cursor_review"]["accepted"] is False
    assert result["final_gate_result"]["cursor_review"]["failure_classification"] is None
    assert result["final_gate_result"]["escalation"]["reason"] == "agents_not_converged"
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["objection"] == "cursor_review_failed: Cursor found an unresolved concern."


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_panel_blocks_important_reviewer_revise(tmp_path):
    def fake_cursor_runner(request):
        return _cursor_review_result(request.task_id, decision="revise", severity="important")

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Panel must hard-block important reviewer objections.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "blocking_reviewer_objection"
    assert panel_decision["blocking_reviewers"] == ["independent-reviewer-0"]
    assert result["final_gate_result"]["cursor_decision"] == "revise"

    reviewer_events = [
        (row["kind"], json.loads(row["payload_json"]))
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] in {"independent_reviewer_review", "tri_agent_cursor_review"}
    ]
    assert reviewer_events
    assert all(
        payload["independent_reviewer_panel_decision"]["decision"] == "revise"
        for _kind, payload in reviewer_events
    )


@pytest.mark.asyncio
async def test_second_reviewer_important_revise_blocks(tmp_path):
    server, state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_revising_codex_reviewer_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="A real second-reviewer objection must block the panel.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "blocking_reviewer_objection"
    assert panel_decision["blocking_reviewers"] == ["independent-reviewer-1"]
    assert result["final_gate_result"]["cursor_decision"] == "revise"

    reviewer_event = next(
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "independent_reviewer_review"
    )
    assert [item["reviewer_id"] for item in reviewer_event["independent_reviewer_results"]] == [
        "independent-reviewer-0",
        "independent-reviewer-1",
    ]
    assert reviewer_event["independent_reviewer_results"][1]["decision"] == "revise"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_split_panel_triggers_adjudication(tmp_path):
    server, state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_revising_codex_reviewer_with_evidence_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="A split reviewer panel must adjudicate the strongest objection.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "blocking_reviewer_objection"
    adjudication = panel_decision["adjudication"]
    assert adjudication["trigger"] == "disagreement"
    assert adjudication["decision"] == "block"
    assert adjudication["majority_vote_used"] is False
    assert adjudication["strongest_objection"]["reviewer_id"] == "independent-reviewer-1"
    assert adjudication["strongest_objection"]["text"] == "cited receipt contradicts the accept path"
    assert adjudication["strongest_objection"]["transcript_sha256"]
    assert adjudication["strongest_objection"]["output_sha256"]
    assert adjudication["evidence_checks"][0]["status"] == "verified"

    reviewer_event = next(
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "independent_reviewer_review"
    )
    assert reviewer_event["independent_reviewer_panel_decision"]["adjudication"]["decision"] == "block"
    adjudication_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "independent_reviewer_adjudication"
    ]
    assert adjudication_events[-1]["adjudication"]["strongest_objection"]["reviewer_id"] == (
        "independent-reviewer-1"
    )


@pytest.mark.asyncio
async def test_real_reviewer_revise_still_hard_blocks_with_adjudication(tmp_path):
    server, state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_revising_codex_reviewer_with_evidence_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="A real important reviewer revise must remain a hard block after adjudication.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "blocking_reviewer_objection"
    assert panel_decision["blocking_reviewers"] == ["independent-reviewer-1"]
    assert panel_decision["adjudication"]["decision"] == "block"
    assert panel_decision["adjudication"]["majority_vote_used"] is False
    assert result["final_gate_result"]["cursor_decision"] == "revise"

    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["codex_decision"] == "revise"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accept_with_strong_objection_escalates(tmp_path):
    server, state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_accepting_codex_reviewer_with_objection_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="All accepts with a strong minority objection should adjudicate and escalate.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "escalate"
    assert panel_decision["reason"] == "adjudicated_strong_objection"
    adjudication = panel_decision["adjudication"]
    assert adjudication["trigger"] == "strong_minority_objection"
    assert adjudication["decision"] == "escalate"
    assert adjudication["strongest_objection"]["reviewer_id"] == "independent-reviewer-1"
    assert result["final_gate_result"]["cursor_decision"] == "revise"

    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["objection"] == (
        "independent_reviewer_adjudicated_strong_objection: independent-reviewer-1"
    )


def test_reviewer_panel_adjudication_checks_bounded_refs(tmp_path):
    from supervisor.reviewer_registry import adjudicate_reviewer_panel

    good = tmp_path / "evidence" / "good.txt"
    good.parent.mkdir(parents=True)
    good.write_text("verified evidence\n", encoding="utf-8")
    bad_hash = tmp_path / "evidence" / "bad-hash.txt"
    bad_hash.write_text("hash mismatch evidence\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-adjudication.txt"
    outside.write_text("outside cwd\n", encoding="utf-8")

    result = adjudicate_reviewer_panel(
        [
            {
                "reviewer_id": "independent-reviewer-0",
                "verdict_present": True,
                "accepted": True,
                "decision": "accept",
                "severity": "none",
                "confidence": 0.9,
                "critical_review": {"strongest_objection": "none", "severity": "none"},
            },
            {
                "reviewer_id": "independent-reviewer-1",
                "verdict_present": True,
                "accepted": False,
                "decision": "revise",
                "severity": "important",
                "confidence": 0.88,
                "critical_review": {
                    "strongest_objection": "bounded evidence must be inspected",
                    "severity": "important",
                    "evidence_refs": [
                        {
                            "ref": str(good.relative_to(tmp_path)),
                            "sha256": sha256(good.read_bytes()).hexdigest(),
                        },
                        {
                            "ref": str(bad_hash.relative_to(tmp_path)),
                            "sha256": "0" * 64,
                        },
                        "missing-evidence.txt",
                        "https://example.invalid/evidence",
                        str(outside),
                    ],
                },
                "tests": [],
                "transcript_refs": [],
                "transcript_sha256": "abc",
                "output_sha256": "def",
            },
        ],
        cwd=tmp_path,
    )

    assert result is not None
    statuses = {check["ref"]: check["status"] for check in result["evidence_checks"]}
    assert statuses[str(good.relative_to(tmp_path))] == "verified"
    assert statuses[str(bad_hash.relative_to(tmp_path))] == "hash_mismatch"
    assert statuses["missing-evidence.txt"] == "missing"
    assert statuses["https://example.invalid/evidence"] == "skipped_external"
    assert statuses[str(outside)] == "skipped_unbounded"


@pytest.mark.asyncio
async def test_independent_reviewer_adjudication_event_and_transcript_export(tmp_path):
    server, state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_revising_codex_reviewer_with_evidence_runner,
    )

    await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Adjudication must appear in transcript and exported artifacts.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["independent_reviewer_adjudications"][-1]["adjudication"]["decision"] == "block"
    assert "cited receipt contradicts the accept path" in json.dumps(transcript)

    output_dir = tmp_path / "docs" / "dual-agent" / "workflow-1"
    export = await _maybe_await(server.tools["export_gate_artifacts"](
        run_id="workflow-run",
        task_id="workflow-1",
        cwd=str(tmp_path),
        output_dir=str(output_dir),
        screenshots=[],
    ))
    assert export["status"] == "ok"
    interactions = (output_dir / "interactions.md").read_text(encoding="utf-8")
    raw_transcript = (output_dir / "transcript.md").read_text(encoding="utf-8")
    for text in (interactions, raw_transcript):
        assert "interaction_type: `independent_reviewer_adjudication`" in text
        assert "cited receipt contradicts the accept path" in text
        assert "majority_vote_used: `False`" in text


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_panel_missing_verdict_does_not_accept(tmp_path):
    server, state = _server(tmp_path, cursor_runner=_cursor_missing_verdict_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Missing reviewer verdict must not count as accept.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="proceed_degraded",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    cursor_review = result["final_gate_result"]["cursor_review"]
    assert "reviewer_unavailable_recovery" not in cursor_review
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "missing_reviewer_verdict"
    assert panel_decision["missing_reviewers"] == ["independent-reviewer-0"]
    assert result["final_gate_result"]["cursor_decision"] == "revise"

    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["codex_decision"] == "revise"


@pytest.mark.asyncio
async def test_second_reviewer_outage_proceeds_only_degraded(tmp_path):
    server, state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_unavailable_codex_reviewer_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="A single second-reviewer outage should recover only as degraded.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="proceed_degraded",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    review = result["final_gate_result"]["cursor_review"]
    assert review["accepted"] is True
    assert review["reviewer_unavailable_recovery"]["evidence_grade"] == "degraded"
    assert review["reviewer_unavailable_recovery"]["reviewer_verdict_counted_as_accept"] is False
    assert review["reviewer_unavailable_recovery"]["unavailable_reviewers"] == [
        "independent-reviewer-1"
    ]
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "missing_reviewer_verdict"
    assert panel_decision["missing_reviewers"] == ["independent-reviewer-1"]

    recovery_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_reviewer_unavailable_recovery"
    ]
    assert recovery_events[-1]["status"] == "proceeded_degraded"
    assert recovery_events[-1]["unavailable_reviewers"] == ["independent-reviewer-1"]
    assert recovery_events[-1]["reviewer_verdict_counted_as_accept"] is False


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_panel_high_confidence_accept_advances_by_default(tmp_path):
    def fake_cursor_runner(request):
        return _cursor_review_result(request.task_id, decision="accept", confidence=0.91)

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Panel must preserve normal high-confidence accept throughput.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "accept"
    assert panel_decision["reason"] == "all_available_reviewers_accept"
    assert panel_decision["low_confidence_threshold"] == 0.0
    assert panel_decision["low_confidence_reviewers"] == []
    assert result["final_gate_result"]["cursor_decision"] == "accept"

    reviewer_event = next(
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "independent_reviewer_review"
    )
    assert reviewer_event["independent_reviewer_panel_decision"]["decision"] == "accept"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_panel_low_confidence_accept_escalates_when_threshold_configured(
    tmp_path,
):
    def fake_cursor_runner(request):
        return _cursor_review_result(request.task_id, decision="accept", confidence=0.42)

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Low-confidence reviewer accepts should escalate when configured.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_low_confidence_threshold=0.5,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["decision"] == "escalate"
    assert panel_decision["reason"] == "low_confidence_accept"
    assert panel_decision["low_confidence_threshold"] == 0.5
    assert panel_decision["low_confidence_reviewers"] == ["independent-reviewer-0"]
    assert result["final_gate_result"]["cursor_decision"] == "revise"

    interactions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_interaction_message"
    ]
    gate_decision = next(
        event
        for event in interactions
        if event["message_type"] == "gate_decision" and event["gate"] == "outcome_review"
    )
    assert gate_decision["metadata"]["independent_reviewer_panel_decision"]["decision"] == "escalate"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_calibrated_correlated_accept_escalates(tmp_path):
    calibration_path = _write_reviewer_panel_calibration(tmp_path, correlated=True)
    server, state = _server(tmp_path, cursor_runner=_accepting_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Measured-correlated reviewer accepts should escalate.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_panel_calibration_path=str(calibration_path),
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["workflow_route"]["reviewer_panel_calibration_active"] is True
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["aggregation_mode"] == "calibrated_weighted"
    assert panel_decision["decision"] == "escalate"
    assert panel_decision["reason"] == "calibrated_dependency_accept"
    calibrated = panel_decision["calibrated_accept"]
    assert calibrated["aggregate_confidence"] < calibrated["accept_confidence_threshold"]
    assert {
        item["reviewer_id"]: item["weight"]
        for item in calibrated["weighted_inputs"]
    } == {
        "independent-reviewer-0": pytest.approx(0.05),
        "independent-reviewer-1": pytest.approx(0.05),
    }
    assert result["final_gate_result"]["cursor_decision"] == "revise"
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["objection"].startswith(
        "independent_reviewer_calibrated_dependency_accept"
    )


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_calibrated_independent_accept_advances(tmp_path):
    calibration_path = _write_reviewer_panel_calibration(tmp_path, correlated=False)
    server, _state = _server(tmp_path, cursor_runner=_accepting_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Effectively independent reviewer accepts should advance.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_panel_calibration_path=str(calibration_path),
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["aggregation_mode"] == "calibrated_weighted"
    assert panel_decision["decision"] == "accept"
    assert panel_decision["reason"] == "all_available_reviewers_accept"
    calibrated = panel_decision["calibrated_accept"]
    assert calibrated["aggregate_confidence"] >= calibrated["accept_confidence_threshold"]
    assert result["final_gate_result"]["cursor_decision"] == "accept"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_missing_calibration_falls_back_to_conservative(tmp_path):
    missing_calibration = tmp_path / "docs" / "dual-agent" / "missing-calibration.json"
    server, _state = _server(tmp_path, cursor_runner=_accepting_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Absent calibration must preserve conservative behavior.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_panel_calibration_path=str(missing_calibration),
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["workflow_route"]["reviewer_panel_calibration_active"] is False
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["aggregation_mode"] == "conservative"
    assert panel_decision["decision"] == "accept"
    assert "calibration" not in panel_decision


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_calibrated_real_revise_still_blocks(tmp_path):
    calibration_path = _write_reviewer_panel_calibration(tmp_path, correlated=False)
    server, _state = _server(
        tmp_path,
        cursor_runner=_accepting_cursor_runner,
        codex_runner=_revising_codex_reviewer_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Real reviewer revise must hard block even under calibration.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_panel_calibration_path=str(calibration_path),
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    panel_decision = result["final_gate_result"]["independent_reviewer_panel_decision"]
    assert panel_decision["aggregation_mode"] == "calibrated_weighted"
    assert panel_decision["decision"] == "revise"
    assert panel_decision["reason"] == "blocking_reviewer_objection"
    assert "calibrated_accept" not in panel_decision


@pytest.mark.asyncio
async def test_panel_decision_is_exported_on_new_and_legacy_reviewer_events(tmp_path):
    server, state = _server(tmp_path, cursor_runner=_accepting_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Panel decision should be visible in reviewer events.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    events = [
        (row["kind"], json.loads(row["payload_json"]))
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] in {"independent_reviewer_review", "tri_agent_cursor_review"}
    ]
    assert {kind for kind, _payload in events} == {
        "independent_reviewer_review",
        "tri_agent_cursor_review",
    }
    assert all(
        payload["independent_reviewer_panel_decision"]["decision"] == "accept"
        for _kind, payload in events
    )
    assert all(
        payload["cursor_review"]["independent_reviewer_panel_decision"]["decision"] == "accept"
        for _kind, payload in events
    )


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_records_cursor_contract_failure_as_recoverable_infra(
    tmp_path,
):
    server, state = _server(tmp_path, cursor_runner=_cursor_contract_unmet_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Malformed Cursor output should become typed infrastructure evidence.",
        max_rounds_per_gate=5,
        cursor_review=True,
        reviewer_unavailable_policy="block",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["escalation"]["reason"] == "reviewer_contract_unmet"
    assert result["final_gate_result"]["cursor_review"]["failure_classification"] == "reviewer_contract_unmet"
    assert result["final_gate_result"]["cursor_review"]["recoverable"] is True
    assert result["final_gate_result"]["cursor_review"]["accepted"] is False
    assert result["steps"][-1]["attempt_count"] == 1

    cursor_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "tri_agent_cursor_review"
    ]
    assert len(cursor_events) == 1
    assert cursor_events[0]["cursor_review"]["failure_classification"] == "reviewer_contract_unmet"
    assert cursor_events[0]["cursor_review"]["probe"]["reason"] == "reviewer_contract_unmet"

    rounds = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    outcome_rounds = [event["round"] for event in rounds if event["gate"] == "outcome_review"]
    assert len(outcome_rounds) == 1
    assert outcome_rounds[0]["objection"] == "cursor_reviewer_infrastructure: reviewer_contract_unmet"

    gate_results = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_result"
    ]
    blocked_gate_results = [
        payload
        for payload in gate_results
        if payload.get("gate") == "outcome_review" and payload.get("status") == "blocked"
    ]
    assert len(blocked_gate_results) == 1
    assert blocked_gate_results[0]["escalation"]["reason"] == "reviewer_contract_unmet"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert len(transcript["cursor_reviews"]) == 1
    assert transcript["cursor_reviews"][0]["cursor_review"]["failure_classification"] == (
        "reviewer_contract_unmet"
    )


@pytest.mark.asyncio
async def test_reviewer_access_denied_blocks_without_degraded_recovery(tmp_path):
    server, state = _server(tmp_path, cursor_runner=_cursor_access_denied_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reviewer access denied should be fixed, not degraded.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="proceed_degraded",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    cursor_review = result["final_gate_result"]["cursor_review"]
    assert cursor_review["accepted"] is False
    assert cursor_review["failure_classification"] == "reviewer_access_denied"
    assert cursor_review["recoverable"] is False
    assert "reviewer_unavailable_recovery" not in cursor_review

    cursor_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "tri_agent_cursor_review"
    ]
    assert len(cursor_events) == 1
    assert cursor_events[0]["cursor_review"]["failure_classification"] == (
        "reviewer_access_denied"
    )

    recovery_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_reviewer_unavailable_recovery"
    ]
    assert recovery_events == []

    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[-1]["objection"] == "cursor_reviewer_access_denied: reviewer_access_denied"


@pytest.mark.asyncio
async def test_reviewer_unavailable_block_policy_stays_blocked_for_high_stakes(tmp_path):
    notifier = _Notifier()
    server, state = _server(
        tmp_path,
        cursor_runner=_cursor_contract_unmet_runner,
        notifier=notifier,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Explicit block policy remains a terminal block even on high-stakes gates.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="block",
        agentic_lead_policy="required",
        min_subagents=1,
        tool_receipts=[*_tool_receipts(), *_dynamic_subagent_result_receipts(tmp_path)],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert "workflow_reviewer_unavailable_escalation" not in result["final_gate_result"]
    assert result["final_gate_result"]["escalation"]["reason"] == "reviewer_contract_unmet"

    recovery_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_reviewer_unavailable_recovery"
    ]
    assert recovery_events
    assert recovery_events[-1]["status"] == "blocked"
    assert recovery_events[-1]["policy"] == "block"
    assert recovery_events[-1]["forced_by_safety"] is True
    assert recovery_events[-1]["reviewer_verdict_counted_as_accept"] is False
    assert not notifier.prompts


@pytest.mark.asyncio
async def test_reviewer_unavailable_proceed_degraded_advances_with_degraded_receipt(tmp_path):
    server, state = _server(tmp_path, cursor_runner=_cursor_contract_unmet_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Proceed degraded only when Cursor infrastructure is unavailable.",
        max_rounds_per_gate=1,
        cursor_review=True,
        cursor_review_profile="rigorous",
        reviewer_unavailable_policy="proceed_degraded",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["steps"][-1]["gate"] == "outcome_review"
    assert result["steps"][-1]["status"] == "accepted"

    cursor_reviews = [
        json.loads(row["payload_json"])["cursor_review"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "tri_agent_cursor_review"
    ]
    assert cursor_reviews
    assert all(review["accepted"] is False for review in cursor_reviews)
    assert all(
        review["reviewer_unavailable_recovery"]["evidence_grade"] == "degraded"
        for review in cursor_reviews
    )
    assert all(
        review["reviewer_unavailable_recovery"]["reviewer_verdict_counted_as_accept"] is False
        for review in cursor_reviews
    )
    assert all(
        review["independent_reviewer_panel_decision"]["decision"] == "revise"
        for review in cursor_reviews
    )
    assert all(
        review["independent_reviewer_panel_decision"]["reason"] == "missing_reviewer_verdict"
        for review in cursor_reviews
    )

    recovery_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_reviewer_unavailable_recovery"
    ]
    assert recovery_events
    assert {event["status"] for event in recovery_events} == {"proceeded_degraded"}
    assert all(event["evidence_grade"] == "degraded" for event in recovery_events)

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["reviewer_unavailable_recoveries"]


@pytest.mark.asyncio
async def test_exhausted_cursor_infra_retry_proceeds_degraded_without_counting_cursor_accept(
    tmp_path,
):
    server, state = _server(tmp_path, cursor_runner=_cursor_exhausted_infra_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Exhausted Cursor infrastructure retries should recover degraded.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="proceed_degraded",
        reviewer_infra_retry_limit=2,
        reviewer_infra_retry_backoff_s=0,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["status"] == "accepted"
    cursor_review = result["final_gate_result"]["cursor_review"]
    assert cursor_review["accepted"] is False
    assert cursor_review["failure_classification"] == "reviewer_infrastructure_unavailable"
    assert cursor_review["probe"]["reason"] == "reviewer_infrastructure_unavailable"
    assert cursor_review["diagnostics"]["infrastructure_retries"]["exhausted"] is True
    assert cursor_review["reviewer_unavailable_recovery"]["evidence_grade"] == "degraded"
    assert (
        cursor_review["reviewer_unavailable_recovery"]["reviewer_verdict_counted_as_accept"]
        is False
    )

    recovery_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_reviewer_unavailable_recovery"
    ]
    assert recovery_events
    assert recovery_events[-1]["status"] == "proceeded_degraded"
    assert recovery_events[-1]["classification"] == "reviewer_infrastructure_unavailable"
    assert recovery_events[-1]["evidence_grade"] == "degraded"
    assert recovery_events[-1]["reviewer_verdict_counted_as_accept"] is False
    assert recovery_events[-1]["available_reviewers"] == {
        "claude": "accept",
        "codex": "accept",
    }


@pytest.mark.asyncio
async def test_reviewer_unavailable_default_escalates_and_resume_continue_advances(tmp_path):
    notifier = _Notifier()
    server, state = _server(
        tmp_path,
        cursor_runner=_cursor_contract_unmet_runner,
        notifier=notifier,
    )

    first = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Default reviewer unavailable policy should be resumable.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert first["status"] == "blocked"
    assert first["current_gate"] == "outcome_review"
    escalation = first["final_gate_result"]["workflow_reviewer_unavailable_escalation"]
    assert escalation["status"] == "paused_for_human"
    assert escalation["policy"] == "escalate"
    assert "reviewer unavailable" in notifier.prompts[-1]["question"].lower()

    action = state._conn.execute(
        """SELECT * FROM actions
           WHERE run_id=? AND action_type='dual_agent_gate_deadlock'
           ORDER BY id DESC LIMIT 1""",
        ("workflow-run",),
    ).fetchone()
    assert action is not None
    state.mark_action_resume_requested(action["id"], payload_update={"answer": "Continue"})

    second = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Human authorized degraded proceed after reviewer unavailable escalation.",
        max_rounds_per_gate=1,
        cursor_review=True,
        tool_receipts=_tool_receipts(),
    ))

    assert second["status"] == "accepted"
    recovery_events = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_reviewer_unavailable_recovery"
    ]
    assert recovery_events[-1]["status"] == "proceeded_degraded"
    assert recovery_events[-1]["authorization"]["status"] == "resumed"


@pytest.mark.asyncio
async def test_reviewer_unavailable_proceed_degraded_escalates_for_agentic_required(tmp_path):
    notifier = _Notifier()
    server, state = _server(
        tmp_path,
        cursor_runner=_cursor_contract_unmet_runner,
        notifier=notifier,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Agentic-required review unavailable must escalate.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="proceed_degraded",
        agentic_lead_policy="required",
        min_subagents=1,
        tool_receipts=[*_tool_receipts(), *_dynamic_subagent_result_receipts(tmp_path)],
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    escalation = result["final_gate_result"]["workflow_reviewer_unavailable_escalation"]
    assert escalation["status"] == "paused_for_human"
    assert escalation["policy"] == "proceed_degraded"
    assert escalation["forced_by_safety"] is True


@pytest.mark.asyncio
async def test_reviewer_unavailable_runtime_native_escalates(tmp_path):
    notifier = _Notifier()
    server, state = _server(
        tmp_path,
        cursor_runner=_cursor_contract_unmet_runner,
        notifier=notifier,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Runtime-native evidence requirement must escalate reviewer unavailable.",
        max_rounds_per_gate=1,
        cursor_review=True,
        reviewer_unavailable_policy="proceed_degraded",
        required_evidence_grade="runtime_native",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    escalation = result["final_gate_result"]["workflow_reviewer_unavailable_escalation"]
    assert escalation["status"] == "paused_for_human"
    assert escalation["forced_by_safety"] is True


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_review_packet_blocker_forces_next_round(tmp_path, monkeypatch):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)

    def blocking_review_packet(**kwargs):
        return {
            "schema_version": "codex-review-packet/v1",
            "task_id": kwargs["task_id"],
            "gate": kwargs["gate"],
            "reviewer": "codex",
            "decision": kwargs["decision"],
            "confidence": {},
            "requirements": [],
            "findings": [{
                "finding_id": "finding-999",
                "severity": "CRITICAL",
                "code": "FM-3.3",
                "title": "forced blocker",
            }],
            "round_policy": {
                "force_next_round": True,
                "blocking_findings": ["finding-999"],
                "close_allowed": False,
            },
            "objections": ["forced blocker"],
            "evidence_refs": [],
            "would_change_if": "The injected blocker is cleared.",
        }

    monkeypatch.setattr(stdio, "codex_review_packet", blocking_review_packet)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Accepted gate should still respect review packet blockers.",
        max_rounds_per_gate=1,
        task_complexity="trivial",
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert rounds[0]["codex_decision"] == "revise"
    assert rounds[0]["objection"] == "blocking_review_findings: finding-999"
    interactions = [
        json.loads(row["payload_json"])
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_interaction_message"
    ]
    decision_messages = [
        event for event in interactions
        if event.get("message_type") == "gate_decision"
    ]
    assert decision_messages[0]["review_packet"]["round_policy"]["force_next_round"] is True


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_enforces_v5_without_prompt_following(tmp_path):
    notifier = _Notifier()
    server, state = _server(tmp_path, decision="revise", notifier=notifier)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Force a disagreement fixture.",
        max_rounds_per_gate=2,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["steps"] == [{"gate": "prd_review", "status": "blocked", "attempt_count": 2}]
    assert result["final_gate_result"]["status"] == "blocked"
    assert result["final_gate_result"]["escalation"]["reason"] == "agents_not_converged"
    assert result["final_gate_result"]["workflow_deadlock_escalation"]["status"] == "paused_for_human"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"
    assert notifier.prompts
    assert any("dual-agent workflow needs_user_input gate=prd_review" in message for message in notifier.messages)
    rounds = [
        json.loads(row["payload_json"])["round"]
        for row in state.read_dual_agent_gate_events("workflow-run")
        if row["kind"] == "dual_agent_gate_round"
    ]
    assert len(rounds) == 2
    assert rounds[-1]["objection"] == "max_rounds_per_gate exhausted without both agents accepting"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_retries_malformed_outcome_once(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    calls = 0

    def fake_runner(argv, **kwargs):
        nonlocal calls
        calls += 1
        stdout = "Worker forgot the typed block." if calls == 1 else _outcome_block("workflow-1")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(stdout),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Retry a malformed worker outcome.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert calls == 7


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_on_claude_failure_and_requests_input(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    notifier = _Notifier()

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="claude failed",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        notifier=notifier,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Surface worker failure.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "prd_review"
    assert result["final_gate_result"]["status"] == "blocked"
    assert any("dual-agent workflow gate_blocked gate=prd_review" in message for message in notifier.messages)
    assert any("dual-agent workflow needs_user_input gate=prd_review" in message for message in notifier.messages)


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_can_rerun_after_corrective_input(tmp_path):
    decision = "revise"

    def decide():
        return decision

    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1", decision=decide()),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    first = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="First run blocks, second run accepts after correction.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))
    assert first["status"] == "blocked"

    decision = "accept"
    second = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Correction supplied; rerun safely.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert second["status"] == "accepted"
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "accepted"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_resumes_after_transport_loss_from_pending_gate(tmp_path):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
    _materialize_runtime_evidence_fixture(
        tmp_path,
        changed_files=None,
        tests=None,
        gate_outcomes=None,
    )
    for gate in ("prd_review", "issues_review", "tdd_review"):
        state.write_event(
            run_id="workflow-run",
            source="dual_agent",
            kind="dual_agent_gate_result",
            payload={
                "task_id": "workflow-1",
                "gate": gate,
                "status": "accepted",
                "attempts": 1,
                "handoff_packet_path": f"/tmp/.handoff/{gate}.json",
                "probes": {},
                "outcome": {
                    "task_id": "workflow-1",
                    "summary": f"{gate} accepted before transport closed.",
                    "specialists": [{"name": "Reviewer", "decision": "accept"}],
                    "decisions": ["accept"],
                    "objections": [],
                    "changed_files": [],
                    "tests": [],
                    "test_status": "passed",
                    "confidence": 0.95,
                    "claims": [],
                },
                "escalation": None,
            },
        )
        state.record_dual_agent_workflow_step(
            run_id="workflow-run",
            task_id="workflow-1",
            gate=gate,
            status="accepted",
            attempt_count=1,
            latest_event_id=None,
        )
    runner_calls: list[str] = []

    def fake_runner(argv, **kwargs):
        prompt = argv[argv.index("-p") + 1]
        gate = prompt.split("Gate mode: ", 1)[1].split(".", 1)[0]
        runner_calls.append(gate)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1"),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        codex_runner=_accepting_codex_reviewer_runner,
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Resume after the previous MCP transport closed.",
        max_rounds_per_gate=1,
        agentic_lead_policy="off",
        cursor_review=False,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert runner_calls == ["implementation_plan", "execution", "outcome_review"]
    assert [step["gate"] for step in result["steps"]] == [
        "prd_review",
        "issues_review",
        "tdd_review",
        "implementation_plan",
        "execution",
        "outcome_review",
    ]
    assert result["workflow_route"]["resume"]["skipped_gates"] == [
        "prd_review",
        "issues_review",
        "tdd_review",
    ]


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_blocks_user_facing_without_visual_evidence(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Review a user-facing UI change.",
        user_facing=True,
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["escalation"]["reason"] == "required_artifacts_missing"
    assert "screenshots" in result["final_gate_result"]["artifact_rigor"]["missing_artifacts"]
    steps = state.list_dual_agent_workflow_steps(run_id="workflow-run", task_id="workflow-1")
    assert [step["status"] for step in steps[:-1]] == ["accepted"] * 5
    assert steps[-1]["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_auto_requires_visual_evidence_for_vela_live_surfaces(tmp_path):
    server, state = _server(tmp_path)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent=(
            "Fix Vela Slack meeting and Google Calendar degraded-path behavior. "
            "Do not claim live provider success without screenshots."
        ),
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["visual_evidence_policy"]["required"] is True
    assert result["visual_evidence_policy"]["source"] == "auto_live_surface"
    assert {"slack", "calendar", "google calendar", "screenshot"} <= set(
        result["visual_evidence_policy"]["matched_terms"]
    )
    assert result["final_gate_result"]["artifact_rigor"]["user_facing"] is True
    assert "screenshots" in result["final_gate_result"]["artifact_rigor"]["missing_artifacts"]
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["user_facing"] == 1


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_auto_visual_policy_accepts_computer_use_evidence(tmp_path):
    server, state = _server(tmp_path)
    screenshot = tmp_path / "vela-slack-final-state.png"
    screenshot.write_bytes(_tiny_png())

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Fix Vela Slack meeting degraded-path behavior.",
        screenshots=[{
            "path": str(screenshot),
            "label": "Vela Slack final state",
            "note": "Captured by Codex with Computer Use before outcome review.",
            "source": "computer_use",
            "validation": {
                "status": "passed",
                "notes": "Slack-visible result was reviewed against the receipt-gating acceptance criteria.",
            },
        }],
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "accepted"
    assert result["visual_evidence_policy"]["required"] is True
    assert result["final_gate_result"]["artifact_rigor"]["visual_validation"]["status"] == "ok"
    assert "docs/dual-agent/workflow-1/screenshots/01-vela-slack-final-state.png" in (
        result["artifact_export"]["files"]
    )
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["user_facing"] == 1


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_verifies_final_claims(tmp_path):
    server, state = _server(tmp_path, claims=["pushed"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Claim remote push without evidence.",
        max_rounds_per_gate=1,
        verified_claims=["pushed"],
        tool_receipts=_tool_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["claim_verification"]["reason"] == "workflow_claim_verification_failed"
    assert "pushed_without_remote_receipt" in result["final_gate_result"]["claim_verification"]["details"]["failures"]
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accepts_claims_with_supervisor_runtime_test_receipt(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    receipts = [
        receipt for receipt in _tool_receipts()
        if receipt.get("receipt_id") != "pytest-focused"
    ]

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Claim tests and implementation without receipts.",
        max_rounds_per_gate=1,
        tool_receipts=receipts,
    ))

    assert result["status"] == "accepted"
    receipts = result["final_gate_result"]["claim_verification"]["details"]["receipts"]
    assert any(
        receipt["kind"] == "test"
        and receipt["source"] == "supervisor"
        and receipt["evidence_grade"] == "runtime_native"
        for receipt in receipts
    )


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_ignores_unrelated_agent_receipts_when_runtime_receipts_pass(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    unrelated_receipts = _tool_receipts()
    for receipt in unrelated_receipts:
        receipt["claims"] = ["unrelated cleanup"]

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject stale receipts that do not map to the current claims.",
        max_rounds_per_gate=1,
        tool_receipts=unrelated_receipts,
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["claim_verification"]["status"] == "green"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accepts_when_supervisor_diff_covers_agent_receipt_without_files(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    receipts = _tool_receipts()
    for receipt in receipts:
        if receipt.get("receipt_id") == "git-diff":
            receipt.pop("changed_files", None)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject implementation receipts that do not name changed files.",
        max_rounds_per_gate=1,
        tool_receipts=receipts,
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["probes"]["P11"]["status"] == "green"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accepts_when_supervisor_diff_covers_partial_agent_receipt(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    receipts = _tool_receipts()
    for receipt in receipts:
        if receipt.get("receipt_id") == "git-diff":
            receipt["changed_files"] = ["supervisor/dual_agent_workflow.py"]

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject stale implementation receipts with incomplete file coverage.",
        max_rounds_per_gate=1,
        tool_receipts=receipts,
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["probes"]["P11"]["status"] == "green"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accepts_when_supervisor_diff_covers_vague_agent_claim_receipt(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])
    receipts = _tool_receipts()
    for receipt in receipts:
        if receipt.get("receipt_id") == "git-diff":
            receipt["claims"] = ["implemented previous task"]

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Reject receipts that only mention implementation as a substring.",
        max_rounds_per_gate=1,
        tool_receipts=receipts,
    ))

    assert result["status"] == "accepted"
    assert result["final_gate_result"]["claim_verification"]["status"] == "green"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_accepts_receipt_backed_claims(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented", "pushed"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Accept only with receipts.",
        max_rounds_per_gate=1,
        tool_receipts=_tool_receipts(include_push=True),
    ))

    assert result["status"] == "accepted"
    receipt_payload = result["final_gate_result"]["claim_verification"]["details"]["receipts"]
    assert {receipt["kind"] for receipt in receipt_payload} >= {"test", "git_diff", "git_remote"}


@pytest.mark.asyncio
async def test_workflow_resume_prompt_tool_is_state_derived(tmp_path):
    server, _state = _server(tmp_path, decision="revise")
    await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Create a blocked workflow.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))

    prompt = await _maybe_await(server.tools["read_dual_agent_workflow_resume_prompt"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))

    assert prompt["status"] == "ok"
    assert "Continue run_id=workflow-run" in prompt["prompt"]
    assert "task_id=workflow-1" in prompt["prompt"]
    assert "next_safe_action=inspect blocker" in prompt["prompt"]
    assert prompt["latest_event_id"] > 0
    assert prompt["steps"][0]["gate"] == "prd_review"
    assert prompt["blocker"]["gate"] == "prd_review"
    assert prompt["artifact_output_dir"].endswith("docs/dual-agent/workflow-1")
    assert "read_gate_transcript" in prompt["prompt"]
