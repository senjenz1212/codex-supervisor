from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from supervisor.dual_agent_lead import DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES


def _complete_dynamic_receipts() -> list[dict]:
    shared = {
        "subagents": [
            {
                "task_id": "workflow-1:audit-1",
                "persona_id": "reviewer.codebase_audit",
                "timeout_s": 300,
                "budget_usd": 1.5,
                "permission_mode": "readOnly",
                "tool_pins": ["rg", "sed", "pytest --collect-only"],
                "transcript_ref": "artifacts/dynamic/audit-1/transcript.jsonl",
                "output_hash": "sha256:abc123",
                "changed_files": [],
            }
        ],
        "supervision_layer": "codex_plus_lead",
        "lead_integrator": "claude_code_lead",
        "output_schema": "dynamic-workflow-output/v1",
        "output_hash": "sha256:abc123",
        "headless": True,
        "no_session_persistence": True,
        "replay_ref": "docs/dual-agent/workflow-1/replay/manifest.json",
        "worktree_comparison_ref": "docs/dual-agent/workflow-1/dynamic-comparison.md",
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


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_replay_files(tmp_path: Path) -> dict[str, str]:
    files = {
        "manifest_ref": tmp_path / "docs" / "dual-agent" / "workflow-1" / "replay" / "dynamic-workflow-manifest.json",
        "transcript_ref": tmp_path / "artifacts" / "dynamic" / "audit-1" / "transcript.jsonl",
        "output_ref": tmp_path / "artifacts" / "dynamic" / "audit-1" / "output.json",
        "replay_ref": tmp_path / "docs" / "dual-agent" / "workflow-1" / "replay" / "manifest.json",
        "worktree_comparison_ref": tmp_path / "docs" / "dual-agent" / "workflow-1" / "dynamic-comparison.md",
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
        result[f"{key.removesuffix('_ref')}_sha256"] = _sha256(path)
    return result


def _replay_verified_dynamic_receipts(tmp_path: Path) -> list[dict]:
    refs = _write_replay_files(tmp_path)
    receipts = _complete_dynamic_receipts()
    for receipt in receipts:
        receipt.update({
            "manifest_ref": refs["manifest_ref"],
            "manifest_sha256": refs["manifest_sha256"],
            "replay_ref": refs["replay_ref"],
            "replay_sha256": refs["replay_sha256"],
            "worktree_comparison_ref": refs["worktree_comparison_ref"],
            "worktree_comparison_sha256": refs["worktree_comparison_sha256"],
        })
        receipt["subagents"][0].update({
            "transcript_ref": refs["transcript_ref"],
            "transcript_sha256": refs["transcript_sha256"],
            "output_ref": refs["output_ref"],
            "output_sha256": refs["output_sha256"],
            "output_hash": f"sha256:{refs['output_sha256']}",
        })
        receipt["output_hash"] = f"sha256:{refs['output_sha256']}"
    return receipts


def _agentic_runtime_native_receipt(tmp_path: Path, *, role: str = "codebase_audit") -> dict:
    worker_dir = tmp_path / ".handoff" / "agentic-workers" / "workflow-1" / "audit-1"
    worker_dir.mkdir(parents=True)
    transcript = worker_dir / "transcript.jsonl"
    output = worker_dir / "output.json"
    transcript.write_text('{"event":"completed","agent_id":"agent-audit-1"}\n', encoding="utf-8")
    output.write_text('{"decision":"accept","changed_files":[]}\n', encoding="utf-8")
    return {
        "receipt_id": "agentic-audit-1",
        "kind": "dynamic_subagent_result",
        "status": "passed",
        "task_id": "workflow-1:audit-1",
        "persona_id": "reviewer.codebase_audit",
        "role": role,
        "decision": "accept",
        "severity": "none",
        "agent_runtime": "claude_code",
        "agent_id": "agent-audit-1",
        "permission_mode": "readOnly",
        "tool_pins": ["rg", "sed"],
        "timeout_s": 300,
        "budget_usd": 1.5,
        "changed_files": [],
        "transcript_ref": str(transcript.relative_to(tmp_path)),
        "transcript_sha256": _sha256(transcript),
        "output_ref": str(output.relative_to(tmp_path)),
        "output_sha256": _sha256(output),
        "output_hash": f"sha256:{_sha256(output)}",
        "evidence_grade": "self_reported",
    }


def test_verify_dynamic_workflow_receipts_rejects_missing_preview_gates():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[],
    )

    assert probe.probe_id == "P13"
    assert probe.status == "red"
    assert probe.reason == "missing_dynamic_workflow_receipts"
    assert set(probe.details["missing_gates"]) == set(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES)


def test_verify_dynamic_workflow_receipts_accepts_complete_preview_manifest():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=_complete_dynamic_receipts(),
    )

    assert probe.probe_id == "P13"
    assert probe.status == "green"
    assert probe.reason == "dynamic_workflow_receipts_verified"
    assert probe.details["verified_gates"] == list(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES)


