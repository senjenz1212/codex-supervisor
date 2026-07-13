from __future__ import annotations

import base64
import json
import subprocess
from hashlib import sha256
from pathlib import Path

from supervisor.quality_trends import (
    run_sampled_p11_false_accept_audit,
    run_weekly_p11_audit_if_due,
)
from supervisor.run_manifest import (
    build_workspace_overlay,
    execution_provenance_issues,
)
from supervisor.state import State


def _verified_component(category: str) -> dict:
    content = f"{category} fixture bytes".encode()
    digest = sha256(content).hexdigest()
    return {
        "component_id": f"{category}:fixture",
        "kind": category.rstrip("s"),
        "source": "runtime_component_receipt",
        "sha256": digest,
        "details": {
            "status": "verified",
            "capture_source": "execution_time",
            "receipt_ref": f"receipt://runtime-component/{category}/fixture",
            "declared_sha256": digest,
            "computed_sha256": digest,
            "canonical_bytes_base64": base64.b64encode(content).decode("ascii"),
            "canonical_size_bytes": len(content),
        },
    }


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_manifest(
    path: Path,
    *,
    repo: Path,
    commit: str,
    immutable_snapshot: dict,
) -> Path:
    tool_contract_bytes = b'{"name":"fixture","inputSchema":{"type":"object"}}'
    tool_contract_sha256 = sha256(tool_contract_bytes).hexdigest()
    manifest_path = path / "recorded-replay-manifest.json"
    manifest_path.write_text(
        json.dumps({
            "schema_version": "dual-agent-replay-manifest/v1",
            "run_id": "trend-run",
            "task_id": "trend-task",
            "schema_versions": {
                "execution_provenance": "dual-agent-execution-provenance/v1",
                "manifest": "dual-agent-replay-manifest/v1",
                "trace_envelope": "dual-agent-trace-envelope/v1",
                "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
                "interaction": "dual-agent-interaction/v1",
            },
            "workspace_snapshot": {
                "status": "captured",
                "capture_source": "accepted_gate_event",
                "root": str(repo),
                "git": {
                    "head": commit,
                    "head_sha": commit,
                    "status_short": "dirty",
                    "diff_bytes": 1,
                },
                "immutable_snapshot": immutable_snapshot,
            },
            "execution_provenance": {
                "schema_version": "dual-agent-execution-provenance/v1",
                "status": "complete",
                "unresolved_model_lanes": [],
                "missing_component_categories": [],
                "required_tool_contracts": ["fixture"],
                "missing_tool_contracts": [],
                "invalid_tool_contracts": [],
                "workspace_issues": [],
                "model_resolutions": [{
                    "lane_id": "fixture",
                    "resolved_model": "provider/model-v1",
                    "resolution_source": "response_model",
                    "provider_response_source": (
                        "receipt://provider-response/fixture"
                    ),
                    "exact_model_identity": True,
                }],
                "component_hashes": {
                    category: (
                        [{
                            "component_id": "tool-contract:fixture",
                            "kind": "tool_contract",
                            "source": "fixture",
                            "sha256": tool_contract_sha256,
                            "details": {
                                "status": "verified",
                                "tool_name": "fixture",
                                "declared_sha256": tool_contract_sha256,
                                "computed_sha256": tool_contract_sha256,
                                "canonical_bytes_base64": base64.b64encode(
                                    tool_contract_bytes
                                ).decode("ascii"),
                                "canonical_size_bytes": len(tool_contract_bytes),
                                "capture_source": "execution_time",
                                "receipt_ref": (
                                    "receipt://tool-contract/fixture"
                                ),
                            },
                        }]
                        if category == "tool_contracts"
                        else [_verified_component(category)]
                    )
                    for category in (
                        "prompts",
                        "tool_contracts",
                        "containers",
                        "cli",
                        "evaluators",
                    )
                },
            },
        }),
        encoding="utf-8",
    )
    return manifest_path


