from __future__ import annotations

from supervisor.replay_versions import check_replay_schema_versions


def test_replay_schema_versions_accept_current_manifest_versions():
    result = check_replay_schema_versions({
        "schema_versions": {
            "manifest": "dual-agent-replay-manifest/v1",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "interaction": "dual-agent-interaction/v1",
        }
    })

    assert result["status"] == "compatible"
    assert result["migrations_required"] == []


def test_replay_schema_versions_accept_real_exported_manifest_keys():
    result = check_replay_schema_versions({
        "schema_versions": {
            "replay_manifest": "dual-agent-replay-manifest/v1",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "agent_interaction": "dual-agent-interaction/v1",
        }
    })

    assert result["status"] == "compatible"
    assert result["unknown_versions"] == []


def test_replay_schema_versions_map_known_forward_migration():
    result = check_replay_schema_versions({
        "schema_versions": {
            "manifest": "dual-agent-replay-manifest/v0",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "interaction": "dual-agent-interaction/v1",
        }
    })

    assert result["status"] == "compatible"
    assert result["migrations_required"] == [{
        "schema": "manifest",
        "from": "dual-agent-replay-manifest/v0",
        "to": "dual-agent-replay-manifest/v1",
        "migration": "manifest.v0_to_v1",
    }]


def test_replay_schema_versions_reject_unknown_future_versions():
    result = check_replay_schema_versions({
        "schema_versions": {
            "manifest": "dual-agent-replay-manifest/v99",
            "trace_envelope": "dual-agent-trace-envelope/v1",
        }
    })

    assert result["status"] == "incompatible"
    assert result["unknown_versions"] == [{
        "schema": "manifest",
        "version": "dual-agent-replay-manifest/v99",
    }]
