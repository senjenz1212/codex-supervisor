# TDD Grill Findings: Reviewer Panel Foundation

## Findings

### Finding T1: First proof must use workflow/read/export boundaries

   - Risk: Helper-only tests could prove a mapper while the supervisor still
     emits legacy-only events.
   - Resolution: TDD includes `run_dual_agent_workflow`, `read_gate_transcript`,
     and artifact export tests before helper coverage.
Status: resolved

### Finding T2: Decision equivalence needs an explicit regression

   - Risk: Panel list introduction changes gate behavior indirectly.
   - Resolution: TDD includes a single-reviewer decision regression for accept
     and revise/deny paths.
Status: resolved

### Finding T3: Real reviewer coverage must use deterministic network fakes

   - Risk: Live Gemini calls would make the suite flaky.
   - Resolution: The real structured adapter is exercised with a fake OpenAI
     client, matching existing reviewer tests.
Status: resolved

### Finding T4: Legacy event compatibility must be tested on old shape

   - Risk: New event support could pass while old exported traces lose
     reviewer evidence.
   - Resolution: TDD requires read/export tests for legacy
     `tri_agent_cursor_review` fixtures.
Status: resolved

## Decision

Proceed to implementation. Findings are resolved in the TDD plan.
