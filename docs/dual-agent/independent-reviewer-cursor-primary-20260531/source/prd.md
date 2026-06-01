# Independent Reviewer Cursor Primary PRD

## Problem Statement

The supervisor currently labels the independent review boundary with Cursor
language even when the runtime is `litellm_structured`. That default was useful
for contract reliability, but it changed the assurance model: Gemini receives
prompt text and strict schema enforcement, while Cursor SDK can run as an
agentic local reviewer with `LocalAgentOptions(cwd=...)`.

Operators need the primary independent reviewer to be the agentic,
codebase-reading Cursor SDK path when it is available, and they need any
Gemini fallback to be honestly labeled as text-only and lower assurance. They
also need failures to be diagnosable. Prior Cursor SDK failures produced empty
transcripts with `status=error`, and a new realistic prompt probe exceeded its
requested timeout without supervisor-owned cancellation.

## Solution

Make `cursor_sdk` the default independent reviewer runtime again, but only with
reliability controls that make the default honest and recoverable:

- force live Cursor probes to use `cursor_sdk` and load credentials the same
  way the workflow CLI does;
- capture prompt size, per-attempt status, raw run metadata, error details,
  duration, and timeout outcomes;
- enforce supervisor-side timeouts around SDK calls that can block;
- fall back to `litellm_structured` only after Cursor genuinely fails after its
  retry/timeout envelope;
- label fallback verdicts as `litellm_structured` and lower assurance, never
  as Cursor;
- expose an `independent_reviewer` boundary while keeping compatibility aliases
  for existing `cursor_review` callers.

## Phase 0 Evidence

The planning gate resolved the blocking open question with live runtime
evidence:

- Minimal forced Cursor SDK probe with `CURSOR_API_KEY` loaded from Codex MCP
  config produced a valid typed outcome in about 13 seconds.
- Prior realistic Cursor SDK probes returned empty transcripts and
  `status=error` across both plan and agent modes.
- The new realistic prompt-size probe forced `cursor_sdk`, used 23662 prompt
  characters, 9 planning artifacts, and 3 receipts, but exceeded the requested
  180 second envelope and required external process termination because the
  current SDK path does not enforce `timeout_s` around `run.text()` / `wait()`.

Decision: Cursor can be the primary reviewer, but only if this slice ships
timeouts, diagnostics, and degraded fallback provenance in the same change.

## User Stories

1. As an operator, I want the default independent reviewer to inspect the local
   repo through Cursor SDK, so high-value gates get agentic review instead of
   prompt-only review.
2. As an auditor, I want the ledger to say which reviewer runtime actually ran,
   so a Gemini fallback is never mistaken for Cursor.
3. As a maintainer, I want Cursor SDK failures to include raw status, prompt
   size, error details, duration, and retry metadata, so 0/4 empty-transcript
   failures become diagnosable.
4. As a gate owner, I want a text-only Gemini fallback to keep the workflow
   moving only as a clearly degraded review, so we do not fake an agentic
   verdict.
5. As a safety reviewer, I want real reviewer `revise` and `deny` outcomes to
   keep blocking, no matter which runtime produced them.
6. As a developer, I want deterministic tests and fixtures for Cursor success,
   Cursor failure, Gemini fallback, both-fail recovery, and legacy aliases.

## PRD Promise Contracts

P1. Cursor SDK is the default primary independent reviewer
User-visible promise: A workflow with reviewer review enabled and no explicit
reviewer mode requests `cursor_sdk` by default.
Representative prompts or actions: Run `run_dual_agent_workflow` with
`cursor_review=true` and no explicit `reviewer_output_mode`.
Public boundary: `supervisor.config`, `run_dual_agent_workflow`, workflow CLI
payload parsing, and reviewer request construction.
Allowed outcomes: default runtime is `cursor_sdk`; explicit
`litellm_structured` still works; `lead_direct` remains unchanged.
Forbidden outcomes: silently defaulting to Gemini text-only review while
reporting Cursor review, or dropping explicit reviewer configuration fields.
Related user stories: 1, 2

P2. Cursor live probe is truthful
User-visible promise: `scripts/probe_cursor_sdk_live.py` always tests
`cursor_sdk`, loads `CURSOR_API_KEY` from the Codex MCP config path, and records
an honest skip when no key is available.
Representative prompts or actions: Run the probe with and without a Cursor key.
Public boundary: `scripts/probe_cursor_sdk_live.py`
Allowed outcomes: real key produces a Cursor SDK attempt; missing key produces
`missing_cursor_api_key`; no probe reports `missing OPENAI_API_KEY`.
Forbidden outcomes: accidentally testing the Gemini default, or treating a
missing OpenAI key as a Cursor result.
Related user stories: 1, 3, 6

