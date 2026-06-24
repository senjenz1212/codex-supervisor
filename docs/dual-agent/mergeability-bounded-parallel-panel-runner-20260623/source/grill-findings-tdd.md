# TDD Grill Findings

## Findings

### Finding T1

Status: resolved
Severity: high
Question: Could the first proof bypass the public runner by testing private scheduler helpers?
Resolution: The TDD plan starts at the public fixture measurement seam and allows fake reviewers only below the configured reviewer adapter seam.

### Finding T2

Status: resolved
Severity: high
Question: Could parallel speed be tested while deterministic evidence and resume safety remain unproven?
Resolution: The tests require deterministic ordering, write-before-aggregate checkpoints, matching checkpoint reuse, and stale checkpoint recomputation.

### Finding T3

Status: resolved
Severity: high
Question: Could timeout or partial reviewer roster accidentally become acceptance?
Resolution: The TDD plan includes timeout, exception, missing verdict, and partial-roster cases that must make S_full unavailable and never accepted.

### Finding T4

Status: resolved
Severity: high
Question: Could checkpoint or HTML output leak hidden oracle material?
Resolution: Leak detection must run against checkpoint payloads and dashboard output, and the dashboard must be generated from public report fields only.

### Finding T5

Status: resolved
Severity: medium
Question: Could an interrupted real configured-reviewer run be mistaken for a trustworthy aggregate?
Resolution: Runtime acceptance allows a checkpointed partial artifact only when it records completed, unavailable, and interrupted counts, checkpoint references, the reason the aggregate is not trusted, and a safe resume command.

### Finding T6

Status: resolved
Severity: high
Question: Could implementation-scope knobs ship without public-boundary RED coverage?
Resolution: The TDD plan now has explicit public runner tests for `max_wall_clock_s`, candidate selector/`max_candidates`, discordance reporting when panel marginal is unavailable, and report-only invariant flags across completed, partial, and diagnostic reports.

## Decision

No open findings remain.
