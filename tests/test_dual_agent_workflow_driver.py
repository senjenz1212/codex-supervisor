from __future__ import annotations

import inspect
import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.config import Config
from supervisor.cursor_agent import CursorInvocationResult
from supervisor.dual_agent import Outcome, ProbeResult
from supervisor.dual_agent_workflow import (
    cursor_review_gates_for_workflow,
    select_workflow_route,
    workflow_visual_evidence_policy,
)
from supervisor.dual_agent_lead import DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES
from supervisor.state import State


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "planning_validator"


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


def _outcome_block(task_id: str, *, decision: str = "accept", claims: list[str] | None = None) -> str:
    payload = {
        "task_id": task_id,
        "summary": "Workflow response complete.",
        "specialists": [{"name": "Planner", "decision": decision}],
        "decisions": [decision],
        "objections": [] if decision == "accept" else ["Needs another revision."],
        "changed_files": ["supervisor/dual_agent_workflow.py", "tests/test_dual_agent_workflow_driver.py"],
        "tests": ["uv run pytest tests/test_dual_agent_workflow_driver.py"],
        "test_status": "passed",
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
            "command": "uv run pytest tests/test_dual_agent_workflow_driver.py",
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
        "irrelevant_field": "must not leak into workflow kwargs",
    })

    assert kwargs["execution_layer_mode"] == "dynamic_workflow_preview"
    assert kwargs["dynamic_workflow_task_class"] == "codebase_audit"
    assert kwargs["agentic_lead_policy"] == "required"
    assert kwargs["min_subagents"] == 2
    assert kwargs["required_roles"] == ["codebase_audit", "independent_reviewer"]
    assert kwargs["solo_exception_for_artifact_only_gates"] is True
    assert kwargs["required_evidence_grade"] == "runtime_native"
    assert "irrelevant_field" not in kwargs


def _server(
    tmp_path: Path,
    *,
    decision: str = "accept",
    claims: list[str] | None = None,
    notifier=None,
    cursor_runner=None,
):
    from mcp_tools.codex_supervisor_stdio import build_codex_supervisor_mcp_server
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))

    def fake_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=build_lead_replay_stdout(
                "Workflow response.\n" + _outcome_block("workflow-1", decision=decision, claims=claims),
            ),
            stderr="",
        )

    server = build_codex_supervisor_mcp_server(
        _cfg(tmp_path),
        state,
        mcp_cls=_FakeMCP,
        runner=fake_runner,
        cursor_runner=cursor_runner or _accepting_cursor_runner,
        notifier=notifier,
    )
    return server, state


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


