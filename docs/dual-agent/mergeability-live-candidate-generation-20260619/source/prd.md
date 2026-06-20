## Problem Statement

AutoResearch mergeability calibration currently evaluates authored candidate fixtures, which proves the oracle-isolated bench and reporting path but does not measure how real agents behave under controlled generation. The next experiment needs live candidate generation with identical model, provider, budget, timeout, prompt surface, and held-out oracle evaluation for both the baseline and supervised arms. The result must remain a calibration artifact until the powered evaluation criteria exist, because one live run or a small sample cannot justify policy evolution or supervisor improvement claims.

## Solution

Add an explicit live generation mode that refuses to run unless `allow_live=true` is supplied by the caller. The public mergeability bench interface will generate a baseline candidate with gates off and a supervisor candidate with the full supervisor flow enabled, using the same model, provider, budget, timeout, public task prompt, and hidden-material exclusion. Each produced candidate will be hashed, recorded with cost and timing metadata, evaluated by the same held-out bench oracle, and reported with report-only invariants that prevent applyable policy proposals.

## User Stories

1. As a supervisor operator, I want live generation disabled unless I explicitly enable it, so that benchmark code cannot spend model budget by accident.
2. As an evaluation owner, I want the baseline and supervisor arms to use the same model and budget, so that measured differences reflect gate scaffolding instead of unequal spend.
3. As a benchmark maintainer, I want hidden oracle material excluded from generator and reviewer inputs, so that reported scores cannot come from data leakage.
4. As a reviewer, I want stable candidate artifact hashes and evaluator hashes, so that every live result can be replayed and audited from recorded evidence.
5. As an AutoResearch maintainer, I want `candidate_affects_evaluated_path` derived from observed path and control deltas, so that quality controls cannot overstate meaningful behavior changes.
6. As a policy owner, I want live calibration reports marked non-applyable until powered criteria exist, so that no automatic proposal path treats early data as policy authority.

## PRD Promise Contracts

P1. Live candidate generation will refuse to invoke a generator unless the public caller sets `allow_live=true`, and the refusal will be recorded as an unavailable report rather than a successful result.
P2. Baseline and supervisor arms will record matching model, provider, budget, and timeout settings before execution, and mismatches will make the live report unavailable.
P3. Hidden oracle paths, final oracle labels, hidden commands, expected outcomes, and protected artifacts will be unavailable to generators, reviewer packets, transcripts, and public receipts.
P4. Each generated candidate artifact will have a stable SHA-256 hash based on canonical payload content, and the report will include prompt hash, evaluator hash, wall-clock, cost, and token usage when supplied.
P5. `candidate_affects_evaluated_path` will be derived from changed paths or evaluated-path deltas instead of a constant value, with false recorded when the candidate does not touch evaluated behavior.
P6. Budget or timeout overrun will reject or mark the live arm unavailable, never accepted, and the report will preserve the overrun reason for audit.
P7. Live generation outputs will keep `metric_applyable=false`, `improvement_claim_allowed=false`, `default_change_allowed=false`, `policy_mutated=false`, and `gate_advanced=false` until powered criteria are implemented separately.

## Implementation Decisions

Use the mergeability bench module as the highest practical seam because it already owns task loading, oracle isolation, public review inputs, full-gate review packets, paired reporting, and report-only invariants. Introduce a small generator adapter interface accepted by the live generation function, with tests using injected fake adapters at the external model boundary. Public worktrees and prompts must be constructed from the existing task public surface, while protected paths are removed before either arm receives input. The AutoResearch evaluator quality manifest will replace the hardcoded evaluated-path flag with a shared derivation helper that compares candidate changed files against mutable evaluated surfaces and optional control deltas.

## Testing Decisions

Tests will start at the public live mergeability report interface, not at adapter helpers, because the promise concerns observable run behavior and evidence. The first tests will prove default refusal without `allow_live`, then budget-matched metadata, oracle exclusion, stable candidate hashes, evaluated-path derivation, and report-only invariants. Fake generator adapters are acceptable because the live model provider is an external boundary; internal mergeability review, hidden oracle grading, and policy derivation should run through real repository code wherever practical.

## Out of Scope

This slice does not run a powered live benchmark, change supervisor gate authority, create policy proposals, approve overlays, tune reviewer prompts, or claim that the supervisor improves agent output. It also does not replace the held-out corpus design, introduce real maintainer judgments as the oracle, or alter the previous `S_probe` and `S_full` fixture calibration semantics except where live reports reuse their public evidence contracts.
