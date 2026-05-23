from __future__ import annotations

import json
import subprocess
from pathlib import Path

from supervisor.dual_agent_lead import (
    LeadInvocationRequest,
    build_claude_lead_command,
    build_lead_prompt,
    invoke_claude_lead,
    select_lead_model,
)


def _outcome_block(**overrides: object) -> str:
    payload = {
        "task_id": "slice0-lead",
        "summary": "Reviewed the handoff and accepted the narrow path.",
        "specialists": [
            {
                "name": "Planner",
                "decision": "keep the gate narrow",
                "objection": None,
            }
        ],
        "decisions": ["keep the gate narrow"],
        "objections": [],
        "changed_files": ["handover.md"],
        "tests": ["pytest tests/test_dual_agent_lead_invoker.py"],
        "test_status": "passed",
        "confidence": 0.96,
    }
    payload.update(overrides)
    return f"<dual_agent_outcome>{json.dumps(payload)}</dual_agent_outcome>"


def test_build_lead_command_uses_non_bare_claude_so_slash_lead_can_resolve(tmp_path):
    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review the PRD gate.",
        cwd=tmp_path,
        expected_specialists=("Planner",),
        expected_decisions=("keep the gate narrow",),
        expected_objections=(),
        quality="best",
    )

    prompt = build_lead_prompt(request)
    argv = build_claude_lead_command(request)

    assert prompt.startswith("/lead Gate mode: prd_review.")
    assert "Task id: slice0-lead." in prompt
    assert "Always end with <dual_agent_outcome>" in prompt
    assert "--bare" not in argv
    assert argv[:2] == ["claude", "--no-session-persistence"]
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("-p") + 1] == prompt


def test_select_lead_model_prefers_best_models_for_gate_decisions():
    assert select_lead_model("prd_review", quality="best") == "opus"
    assert select_lead_model("outcome_review", quality="best") == "opus"
    assert select_lead_model("execution", quality="best") == "sonnet"
    assert select_lead_model("prd_review", quality="balanced") == "sonnet"
    assert select_lead_model("prd_review", quality="cheap") == "haiku"
    assert select_lead_model("prd_review", quality="best", explicit_model="sonnet") == "sonnet"


def test_invoke_claude_lead_parses_json_output_and_validates_outcome(tmp_path):
    calls: list[dict[str, object]] = []
    stdout = json.dumps({
        "result": "lead prose\n" + _outcome_block(),
        "model": "claude-opus-4-7",
        "total_cost_usd": 0.03,
    })

    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review the PRD gate.",
        cwd=tmp_path,
        expected_specialists=("Planner",),
        expected_decisions=("keep the gate narrow",),
        expected_objections=(),
        explicit_env={"ANTHROPIC_BASE_URL": "https://uai-litellm.internal.unity.com"},
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert result.probe.ok
    assert result.outcome is not None
    assert result.outcome.task_id == "slice0-lead"
    assert result.model == "claude-opus-4-7"
    assert result.cost_usd == 0.03
    assert result.stdout_bytes == len(stdout.encode())
    assert calls[0]["cwd"] == str(tmp_path)
    assert calls[0]["capture_output"] is True
    assert calls[0]["text"] is True
    assert calls[0]["env"]["ANTHROPIC_BASE_URL"] == "https://uai-litellm.internal.unity.com"


def test_invoke_claude_lead_reports_subprocess_failure(tmp_path):
    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="missing /lead")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="intent",
        instruction="Review intent.",
        cwd=tmp_path,
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert not result.probe.ok
    assert result.probe.reason == "lead_invocation_failed"
    assert result.probe.details["returncode"] == 2
    assert result.stderr == "missing /lead"
    assert result.outcome is None


def test_invoke_claude_lead_surfaces_outcome_fidelity_failure(tmp_path):
    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = json.dumps({
            "result": _outcome_block(
                specialists=[],
                decisions=[],
                changed_files=["handover.md"],
            )
        })
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
        expected_specialists=("Planner",),
        expected_decisions=("keep the gate narrow",),
        expected_objections=(),
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert not result.probe.ok
    assert result.probe.reason == "outcome_signal_loss"
    assert result.outcome is not None
