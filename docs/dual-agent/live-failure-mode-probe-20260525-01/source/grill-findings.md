# Live Failure Mode Probe Grill Findings

## Findings

### Finding G1

status: resolved
severity: high
question: Could model agreement accidentally become acceptance?
resolution: The final supervisor event includes P11 in the blocking probe set,
so accepted model output remains blocked when receipts are absent.

### Finding G2

status: resolved
severity: high
question: Could the Cursor API key leak into artifacts?
resolution: The redactor now covers standalone `crsr_` tokens, and the probe
stores only boolean key presence plus redacted transcripts.

### Finding G3

status: waived
severity: medium
question: Should this probe perform real Browser or Computer Use validation?
reason: The failure path is receipt governance for code claims; visual evidence
is a separate user-facing surface and would blur this probe's acceptance rule.

## Decision

No open findings remain. The slice can proceed if P11 blocks and the trace
envelope classifies the failure as missing or stale receipt evidence.
