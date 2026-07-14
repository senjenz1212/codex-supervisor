from __future__ import annotations

import sys
from hashlib import sha256

import pytest

from supervisor.run_manifest import (
    _acceptance_artifact_roots,
    _canonical_json_bytes,
    build_execution_provenance,
    execution_provenance_issues,
)


def _runtime_component_receipt(
    category: str,
    component_id: str,
    *,
    content: bytes | None = None,
    digest: str | None = None,
) -> dict:
    receipt = {
        "category": category,
        "component_id": component_id,
        "sha256": digest or sha256(content or b"").hexdigest(),
        "receipt_ref": f"receipt://runtime-component/{category}/{component_id}",
        "capture_source": "execution_time",
        "source": "runtime_component_receipt",
    }
    if content is not None:
        receipt["canonical_bytes"] = content
    return receipt


def _canonical_tool_contract(name: str, content: bytes) -> dict:
    return {
        "tool_name": name,
        "canonical_bytes": content,
        "sha256": sha256(content).hexdigest(),
        "receipt_ref": f"receipt://tool-contract/{name}",
        "capture_source": "execution_time",
        "source": "runtime_tool_registry",
    }


def test_acceptance_artifact_root_rejects_parent_component(tmp_path):
    assert _acceptance_artifact_roots(
        tmp_path,
        task_id="..",
    ) == (
        tmp_path.resolve()
        / "docs"
        / "dual-agent"
        / "dual-agent-task",
    )


def test_hash_bearing_json_rejects_non_json_values() -> None:
    with pytest.raises(TypeError):
        _canonical_json_bytes({"opaque": object()})
    with pytest.raises(ValueError, match="Out of range float values"):
        _canonical_json_bytes({"not_a_number": float("nan")})


def test_observed_call_shape_cannot_stand_in_for_canonical_tool_contract():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "execution",
            "kind": "agent.message",
            "payload": {
                "trace_envelope": {
                    "tool_calls": [{
                        "name": "mutate_repository",
                        "args": {"path": "README.md"},
                        "result_summary": {"status": "ok"},
                    }],
                },
            },
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    assert "tool_contracts" in provenance["missing_component_categories"]
    assert provenance["missing_tool_contracts"] == ["mutate_repository"]
    [contract] = provenance["component_hashes"]["tool_contracts"]
    assert contract["sha256"] == ""
    assert contract["details"]["status"] == "not_recorded"


