## Public Boundary First

test_official_replay_smoke_writes_report_with_selected_instances

Maps to: P1, P2.
Red: Invoke the public official replay smoke path with one or more selected instance identifiers and assert official_replay_report.json exists with instance_count greater than zero, candidate_count greater than zero, and selected instance metadata.
Green: Wire the smoke path to the filtered official replay runner and ensure reports are written only for selected rows.

test_official_replay_smoke_records_frozen_before_oracle_receipts

Maps to: P3.
Red: Run the smoke with a fake official oracle adapter and assert frozen decisions exist before oracle receipts and receipt hashes are referenced in the report.
Green: Preserve the official replay runner ordering and expose the receipt paths in the smoke artifact.

test_verified_smoke_is_labeled_plumbing_only

Maps to: P4.
Red: Assert the report labels SWE-bench Verified as plumbing_smoke_only and refuses powered improvement or human mergeability claims.
Green: Add or thread smoke-only labeling into the official replay report.

test_full_panel_metric_unavailable_without_full_roster

Maps to: P5.
Red: Run the smoke without a fully available reviewer roster and assert S_full or panel metrics are unavailable, not imputed from Codex-only or partial-roster evidence.
Green: Reuse Slice 2 availability semantics in the replay report path.

test_official_replay_smoke_emits_no_policy_proposal

Maps to: P6.
Red: Pass the smoke report through policy proposal derivation and assert no applyable proposal is created.
Green: Keep report-only flags false and preserve smoke status in promotion guardrails.
