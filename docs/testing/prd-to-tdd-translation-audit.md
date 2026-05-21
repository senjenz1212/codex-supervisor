# PRD To TDD Translation Audit

## Checklist

- [x] Every PRD promise has at least one issue claimant.
- [x] `docs/grill-findings.md` was created and findings are resolved.
- [x] `docs/grill-findings-tdd.md` was created and findings are resolved.
- [x] Every issue has a `PRD promise` block.
- [x] Every issue names a first public-boundary RED test.
- [x] Forbidden outcomes are copied or narrowed into issue TDD plans.
- [x] Integration promises are represented by integration-scenario issues.
- [x] Live external dependencies are mocked below the public boundary.
- [x] Replay is required before enforcement.

## Local Issue Tracker

Issues are published as local markdown under `.scratch/agent-supervisor/`.

## Residual Risks

- The repo has no `AGENTS.md` or `CLAUDE.md` yet, so engineering-skill tracker
  setup is represented by local markdown conventions instead of a repo agent
  block.
- Exact Claude Code hook payload fixtures should be captured from the user's
  machine before implementation begins.