def test_canonical_tool_contract_digest_mismatch_fails_closed():
    contract_bytes = b'{"name":"mutate_repository","inputSchema":{"type":"object"}}'
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "execution",
            "kind": "agent.message",
            "payload": {
                "trace_envelope": {
                    "tool_calls": [{"name": "mutate_repository", "args": {}}],
                },
            },
        }],
        canonical_tool_contracts=[{
            "tool_name": "mutate_repository",
            "canonical_bytes": contract_bytes,
            "sha256": "0" * 64,
            "receipt_ref": "receipt://tool-contract/mutate_repository",
            "capture_source": "execution_time",
            "source": "runtime_tool_registry",
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    assert provenance["status"] == "incomplete"
    assert provenance["missing_tool_contracts"] == ["mutate_repository"]
    assert provenance["invalid_tool_contracts"] == ["mutate_repository"]
    [contract] = provenance["component_hashes"]["tool_contracts"]
    assert contract["sha256"] == sha256(contract_bytes).hexdigest()
    assert contract["details"]["status"] == "digest_mismatch"


def test_tool_contract_hash_comes_from_canonical_bytes_not_observed_fields():
    contract_bytes = b'{"name":"mutate_repository","inputSchema":{"type":"object"}}'
    contract = _canonical_tool_contract("mutate_repository", contract_bytes)

    def build(args, result_summary):
        return build_execution_provenance(
            events=[{
                "event_id": 1,
                "gate": "execution",
                "kind": "agent.message",
                "payload": {
                    "trace_envelope": {
                        "tool_calls": [{
                            "name": "mutate_repository",
                            "args": args,
                            "result_summary": result_summary,
                        }],
                    },
                },
            }],
            canonical_tool_contracts=[contract],
            workspace_snapshot={"root": "/recorded/workspace"},
        )

    first = build({"path": "README.md"}, {"status": "ok"})
    adversarial = build(
        {"invented_field": {"nested": True}},
        {"different_result_shape": [1, 2, 3]},
    )

    [first_contract] = first["component_hashes"]["tool_contracts"]
    [adversarial_contract] = adversarial["component_hashes"]["tool_contracts"]
    expected = sha256(contract_bytes).hexdigest()
    assert first_contract["sha256"] == expected
    assert adversarial_contract["sha256"] == expected
    assert first_contract["details"]["status"] == "verified"
    assert first["missing_tool_contracts"] == []
    assert first["invalid_tool_contracts"] == []


def test_requested_exact_looking_model_is_not_a_provider_returned_identity():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 2,
            "gate": "execution",
            "kind": "supervisor_worker_completed",
            "payload": {
                "worker_id": "executor-0",
                "runtime": "custom",
                "provider_family": "provider",
                "requested_model": "provider/model-v1",
                "model": "provider/model-v1",
            },
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [lane] = provenance["model_resolutions"]
    assert lane["resolved_model"] == "provider/model-v1"
    assert lane["resolution_source"] == "configured_model"
    assert lane["exact_model_identity"] is False
    assert provenance["unresolved_model_lanes"] == [lane["lane_id"]]


def test_provider_model_resolution_input_supersedes_a_route_alias():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 3,
            "gate": "execution",
            "kind": "supervisor_worker_completed",
            "payload": {
                "worker_id": "executor-0",
                "runtime": "custom",
                "provider_family": "provider",
                "requested_model": "default",
                "model": "default",
            },
        }],
        provider_model_resolutions=[{
            "event_id": 3,
            "gate": "execution",
            "lane": "executor-0",
            "runtime": "custom",
            "provider_family": "provider",
            "requested_model": "default",
            "resolved_model": "provider/model-v1-20260713",
            "provider_response_receipt_ref": (
                "receipt://provider-response/executor-0"
            ),
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [lane] = provenance["model_resolutions"]
    assert lane["requested_model"] == "default"
    assert lane["resolved_model"] == "provider/model-v1-20260713"
    assert (
        lane["provider_response_source"]
        == "receipt://provider-response/executor-0"
    )
    assert lane["resolution_source"] == "response_model"
    assert lane["exact_model_identity"] is True
    assert provenance["unresolved_model_lanes"] == []


def test_provider_model_resolution_without_bound_response_receipt_stays_unresolved():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 4,
            "gate": "execution",
            "kind": "supervisor_worker_completed",
            "payload": {
                "worker_id": "executor-0",
                "runtime": "custom",
                "provider_family": "provider",
                "requested_model": "default",
                "model": "default",
            },
        }],
        provider_model_resolutions=[{
            "event_id": 4,
            "gate": "execution",
            "lane": "executor-0",
            "runtime": "custom",
            "provider_family": "provider",
            "requested_model": "default",
            "resolved_model": "provider/model-v1-20260713",
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [lane] = provenance["model_resolutions"]
    assert lane["provider_response_source"] == ""
    assert lane["resolution_source"] != "response_model"
    assert lane["exact_model_identity"] is False
    assert provenance["unresolved_model_lanes"] == [lane["lane_id"]]


def test_default_model_route_is_resolved_but_missing_components_stay_incomplete():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 7,
            "gate": "outcome_review",
            "kind": "supervisor_worker_completed",
            "payload": {
                "gate": "outcome_review",
                "worker_id": "independent-reviewer-0",
                "runtime": "cursor_sdk",
                "provider_family": "cursor",
                "model": "default",
            },
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [lane] = provenance["model_resolutions"]
    assert lane["requested_model"] == "default"
    assert lane["resolved_model"] == "cursor:auto/default"
    assert lane["resolution_source"] == "provider_route"
    assert lane["exact_model_identity"] is False
    assert provenance["status"] == "incomplete"
    assert provenance["missing_component_categories"] == [
        "prompts",
        "tool_contracts",
        "containers",
        "cli",
        "evaluators",
    ]
    assert provenance["unresolved_model_lanes"]
    assert provenance["workspace_issues"] == ["workspace_snapshot_not_captured"]


def test_observed_alias_is_recorded_as_a_route_resolution_not_an_exact_model():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 8,
            "gate": "outcome_review",
            "kind": "supervisor_worker_completed",
            "payload": {
                "gate": "outcome_review",
                "worker_id": "independent-reviewer-0",
                "runtime": "cursor_sdk",
                "provider_family": "cursor",
                "requested_model": "default",
                "model": "auto",
                "resolved_model": "auto",
            },
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [lane] = provenance["model_resolutions"]
    assert lane["requested_model"] == "default"
    assert lane["observed_model"] == "auto"
    assert lane["resolved_model"] == "cursor:auto/auto"
    assert lane["resolution_source"] == "provider_route"
    assert lane["exact_model_identity"] is False


def test_unresolved_workspace_image_cli_and_evaluator_keep_manifest_incomplete(
    tmp_path,
):
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "outcome_review",
            "kind": "agent.message",
            "payload": {
                "prompt": "Review the result.",
                "model": "model-v1",
                "runtime": "custom",
                "provider_family": "provider",
                "container_image": "latest",
                "trace_envelope": {
                    "tool_calls": [
                        {
                            "name": "invoke_missing_runtime",
                            "args": {"cli_command": "definitely-not-installed"},
                        },
                        {
                            "name": "verify_result",
                            "args": {"verifier_id": "named-only"},
                        },
                    ],
                },
            },
        }],
        workspace_snapshot={
            "status": "missing_at_export",
            "root": str(tmp_path / "missing"),
        },
    )

    assert provenance["status"] == "incomplete"
    assert set(provenance["missing_component_categories"]) >= {
        "containers",
        "cli",
        "evaluators",
    }
    assert provenance["workspace_issues"] == ["workspace_snapshot_not_captured"]


