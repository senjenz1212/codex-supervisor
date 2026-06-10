# TDD Plan

## test_quality_trends_record_run_computes_first_pass_revision_rounds_and_time_to_accept

Maps to: P1, P4.

Red: A seeded ledger with one first-pass accepted gate and one revised gate produces no persisted trend rows or wrong round counts.

Green: `record_quality_trends_for_run` persists rows with correct `first_pass_accepted`, `revision_rounds`, `accepted`, and `time_to_accepted_outcome_s`.

## test_quality_trends_uses_final_gate_acceptance_after_reviewer_override

Maps to: P1, P4.

Red: A gate with an initial accepted result, a reviewer/runtime blocked override, and a later accepted result is counted as first-pass accepted.

Green: Trend computation uses the final accepted gate result, marks first-pass acceptance false, records one revision round, and points details at the final accepted event.

## test_quality_trends_does_not_keep_stale_acceptance_for_final_block

Maps to: P1, P4.

Red: A gate with an accepted result followed by a final blocked override keeps a stale accepted event for `time_to_accepted_outcome_s` and revision math.

Green: Trend computation records `accepted=false`, clears `accepted_gate_result_event_id` and `time_to_accepted_outcome_s`, and counts the final blocked state as unresolved revision pressure.

## test_quality_trends_sampled_p11_audit_catches_false_accept

Maps to: P2, P4.

Red: An accepted runtime evidence fixture with a missing declared deliverable records `false_accept_rate=0`.

Green: The sampled audit re-verifies the deliverable against git state and records `false_accept_count=1`, denominator `1`, and rate `1.0`.

## test_quality_trends_query_filters_by_task_class_and_gate_without_writes

Maps to: P3, P4.

Red: Trend query either ignores filters or writes an event/row while reading.

Green: `query_quality_trends` returns only the requested `task_class`/`gate` aggregate and leaves event and trend write counts unchanged.

## test_quality_trends_cli_query_is_read_only_json

Maps to: P3, P4.

Red: The CLI query command fails to parse, writes rows, or emits non-JSON.

Green: `scripts/run_supervisor_trend_metrics.py query` returns aggregate rows as JSON and performs no writes.

## test_quality_trends_metrics_do_not_advance_or_block_gates

Maps to: P4.

Red: Metrics recording writes `dual_agent_gate_result` events or mutates workflow step status.

Green: Metrics writes only trend rows and leaves gate/workflow authority untouched.

## test_quality_trends_prefers_supervisor_final_status_over_claude_status

Maps to: P1, P4.

Red: A gate result with `claude_gate_status=accepted` and `supervisor_final_status=blocked` is counted as accepted.

Green: Trend computation treats the supervisor final status as authoritative and records the gate as not accepted.

## test_forward_migration_adds_supervisor_quality_trends

Maps to: P1.

Red: Forward migrations do not create the `supervisor_quality_trends` table or task/gate index.

Green: Migration version 8 creates the table with metric and audit columns plus `idx_supervisor_quality_trends_task_gate`.
