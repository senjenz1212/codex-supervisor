# Agent Interactions: dual-agent-branch-a-b-hardening-20260524

- run_id: `dual-agent-branch-a-b-hardening-20260524`
- task_id: `dual-agent-branch-a-b-hardening-20260524`
- source: Codex Desktop chat, sidecar subagents, local supervisor tests
- purpose: readable projection of the tri-agent implementation dialogue

## 1. Sam -> Codex

Request: implement both Branch A and Branch B with the tri-agent approach.

Constraints recorded:

- Use PRD/TDD skills and leave inspectable artifacts.
- Keep Codex as lifecycle owner/reviewer.
- Use Claude/Cursor/sidecar review where useful, but do not fake live proof.
- Do not treat cost caps as a default concern.

## 2. Codex -> Sidecar Reviewer A

Role: Branch A hardening reviewer.

Ask: inspect MAST/failure taxonomy, universal trace envelope, hardcoded confidence, and health matrix correction. Do not edit files.

Returned findings:

- Centralize blocking probe ids so P11, P12, and P_planning do not collapse into generic blocked states.
- Add non-breaking trace envelope metadata at event write sites.
- Replace inline `0.95` / `0.7` confidence callsites with `ConfidenceReport.value`.
- Correct artifact-only lifecycle in the health matrix to yellow.

## 3. Codex -> Sidecar Reviewer B

Role: Branch B expansion reviewer.

Ask: inspect Cursor live probe support, UI visual evidence path, Codex rich reviewer packet, PRD/TDD skill receipts, and Cortex cockpit docs. Do not edit files.

Returned findings:

- Cursor SDK boundary exists and is fixture-tested; live SDK probe remains key-gated.
- UI visual evidence path already enforces Browser/Computer Use receipts.
- Rich mailbox fields exist, but Codex needs a structured reviewer packet.
- PRD/TDD artifact substance is enforced, but skill execution receipts are missing.
- Cortex cockpit ADR already selects current Cortex as the ledger renderer.

## 4. Codex Implementation Decisions

- Added deterministic `supervisor/failure_taxonomy.py`.
- Added non-breaking `supervisor/trace_envelope.py` stamping through `State.write_event`.
- Added `P12` PRD/TDD skill receipt validation and blocked `run_dual_agent_workflow` at `workflow_start` when receipts are missing.
- Added `codex_review_packet` and threaded it into Codex gate-decision mailbox messages.
- Reused `ConfidenceReport.value` for gate-round confidence.
- Added `scripts/probe_cursor_sdk_live.py`.

## 5. Cursor Probe Result

Command:

```text
uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/live-cursor-sdk-probe-20260524-01
```

Observed result:

- `cursor_sdk_import`: `ok`
- `api_key_present`: `false`
- `status`: `skipped`
- `reason`: `missing_cursor_api_key`

Decision: keep live Cursor green proof open until `CURSOR_API_KEY` is present in the shell environment.

## 6. Validation Receipts

Focused suite:

```text
uv run pytest tests/test_failure_taxonomy.py tests/test_agent_mailbox.py tests/test_agent_interaction_snapshot.py tests/test_dual_agent_workflow_driver.py tests/test_codex_supervisor_mcp_stdio.py tests/test_cursor_agent.py -q
```

Result: `102 passed`.

Compile:

```text
python3 -m compileall -q supervisor mcp_tools scripts tests
```

Result: passed.

Full suite:

```text
uv run pytest -q
```

Result: `396 passed`.

Secret scan over new branch artifacts, Cursor diagnostic, probe script, and new modules found no key-shaped values.