def test_one_runtime_component_receipt_cannot_mask_an_unrecorded_used_component():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "execution",
            "kind": "agent.message",
            "payload": {
                "prompt": "Execute both tools.",
                "trace_envelope": {
                    "tool_calls": [
                        {
                            "name": "invoke_recorded_runtime",
                            "args": {"runtime": "custom"},
                        },
                        {
                            "name": "invoke_unrecorded_runtime",
                            "args": {"runtime": "custom"},
                        },
                    ],
                },
            },
        }],
        runtime_component_receipts=[
            _runtime_component_receipt(
                "cli",
                "cli:invoke_recorded_runtime",
                content=b"recorded runtime bytes",
            ),
        ],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    cli_components = {
        item["component_id"]: item
        for item in provenance["component_hashes"]["cli"]
    }
    assert cli_components["cli:invoke_recorded_runtime"]["details"]["status"] == (
        "verified"
    )
    assert cli_components["cli:invoke_unrecorded_runtime"]["details"]["status"] == (
        "not_recorded"
    )
    assert "cli" in provenance["missing_component_categories"]


def test_cli_and_evaluator_claims_are_not_rehashed_or_trusted_at_export():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "execution",
            "kind": "agent.message",
            "payload": {
                "prompt": "Run and evaluate.",
                "trace_envelope": {
                    "tool_calls": [
                        {
                            "name": "invoke_custom",
                            "args": {
                                "runtime": "custom",
                                "cli_command": sys.executable,
                                "executable_sha256": "a" * 64,
                            },
                        },
                        {
                            "name": "verify_result",
                            "args": {
                                "evaluator_sha256": "b" * 64,
                            },
                        },
                    ],
                },
            },
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [cli] = provenance["component_hashes"]["cli"]
    [evaluator] = provenance["component_hashes"]["evaluators"]
    assert cli["sha256"] == ""
    assert cli["details"]["status"] == "not_recorded"
    assert evaluator["sha256"] == ""
    assert evaluator["details"]["status"] == "not_recorded"
    assert {"cli", "evaluators"} <= set(
        provenance["missing_component_categories"]
    )


def test_bound_execution_time_cli_and_evaluator_digests_are_accepted():
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "execution",
            "kind": "agent.message",
            "payload": {
                "prompt": "Run and evaluate.",
                "trace_envelope": {
                    "tool_calls": [
                        {
                            "name": "invoke_custom",
                            "args": {"runtime": "custom"},
                        },
                        {
                            "name": "verify_result",
                            "args": {},
                        },
                    ],
                },
            },
        }],
        runtime_component_receipts=[
            _runtime_component_receipt(
                "cli",
                "cli:invoke_custom",
                digest="a" * 64,
            ),
            _runtime_component_receipt(
                "evaluators",
                "evaluator:verify_result",
                digest="b" * 64,
            ),
        ],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [cli] = provenance["component_hashes"]["cli"]
    [evaluator] = provenance["component_hashes"]["evaluators"]
    assert cli["sha256"] == "a" * 64
    assert cli["details"]["status"] == "verified"
    assert cli["details"]["digest_only"] is True
    assert evaluator["sha256"] == "b" * 64
    assert evaluator["details"]["status"] == "verified"
    assert evaluator["details"]["digest_only"] is True
    assert "cli" not in provenance["missing_component_categories"]
    assert "evaluators" not in provenance["missing_component_categories"]