def test_strict_provenance_rejects_hash_only_tool_contract_placeholder(tmp_path):
    manifest_path = _write_manifest(
        tmp_path,
        repo=tmp_path,
        commit="a" * 40,
        immutable_snapshot={},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provenance = manifest["execution_provenance"]
    provenance["component_hashes"]["tool_contracts"] = [{
        "component_id": "tool-contract:fixture",
        "sha256": "f" * 64,
    }]

    issues = execution_provenance_issues(provenance)

    assert "tool_contract_artifacts_invalid" in issues


def test_strict_provenance_recomputes_canonical_tool_contract_digest(tmp_path):
    manifest_path = _write_manifest(
        tmp_path,
        repo=tmp_path,
        commit="a" * 40,
        immutable_snapshot={},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    [contract] = manifest["execution_provenance"]["component_hashes"][
        "tool_contracts"
    ]
    contract["details"]["canonical_bytes_base64"] = base64.b64encode(
        b'{"name":"tampered"}'
    ).decode("ascii")
    contract["details"]["canonical_size_bytes"] = len(b'{"name":"tampered"}')

    issues = execution_provenance_issues(manifest["execution_provenance"])

    assert "tool_contract_artifacts_invalid" in issues


def test_strict_provenance_rejects_alias_marked_as_exact(tmp_path):
    manifest_path = _write_manifest(
        tmp_path,
        repo=tmp_path,
        commit="a" * 40,
        immutable_snapshot={},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    [lane] = manifest["execution_provenance"]["model_resolutions"]
    lane["resolved_model"] = "cursor:auto/default"

    issues = execution_provenance_issues(manifest["execution_provenance"])

    assert "model_resolutions_not_exact" in issues


def _write_event(
    state: State,
    *,
    kind: str,
    payload: dict,
) -> None:
    state.write_event(
        run_id="trend-run",
        source="dual_agent",
        kind=kind,
        payload=payload,
    )


def test_p11_regrade_uses_hashed_immutable_snapshot_after_live_tree_changes(tmp_path):
    baseline_head = _init_git_repo(tmp_path)
    (tmp_path / "artifact.txt").write_text("recorded dirty result\n", encoding="utf-8")
    immutable_snapshot = build_workspace_overlay(
        tmp_path,
        base_commit=baseline_head,
    )
    manifest_path = _write_manifest(
        tmp_path,
        repo=tmp_path,
        commit=baseline_head,
        immutable_snapshot=immutable_snapshot,
    )
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
        cwd=str(tmp_path),
        intent="audit accepted dirty deliverables",
        current_gate="outcome_review",
        status="accepted",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
            "cwd": str(tmp_path),
            "replay_manifest_path": str(manifest_path),
            "replay_manifest_sha256": sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
        },
    )
    _write_event(
        state,
        kind="dual_agent_runtime_evidence",
        payload={
            "gate": "outcome_review",
            "round_index": 1,
            "probe": {
                "details": {
                    "baseline": {
                        "status": "passed",
                        "head": baseline_head,
                        "reason": "git_head_captured",
                    },
                },
            },
            "receipts": [],
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "trend-task",
            "gate": "outcome_review",
            "status": "accepted",
            "supervisor_final_status": "accepted",
            "claude_gate_status": "accepted",
            "attempts": 1,
            "outcome": {
                "decision": "accept",
                "changed_files": ["artifact.txt"],
                "tests": [],
                "summary": "done",
            },
        },
    )

    first = run_sampled_p11_false_accept_audit(
        state,
        run_id="trend-run",
        sample_size=1,
        test_timeout_s=1,
    )
    (tmp_path / "artifact.txt").unlink()
    second = run_sampled_p11_false_accept_audit(
        state,
        run_id="trend-run",
        sample_size=1,
        test_timeout_s=1,
    )

    assert first["false_accept_count"] == 0
    assert second["false_accept_count"] == 0
    assert second["operation"]["kind"] == "regrade"
    assert second["recorded_checkout"]["immutable_snapshot"]["sha256"] == (
        immutable_snapshot["sha256"]
    )


def test_p11_regrade_fails_closed_when_manifest_has_no_recorded_repo_location(
    tmp_path,
):
    commit = _init_git_repo(tmp_path)
    manifest_path = _write_manifest(
        tmp_path,
        repo=tmp_path,
        commit=commit,
        immutable_snapshot={},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["workspace_snapshot"].pop("root")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
        cwd=str(tmp_path),
        intent="audit accepted deliverables",
        current_gate="outcome_review",
        status="accepted",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
            "cwd": str(tmp_path),
            "replay_manifest_path": str(manifest_path),
            "replay_manifest_sha256": sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "trend-task",
            "gate": "outcome_review",
            "status": "accepted",
            "supervisor_final_status": "accepted",
            "claude_gate_status": "accepted",
            "attempts": 1,
            "outcome": {
                "decision": "accept",
                "changed_files": ["README.md"],
                "tests": [],
                "summary": "done",
            },
        },
    )

    def forbidden_runner(*args, **kwargs):
        raise AssertionError(
            "missing recorded repo location must not use the live checkout"
        )

    audit = run_sampled_p11_false_accept_audit(
        state,
        run_id="trend-run",
        sample_size=1,
        runner=forbidden_runner,
    )

    assert audit["status"] == "incompatible"
    assert audit["reason"] == "recorded_checkout_missing"
    assert audit["details"]["missing_field"] == "workspace_snapshot.root"
    assert audit["audited"] == []


def test_weekly_p11_audit_preserves_incompatible_status_without_recorded_checkout(tmp_path):
    _init_git_repo(tmp_path)
    state = State(str(tmp_path / "state.db"))
    state.upsert_dual_agent_workflow(
        run_id="trend-run",
        task_id="trend-task",
        cwd=str(tmp_path),
        intent="audit accepted deliverables",
        current_gate="outcome_review",
        status="accepted",
        max_rounds_per_gate=2,
        user_facing=False,
    )
    _write_event(
        state,
        kind="dual_agent_workflow_route",
        payload={
            "task_id": "trend-task",
            "run_id": "trend-run",
            "lesson_task_class": "source_change",
            "cwd": str(tmp_path),
        },
    )
    _write_event(
        state,
        kind="dual_agent_gate_result",
        payload={
            "task_id": "trend-task",
            "gate": "outcome_review",
            "status": "accepted",
            "supervisor_final_status": "accepted",
            "claude_gate_status": "accepted",
            "attempts": 1,
            "outcome": {
                "decision": "accept",
                "changed_files": ["README.md"],
                "tests": [],
                "summary": "done",
            },
        },
    )

    audit = run_weekly_p11_audit_if_due(
        state,
        run_id="trend-run",
        sample_size=1,
        now=10_000,
    )

    assert audit["status"] == "incompatible"
    assert audit["reason"] == "recorded_checkout_missing"
    assert audit["policy_regression_rollbacks"] == []
