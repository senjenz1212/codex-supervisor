# Read-Only Observability Sink TDD

Use one RED -> one GREEN -> repeat.

## Cycle 1

RED: `tests/test_auto_evolve_benchmark_observability_sink.py::test_observability_sink_requires_aeb0_artifact_ref`

GREEN: implement a local sink report writer that blocks without an AEB-0 artifact ref.

## Cycle 2

RED: `tests/test_auto_evolve_benchmark_observability_sink.py::test_observability_sink_preserves_false_authority_flags_from_aeb0`

GREEN: read the AEB-0 artifact, mirror source authority flags, and emit false sink authority flags.

## Cycle 3

RED: `tests/test_auto_evolve_benchmark_observability_sink.py::test_observability_sink_payload_cannot_set_applyability_or_promotion`

GREEN: reject sink-origin authority fields.

## Cycle 4

RED: `tests/test_auto_evolve_benchmark_observability_sink.py::test_observability_sink_annotations_cannot_satisfy_evaluator_or_quality_controls`

GREEN: reject sink-origin evaluator provenance, quality controls, empty-floor, and overlay fields as trust-path evidence.

## Cycle 5

RED: `tests/test_auto_evolve_benchmark_observability_sink.py::test_observability_sink_cannot_change_roster_or_effective_vote`

GREEN: reject sink-origin roster/effective-vote fields.

## Cycle 6

RED: `tests/test_auto_evolve_benchmark_observability_sink.py::test_blocked_downstream_ledger_record_preserves_false_authority_flags`

GREEN: append a schema-versioned JSONL ledger event.
