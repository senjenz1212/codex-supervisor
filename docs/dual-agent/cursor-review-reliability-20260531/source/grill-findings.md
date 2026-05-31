# PRD Grill Findings

### Finding 1: Missing verdict must not become acceptance

status: resolved

question: Does the PRD prevent a malformed Cursor response from being counted as accept?

resolution: P2 and P4 explicitly forbid fabricated acceptance and require infrastructure classification plus bounded recovery or escalation.

### Finding 2: Real reviewer objections must still block

status: resolved

question: Could the reliability path weaken Cursor's independent-review role?

resolution: P3 preserves existing AND-verdict semantics for valid Cursor `revise`, `deny`, and blocking critical-review outcomes.

### Finding 3: Recovery needs durable evidence

status: resolved

question: Can `Transport closed` during transcript reads erase the reviewer reason?

resolution: P5 requires writing the Cursor verdict or infrastructure failure into existing ledger events before export/read operations.

### Finding 4: Retry boundary belongs in Cursor invocation

status: resolved

question: Should malformed reviewer-output retries happen at the gate loop or inside the Cursor runner?

resolution: P1 places bounded corrective retries in `invoke_cursor_agent`; the gate layer receives a typed valid verdict or typed infrastructure classification.

### Finding 5: Transport reconnect is not part of this promise

status: resolved

question: Does this PRD overclaim lower-level MCP transport recovery?

resolution: Out of Scope excludes raw MCP auto-reconnect. This slice only makes reviewer results durable and classifiable when a later read/export transport fails.
