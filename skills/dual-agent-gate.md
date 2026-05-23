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
- `mcp__codex_supervisor__read_gate_transcript(run_id, task_id)`
- `mcp__codex_supervisor__start_codex_session(prompt, cwd, model, reasoning_effort, execute, timeout_s)`

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
   confidences, objections, and final outcome.
10. Read the final gate result with `read_outcome` when you only need the
    latest outcome without the dialogue history.

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
