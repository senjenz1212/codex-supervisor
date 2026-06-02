# TDD Grill Findings: Reviewer Panel Eval Runner

Stage: `tdd_grill`

### Finding 1: First RED Tests Must Hit The Runner Boundary

Status: resolved

Question: Does the TDD plan prove behavior through the public eval runner, or
does it start with helper-only metric reducers?

Resolution: Every planned metric test invokes `reviewer_panel_eval_runner`.
Helper tests for schema or math are allowed only after the boundary tests
exist.

### Finding 2: Fakes Must Sit Below Reviewer Execution, Not Below The Runner

Status: resolved

Question: Could tests fake the final rows and bypass the part that proves the
runner iterates the panel?

Resolution: The plan allows fake/cassette reviewer adapters below the runner
boundary, but the runner must still load labels, iterate reviewers, normalize
rows, compute metrics, export artifacts, and write ledger refs.

### Finding 3: Pairwise Correlation Needs Edge-Case Fixtures

Status: resolved

Question: Does the test plan cover zero variance, no comparable tasks, and
small denominators?

Resolution: The pairwise test requires raw contingency counts and
`not_applicable` correlation status for zero variance. Additional helper tests
can cover no comparable tasks after the boundary test exists.

### Finding 4: Missing Verdicts Need Separate Assertions From Explicit Blocks

Status: resolved

Question: Can false blocks hide whether the cause was a real reviewer objection
or missing infrastructure?

Resolution: The per-reviewer metrics test asserts false-block cause breakdowns,
including unavailable/missing verdicts separately from explicit revise/deny.

### Finding 5: Export Tests Must Prove Replayability, Not Just Markdown

Status: resolved

Question: Could an implementation pass by writing a readable markdown summary
without replayable machine-readable evidence?

Resolution: The export test requires report JSON, raw rows, replay manifest,
labeled-set hash, cassette ids, reviewer roster, report hash, and ledger event
ids.

### Finding 6: No-Policy Proof Must Be Tested

Status: resolved

Question: How does the plan prove the runner cannot introduce weighting or gate
changes?

Resolution: The export and distinction tests assert `policy_change_allowed=false`,
no active weights, no config/default mutation, and continued green behavior for
the existing `agentic_eval_report` boundary.

### Finding 7: Full Suite Is Necessary Because Reviewer Panel Events Are Shared

Status: resolved

Question: Is a focused eval-runner suite enough when reviewer panel events,
artifact export, and lead-mode eval share project-level reporting concepts?

Resolution: The regression commands include focused eval-runner tests,
selected existing reviewer-panel workflow tests, existing lead-mode eval tests,
and the full repository suite.
