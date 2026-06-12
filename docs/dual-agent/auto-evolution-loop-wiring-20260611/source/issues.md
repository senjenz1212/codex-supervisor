# Issues: Auto-Evolution Loop Wiring

## Slice W1: Draft Experiments At Workflow Finalization

Priority: P0
Estimate: M
Scope: Wire finalization-time experiment drafting and lesson feedback without changing gate authority.

### PRD Promise

- Covers: P1 and P6.
- Public boundary: `CodexSupervisorMcpAPI._workflow_result`.
- Representative action: Finalize a run with seeded recurring failure signals and lesson injections.
- Allowed outcome: Draft experiment rows and lesson feedback events are emitted.
- Forbidden outcome: Finalization activates experiments, advances gates, or ignores recurrence thresholds.

### Implementation Notes

- Call `generate_autoresearch_experiment_drafts` in the same finalization hook family as quality trend recording.
- Return a small `autoresearch_drafts` summary in the workflow result.
- Call `record_supervisor_lesson_injection_feedback` for lesson ids injected into the run.

### Tests

Acceptance criteria:

- [ ] Seed three matching taxonomy failures and assert exactly one draft at finalization.
- [ ] Seed below threshold and assert no draft.
- [ ] Seed injected lesson recurrence and assert counters plus retirement exclusion.

## Slice W2: Daemon Cadences For AutoResearch And P11 Audit

Priority: P0
Estimate: M
Scope: Add daemon-owned cadence tasks for activated experiments and weekly P11 audits.

### PRD Promise

- Covers: P2 and P4.
- Public boundary: `AutoResearchRunnerTask.tick_once`, `WeeklyP11AuditTask.tick_once`, and daemon task registration.
- Representative action: Run one daemon tick with draft/runnable experiments and due P11 candidates.
- Allowed outcome: Only runnable experiments execute, weekly caps apply, and audit rows are observational.
- Forbidden outcome: Request-path execution or draft self-activation.

### Implementation Notes

- Add daemon task classes in `supervisor/autoresearch/daemon_tasks.py`.
- Register restartable `autoresearch_runner` and `weekly_p11_audit` tasks in `daemon.py`.
- Add SQLite and Postgres candidate-run queries for accepted execution/outcome gates.

### Tests

Acceptance criteria:

- [ ] Daemon tick skips drafts and executes activated experiments.
- [ ] Daemon tick respects weekly cap.
- [ ] Weekly P11 audit tick writes a scheduled audit result.

## Slice W3: Derive Policy Proposals On Accepted Report Emission

Priority: P0
Estimate: S
Scope: Derive draft proposals from accepted derivable AutoResearch reports at report emission.

### PRD Promise

- Covers: P3.
- Public boundary: `run_autoresearch_fixture`.
- Representative action: Emit a report containing an accepted evaluator-backed policy overlay candidate.
- Allowed outcome: One draft proposal event is derived from report evidence.
- Forbidden outcome: Gaming-flagged, zero/negative delta, or non-derivable records produce applyable proposals.

### Implementation Notes

- Invoke `derive_policy_evolution_proposals_from_report` after report emission only when a derivable record exists.
- Preserve the manual proposal tool.
- Add a report summary of derived proposal ids without mutating policy.

### Tests

Acceptance criteria:

- [ ] Accepted applyable report auto-drafts one proposal.
- [ ] Existing negative/gaming derivation tests remain green.
- [ ] Existing AutoResearch event sequence does not get skipped-derivation noise for non-derivable reports.

## Slice W4: AXI Operator Verbs

Priority: P0
Estimate: M
Scope: Add AXI experiment and proposal decision verbs while preserving generic operator decisions.

### PRD Promise

- Covers: P5 and P7.
- Public boundary: `mcp_tools.codex_supervisor_axi.main`.
- Representative action: Use `experiments activate`, `approve --proposal-id`, and `deny --proposal-id`.
- Allowed outcome: Operator actions write ledger events and preserve generic approve/deny behavior without proposal ids.
- Forbidden outcome: CLI request paths spawn workers, drive workflow phases, or apply unrecorded diffs.

### Implementation Notes

- Add experiment list/generate/activate subcommands.
- Add proposal-aware approve/deny branch that loads the proposal from run events.
- Keep AXI TOON/JSON output conventions with help hints.

### Tests

Acceptance criteria:

- [ ] Activation transitions draft to runnable.
- [ ] Approval applies exactly recorded overlay diff and writes hashes plus rollback pointer.
- [ ] Denial records denial and applies nothing.
- [ ] Request-path guard monkeypatches dispatcher/spawn seams and invokes new CLI/MCP verbs.