def _cursor_review_result(task_id: str, *, decision: str = "accept") -> CursorInvocationResult:
    outcome = Outcome(
        task_id=task_id,
        summary="Cursor independently reviewed the gate.",
        specialists=[{"name": "Cursor Reviewer", "decision": decision}],
        decisions=[decision],
        objections=[] if decision == "accept" else ["Cursor found an unresolved concern."],
        changed_files=[],
        tests=[],
        test_status="unknown",
        confidence=0.91,
        confidence_rationale="Cursor reviewed the gate evidence independently.",
        confidence_criteria=["typed outcome complete", "review decision present"],
        claims=[],
        critical_review={
            "strongest_objection": "none" if decision == "accept" else "unresolved concern",
            "missing_evidence": [],
            "contradictions_checked": ["gate evidence"],
            "assumptions_to_verify": [],
            "what_would_change_my_mind": "New contradictory evidence.",
            "decision": decision,
            "severity": "none" if decision == "accept" else "important",
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
        duration_ms=10,
    )


def _accepting_cursor_runner(request) -> CursorInvocationResult:
    return _cursor_review_result(request.task_id)


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
async def test_workflow_cli_payload_runs_same_supervisor_api(tmp_path):
    from mcp_tools.codex_supervisor_workflow_cli import run_workflow_payload
    from supervisor.dual_agent_runner import build_lead_replay_stdout

    _write_good_workflow_source_artifacts(tmp_path)
    state = State(str(tmp_path / "state.db"))
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
async def test_submit_dual_agent_workflow_job_spawns_detached_worker_and_records_job(monkeypatch, tmp_path):
    import mcp_tools.codex_supervisor_stdio as stdio

    server, state = _server(tmp_path)
    popen_calls: list[dict] = []

    class FakePopen:
        pid = 43210

        def __init__(self, argv, **kwargs):
            popen_calls.append({"argv": list(argv), "kwargs": kwargs})

    monkeypatch.setattr(stdio.subprocess, "Popen", FakePopen)

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

    assert result["status"] == "running"
    assert result["poll_tool"] == "poll_dual_agent_workflow_job"
    assert popen_calls
    assert popen_calls[0]["argv"][1:3] == ["-m", "mcp_tools.codex_supervisor_workflow_cli"]
    assert popen_calls[0]["kwargs"]["start_new_session"] is True

    request = json.loads(Path(result["request_path"]).read_text(encoding="utf-8"))
    assert request["execution_layer_mode"] == "dynamic_workflow_preview"
    assert request["dynamic_workflow_task_class"] == "codebase_audit"

    job = state.get_dual_agent_workflow_job(job_id=result["job_id"])
    assert job is not None
    assert job["status"] == "running"
    assert job["pid"] == 43210

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["workflow_jobs"][0]["status"] == "running"


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
    assert result["result"]["steps"] == [{"gate": "outcome_review", "status": "accepted"}]
    job = state.get_dual_agent_workflow_job(job_id="job-1")
    assert job["status"] == "accepted"

    transcript = await _maybe_await(server.tools["read_gate_transcript"](
        run_id="workflow-run",
        task_id="workflow-1",
    ))
    assert transcript["workflow_jobs"][0]["status"] == "accepted"


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
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run workflow with a non-default live probe budget.",
        max_rounds_per_gate=1,
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
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a forged dynamic preview receipt.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
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
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Run a fan-out task with a critical reviewer disagreement.",
        max_rounds_per_gate=1,
        execution_layer_mode="dynamic_workflow_preview",
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
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Build the supervisor-owned workflow driver.",
        max_rounds_per_gate=5,
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
    gate_decision = next(event for event in interactions if event["message_type"] == "gate_decision")
    assert "Critical review:" in review_request["content"]
    assert review_request["critical_review"]["schema_version"] == "critical-review/v1"
    assert review_response["critical_review"]["schema_version"] == "critical-review/v1"
    assert gate_decision["critical_review"]["schema_version"] == "critical-review/v1"


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
async def test_run_dual_agent_workflow_records_cursor_contract_failure_as_recoverable_infra(
    tmp_path,
):
    def fake_cursor_runner(request):
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

    server, state = _server(tmp_path, cursor_runner=fake_cursor_runner)

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Malformed Cursor output should become typed infrastructure evidence.",
        max_rounds_per_gate=5,
        cursor_review=True,
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
        cursor_runner=_accepting_cursor_runner,
    )

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Resume after the previous MCP transport closed.",
        max_rounds_per_gate=1,
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
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    assert result["current_gate"] == "outcome_review"
    assert result["final_gate_result"]["claim_verification"]["reason"] == "workflow_claim_verification_failed"
    assert "pushed_without_remote_receipt" in result["final_gate_result"]["claim_verification"]["details"]["failures"]
    workflow = state.get_dual_agent_workflow(run_id="workflow-run", task_id="workflow-1")
    assert workflow["status"] == "blocked"


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_requires_test_and_diff_receipts_for_claims(tmp_path):
    server, _state = _server(tmp_path, claims=["tests passed", "implemented"])

    result = await _maybe_await(server.tools["run_dual_agent_workflow"](
        cwd=str(tmp_path),
        task_id="workflow-1",
        run_id="workflow-run",
        intent="Claim tests and implementation without receipts.",
        max_rounds_per_gate=1,
        tool_receipts=_skill_receipts(),
    ))

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "tests_passed_without_test_receipt" in failures
    assert "implemented_without_diff_receipt" in failures


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_rejects_unrelated_receipts_for_claims(tmp_path):
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

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "tests_passed_without_test_receipt" in failures
    assert "implemented_without_diff_receipt" in failures


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_rejects_diff_receipt_without_changed_file_replay(tmp_path):
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

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "implemented_without_diff_receipt" in failures


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_rejects_partial_changed_file_receipt(tmp_path):
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

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "implemented_without_diff_receipt" in failures


@pytest.mark.asyncio
async def test_run_dual_agent_workflow_rejects_vague_substring_claim_receipts(tmp_path):
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

    assert result["status"] == "blocked"
    failures = result["final_gate_result"]["claim_verification"]["details"]["failures"]
    assert "implemented_without_diff_receipt" in failures


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
