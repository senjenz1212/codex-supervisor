# Dual-Agent Version Drift Replay

## Purpose

Replay artifacts are allowed to outlive the code that produced them. The supervisor now treats replay schema compatibility as an explicit gate instead of assuming old manifests are still readable.

## Compatibility Contract

- Current replay schemas are declared in `supervisor/replay_versions.py`.
- Known forward migrations are named and deterministic.
- Unknown future or incompatible versions fail closed with `status: incompatible`.
- The replay checker is pure and does not invoke live tools, model calls, or subprocesses.

## Current Schemas

| Schema | Current version |
|---|---|
| manifest | `dual-agent-replay-manifest/v1` |
| trace_envelope | `dual-agent-trace-envelope/v1` |
| failure_taxonomy | `dual-agent-failure-taxonomy/v1` |
| interaction | `dual-agent-interaction/v1` |

## Verification

Focused tests:

```bash
uv run pytest -q tests/test_version_drift_replay.py tests/test_schema_migrations.py
```