P3. Cursor SDK failures are diagnosable and bounded
User-visible promise: Each Cursor attempt records prompt size, status, raw run
metadata or conversation metadata when available, errors, duration, retry
reason, and timeout classification.
Representative prompts or actions: Simulate SDK error, empty output, and
timeout.
Public boundary: `supervisor.cursor_agent.invoke_cursor_agent`
Allowed outcomes: `reviewer_contract_unmet` or
`reviewer_infrastructure_unavailable` includes diagnostic metadata and never
hangs indefinitely past the supervisor timeout.
Forbidden outcomes: empty 0/4 transcripts with no raw metadata, or blocking
forever inside SDK wait/text calls.
Related user stories: 3, 6

P4. Gemini fallback is honest and lower assurance
User-visible promise: If Cursor SDK genuinely fails after the retry/timeout
envelope, the supervisor may fall back to `litellm_structured`, but the verdict
records `reviewer_runtime="litellm_structured"` and a lower-assurance/degraded
grade.
Representative prompts or actions: Force Cursor SDK failure with OpenAI
fallback credentials available.
Public boundary: `invoke_cursor_agent`, `run_dual_agent_workflow`, ledger
payloads, and exported artifacts.
Allowed outcomes: fallback can accept/revise/deny as its own text-only review;
the payload includes fallback reason and lower assurance.
Forbidden outcomes: counting Gemini as Cursor, hiding fallback, or treating
fallback assurance as equal to agentic Cursor.
Related user stories: 2, 4, 6

P5. Independent reviewer naming is truthful
User-visible promise: New ledger and result payloads expose an
`independent_reviewer` boundary carrying `reviewer_runtime`, while legacy
`cursor_review` aliases remain available for compatibility.
Representative prompts or actions: Read gate transcript, interaction exports,
workflow result JSON, and review packets after a Cursor run and after a Gemini
fallback.
Public boundary: MCP workflow result, state event payloads, transcript export,
and `agent_mailbox` review packet construction.
Allowed outcomes: no new record says Cursor accepted when the runtime was
Gemini; old callers can still read `cursor_review`.
Forbidden outcomes: provenance mislabels, breaking existing tests/callers, or
removing reviewer runtime from exported artifacts.
Related user stories: 2, 6

P6. Review safety semantics are unchanged
User-visible promise: A real `revise` or `deny` from Cursor or fallback still
blocks, and both-runtimes-fail uses existing reviewer-unavailable recovery.
Representative prompts or actions: Simulate valid revise/deny, Cursor failure
plus fallback failure, and proceed-degraded recovery.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: valid rejections block; both-fail routes to
`reviewer_unavailable_policy` with default `escalate`; missing verdict is never
counted as accept.
Forbidden outcomes: fake accept, bypassing P1/P2/P3/P13/P14, or weakening
AND-verdict algebra.
Related user stories: 4, 5, 6

P7. Replay evidence stays deterministic
User-visible promise: Unit and workflow tests do not need live model access,
while live probe artifacts remain durable planning evidence.
Representative prompts or actions: Run focused tests and full suite without
live Cursor/OpenAI credentials.
Public boundary: pytest suite and exported dual-agent artifacts.
Allowed outcomes: deterministic fake runners cover Cursor success, Cursor
failure, fallback success, fallback rejection, both-fail recovery, and alias
payloads.
Forbidden outcomes: live credentials required for default tests, or replay
artifacts that omit reviewer runtime/assurance.
Related user stories: 6

## Implementation Decisions

- Keep Claude Code `/lead` and `lead_direct` unchanged; this slice changes only
  the independent reviewer boundary.
- Make `cursor_sdk` the configured default reviewer output mode, while leaving
  explicit `litellm_structured` selection available for operators and tests.
- Treat Cursor SDK as the primary runtime only when its attempt is bounded by
  supervisor-owned timeout handling and emits diagnostics on failure.
- Add fallback from Cursor SDK to `litellm_structured` inside the reviewer
  invocation boundary, so workflow gate algebra still sees a single typed
  independent reviewer result with accurate runtime metadata.
- Add `independent_reviewer` as the truthful persisted boundary and keep
  `cursor_review` as a compatibility alias during migration.
- Store reviewer assurance as metadata derived from runtime: Cursor SDK is
  agentic, while LiteLLM structured fallback is text-only degraded assurance.
- Record a short ADR for the boundary rename and assurance model.

## Testing Decisions

- The first RED tests target `invoke_cursor_agent`,
  `run_dual_agent_workflow`, the workflow CLI payload boundary, and
  `scripts/probe_cursor_sdk_live.py`.
- Cursor SDK, OpenAI-compatible responses, SDK errors, and timeouts are mocked
  below those public boundaries so the suite does not require live credentials.
- Live Cursor evidence remains a planning artifact, not a default test
  dependency.
- Tests must include valid `accept`, `revise`, and `deny` outcomes, because
  reliability work must not weaken gate blocking semantics.
- Tests must assert both `independent_reviewer` and legacy `cursor_review`
  payload compatibility until downstream callers migrate.

## Out Of Scope

- Giving Gemini tools, cwd, or local filesystem access.
- Removing reviewer-unavailable recovery.
- Building a reviewer registry or N-reviewer synthesis.
- Changing Claude Code `/lead` implementation ownership.
- Building the durable supervisor substrate or agentic executor wiring.
- Weakening typed `<dual_agent_outcome>` validation.