def test_verify_dynamic_workflow_receipts_canonicalizes_task_class():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic-workflow-preview",
        dynamic_workflow_task_class="codebase-audit",
        tool_receipts=_complete_dynamic_receipts(),
    )

    assert probe.status == "green"
    assert probe.details["dynamic_workflow_task_class"] == "codebase_audit"
    assert probe.details["requested_dynamic_workflow_task_class"] == "codebase-audit"


def test_verify_dynamic_workflow_receipts_rejects_names_only_manifest():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic-workflow-preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=[
            {
                "receipt_id": "dyn-names-only",
                "kind": "dynamic_workflow_manifest",
                "status": "passed",
                "gates": {gate: {} for gate in DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES},
            }
        ],
    )

    assert probe.probe_id == "P13"
    assert probe.status == "red"
    assert probe.reason == "missing_dynamic_workflow_receipts"
    assert set(probe.details["missing_gates"]) == set(DEFAULT_DYNAMIC_WORKFLOW_PREVIEW_GATES)
    assert probe.details["invalid_gates"]


def test_verify_dynamic_workflow_receipts_skips_lead_direct():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[],
    )

    assert probe.probe_id == "P13"
    assert probe.status == "green"
    assert probe.reason == "dynamic_workflow_not_requested"


def test_verify_dynamic_workflow_receipts_requires_timeout_and_budget():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipts = _complete_dynamic_receipts()
    receipts[0]["subagents"][0].pop("budget_usd")

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=receipts,
    )

    assert probe.status == "red"
    assert probe.reason in {
        "missing_dynamic_workflow_receipts",
        "invalid_dynamic_workflow_receipts",
    }
    assert "subagent_0_missing_budget_usd" in str(probe.details["invalid_gates"])


def test_verify_dynamic_workflow_receipts_requires_changed_file_manifest():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipts = _complete_dynamic_receipts()
    receipts[0]["subagents"][0].pop("changed_files")

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=receipts,
    )

    assert probe.status == "red"
    assert "subagent_0_missing_changed_files" in str(probe.details["invalid_gates"])


def test_dynamic_receipt_hash_mismatch_returns_tamper_reason(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipts = _replay_verified_dynamic_receipts(tmp_path)

    accepted = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=receipts,
        cwd=tmp_path,
    )

    assert accepted.status == "green"
    assert accepted.details["verified_refs"]

    transcript = tmp_path / receipts[0]["subagents"][0]["transcript_ref"]
    transcript.write_text('{"event":"tampered"}\n', encoding="utf-8")

    rejected = verify_dynamic_workflow_receipts(
        execution_layer_mode="dynamic_workflow_preview",
        dynamic_workflow_task_class="codebase_audit",
        tool_receipts=receipts,
        cwd=tmp_path,
    )

    assert rejected.status == "red"
    assert rejected.reason == "dynamic_workflow_receipt_hash_mismatch"
    assert "subagent_0_transcript_sha256_mismatch" in str(rejected.details["invalid_gates"])


