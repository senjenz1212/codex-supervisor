# TDD Grill Findings

### Finding 1: First Tests Hit Public Boundaries

- Check: loader tests call `load_agentic_eval_labeled_set`; miner tests call the
  script/core boundary.
- Resolution: helper-level digest tests are not the first proof. Public-boundary
  failures are listed first.
Status: resolved

### Finding 2: Corpus Schema Must Not Mirror Runner Arms

- Check: TDD includes a per-arm budget rejection test.
- Resolution: the loader enforces task-level `budget.total_tokens` and
  `budget.total_usd` only.
Status: resolved

### Finding 3: Evidence Refs Must Be Concrete

- Check: TDD includes missing evidence rejection and seed ref resolution.
- Resolution: all seed required verdicts and cassette transcript refs point to
  files in the repo.
Status: resolved

### Finding 4: Miner Review Pause Is Testable

- Check: TDD includes a test that the miner refuses the curated corpus path.
- Resolution: the script writes candidates to a staging file only.
Status: resolved

### Finding 5: Report-Only Guard Is Explicit

- Check: TDD includes no-runner and no-policy mutation assertions.
- Resolution: implementation scope excludes config, state, and production gate
  defaults.
Status: resolved
