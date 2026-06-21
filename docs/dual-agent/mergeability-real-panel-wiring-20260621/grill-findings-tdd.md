# TDD Grill Findings

### Finding 1

status: resolved

The first test could have targeted only a helper factory. The TDD plan now begins at `run_paired_acceptance_pilot`, which is the public calibration boundary that produces the report operators inspect.

### Finding 2

status: resolved

The panel path could accidentally call live reviewers during unit tests. The TDD plan requires fake reviewer adapters below the registry seam and keeps live Cursor or Codex execution out of default tests.

### Finding 3

status: resolved

Unavailable reviewer behavior needed its own RED case. The TDD plan now requires missing verdicts to keep S_full unavailable or rejected and to avoid counting as an acceptance.

### Finding 4

status: resolved

Oracle isolation needed to prove the reviewer was not invoked after a leak. The TDD plan now asserts forbidden packet material blocks invocation and records an isolation violation.
