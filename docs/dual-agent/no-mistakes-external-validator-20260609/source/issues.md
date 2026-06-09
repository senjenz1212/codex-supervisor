# Issues: no-mistakes External Validator

## Slice ISS-1: External Adapter And Safe Config

Type: AFK
Priority: P0
Estimate: M
Scope: Add `supervisor/no_mistakes.py` with typed config, request, result, and
finding objects; add `NoMistakesCfg` to `supervisor/config.py`; document safe
defaults in `config.example.yaml`.
PRD promise: P1, P2, P4
First public-boundary RED test:
`test_no_mistakes_adapter_builds_safe_default_command`.

Acceptance Criteria:
- [ ] Adapter builds `no-mistakes axi run --intent ... --skip=push,pr,ci`.
- [ ] Adapter never passes `--yes` by default.
- [ ] Missing binary returns advisory unavailable or required blocked.
- [ ] Clean committed branch validation runs in an isolated worktree.
- [ ] File or HEAD changes produce `changed_requires_rerun`.

## Slice ISS-2: Workflow Integration And Ledger Evidence

Type: AFK
Priority: P0
Estimate: M
Scope: Add no-mistakes workflow parameters to direct and durable MCP paths,
invoke validation after accepted `outcome_review`, and write validation events
plus receipts to the supervisor ledger.
PRD promise: P2, P3, P4
First public-boundary RED test:
`test_run_dual_agent_workflow_records_advisory_no_mistakes_after_outcome_review`.

Acceptance Criteria:
- [ ] Direct workflow accepts `no_mistakes_policy`, `no_mistakes_skip_steps`,
      and `no_mistakes_timeout_s`.
- [ ] Detached workflow payload replay preserves the same no-mistakes knobs.
- [ ] Advisory mode writes started and completed/skipped/failed events after
      accepted `outcome_review`.
- [ ] Required mode blocks final completion when no-mistakes is unavailable or
      reports findings, without rewriting prior gate results.
- [ ] no-mistakes receipts remain evidence only and do not bypass supervisor
      probes or reviewer-panel decisions.
