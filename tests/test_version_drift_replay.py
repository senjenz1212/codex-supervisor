from __future__ import annotations

from supervisor.replay_versions import check_replay_schema_versions


def test_replay_schema_versions_reject_missing_schema_declaration():
    result = check_replay_schema_versions({})

    assert result["status"] == "incompatible"
    assert result["missing_current_schemas"] == [
        "execution_provenance",
        "failure_taxonomy",
        "interaction",
        "manifest",
        "production_trace_export",
        "trace_envelope",
    ]


def test_replay_schema_versions_accept_current_manifest_versions():
    result = check_replay_schema_versions({
        "schema_versions": {
            "execution_provenance": "dual-agent-execution-provenance/v1",
            "manifest": "dual-agent-replay-manifest/v1",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "interaction": "dual-agent-interaction/v1",
            "production_trace_export": (
                "dual-agent-production-trace-export/v1"
            ),
        }
    })

    assert result["status"] == "compatible"
    assert result["migrations_required"] == []


def test_replay_schema_versions_accept_real_exported_manifest_keys():
    result = check_replay_schema_versions({
        "schema_versions": {
            "execution_provenance": "dual-agent-execution-provenance/v1",
            "replay_manifest": "dual-agent-replay-manifest/v1",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "agent_interaction": "dual-agent-interaction/v1",
            "production_trace_export": (
                "dual-agent-production-trace-export/v1"
            ),
        }
    })

    assert result["status"] == "compatible"
    assert result["unknown_versions"] == []


def test_replay_schema_versions_reject_unapplied_known_migration():
    result = check_replay_schema_versions({
        "schema_versions": {
            "execution_provenance": "dual-agent-execution-provenance/v1",
            "manifest": "dual-agent-replay-manifest/v0",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "interaction": "dual-agent-interaction/v1",
            "production_trace_export": (
                "dual-agent-production-trace-export/v1"
            ),
        }
    })

    assert result["status"] == "incompatible"
    assert result["migrations_required"] == [{
        "schema": "manifest",
        "from": "dual-agent-replay-manifest/v0",
        "to": "dual-agent-replay-manifest/v1",
        "migration": "manifest.v0_to_v1",
    }]


def test_replay_schema_versions_accept_manifest_predating_new_schemas():
    result = check_replay_schema_versions({
        "schema_versions": {
            "manifest": "dual-agent-replay-manifest/v1",
            "trace_envelope": "dual-agent-trace-envelope/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "interaction": "dual-agent-interaction/v1",
        }
    })

    assert result["status"] == "compatible"
    assert result["missing_current_schemas"] == []
    assert result["missing_schema_migrations"] == [
        {
            "schema": "execution_provenance",
            "from": "__schema_absent__",
            "to": "dual-agent-execution-provenance/v1",
            "migration": "execution_provenance.absent_to_v1",
        },
        {
            "schema": "production_trace_export",
            "from": "__schema_absent__",
            "to": "dual-agent-production-trace-export/v1",
            "migration": "production_trace_export.absent_to_v1",
        },
    ]


def test_replay_schema_versions_reject_absence_without_registered_migration():
    result = check_replay_schema_versions({
        "schema_versions": {
            "manifest": "dual-agent-replay-manifest/v1",
            "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
            "interaction": "dual-agent-interaction/v1",
        }
    })

    assert result["status"] == "incompatible"
    assert result["missing_current_schemas"] == ["trace_envelope"]


def test_replay_schema_versions_reject_empty_schema_declaration():
    result = check_replay_schema_versions({"schema_versions": {}})

    assert result["status"] == "incompatible"
    assert result["missing_schema_migrations"] == []
    assert result["missing_current_schemas"] == [
        "execution_provenance",
        "failure_taxonomy",
        "interaction",
        "manifest",
        "production_trace_export",
        "trace_envelope",
    ]


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
