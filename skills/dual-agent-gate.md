# Skill: dual-agent-gate

Use this when Codex is coordinating a dual-agent implementation gate with
Claude Code as the implementer.

## Tools

- `mcp__codex_supervisor__start_dual_agent_gate(task_id, run_id, gate, instruction, cwd, expected_specialists, expected_decisions, expected_objections, quality, model, budget_usd, timeout_s, planning_artifacts, artifact_policy, user_facing, screenshots)`
- `mcp__codex_supervisor__record_gate_round(run_id, task_id, gate, round_index, codex_decision, claude_decision, codex_confidence, claude_confidence, objection, cwd)`
- `mcp__codex_supervisor__check_budget(rounds, per_gate_cap, task_budget)`
- `mcp__codex_supervisor__escalate_deadlock(run_id, task_id, gate, rounds, per_gate_cap, task_budget)`
- `mcp__codex_supervisor__poll_resume_signal(task_id, run_id, gate, instruction, cwd, expected_specialists, expected_decisions, expected_objections, quality, model, budget_usd, timeout_s, planning_artifacts, artifact_policy, user_facing, screenshots)`
- `mcp__codex_supervisor__read_outcome(run_id, task_id)`
- `mcp__codex_supervisor__read_gate_transcript(run_id, task_id)`
- `mcp__codex_supervisor__export_gate_artifacts(run_id, task_id, cwd, output_dir, screenshots)`
- `mcp__codex_supervisor__start_codex_session(prompt, cwd, model, reasoning_effort, execute, timeout_s)`
- `mcp__codex_supervisor__run_dual_agent_workflow(cwd, task_id, run_id, intent, user_facing, max_rounds_per_gate, quality, timeout_s, planning_artifacts, screenshots, verified_claims, cursor_review, cursor_model)`
- `mcp__codex_supervisor__read_dual_agent_workflow_resume_prompt(run_id, task_id)`

## Gate Policy

Codex owns PRD, TDD, and review gates. Claude Code owns implementation through
`/lead`. Both agents participate at major decision points.

Prefer `run_dual_agent_workflow` when the user wants the whole implementation
process. It makes the supervisor own the lifecycle order, max-round cap,
artifact generation, final artifact export, and final claim checks. Use the
lower-level gate tools only when manually repairing, inspecting, or testing a
single gate.

Set `cursor_review=true` only when the user explicitly wants tri-agent review.
In that mode Cursor is an independent reviewer/challenger. Claude Code remains
the implementer through `/lead`; Cursor must return the same typed
`dual_agent_outcome` contract and should not edit files. If Cursor does not
accept, the supervisor treats the gate as not converged and applies the same
max-round and escalation rules.

Before implementation, Codex must run the repo's `prd-to-tdd` workflow, not an
ad hoc PRD/TDD prompt. That workflow creates or updates durable artifacts:

- `docs/dual-agent/<task_id>/prd.md`
- `docs/dual-agent/<task_id>/grill-findings.md`
- `docs/dual-agent/<task_id>/issues.md`
- `docs/dual-agent/<task_id>/tdd.md`
- `docs/dual-agent/<task_id>/grill-findings-tdd.md`

The `prd-to-tdd` workflow includes the two `grill-with-docs` gates. Do not
advance from PRD to TDD, or from TDD to implementation, until the corresponding
grill findings are resolved or explicitly waived in the artifact.

The supervisor ledger enforces the gate sequence in strict mode. Before
`implementation_plan`, the ledger must already contain accepted `prd_review`,
`issues_review`, and `tdd_review` results for the same `run_id` and `task_id`.
Before `execution`, it must contain accepted `implementation_plan`. Before
`outcome_review`, it must contain accepted `execution`. If this chain is
missing, `start_dual_agent_gate` returns `gate_prerequisites_missing` and does
not launch Claude Code.

When calling `start_dual_agent_gate`, pass the current PRD/TDD/grill/issue
documents through `planning_artifacts` with `mutable_by_worker=false`, and keep
`artifact_policy="strict"` unless the user explicitly approves relaxing the
gate. Strict implementation, execution, and outcome-review gates require PRD,
TDD, grill findings, issues, and implementation-plan artifacts as applicable;
otherwise the tool returns `required_artifacts_missing` and does not launch
Claude Code. The runner also enforces deterministic planning-substance checks
below MCP preflight: stub, TBD, generated, unresolved-grill, or traceability-broken
artifacts block before `/lead` is invoked, even when MCP artifact preflight is
relaxed. This pins checksums in the handoff packet so Claude Code cannot
silently rewrite the spec to match the implementation, and it records
`dual_agent_planning_validation` receipts for later transcript review.
Claude Code is launched in the same worktree with built-in tools enabled from
the first gate (`--tools default`, `--permission-mode bypassPermissions`) so it
can inspect files, run tests, and review diffs directly instead of relying only
on Codex-supplied summaries.

