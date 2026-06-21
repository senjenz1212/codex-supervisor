# TDD Plan

## Public Boundary Test 1: Live Refuses Without Allow Flag

RED: Call `swebench_mergeability_live_runner` with fake generators and `allow_live=false`; assert a refusal before generator calls.

GREEN: Add the live runner guard and unavailable/refusal path before adapter invocation.

## Public Boundary Test 2: Live Refuses Without Budget

RED: Call the live runner with `allow_live=true` and `max_budget_usd=0`; assert a refusal before generator calls.

GREEN: Add the positive-budget guard and CLI error handling.

## Public Boundary Test 3: Matched Live Generation And Replay Delegation

RED: Use fake baseline and supervisor generators returning patch text plus cost, wall-clock, and token usage; assert matched config receipts, stable prompt hashes, generated replay manifest path, replay report, frozen decisions, and hidden-oracle exclusion.

GREEN: Build public generator inputs, record generation metadata, write generated patch artifacts, and delegate to the replay runner.

## Public Boundary Test 4: Budget Overrun Unavailable

RED: Return a generator result with cost above `max_budget_usd`; assert the run is unavailable, the overrun arm is not accepted, no policy claim is allowed, and the oracle path is not used for that failed live run.

GREEN: Add budget-overrun detection before replay evaluation.

## Public Boundary Test 5: CLI Live Mode

RED: Invoke the CLI live mode without required guards and with a tiny fake command generator; assert refusal cases return non-zero and the approved case writes a report.

GREEN: Add explicit live CLI flags and command-backed generator adapters below the public boundary.
