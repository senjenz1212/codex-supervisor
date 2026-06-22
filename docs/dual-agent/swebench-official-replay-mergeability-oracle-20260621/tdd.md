## test_official_replay_refuses_dataset_fetch_without_opt_in

Maps to: P1 / Slice 1

RED: Call the official replay public function without allow_dataset_fetch and without an injected loader, then assert it raises before materializer or oracle callables run.

GREEN: Add the public official replay function with an explicit opt-in guard around lazy dataset loading.

## test_official_replay_materializes_public_bundle_and_excludes_hidden_oracle

Maps to: P2 / Slice 1

RED: Run official replay with an injected official-style record and materializer, then assert public packets and reviewer packets omit test_patch, FAIL_TO_PASS, PASS_TO_PASS, oracle_accept, and final_score.

GREEN: Convert official records into replay entries using only public fields and protected hidden path policy.

## test_official_replay_freezes_decisions_before_oracle_adapter

Maps to: P3 / Slice 2

RED: Use an oracle adapter that records whether frozen_decisions.json exists before it receives hidden oracle fields, and assert the oracle output timestamp follows the frozen decisions timestamp.

GREEN: Add an oracle adapter hook to the replay runner and invoke it only after decision freeze.

## test_official_replay_report_labels_oracle_adapter_and_stays_report_only

Maps to: P4 / Slice 2

RED: Run the official replay path and assert oracle_adapter_kind is present while metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced remain false.

GREEN: Wrap the replay report with official replay metadata and preserve report-only invariants.

## test_official_replay_cli_refuses_without_opt_in

Maps to: P1 / Slice 1

RED: Invoke the replay CLI in official mode without allow_dataset_fetch and assert return code 2 with a clear opt-in message.

GREEN: Add CLI flags for official replay and enforce the same opt-in policy as the Python interface.
