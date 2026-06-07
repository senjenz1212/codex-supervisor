# TDD Grill Findings: Supervisor AutoResearch Foundation

## Reviewed Surfaces

- PRD promise contracts P1 through P5.
- TDD public boundaries for orchestrator, validation, report builder, runner,
  and config compatibility.
- Existing planning validator expectations for non-thin artifacts and concrete
  test names.

### Finding 1: First tests must hit public AutoResearch boundaries

Status: resolved

The TDD plan starts with `run_autoresearch_fixture`, `validate_attempt`, and
`build_autoresearch_report` rather than private helpers. Helper-level tests may
support the implementation only after the public-boundary tests exist.

### Finding 2: Fixture-only guard must be tested as behavior, not prose

Status: resolved

The TDD plan includes `test_autoresearch_fixture_runner_blocks_live_calls_by_default`
against the script boundary. The expected result is a clear failure for
non-fixture execution without `--allow-live`.

### Finding 3: Gate authority preservation needs an explicit regression

Status: resolved

The TDD plan includes `test_autoresearch_validator_cannot_advance_gates` and
`test_autoresearch_cursor_reviewer_defaults_remain_compatible` so report-only
and reviewer compatibility are checked in code.
