# Run Dual-Agent From A New Chat

Use this when you want a different Codex chat to start, continue, or inspect a
Codex plus Claude Code dual-agent run.

## What Carries Across Chats

Dual-agent state is not tied to one chat window. It is tied to:

- `run_id`
- `task_id`
- the supervisor SQLite ledger at `~/.codex-supervisor/state.db`
- artifacts under `docs/dual-agent/<task_id>/`
- the target worktree passed as `cwd`

Any chat that can load the `codex_supervisor` MCP server and access the same
worktree can continue the run.

## One-Time Setup

From `/Users/sam.zhang/Documents/codex-supervisor`, register the supervisor MCP
server with Codex:

```bash
codex mcp add codex_supervisor -- \
  uv --directory /Users/sam.zhang/Documents/codex-supervisor \
  run codex-supervisor-mcp --config ~/.codex-supervisor/config.yaml
```

Confirm it is visible:

```bash
codex mcp get codex_supervisor
```

The chat should have these tools available:

- `mcp__codex_supervisor__start_dual_agent_gate`
- `mcp__codex_supervisor__record_gate_round`
- `mcp__codex_supervisor__read_gate_transcript`
- `mcp__codex_supervisor__read_outcome`
- `mcp__codex_supervisor__export_gate_artifacts`
- `mcp__codex_supervisor__check_budget`
- `mcp__codex_supervisor__escalate_deadlock`
- `mcp__codex_supervisor__poll_resume_signal`

## Continue An Existing Run

Open the new chat in the target worktree and send:

```text
Use the codex_supervisor MCP server and the dual-agent-gate workflow.

Continue or inspect this dual-agent run:
- cwd: "<ABSOLUTE_TARGET_WORKTREE>"
- run_id: "<RUN_ID>"
- task_id: "<TASK_ID>"

First call read_gate_transcript, then read_outcome.
Then inspect docs/dual-agent/<TASK_ID>/interactions.md.
Summarize the current gate state, latest Claude Code outcome, missing
prerequisites, and the next safe action.
Do not invent state from chat memory.
```

The primary human-readable artifact is:

```text
docs/dual-agent/<TASK_ID>/interactions.md
```

Use `transcript.md` only when raw ledger detail is needed.

## Start A Fresh Run

Use a unique task id and run id:

```bash
export TASK_ID="dual-agent-$(date +%Y%m%d-%H%M%S)"
export RUN_ID="$TASK_ID"
```

Send this prompt in the new Codex chat:

```text
Use the codex_supervisor MCP server and the dual-agent-gate workflow.

Start a new strict dual-agent run:
- cwd: "<ABSOLUTE_TARGET_WORKTREE>"
- run_id: "<RUN_ID>"
- task_id: "<TASK_ID>"

Use the repo's prd-to-tdd workflow first. Create durable planning artifacts
under docs/dual-agent/<TASK_ID>/source/:
- prd.md
- issues.md
- tdd.md
- grill-findings.md
- implementation-plan.md

Then run gates in this order:
1. prd_review
2. issues_review
3. tdd_review
4. implementation_plan
5. execution
6. outcome_review

For every start_dual_agent_gate call:
- use artifact_policy="strict"
- pass the current planning_artifacts with mutable_by_worker=false
- use quality="best"
- use timeout_s=900 unless there is a clear reason to lower it

Before summarizing, call read_gate_transcript and point me to
docs/dual-agent/<TASK_ID>/interactions.md.
```

## Planning Artifact Payload

Pass planning artifacts to `start_dual_agent_gate` in this shape:

```json
[
  {"path": "<CWD>/docs/dual-agent/<TASK_ID>/source/prd.md", "kind": "prd", "mutable_by_worker": false},
  {"path": "<CWD>/docs/dual-agent/<TASK_ID>/source/issues.md", "kind": "issues", "mutable_by_worker": false},
  {"path": "<CWD>/docs/dual-agent/<TASK_ID>/source/tdd.md", "kind": "tdd_plan", "mutable_by_worker": false},
  {"path": "<CWD>/docs/dual-agent/<TASK_ID>/source/grill-findings.md", "kind": "grill_findings", "mutable_by_worker": false},
  {"path": "<CWD>/docs/dual-agent/<TASK_ID>/source/implementation-plan.md", "kind": "implementation_plan", "mutable_by_worker": false}
]
```

