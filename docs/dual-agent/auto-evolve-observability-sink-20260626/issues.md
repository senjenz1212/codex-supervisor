# Read-Only Observability Sink Issues

## SINK-1: Artifact Ingest Gate

Public boundary: `ingest_auto_evolve_observability_sink`.

First RED: sink refuses to ingest without `aeb0_artifact_path`.

GREEN: write `observability_sink_report.json` with `status=blocked`, reason `aeb0_artifact_ref_required`, and all false authority flags.

## SINK-2: Authority Flag Preservation

Public boundary: `ingest_auto_evolve_observability_sink`.

First RED: ingest an AEB-0 blocked artifact plus a normal trace/dashboard payload.

GREEN: source and sink authority flags remain false; trust-path booleans forbid evaluator/policy satisfaction.

## SINK-3: Sink Payload Rejection

Public boundary: `ingest_auto_evolve_observability_sink`.

First RED: payload attempts to set applyability, promotion, mutation, evaluator provenance, empty-floor, candidate overlay, effective vote, and roster fields.

GREEN: report lists rejected fields and preserves false sink authority flags.

## SINK-4: Ledger Event

Public boundary: `append_auto_evolve_benchmark_event`.

First RED: write one blocked sink/ledger event without AEB-0.

GREEN: JSONL row preserves schema version, artifact hash/existence, AEB-0 dependency status, promise IDs, blocked reasons, false authority flags, and deferred annotation status.
