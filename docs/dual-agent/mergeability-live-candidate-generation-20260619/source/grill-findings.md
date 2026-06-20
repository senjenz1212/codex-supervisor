# PRD Grill Findings

### Finding 1 - Accidental spend must fail closed

Status: resolved

Question: What prevents a caller from turning an ordinary calibration command into a live model-spending run?

Resolution: The PRD now makes `allow_live=true` a promise contract and requires the public live report interface to return an unavailable report before invoking any generator when the flag is absent. The first TDD slice starts at that boundary.

### Finding 2 - Budget matching must be measured before execution

Status: resolved

Question: How do we know the baseline and supervisor arms differ by gating rather than unequal model, timeout, or spend?

Resolution: The PRD requires arm metadata equality for model, provider, budget, and timeout. A mismatch is treated as an unavailable live report, and tests assert the equality contract before accepting generated artifacts.

### Finding 3 - Oracle isolation must cover generator inputs, not only reviewer packets

Status: resolved

Question: The previous slices protected reviewer packets; does live candidate generation add a new leak channel?

Resolution: The PRD explicitly extends hidden-material exclusion to generator prompts, public worktrees, transcripts, and receipts. The TDD plan includes a public-boundary test that scans generator input and reviewer material for forbidden oracle markers.

### Finding 4 - Evaluated-path meaning cannot remain a constant

Status: resolved

Question: Why would AutoResearch quality controls remain trustworthy if `candidate_affects_evaluated_path` is always true?

Resolution: The PRD includes a promise to derive the flag from changed paths or evaluated-path deltas. The TDD plan covers both false and true cases through the evaluator quality surface rather than a helper-only assertion.

### Finding 5 - Live calibration must not become policy authority

Status: resolved

Question: What prevents an early live result from being treated as an applyable improvement?

Resolution: The PRD preserves all report-only invariants and makes proposal creation out of scope until powered criteria exist. The TDD plan includes a policy derivation guard that must return no proposals for the live report.