def test_agentic_evidence_grade_ignores_lead_declared_grade(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipt = _agentic_runtime_native_receipt(tmp_path)
    receipt["evidence_grade"] = "runtime_native"
    receipt["transcript_ref"] = "lead-authored/transcript.jsonl"
    receipt["output_ref"] = "lead-authored/output.json"

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "red"
    assert probe.reason == "agentic_lead_policy_blocked"
    assert probe.details["agentic_policy"]["subagents"][0]["declared_evidence_grade"] == "runtime_native"
    assert probe.details["agentic_policy"]["subagents"][0]["evidence_grade"] == "self_reported"
    assert "required_evidence_grade_not_met" in str(probe.details["agentic_policy"]["blocking_findings"])


def test_agentic_required_accepts_supervisor_owned_runtime_native_receipts(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[_agentic_runtime_native_receipt(tmp_path)],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "green"
    assert probe.reason == "dynamic_workflow_not_requested"
    subagent = probe.details["agentic_policy"]["subagents"][0]
    assert subagent["evidence_grade"] == "runtime_native"
    assert subagent["agent_runtime"] == "claude_code"
    assert subagent["agent_id"] == "agent-audit-1"


def test_agentic_required_blocks_failed_supervisor_owned_worker_receipts(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipt = _agentic_runtime_native_receipt(tmp_path)
    receipt["status"] = "timeout"
    receipt["decision"] = "revise"

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "red"
    assert "subagent_status_not_passing" in str(probe.details["agentic_policy"]["blocking_findings"])


def test_solo_exception_only_applies_to_artifact_only_gates(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    solo_receipt = {
        "receipt_id": "lead-solo",
        "kind": "agentic_lead_execution",
        "status": "passed",
        "execution_mode": "solo",
    }

    execution_probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[solo_receipt],
        cwd=tmp_path,
        gate="execution",
        agentic_lead_policy="required",
        solo_exception_for_artifact_only_gates=True,
    )
    artifact_probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[solo_receipt],
        cwd=tmp_path,
        gate="tdd_review",
        agentic_lead_policy="required",
        solo_exception_for_artifact_only_gates=True,
    )

    assert execution_probe.status == "red"
    assert "agentic_lead_solo_execution" in str(execution_probe.details["agentic_policy"]["blocking_findings"])
    assert execution_probe.details["agentic_policy"]["solo_exception_applies"] is False
    assert artifact_probe.status == "green"
    assert artifact_probe.details["agentic_policy"]["solo_exception_applies"] is True


def test_docs_dual_agent_refs_do_not_count_as_runtime_native_worker_evidence(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    worker_dir = tmp_path / "docs" / "dual-agent" / "workflow-1" / "workers" / "audit-1"
    worker_dir.mkdir(parents=True)
    transcript = worker_dir / "transcript.jsonl"
    output = worker_dir / "output.json"
    transcript.write_text('{"event":"completed"}\n', encoding="utf-8")
    output.write_text('{"decision":"accept"}\n', encoding="utf-8")
    receipt = {
        **_agentic_runtime_native_receipt(tmp_path),
        "transcript_ref": str(transcript.relative_to(tmp_path)),
        "transcript_sha256": _sha256(transcript),
        "output_ref": str(output.relative_to(tmp_path)),
        "output_sha256": _sha256(output),
        "evidence_grade": "runtime_native",
    }

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "red"
    subagent = probe.details["agentic_policy"]["subagents"][0]
    assert subagent["declared_evidence_grade"] == "runtime_native"
    assert subagent["evidence_grade"] == "lead_captured"
    assert "required_evidence_grade_not_met" in str(probe.details["agentic_policy"]["blocking_findings"])


def test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    captured_dir = tmp_path / "artifacts" / "lead-captured-workers" / "audit-1"
    captured_dir.mkdir(parents=True)
    transcript = captured_dir / "transcript.jsonl"
    output = captured_dir / "output.json"
    transcript.write_text('{"event":"completed","agent_id":"agent-audit-1"}\n', encoding="utf-8")
    output.write_text('{"decision":"accept","changed_files":[]}\n', encoding="utf-8")
    receipt = {
        **_agentic_runtime_native_receipt(tmp_path),
        "transcript_ref": str(transcript.relative_to(tmp_path)),
        "transcript_sha256": _sha256(transcript),
        "output_ref": str(output.relative_to(tmp_path)),
        "output_sha256": _sha256(output),
        "evidence_grade": "runtime_native",
    }

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["codebase_audit"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "red"
    subagent = probe.details["agentic_policy"]["subagents"][0]
    assert subagent["declared_evidence_grade"] == "runtime_native"
    assert subagent["evidence_grade"] == "lead_captured"
    assert "required_evidence_grade_not_met" in str(probe.details["agentic_policy"]["blocking_findings"])


def test_agentic_required_blocks_critical_independent_reviewer(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipt = _agentic_runtime_native_receipt(tmp_path, role="independent_reviewer")
    receipt["decision"] = "revise"
    receipt["severity"] = "critical"
    receipt["objections"] = ["unresolved contradiction"]

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        agentic_lead_policy="required",
        min_subagents=1,
        required_roles=["independent_reviewer"],
        required_evidence_grade="runtime_native",
    )

    assert probe.status == "red"
    assert probe.reason == "agentic_lead_policy_blocked"
    assert "independent_reviewer_blocked" in str(probe.details["agentic_policy"]["blocking_findings"])


def test_execution_fanout_requires_runtime_native_evidence_by_default(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    worker_dir = tmp_path / "artifacts" / "lead-captured-workers" / "audit-1"
    worker_dir.mkdir(parents=True)
    transcript = worker_dir / "transcript.jsonl"
    output = worker_dir / "output.json"
    transcript.write_text('{"event":"completed"}\n', encoding="utf-8")
    output.write_text('{"decision":"accept"}\n', encoding="utf-8")
    receipt = {
        **_agentic_runtime_native_receipt(tmp_path),
        "transcript_ref": str(transcript.relative_to(tmp_path)),
        "transcript_sha256": _sha256(transcript),
        "output_ref": str(output.relative_to(tmp_path)),
        "output_sha256": _sha256(output),
        "output_hash": f"sha256:{_sha256(output)}",
    }

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        gate="execution",
        agentic_lead_policy="off",
    )

    assert probe.status == "red"
    policy = probe.details["agentic_policy"]
    assert policy["policy"] == "off"
    assert policy["required_evidence_grade"] == "self_reported"
    assert policy["effective_required_evidence_grade"] == "runtime_native"
    assert "fanout_evidence_grade_not_met" in str(policy["blocking_findings"])


def test_outcome_review_fanout_requires_runtime_native_evidence_by_default(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    worker_dir = tmp_path / "artifacts" / "lead-captured-workers" / "audit-1"
    worker_dir.mkdir(parents=True)
    transcript = worker_dir / "transcript.jsonl"
    output = worker_dir / "output.json"
    transcript.write_text('{"event":"completed"}\n', encoding="utf-8")
    output.write_text('{"decision":"accept"}\n', encoding="utf-8")
    receipt = {
        **_agentic_runtime_native_receipt(tmp_path),
        "transcript_ref": str(transcript.relative_to(tmp_path)),
        "transcript_sha256": _sha256(transcript),
        "output_ref": str(output.relative_to(tmp_path)),
        "output_sha256": _sha256(output),
        "output_hash": f"sha256:{_sha256(output)}",
    }

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        gate="outcome_review",
        agentic_lead_policy="off",
    )

    assert probe.status == "red"
    policy = probe.details["agentic_policy"]
    assert policy["effective_required_evidence_grade"] == "runtime_native"
    assert "fanout_evidence_grade_not_met" in str(policy["blocking_findings"])


def test_non_execution_fanout_requires_lead_captured_evidence_by_default(tmp_path):
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    receipt = _agentic_runtime_native_receipt(tmp_path)
    worker_dir = tmp_path / "artifacts" / "lead-captured-workers" / "audit-1"
    worker_dir.mkdir(parents=True)
    transcript = worker_dir / "transcript.jsonl"
    output = worker_dir / "output.json"
    transcript.write_text('{"event":"completed"}\n', encoding="utf-8")
    output.write_text('{"decision":"accept"}\n', encoding="utf-8")
    receipt["transcript_ref"] = str(transcript.relative_to(tmp_path))
    receipt["transcript_sha256"] = _sha256(transcript)
    receipt["output_ref"] = str(output.relative_to(tmp_path))
    receipt["output_sha256"] = _sha256(output)
    receipt["output_hash"] = f"sha256:{_sha256(output)}"

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[receipt],
        cwd=tmp_path,
        gate="tdd_review",
        agentic_lead_policy="off",
    )

    assert probe.status == "green"
    policy = probe.details["agentic_policy"]
    assert policy["policy"] == "off"
    assert policy["status"] == "accepted"
    assert policy["effective_required_evidence_grade"] == "lead_captured"
    assert policy["subagents"][0]["evidence_grade"] == "lead_captured"


def test_fanout_free_runs_do_not_raise_evidence_grade_floor():
    from supervisor.dynamic_workflow_receipts import verify_dynamic_workflow_receipts

    probe = verify_dynamic_workflow_receipts(
        execution_layer_mode="lead_direct",
        dynamic_workflow_task_class=None,
        tool_receipts=[],
        gate="execution",
        agentic_lead_policy="off",
    )

    assert probe.status == "green"
    assert probe.details["agentic_policy"]["status"] == "not_applicable"
    assert probe.details["agentic_policy"]["effective_required_evidence_grade"] == "self_reported"
