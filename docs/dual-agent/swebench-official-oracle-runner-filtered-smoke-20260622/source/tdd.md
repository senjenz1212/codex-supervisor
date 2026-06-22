## Public Boundary First

test_official_replay_cli_requires_oracle_adapter_before_metrics

Maps to: Slice 1, P1, P5

Red: Invoke the official replay CLI without an oracle adapter and assert it exits before writing FAR/TAR metrics.

Green: Add adapter validation at the CLI boundary and return an unavailable report or clear failure without metric emission.

test_official_replay_cli_rejects_unknown_adapter_kind_before_metrics

Maps to: Slice 1, P1, P5

Red: Invoke the official replay CLI with a configured adapter but an unsupported adapter kind and assert it exits before writing any official replay report.

Green: Restrict official replay adapter kinds to official_docker_or_equivalent and official_equivalent at the CLI boundary.

test_official_replay_runner_rejects_unknown_adapter_kind_before_metrics

Maps to: Slice 1, P1, P5

Red: Invoke the direct official replay runner with an unsupported adapter kind and assert it fails before loading predictions, materializing bundles, or writing metrics.

Green: Enforce the same adapter-kind allowlist inside the runner so library callers cannot bypass CLI validation.

test_official_replay_cli_passes_fake_runner_and_writes_report

Maps to: Slice 1, P1, P4, P5

Red: Invoke the CLI with a fake official adapter and assert the replay runner receives oracle_runner and writes official_replay_report.json.

Green: Wire the configured oracle runner through the official replay path while preserving injectable fake runners.

test_official_replay_label_only_adapter_receipt_is_unavailable

Maps to: Slice 1, P1, P4, P5

Red: Run an adapter labeled official_docker_or_equivalent that returns only command and return_code, then assert the report is unavailable instead of completed.

Green: Validate official-labeled oracle receipts for command, return code, output hashes, evaluator metadata, harness or Docker metadata, artifact paths, and FAIL_TO_PASS/PASS_TO_PASS statuses.

test_official_replay_cli_suppresses_metrics_when_oracle_proof_invalid

Maps to: Slice 1, P1, P4, P5

Red: Invoke the CLI with an official-labeled adapter whose receipt proof is structurally invalid, then assert the summary, top-level report, nested replay report, and instance report suppress FAR/TAR instead of emitting metrics.

Green: Propagate unavailable official evidence to every report and CLI FAR/TAR surface with explicit unavailable reasons, rewriting stale nested replay artifacts when proof fails.

test_instance_id_filtering_happens_before_prediction_coverage

Maps to: Slice 2, P2

Red: Load multiple rows with predictions for only the selected --instance-id and assert non-selected rows do not fail coverage.

Green: Apply selected instance filtering before prediction validation and persist selection metadata.

test_limit_filtering_is_deterministic_and_reported

Maps to: Slice 2, P2

Red: Run --limit twice on the same replay input and assert the same selected instances and manifest metadata.

Green: Implement deterministic limit selection and report the chosen row identifiers.

test_oracle_receipts_are_after_frozen_decisions_and_hide_oracle_fields

Maps to: Slice 3, P3, P4

Red: Run a fake official adapter and assert frozen_decisions_path exists before oracle receipts while public artifacts omit hidden fields.

Green: Write frozen decisions before oracle execution and add a hidden-field leak check across public artifacts.

test_official_equivalent_label_validation_failure_is_unavailable

Maps to: Slice 3, P4, P5

Red: Configure a fake official-equivalent adapter with known mismatched labels and assert the report is unavailable, not accepted.

Green: Add smoke label validation and propagate mismatch as an unavailable report state with report-only invariants.
