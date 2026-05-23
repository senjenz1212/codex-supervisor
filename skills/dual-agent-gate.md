# Skill: dual-agent-gate

Use this when Codex is coordinating a dual-agent implementation gate with
Claude Code as the implementer.

## Tools

- `mcp__codex_supervisor__start_dual_agent_gate(task_id, run_id, gate, instruction, cwd, expected_specialists, expected_decisions, expected_objections, quality, model, budget_usd, timeout_s)`
- `mcp__codex_supervisor__record_gate_round(run_id, task_id, gate, round_index, codex_decision, claude_decision, codex_confidence, claude_confidence, objection)`
- `mcp__codex_supervisor__check_budget(rounds, per_gate_cap, task_budget)`
- `mcp__codex_supervisor__escalate_deadlock(run_id, task_id, gate, rounds, per_gate_cap, task_budget)`
- `mcp__codex_supervisor__poll_resume_signal(task_id, run_id, gate, instruction, cwd, expected_specialists, expected_decisions, expected_objections, quality, model, budget_usd, timeout_s)`
- `mcp__codex_supervisor__read_outcome(run_id, task_id)`
- `mcp__codex_supervisor__start_codex_session(prompt, cwd, model, execute, timeout_s)`

## Gate Policy

Codex owns PRD, TDD, and review gates. Claude Code owns implementation through
`/lead`. Both agents participate at major decision points.

For each major decision gate:

1. State the gate, the decision to make, and the acceptance criteria.
2. Ask Claude Code for critique or implementation feedback through
   `start_dual_agent_gate`.
3. Record each Codex/Claude disagreement with `record_gate_round`.
4. After each round, call `check_budget`.
5. If budget is exhausted and the agents still disagree, call
   `escalate_deadlock`. Do not choose a winner silently.
6. If Telegram later records `Continue` or `Retry`, call `poll_resume_signal`.
7. Read the final gate result with `read_outcome` before advancing.

## Defaults

- Use `quality="best"` for PRD, TDD, and outcome-review gates.
- Use `quality="balanced"` for execution gates unless the task is high-risk.
- Keep `timeout_s` high enough for `/lead`; default to 600 seconds.
- Use the same approved worktree for one task. Use separate worktrees for
  parallel tasks.

## Stop Conditions

Pause and escalate instead of continuing when:

- `check_budget` returns `paused_for_human`.
- The gate result is `blocked`.
- The result has a validation escalation.
- The worker changed immutable planning artifacts.
- Test coverage is absent for code changes and no explicit exception was
  approved.
