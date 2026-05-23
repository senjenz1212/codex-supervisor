# Codex Desktop Dual-Agent Probes

Scope: Codex Desktop is the initiator. Telegram is intentionally out of scope.
If a gate blocks, the Desktop chat is the human escalation surface.

## Prerequisites

Register the supervisor MCP server with Codex:

```bash
codex mcp add codex_supervisor -- \
  uv --directory /Users/sam.zhang/Documents/codex-supervisor \
  run codex-supervisor-mcp --config ~/.codex-supervisor/config.yaml
```

Confirm Codex can see the server:

```bash
codex mcp get codex_supervisor
```

Use a disposable task id and run id for every probe:

```bash
export TASK_ID="desktop-dual-agent-$(date +%Y%m%d-%H%M%S)"
export RUN_ID="$TASK_ID"
```

`start_dual_agent_gate` has no `execute=true` flag. A live G-2 probe means the
Desktop calls `start_dual_agent_gate` for real and Claude `/lead` runs. The
`execute` flag belongs only to `start_codex_session`, which is not part of the
narrow Desktop-initiated gate path.

## G-1: Desktop Loads Supervisor MCP

Open Codex Desktop in `/Users/sam.zhang/Documents/codex-supervisor` and send:

```text
Use the codex_supervisor MCP server. Do not use shell commands.

Call mcp__codex_supervisor__start_codex_session exactly once with:
- prompt: "desktop mcp visibility probe"
- cwd: "/Users/sam.zhang/Documents/codex-supervisor"
- execute: false

Reply with the returned status and argv.
```

Pass criteria:

- The Desktop transcript shows an MCP tool call to
  `codex_supervisor.start_codex_session`.
- The tool result has `status="dry_run"`.
- No shell command is used.

Fail criteria:

- Codex says the tool is unavailable.
- Codex uses shell instead of the MCP tool.
- The server fails to start.

## G-2: Desktop Runs A Real Claude `/lead` Gate

Open Codex Desktop and send:

```text
Run a live dual-agent gate through codex_supervisor. Do not use shell commands.

Call mcp__codex_supervisor__start_dual_agent_gate with:
- task_id: "<TASK_ID>"
- run_id: "<RUN_ID>"
- gate: "tdd_review"
- cwd: "/Users/sam.zhang/Documents/codex-supervisor"
- instruction: "Use /lead to review this tiny probe. Do not edit files. Return a dual_agent_outcome that includes specialist Planner and decision accept plan."
- expected_specialists: ["Planner"]
- expected_decisions: ["accept plan"]
- expected_objections: []
- quality: "cheap"
- timeout_s: 600

After the tool returns, call mcp__codex_supervisor__read_outcome with the same
run_id and task_id. Reply with both statuses.
```

Pass criteria:

- The Desktop transcript shows a call to `start_dual_agent_gate`.
- Claude Code `/lead` actually runs and returns a parseable
  `<dual_agent_outcome>` block.
- The gate result is `accepted`.
- `read_outcome` returns `status="ok"`.

Verify in SQLite:

```bash
sqlite3 ~/.codex-supervisor/state.db '
select event_id, kind,
       json_extract(payload_json, "$.task_id") as task_id,
       json_extract(payload_json, "$.status") as status
from events
where source="dual_agent"
order by event_id desc
limit 10;
'
```

Fail criteria:

- Claude `/lead` is not invoked.
- The outcome is malformed or missing expected signal.
- The Desktop cannot read the outcome back from the supervisor ledger.

## G-3: Desktop Drives Three Dialogue Rounds

Open Codex Desktop and send:

```text
Drive a live three-round dual-agent PRD/TDD dialogue through codex_supervisor.
Do not use shell commands.

Use task_id "<TASK_ID>" and run_id "<RUN_ID>".

Round 1:
- Call start_dual_agent_gate with gate="prd_review".
- Instruction: "Critique this PRD idea: implement a behavior change without adding tests. Return objections if tests are missing."
- Expected specialist: Planner.
- Expected decision: tests required.
- Then call record_gate_round with your Codex decision, Claude's decision, both confidences, and the main objection.

Round 2:
- Re-invoke start_dual_agent_gate with a corrective instruction that adds test expectations.
- Then call record_gate_round again.

Round 3:
- Re-invoke start_dual_agent_gate asking whether the revised PRD/TDD gate is acceptable.
- Then call record_gate_round again.

After all three rounds, call read_outcome and summarize whether the gate
converged. If a gate returns blocked or escalation returns telegram_disabled,
ask me in this Desktop chat for corrective input instead of calling
poll_resume_signal.

Before summarizing, call mcp__codex_supervisor__read_gate_transcript with the
same run_id and task_id. Use that result to show the clean Codex/Claude
dialogue, not just the final outcome.
```

Pass criteria:

- Desktop makes at least three `start_dual_agent_gate` calls.
- Desktop makes at least three `record_gate_round` calls.
- Desktop calls `read_gate_transcript` before summarizing.
- The final response states whether the gate converged or asks the user for
  corrective input in chat.
- `poll_resume_signal` is not called unless a Telegram callback was actually
  recorded.

Verify round persistence:

```bash
sqlite3 ~/.codex-supervisor/state.db '
select event_id,
       json_extract(payload_json, "$.task_id") as task_id,
       json_extract(payload_json, "$.gate") as gate,
       json_extract(payload_json, "$.round.round_index") as round_index,
       json_extract(payload_json, "$.round.codex_decision") as codex_decision,
       json_extract(payload_json, "$.round.claude_decision") as claude_decision
from events
where source="dual_agent"
  and kind="dual_agent_gate_round"
order by event_id desc
limit 10;
'
```

Verify transcript retrieval:

```text
In Codex Desktop, ask:
"Call mcp__codex_supervisor__read_gate_transcript for run_id <RUN_ID> and
task_id <TASK_ID>, then show me the rounds and final result."
```

Fail criteria:

- The rounds are synthesized without calling `start_dual_agent_gate`.
- Fewer than three `dual_agent_gate_round` events land in SQLite.
- `read_gate_transcript` cannot reconstruct the rounds and final result.
- The Desktop tries to wait on `poll_resume_signal` in the no-Telegram path.

## Expected Narrow-Scope Verdict

After G-1, G-2, and G-3 pass, the narrow goal is empirically proven:

> Codex Desktop can initiate dual-agent gates, Claude Code can run `/lead`,
> Codex can record and review multi-round dialogue, and no-Telegram escalation
> falls back to Desktop chat instead of a dead resume poll.
