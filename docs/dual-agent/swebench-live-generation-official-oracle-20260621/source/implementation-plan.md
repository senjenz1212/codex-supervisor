## Files / Modules to touch

- supervisor/swe_bench_mergeability.py: add the official live runner, public generator input builder, prediction artifact writer, and unavailable report helper.
- supervisor/swe_bench_mergeability_cli.py: leave current CLI stable unless the public runner requires a minimal flag surface.
- tests/test_swe_bench_pro_mergeability_bridge.py: add public-boundary official live tests next to existing local live and official replay tests.

## Risks

- Hidden oracle material could leak through official records into generator prompts. Tests must inspect serialized generator inputs directly.
- Live generation could accidentally compare different budgets or prompts across arms. The report must store matched configuration fields per arm.
- A partial over-budget run could still produce an official replay artifact. The overrun path must return before replay delegation.

## Traceability

- P1 -> test_official_live_refuses_without_allow_live_before_generators_run and test_official_live_refuses_without_budget_before_generators_run.
- P2 -> test_official_live_generator_inputs_exclude_hidden_oracle and test_official_live_generates_matched_arms_and_reuses_official_replay.
- P3 -> test_official_live_generates_matched_arms_and_reuses_official_replay.
- P4 -> test_official_live_budget_overrun_is_unavailable_not_accepted.
- P5 -> test_official_live_generates_matched_arms_and_reuses_official_replay and test_official_live_budget_overrun_is_unavailable_not_accepted.
