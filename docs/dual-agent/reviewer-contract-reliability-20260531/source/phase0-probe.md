# Reviewer Contract Reliability Phase 0 Probe

## Question

The blocking question was whether Cursor `composer-2.5` fails because of
prompt/schema non-compliance, truncation, parser strictness, or reviewer
runtime infrastructure, and which minimal reviewer path reliably emits the same
typed `dual_agent_outcome` contract.

## Candidate A: Cursor SDK, Current Plan Mode

Command:

`uv run python scripts/probe_cursor_sdk_live.py --output-dir docs/dual-agent/reviewer-contract-reliability-20260531/phase0/current-cursor-sdk --timeout-s 120`

Result:

- Model: `composer-2.5`
- Mode: `plan`
- Attempts: 4
- Status: blocked
- Probe reason: `reviewer_contract_unmet`
- Original reason: `missing dual_agent_outcome block`
- Cursor run status: `error`
- Transcript size: 90 bytes, only attempt markers
- Typed outcome success rate: 0/4

Evidence:

- `docs/dual-agent/reviewer-contract-reliability-20260531/phase0/current-cursor-sdk/summary.json`
- `docs/dual-agent/reviewer-contract-reliability-20260531/phase0/current-cursor-sdk/transcript.txt`

Interpretation: this is not a parser-strictness failure. The SDK returned no
usable review text, so a robust extractor cannot recover an outcome from the
current path.

## Candidate A2: Cursor SDK, Agent Mode

Command:

Inline probe using `CursorInvocationRequest(mode="agent")`.

Result:

- Model: `composer-2.5`
- Mode: `agent`
- Attempts: 4
- Status: blocked
- Probe reason: `reviewer_contract_unmet`
- Original reason: `missing dual_agent_outcome block`
- Cursor run status: `error`
- Typed outcome success rate: 0/4

Evidence:

- `docs/dual-agent/reviewer-contract-reliability-20260531/phase0/current-cursor-sdk-agent-mode/summary.json`
- `docs/dual-agent/reviewer-contract-reliability-20260531/phase0/current-cursor-sdk-agent-mode/transcript.txt`

Interpretation: switching Cursor SDK mode does not repair the failure.

## Cursor SDK Structured Output Capability

Runtime introspection of the installed `cursor_sdk` found:

- `Agent.create(options=None, *, client=None, model=None, api_key=None, name=None, local=None, cloud=None, idempotency_key=None)`
- `Agent.send(self, message, options=None, *, idempotency_key=None)`
- `SendOptions` includes `model`, `mcp_servers`, `local`, callbacks, and
  `mode`.
- No `response_format`, `json_schema`, tool-call schema, or equivalent
  structured-output parameter is exposed by the installed SDK.

Interpretation: Cursor SDK cannot currently enforce schema-native output from
the supervisor side.

## Candidate B: LiteLLM Gemini 3.1 Pro With Strict JSON Schema

Model discovery through the configured Unity LiteLLM OpenAI-compatible gateway
showed `gemini-3.1-pro-preview` and `gemini-3.1-pro-preview-ent-ai` are
available.

Initial structured-output probe with a low token budget produced schema JSON
but stopped with `finish_reason=length`; this identified truncation as a
separate budget issue for the gateway path, not an output-format failure.

The final probe used:

- Model: `gemini-3.1-pro-preview`
- API shape: `chat.completions`
- Output enforcement: `response_format={"type": "json_schema", "strict": true}`
- Schema constraints: same `Outcome` fields, required `critical_review`, and
  lowercase decision enums.
- Validation: wrap the model JSON in `<dual_agent_outcome>...</dual_agent_outcome>`
  and run the existing `evaluate_outcome_fidelity` plus Cursor completeness
  check.

Result:

- Attempts: 3
- Typed outcome success rate: 3/3
- Probe reason: `outcome_fidelity_ok`
- Finish reason: `stop`

Evidence:

- `docs/dual-agent/reviewer-contract-reliability-20260531/phase0/litellm-gemini-structured-enum/summary.json`
- `docs/dual-agent/reviewer-contract-reliability-20260531/phase0/litellm-gemini-structured-enum/transcript.txt`

## Decision

Default the independent reviewer to a structured LiteLLM route using
`gemini-3.1-pro-preview` with JSON schema output enforcement.

Keep the current Cursor SDK route available as an explicit compatibility mode,
but do not keep it as the default reviewer path because the live failure is
runtime-empty output, not a recoverable formatting issue.

The structured route must still validate through the same
`evaluate_outcome_fidelity` boundary. The supervisor may wrap schema JSON in
the typed block internally for validation, but it must not loosen P3, count
missing verdicts as accept, or change the real revise/deny blocking algebra.
