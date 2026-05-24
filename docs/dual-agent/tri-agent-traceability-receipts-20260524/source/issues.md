# Issues

## Slice 1: Rich Interaction Mailbox

Priority: P0
Estimate: M
Scope: Extend the typed interaction message schema and transcript/export readers so every agent message can expose claims, objections, questions, evidence refs, tool receipts, transcript refs, persona id, and would-change-if criteria.
Acceptance Criteria:
- [x] `AgentMailboxMessage` has first-class fields for the new trace metadata.
- [x] `read_gate_transcript` includes rich interaction fields.
- [x] `interactions.md` and `transcript.md` render rich interaction fields.
PRD promise: P1, P4.

## Slice 2: Receipt-Backed Claim Verification

Priority: P0
Estimate: M
Scope: Add workflow tool receipts and require them for built-in claims at outcome review.
Acceptance Criteria:
- [x] `tests passed` requires a passed test receipt mapped to the claim.
- [x] `implemented` requires a diff/changed-files receipt mapped to the claim.
- [x] `pushed` requires a remote push receipt mapped to the claim.
- [x] `verified_claims` alone cannot satisfy built-in claims.
PRD promise: P2.

## Slice 3: Rich Resume Prompt

Priority: P1
Estimate: S
Scope: Expand fresh-chat resume output with state, steps, latest event id, blockers, artifact output dir, and safe inspection commands.
Acceptance Criteria:
- [x] Blocked workflow resume output includes latest event id and blocker details.
- [x] Prompt tells Codex to call `read_gate_transcript` and inspect artifacts before continuing.
- [x] Accepted workflow resume output points to artifacts and final transcript.
PRD promise: P3.

## Slice 4: Tri-Agent Trace Artifact

Priority: P1
Estimate: S
Scope: Document the Codex, Claude Code, and Cursor roles plus implementation trace for this slice.
Acceptance Criteria:
- [x] `interactions.md` records planned/actual agent roles, decisions, and validation commands.
- [x] `outcome-review.md` summarizes what passed, what remains, and why.
PRD promise: P1, P4.
