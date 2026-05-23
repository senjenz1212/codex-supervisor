from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from supervisor.dual_agent_lead import (
    HANDOFF_PACKET_SCHEMA_VERSION,
    LeadInvocationRequest,
    OutcomeValidationPolicy,
    PlanningArtifact,
    build_claude_lead_command,
    build_handoff_packet,
    build_lead_prompt,
    compute_file_sha256,
    invoke_claude_lead,
    verify_planning_artifact_boundaries,
    select_lead_model,
    write_handoff_packet,
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
    assert "Every specialist object must include a string name and a string decision" in prompt
    assert "do not use null for specialist decisions" in prompt
    assert "--bare" not in argv
    assert argv[:2] == ["claude", "--no-session-persistence"]
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--tools") + 1] == "default"
    assert argv[argv.index("-p") + 1] == prompt


def test_lead_invocation_defaults_to_same_worktree_tool_access(tmp_path):
    calls: list[dict[str, object]] = []
    stdout = json.dumps({"result": _outcome_block()})

    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="outcome_review",
        instruction="Review the implementation directly.",
        cwd=tmp_path,
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert result.probe.ok
    argv = calls[0]["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("--tools") + 1] == "default"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert calls[0]["cwd"] == str(tmp_path)


def test_select_lead_model_prefers_best_models_for_all_best_quality_work():
    assert select_lead_model("prd_review", quality="best") == "opus"
    assert select_lead_model("outcome_review", quality="best") == "opus"
    assert select_lead_model("execution", quality="best") == "opus"
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
        return subprocess.CompletedProcess(
            argv,
            2,
            stdout="",
            stderr='{"error":"missing /lead","result":"not an outcome"}',
        )

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
    assert result.stderr == '{"error":"missing /lead","result":"not an outcome"}'
    assert result.outcome is None


def test_invoke_claude_lead_reports_timeout_without_auto_retry(tmp_path):
    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, timeout=3, output="partial stdout", stderr=b"partial stderr")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="intent",
        instruction="Review intent.",
        cwd=tmp_path,
        timeout_s=3,
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert not result.probe.ok
    assert result.probe.reason == "lead_invocation_timeout"
    assert result.probe.details["timeout_s"] == 3
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
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


def test_invoke_claude_lead_fails_loudly_on_claude_json_schema_drift(tmp_path):
    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"message": _outcome_block(), "model": "claude-opus-4-7"}),
            stderr="",
        )

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert not result.probe.ok
    assert result.probe.reason == "claude_json_schema_drift"
    assert result.probe.details["missing_or_invalid"] == "result"
    assert result.outcome is None


def test_invoke_claude_lead_reports_malformed_outcome_block(tmp_path):
    def fake_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = json.dumps({"result": "<dual_agent_outcome>{not-json}</dual_agent_outcome>"})
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
    )

    result = invoke_claude_lead(request, runner=fake_runner)

    assert not result.probe.ok
    assert result.probe.reason.startswith("invalid dual_agent_outcome block")


