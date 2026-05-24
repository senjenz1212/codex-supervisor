# Grill Findings

### Finding 1

status: resolved
question: Could receipt-backed claim verification become another caller-supplied escape hatch?
resolution: Built-in claims ignore `verified_claims` unless a typed receipt with the matching kind/status exists. The legacy list remains only as auxiliary context.

### Finding 2

status: resolved
question: Should this create a dedicated receipts table now?
resolution: No. The event ledger is already the acceptance boundary, and generic receipt dictionaries inside interaction/result events are enough for this slice. A separate table belongs to the universal trace-envelope slice.

### Finding 3

status: resolved
question: Does richer mailbox metadata risk hiding important details in raw JSON?
resolution: The transcript tool and Markdown export must render claims, objections, receipts, evidence refs, and confidence rationale as first-class sections.

### Finding 4

status: waived
question: Should live Cursor be required before shipping this traceability slice?
reason: Cursor SDK remains optional and live probing is already yellow in the health matrix. This slice hardens the supervisor-owned receipt and transcript layer with fixture runners.
