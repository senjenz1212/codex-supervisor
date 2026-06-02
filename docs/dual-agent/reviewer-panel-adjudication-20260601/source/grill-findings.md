# PRD Grill Findings: Reviewer Panel Adjudication

### Finding 1: Adjudication must not become majority vote

Status: resolved

Risk: The intent mentions disagreement and minority-correct objections, which
could be misread as vote counting across reviewer decisions.

Resolution: The PRD forbids majority vote and defines adjudication as an
evidence packet over the strongest objection. Conservative hard-block semantics
remain authoritative for real important or critical revise/deny verdicts.

### Finding 2: "Tool-backed" needs a bounded repo-local meaning

Status: resolved

Risk: A broad tool-backed adjudicator could accidentally launch live model calls
or read arbitrary files during replay.

Resolution: The PRD defines bounded evidence inspection over local, relative
refs under the workflow cwd, with capped refs and hash validation where
possible. Tests must inject reviewers and avoid external model services by
default.

### Finding 3: Strong objections inside accept verdicts need explicit semantics

Status: resolved

Risk: Existing conservative aggregation advances when all reviewer decisions are
accept. A reviewer may still encode a serious objection in `critical_review`.

Resolution: P2 requires important or critical strongest objections inside
accepting reviewer results to trigger adjudication and escalation rather than
auto-advance.

### Finding 4: Replay readers must see the same boundary

Status: resolved

Risk: If adjudication is only attached to the final result, event replay and
artifact export cannot explain a gate decision.

Resolution: P4 requires adjudication metadata on `independent_reviewer_review`,
legacy `tri_agent_cursor_review`, and exported artifacts, plus a dedicated
ledger event.