def test_write_handoff_packet_pins_schema_planning_checksums_and_lead_skill(tmp_path):
    prd = tmp_path / "docs" / "prd.md"
    tdd = tmp_path / "docs" / "tdd.md"
    lead_skill = tmp_path / ".claude" / "skills" / "lead" / "SKILL.md"
    prd.parent.mkdir()
    lead_skill.parent.mkdir(parents=True)
    prd.write_text("# PRD\nShip the small thing.\n")
    tdd.write_text("# TDD\nPublic boundary first.\n")
    lead_skill.write_text("---\nname: lead\nversion: 0.2.1\n---\n# Lead\n")

    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="tdd_review",
        instruction="Review the TDD plan.",
        cwd=tmp_path,
        expected_specialists=("Reviewer",),
        expected_decisions=("public boundary first",),
        expected_objections=(),
    )

    packet_path = write_handoff_packet(
        request,
        planning_artifacts=(
            PlanningArtifact(path=prd, kind="prd"),
            PlanningArtifact(path=tdd, kind="tdd_plan"),
        ),
        lead_skill_path=lead_skill,
    )

    assert packet_path == tmp_path / ".handoff" / "slice0-lead.json"
    payload = json.loads(packet_path.read_text())
    assert payload["packet_schema_version"] == HANDOFF_PACKET_SCHEMA_VERSION
    assert payload["task_id"] == "slice0-lead"
    assert payload["cwd"] == str(tmp_path.resolve())
    assert payload["planning_artifacts"] == [
        {
            "kind": "prd",
            "path": "docs/prd.md",
            "sha256": compute_file_sha256(prd),
            "mutable_by_worker": False,
        },
        {
            "kind": "tdd_plan",
            "path": "docs/tdd.md",
            "sha256": compute_file_sha256(tdd),
            "mutable_by_worker": False,
        },
    ]
    assert payload["lead_skill"]["path"] == str(lead_skill.resolve())
    assert payload["lead_skill"]["sha256"] == compute_file_sha256(lead_skill)
    assert payload["lead_skill"]["version"] == "0.2.1"
    assert payload["outcome_validation_policy"] == {
        "malformed_outcome": "retry_once_with_corrective_packet",
        "fidelity_failure": "abort_to_operator",
        "subprocess_failure": "abort_to_operator",
        "timeout": "abort_to_operator",
    }


def test_build_lead_prompt_references_handoff_packet_instead_of_raw_context(tmp_path):
    packet = tmp_path / ".handoff" / "slice0-lead.json"
    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review using the handoff packet.",
        cwd=tmp_path,
        handoff_packet_path=packet,
    )

    prompt = build_lead_prompt(request)

    assert str(packet) in prompt
    assert "Treat the handoff packet as the bounded context source" in prompt


def test_handoff_packet_rejects_planning_artifacts_outside_worktree(tmp_path):
    outside = tmp_path.parent / "outside-prd.md"
    outside.write_text("# PRD\n")
    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="prd_review",
        instruction="Review PRD.",
        cwd=tmp_path,
    )

    with pytest.raises(ValueError, match="planning artifact outside cwd"):
        build_handoff_packet(
            request,
            planning_artifacts=(PlanningArtifact(path=outside, kind="prd"),),
        )


def test_custom_outcome_validation_policy_is_pinned_in_packet(tmp_path):
    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="outcome_review",
        instruction="Review outcome.",
        cwd=tmp_path,
    )
    packet = build_handoff_packet(
        request,
        outcome_validation_policy=OutcomeValidationPolicy(
            malformed_outcome="abort_to_operator",
            fidelity_failure="retry_once_with_corrective_packet",
            subprocess_failure="abort_to_operator",
            timeout="abort_to_operator",
        ),
    )

    assert packet.outcome_validation_policy.malformed_outcome == "abort_to_operator"
    assert packet.outcome_validation_policy.fidelity_failure == "retry_once_with_corrective_packet"


def test_verify_planning_artifact_boundaries_detects_worker_spec_rewrite(tmp_path):
    prd = tmp_path / "docs" / "prd.md"
    prd.parent.mkdir()
    prd.write_text("# PRD\nOriginal scope.\n")
    request = LeadInvocationRequest(
        task_id="slice0-lead",
        gate="execution",
        instruction="Implement without editing the PRD.",
        cwd=tmp_path,
    )
    packet_path = write_handoff_packet(
        request,
        planning_artifacts=(PlanningArtifact(path=prd, kind="prd"),),
    )

    assert verify_planning_artifact_boundaries(packet_path).ok

    prd.write_text("# PRD\nRewritten after implementation.\n")
    result = verify_planning_artifact_boundaries(packet_path)

    assert not result.ok
    assert result.reason == "planning_artifact_checksum_changed"
    assert result.details["paths"] == ["docs/prd.md"]