Strict mode enforces the ledger sequence:

- `issues_review` requires accepted `prd_review`
- `tdd_review` requires accepted `prd_review` and `issues_review`
- `implementation_plan` requires accepted `prd_review`, `issues_review`, and `tdd_review`
- `execution` requires accepted `implementation_plan`
- `outcome_review` requires accepted `execution`

If the sequence is incomplete, the gate blocks with
`gate_prerequisites_missing` before Claude Code launches.

## User-Facing Work

For UI or visual changes, capture screenshots before outcome review and call the
outcome-review gate with:

```text
user_facing=True
screenshots=[{
  "path": "<ABSOLUTE_SCREENSHOT_PATH>",
  "label": "Final state",
  "note": "Captured by Codex before outcome review.",
  "source": "computer_use",
  "validation": {
    "status": "passed",
    "notes": "Codex reviewed the visual state against the acceptance criteria."
  }
}]
```

Strict mode blocks user-facing outcome review without valid screenshot evidence,
Browser/Computer Use provenance, and a passed visual validation status.

## Reliable Transport Paths

Use AXI JSON as the default automation surface. AXI and MCP share the same
durable ledger core and idempotent `client_token` reattach, so either surface
can reach the same job.

```bash
codex-supervisor-axi --json submit \
  --cwd "<ABSOLUTE_TARGET_WORKTREE>" \
  --task-id "<TASK_ID>" \
  --run-id "<RUN_ID>" \
  --intent "<WHAT THE WORKFLOW SHOULD REVIEW>" \
  --cursor-review --cursor-review-profile rigorous \
  --client-token "<STABLE_TOKEN>"

codex-supervisor-axi --json poll <job_id>
codex-supervisor-axi --json catch-up "<RUN_ID>" --last-event-id <event_id>
```

MCP remains available as a compatibility shim over the same submit, poll, and
catch-up core. It is non-blocking and never executes gates synchronously:

```text
mcp__codex_supervisor__run_dual_agent_workflow
mcp__codex_supervisor__poll_dual_agent_workflow_job
mcp__codex_supervisor__catch_up_dual_agent_workflow
```

If the transport closes mid-call, do not treat that as a supervisor verdict.
Rerun the AXI submit with the same `--client-token` to reattach to the same
durable job, then resume polling and catching up through AXI JSON.

If neither AXI nor the MCP compatibility tools can run, the chat can only review exported
artifacts:

- `docs/dual-agent/<TASK_ID>/interactions.md`
- `docs/dual-agent/<TASK_ID>/outcome-review.md`
- `docs/dual-agent/<TASK_ID>/transcript.md`
- `docs/dual-agent/<TASK_ID>/source/prd.md`
- `docs/dual-agent/<TASK_ID>/source/tdd.md`
- `docs/dual-agent/<TASK_ID>/source/issues.md`

In that artifact-only mode, ask the chat to review artifacts only. Do not treat
it as a live dual-agent run.

## Common Failures

- Tool unavailable: re-run `codex mcp get codex_supervisor`; if missing,
  register the MCP server again.
- `Transport closed`: do not treat the dropped call as a verdict. Run
  `codex-supervisor-axi --json catch-up "<RUN_ID>"` or rerun
  `codex-supervisor-axi --json submit ... --client-token "<STABLE_TOKEN>"` to
  reattach to the durable job. If MCP server code or registration changed
  during the same Desktop session, restart Codex Desktop before trusting
  another MCP retry.
- `gate_prerequisites_missing`: inspect `interactions.md` and run the missing
  earlier gate instead of skipping forward.
- `required_artifacts_missing`: create or pass the missing planning artifacts.
- `handoff_lock_held`: confirm no live gate is running before removing a stale
  `.handoff/.dual-agent.lock`.
- No Telegram: use the Codex chat as the escalation surface. Do not call
  `poll_resume_signal` unless a Telegram callback actually recorded Continue or
  Retry.