def test_manifest_is_complete_only_when_bytes_models_and_workspace_are_pinned(
    tmp_path,
):
    tool_contract_bytes = {
        "invoke_custom": (
            b'{"name":"invoke_custom","inputSchema":{"type":"object"}}'
        ),
        "verify_result": (
            b'{"name":"verify_result","inputSchema":{"type":"object"}}'
        ),
    }
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provenance = build_execution_provenance(
        events=[{
            "event_id": 1,
            "gate": "outcome_review",
            "kind": "agent.message",
            "payload": {
                "prompt": "Review the result.",
                "requested_model": "provider/model-v1",
                "runtime": "custom",
                "provider_family": "provider",
                "container_digest": "sha256:" + ("b" * 64),
                "trace_envelope": {
                    "tool_calls": [
                        {
                            "name": "invoke_custom",
                            "args": {
                                "runtime": "custom",
                                "cli_command": "custom-cli",
                            },
                        },
                        {
                            "name": "verify_result",
                            "args": {},
                        },
                    ],
                },
            },
        }],
        provider_model_resolutions=[{
            "event_id": 1,
            "gate": "outcome_review",
            "lane": "agent.message",
            "runtime": "custom",
            "provider_family": "provider",
            "requested_model": "provider/model-v1",
            "resolved_model": "provider/model-v1-20260713",
            "provider_response_receipt_ref": (
                "receipt://provider-response/agent-message"
            ),
        }],
        canonical_tool_contracts=[
            _canonical_tool_contract(name, canonical_bytes)
            for name, canonical_bytes in tool_contract_bytes.items()
        ],
        runtime_component_receipts=[
            _runtime_component_receipt(
                "containers",
                "container:container_digest",
                digest="b" * 64,
            ),
            _runtime_component_receipt(
                "cli",
                "cli:invoke_custom",
                content=b"custom cli executable bytes",
            ),
            _runtime_component_receipt(
                "evaluators",
                "evaluator:verify_result",
                content=b"verify result evaluator bytes",
            ),
        ],
        workspace_snapshot={
            "status": "captured",
            "capture_source": "accepted_gate_event",
            "root": str(workspace),
            "git": {"head_sha": "a" * 40},
            "file_tree_sha256": "d" * 64,
            "immutable_snapshot": {
                "status": "captured",
                "sha256": "e" * 64,
            },
        },
    )

    assert provenance["status"] == "complete"
    assert provenance["missing_component_categories"] == []
    assert provenance["missing_tool_contracts"] == []
    assert provenance["invalid_tool_contracts"] == []
    assert provenance["unresolved_model_lanes"] == []
    assert provenance["workspace_issues"] == []
    assert execution_provenance_issues(provenance) == []


def test_source_artifact_hashes_skip_paths_escaping_workspace_root(tmp_path):
    from supervisor.run_manifest import _source_artifact_hashes

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    hashes = _source_artifact_hashes(
        root,
        {
            "planning_artifacts": [
                {"kind": "prd", "path": "../outside.md"},
                {"kind": "tdd", "path": str(outside)},
            ],
        },
    )

    assert hashes == {}


def test_source_artifact_hashes_fall_back_when_read_fails(
    tmp_path,
    monkeypatch,
):
    from pathlib import Path

    from supervisor.run_manifest import _source_artifact_hashes

    root = tmp_path / "repo"
    root.mkdir()
    artifact = root / "prd.md"
    artifact.write_text("plan", encoding="utf-8")
    declared = "f" * 64

    def _deny(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", _deny)

    hashes = _source_artifact_hashes(
        root,
        {
            "planning_artifacts": [
                {"kind": "prd", "path": "prd.md", "sha256": declared},
                {"kind": "tdd", "path": "tdd.md"},
            ],
        },
    )

    assert hashes == {"prd": declared}


def test_workspace_overlay_entry_degrades_on_unreadable_path(tmp_path):
    from supervisor.run_manifest import _workspace_overlay_entry

    directory = tmp_path / "now-a-directory"
    directory.mkdir()

    entry = _workspace_overlay_entry(directory, root=tmp_path)

    assert entry == {
        "path": "now-a-directory",
        "kind": "file",
        "content_omitted": "unreadable:IsADirectoryError",
    }


def test_acceptance_evidence_degrades_when_snapshot_capture_fails(
    tmp_path,
    monkeypatch,
):
    import json

    from supervisor import run_manifest

    repo = tmp_path / "repo"
    repo.mkdir()
    handoff = repo / "handoff.json"
    handoff.write_text(
        json.dumps({
            "cwd": str(repo),
            "planning_artifacts": [
                {"kind": "prd", "path": "../outside.md"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "status": "accepted",
        "task_id": "task-1",
        "handoff_packet_path": str(handoff),
    }

    evidence = run_manifest.capture_acceptance_evidence(dict(payload))
    assert evidence is not None
    assert evidence["status"] == "incomplete"

    def _boom(*args, **kwargs):
        raise ValueError("escapes workspace root")

    monkeypatch.setattr(run_manifest, "capture_workspace_snapshot", _boom)

    degraded = run_manifest.capture_acceptance_evidence(dict(payload))

    assert degraded is not None
    assert degraded["status"] == "incomplete"
    assert degraded["snapshot_ref"] is not None
