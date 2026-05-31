# TDD Grill Findings

### Finding 1: First RED must hit the reviewer boundary

status: resolved

question: Are tests only helper-level parser checks?

resolution: The first test targets `invoke_cursor_agent`, the boundary where the Cursor SDK transcript is converted into a supervisor verdict.

### Finding 2: Missing outcome cannot be faked into an Outcome

status: resolved

question: Could the GREEN path synthesize a fake Cursor acceptance to satisfy downstream code?

resolution: `test_cursor_contract_miss_returns_reviewer_infrastructure_unavailable` requires `outcome is None` and a separate infrastructure classification.

### Finding 3: Quality rejections need regression protection

status: resolved

question: Could infrastructure recovery weaken real Cursor `revise` or `deny` decisions?

resolution: `test_valid_cursor_revise_still_blocks_after_retry_hardening` maps to P3 and must stay red if a valid reviewer rejection stops blocking.

### Finding 4: Ledger durability must be tested through reads

status: resolved

question: Is durable reviewer evidence only asserted at write time?

resolution: `test_read_gate_transcript_preserves_persisted_cursor_infrastructure_verdict` exercises the persisted read boundary.

### Finding 5: Recovery policy stays bounded

status: resolved

question: Does the plan allow infinite reviewer retries?

resolution: The tests require bounded retry metadata and deterministic terminal classification after retry exhaustion.
