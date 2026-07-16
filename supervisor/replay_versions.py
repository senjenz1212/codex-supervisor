"""Compatibility checks for replay artifact schema versions."""
from __future__ import annotations

from typing import Any


CURRENT_SCHEMA_VERSIONS: dict[str, str] = {
    "execution_provenance": "dual-agent-execution-provenance/v1",
    "manifest": "dual-agent-replay-manifest/v1",
    "trace_envelope": "dual-agent-trace-envelope/v1",
    "failure_taxonomy": "dual-agent-failure-taxonomy/v1",
    "interaction": "dual-agent-interaction/v1",
    "production_trace_export": "dual-agent-production-trace-export/v1",
}

SCHEMA_ALIASES: dict[str, str] = {
    "replay_manifest": "manifest",
    "agent_interaction": "interaction",
}

# Sentinel version meaning "the schema key is absent from the manifest".
# A KNOWN_MIGRATIONS entry keyed on it declares that the schema was
# introduced after older manifests were exported, so its absence is
# migratable rather than incompatible.
SCHEMA_ABSENT = "__schema_absent__"

KNOWN_MIGRATIONS: dict[tuple[str, str], dict[str, str]] = {
    ("manifest", "dual-agent-replay-manifest/v0"): {
        "to": "dual-agent-replay-manifest/v1",
        "migration": "manifest.v0_to_v1",
    },
    ("execution_provenance", SCHEMA_ABSENT): {
        "to": "dual-agent-execution-provenance/v1",
        "migration": "execution_provenance.absent_to_v1",
    },
    ("production_trace_export", SCHEMA_ABSENT): {
        "to": "dual-agent-production-trace-export/v1",
        "migration": "production_trace_export.absent_to_v1",
    },
}


def check_replay_schema_versions(manifest: dict[str, Any]) -> dict[str, Any]:
    versions = manifest.get("schema_versions")
    if not isinstance(versions, dict):
        versions = {}
    migrations_required: list[dict[str, str]] = []
    unknown_versions: list[dict[str, str]] = []

    observed_canonical: set[str] = set()
    for schema, observed in sorted(versions.items()):
        schema_name = str(schema)
        canonical_schema = SCHEMA_ALIASES.get(schema_name, schema_name)
        observed_canonical.add(canonical_schema)
        observed_version = str(observed)
        current = CURRENT_SCHEMA_VERSIONS.get(canonical_schema)
        if current is None:
            unknown_versions.append({"schema": schema_name, "version": observed_version})
            continue
        if observed_version == current:
            continue
        migration = KNOWN_MIGRATIONS.get((canonical_schema, observed_version))
        if migration is not None and migration["to"] == current:
            migrations_required.append({
                "schema": canonical_schema,
                "from": observed_version,
                "to": migration["to"],
                "migration": migration["migration"],
            })
            continue
        unknown_versions.append({"schema": schema_name, "version": observed_version})

    declared_any = bool(versions)
    missing_current: list[str] = []
    missing_schema_migrations: list[dict[str, str]] = []
    for schema in sorted(CURRENT_SCHEMA_VERSIONS):
        if schema in observed_canonical:
            continue
        current = CURRENT_SCHEMA_VERSIONS[schema]
        migration = (
            KNOWN_MIGRATIONS.get((schema, SCHEMA_ABSENT))
            if declared_any
            else None
        )
        if migration is not None and migration["to"] == current:
            missing_schema_migrations.append({
                "schema": schema,
                "from": SCHEMA_ABSENT,
                "to": current,
                "migration": migration["migration"],
            })
            continue
        missing_current.append(schema)
    return {
        "schema_version": "dual-agent-replay-version-check/v1",
        "status": (
            "incompatible"
            if (
                unknown_versions
                or missing_current
                or migrations_required
                or missing_schema_migrations
            )
            else "compatible"
        ),
        "current_versions": dict(CURRENT_SCHEMA_VERSIONS),
        "observed_versions": {str(key): str(value) for key, value in versions.items()},
        "migrations_required": migrations_required,
        "missing_schema_migrations": missing_schema_migrations,
        "unknown_versions": unknown_versions,
        "missing_current_schemas": missing_current,
    }
