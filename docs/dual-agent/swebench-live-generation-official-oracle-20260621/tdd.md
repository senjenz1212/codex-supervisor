## test_official_live_refuses_without_allow_live_before_generators_run

Maps to: P1 / Issue 1

RED: Call the official live runner with allow_live false and failing loader, materializer, and generators, then assert the failure occurs before any side effect.

GREEN: Add an allow_live guard at the public runner entrypoint.

## test_official_live_refuses_without_budget_before_generators_run

Maps to: P1 / Issue 1

RED: Call the official live runner with max_budget_usd equal to zero and assert no generator is invoked.

GREEN: Add a positive budget guard before loading official records.

## test_official_live_generates_matched_arms_and_reuses_official_replay

Maps to: P2, P3, P5 / Issue 2

RED: Use injected loader, materializer, oracle adapter, and fake generators, then assert both arms share model, provider, budget, timeout, prompt hash, and the report embeds an official replay report.

GREEN: Generate prediction JSONL artifacts and delegate evaluation to the official replay adapter.

## test_official_live_generator_inputs_exclude_hidden_oracle

Maps to: P2 / Issue 1

RED: Have fake generators serialize their input and assert patch, test_patch, FAIL_TO_PASS, PASS_TO_PASS, final_score, and oracle_accept are absent.

GREEN: Build generator input only from public official record fields and public worktree manifests.

## test_official_live_budget_overrun_is_unavailable_not_accepted

Maps to: P4, P5 / Issue 3

RED: Return a generation cost above max_budget_usd and assert the live report is unavailable, no replay report exists, and policy switches remain false.

GREEN: Track cumulative arm cost and stop before official replay when a budget overrun occurs.
