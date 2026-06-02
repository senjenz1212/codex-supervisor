# PRD Grill Findings: Reviewer Panel Eval Runner

Stage: `prd_grill`

### Finding 1: Existing `agentic_eval` Is Not This Runner

Status: resolved

Question: Does the PRD accidentally reuse `supervisor.agentic_eval.build_agentic_eval_report(rows)`
as if it executed reviewers?

Resolution: The PRD explicitly keeps `agentic_eval_report` as the existing
lead-mode report boundary and defines a new `reviewer_panel_eval_runner`
boundary for labeled reviewer-panel replay. Promise P6 requires tests
to keep both schemas and responsibilities distinct.

### Finding 2: Public Boundary Is New And Must Be Registered During Implementation

Status: resolved

Question: The repo's current `docs/testing/public-boundaries.md` includes
`agentic_eval_report`, but no reviewer-panel eval runner boundary. How can the
first RED test name an approved public boundary?

Resolution: This planning run is restricted to the task artifact directory, so
it cannot update the repo-wide registry now. The PRD, issues, and TDD plan name
`reviewer_panel_eval_runner` as the public boundary and make Slice 1 responsible
for registering that boundary before implementation RED tests land.

### Finding 3: False Accept And False Block Need Stable Label Semantics

Status: resolved

Question: Without a label schema, false accept and false block can be
interpreted differently by future issue authors.

Resolution: The PRD defines labeled tasks as `accept_allowed` or
`block_required`. A false accept is reviewer `accept` on `block_required`. A
false block is explicit non-accept or missing verdict on `accept_allowed`, with
cause subcounts separating explicit objections from missing/unavailable
verdicts.

### Finding 4: Correlation Must Not Hide Small-Sample Or Zero-Variance Cases

Status: resolved

Question: Can pairwise correlation create false confidence when both reviewers
always accept, always block, or have too few comparable rows?

Resolution: The PRD requires raw contingency counts next to every pairwise
metric and permits numeric phi correlation only when both binary vectors have
variance. Otherwise the report must emit `correlation_status=not_applicable`
and a concrete reason.

### Finding 5: Deterministic Replay Must Be The Default

Status: resolved

Question: Could the eval runner call live reviewers by default and make CI
costly, flaky, or dependent on credentials?

Resolution: The PRD requires default fixture/cassette replay. Live capture or
cassette recording is explicit opt-in and out of CI defaults. Fixture tests must
prove no live Cursor, Codex, Claude, Telegram, or model APIs are called by
default.

### Finding 6: Reports-Only Means No Policy Side Effects

Status: resolved

Question: Could a "data substrate" slice sneak in calibrated weights or mutate
gate aggregation because the report computes dependence metrics?

Resolution: The PRD makes reports-only behavior a promise contract. The runner
must emit `policy_change_allowed=false`, must not change config/defaults, and
must not change conservative panel aggregation, low-confidence thresholds, or
reviewer roster policy.

### Finding 7: Pairwise Metrics Must Use Per-Reviewer Rows

Status: resolved

Question: If pairwise agreement is computed from the final panel decision, it
will hide individual reviewer dependency.

Resolution: The PRD requires raw per-reviewer rows and computes pairwise
agreement, overlap, and correlation from each reviewer's task-level verdict and
label outcome, not from the aggregate panel decision.
