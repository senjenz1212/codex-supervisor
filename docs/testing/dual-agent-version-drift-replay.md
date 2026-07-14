# Dual-Agent Version Drift Replay

## Purpose

Replay artifacts are allowed to outlive the code that produced them. The supervisor now treats replay schema compatibility as an explicit gate instead of assuming old manifests are still readable.

## Compatibility Contract

- Current replay schemas are declared in `supervisor/replay_versions.py`.
- Known forward migrations are named and deterministic.
- Unknown or incompatible versions, manifests missing any current schema
  (`missing_current_schemas`), and manifests that still require a known forward
  migration (`migrations_required`) all fail closed with `status: incompatible`.
- The replay checker is pure and does not invoke live tools, model calls, or subprocesses.

## Current Schemas

| Schema | Current version |
|---|---|
| execution_provenance | `dual-agent-execution-provenance/v1` |
| manifest | `dual-agent-replay-manifest/v1` |
| trace_envelope | `dual-agent-trace-envelope/v1` |
| failure_taxonomy | `dual-agent-failure-taxonomy/v1` |
| interaction | `dual-agent-interaction/v1` |

## Verification

Focused tests:

```bash
uv run pytest -q tests/test_version_drift_replay.py tests/test_schema_migrations.py
```
