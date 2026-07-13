from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path

from supervisor.run_manifest import (
    build_execution_provenance,
    execution_provenance_issues,
)


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
    assert len(contract["sha256"]) == 64
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
    contract = {
        "tool_name": "mutate_repository",
        "canonical_bytes": contract_bytes,
        "sha256": sha256(contract_bytes).hexdigest(),
        "source": "runtime_tool_registry",
    }

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
            "provider_response_source": "transport.response.model",
        }],
        workspace_snapshot={"root": "/recorded/workspace"},
    )

    [lane] = provenance["model_resolutions"]
    assert lane["requested_model"] == "default"
    assert lane["resolved_model"] == "provider/model-v1-20260713"
    assert lane["provider_response_source"] == "transport.response.model"
    assert lane["resolution_source"] == "response_model"
    assert lane["exact_model_identity"] is True
    assert provenance["unresolved_model_lanes"] == []


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


def test_manifest_is_complete_only_when_bytes_models_and_workspace_are_pinned(
    tmp_path,
):
    executable_hash = sha256(Path(sys.executable).read_bytes()).hexdigest()
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
                                "cli_command": sys.executable,
                            },
                        },
                        {
                            "name": "verify_result",
                            "args": {"verifier_hash": "c" * 64},
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
            "provider_response_source": "transport.response.model",
        }],
        canonical_tool_contracts=[
            {
                "tool_name": name,
                "canonical_bytes": canonical_bytes,
                "sha256": sha256(canonical_bytes).hexdigest(),
                "source": "runtime_tool_registry",
            }
            for name, canonical_bytes in tool_contract_bytes.items()
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

    assert executable_hash
    assert provenance["status"] == "complete"
    assert provenance["missing_component_categories"] == []
    assert provenance["missing_tool_contracts"] == []
    assert provenance["invalid_tool_contracts"] == []
    assert provenance["unresolved_model_lanes"] == []
    assert provenance["workspace_issues"] == []
    assert execution_provenance_issues(provenance) == []