For each major decision gate:

1. State the gate, the decision to make, and the acceptance criteria.
2. Ask Claude Code for critique or implementation feedback through
   `start_dual_agent_gate`, passing `artifact_policy="strict"` and the current
   `planning_artifacts`.
3. Record each Codex/Claude disagreement with `record_gate_round`; pass `cwd`
   so the readable artifacts are refreshed after the round is recorded.
4. After each round, call `check_budget`.
5. If budget is exhausted and the agents still disagree, call
   `escalate_deadlock`. Do not choose a winner silently.
6. If `escalate_deadlock` returns `telegram_disabled`, or if
   `start_dual_agent_gate` returns `blocked` without a validation escalation,
   use Codex Desktop chat as the escalation surface: ask the user for corrective
   input or permission to stop. Do not call `poll_resume_signal` in this
   no-Telegram path; nothing will write a resume signal.
7. After the user answers in chat, re-invoke `start_dual_agent_gate` with the
   corrective instruction and record the new round with `record_gate_round`.
8. Call `poll_resume_signal` only when Telegram is configured and a
   `Continue` or `Retry` callback was actually recorded.
9. Before advancing or summarizing for the user, call `read_gate_transcript`
   and use it to show the clean Codex/Claude dialogue: rounds, decisions,
   confidences, objections, and final outcome. Also point the operator to
   `interactions.md`, the readable Codex/Claude dialogue projection generated
   in the artifact folder.
10. Read the final gate result with `read_outcome` when you only need the
    latest outcome without the dialogue history.
11. After each accepted PRD, TDD, implementation, or outcome-review milestone,
    confirm `artifact_export.status == "ok"` in the tool result. The gate tool
    auto-exports readable Markdown artifacts into
    `docs/dual-agent/<task_id>/`; call `export_gate_artifacts` manually only to
    refresh or add external evidence.
12. For user-facing UI, Vela, Slack, Calendar, live-provider, or otherwise
    visual changes, Codex must capture screenshots through Browser or Computer Use,
    review the visible state against the acceptance criteria, call the
    outcome-review gate with `user_facing=True`, and pass visual evidence as
    `screenshots=[{"path": "...", "label": "...", "note": "...", "source": "computer_use", "validation": {"status": "passed", "notes": "..."}}]`.
    `source` must be `computer_use` or `browser`, and `validation.status` must
    be passed/accepted/ok. Strict user-facing gates block with
    `visual_validation` if this provenance and review evidence is missing. Include
    the generated `screenshots.md` plus code diff and test output in the final
    outcome-review gate. Do not accept a user-facing change on code/tests alone
    when the visual state is inspectable.
    `run_dual_agent_workflow` also auto-upgrades intents/artifacts mentioning
    Vela, Slack, Calendar, screenshots, visual review, user-visible surfaces, or
    live providers into visual-evidence-required mode, so omitting
    `user_facing=True` no longer bypasses the screenshot gate.

## Defaults

- Use `quality="best"` for PRD, TDD, and outcome-review gates.
- Use `quality="best"` for execution gates too unless the user explicitly asks
  to save cost. Cost is not the default constraint for this workflow.
- When spawning Codex sessions through the supervisor, use
  `model="gpt-5.5"` and `reasoning_effort="xhigh"` unless the user
  explicitly chooses a cheaper tier.
- Keep `timeout_s` high enough for `/lead`; default to 600 seconds.
- Use the same approved worktree for one task. Use separate worktrees for
  parallel tasks.
- In Codex Desktop initiated runs, assume Telegram may be absent. The Desktop
  chat is then the human escalation channel.
- Treat `docs/dual-agent/<task_id>/` as the durable artifact folder for the
  run. The supervisor SQLite ledger remains the source of truth; these files are
  readable projections and planning artifacts.
- Use `docs/dual-agent/<task_id>/interactions.md` as the primary human-readable
  interaction record. Use `transcript.md` when raw ledger detail is needed.
- Store visual evidence under `docs/dual-agent/<task_id>/screenshots/` through
  `export_gate_artifacts`; review `screenshots.md` together with code, tests,
  and gate transcript before final acceptance. A screenshot file alone is not
  enough for `user_facing=True`; the gate requires Browser/Computer Use source
  metadata and an explicit passed visual validation.

## Stop Conditions

Pause and escalate instead of continuing when:

- `check_budget` returns `paused_for_human`.
- The gate result is `blocked`.
- The result has a validation escalation.
- The worker changed immutable planning artifacts.
- Test coverage is absent for code changes and no explicit exception was
  approved.

When Telegram is absent, "pause and escalate" means ask the user in Codex
Desktop chat, then either stop or re-run the gate with the user's corrective
instruction. It does not mean polling `poll_resume_signal`.
