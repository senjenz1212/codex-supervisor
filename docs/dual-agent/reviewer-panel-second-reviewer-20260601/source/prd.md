# PRD: Reviewer Panel Second Reviewer

## Problem Statement

The reviewer panel foundation and conservative aggregator can represent multiple
independent reviewer results, but the production roster still effectively
contains one working independent reviewer: the Gemini/LiteLLM structured route.
That means the panel has schema support for cross-reviewer signal without a
second real lineage to exercise it.

The workflow needs a second reviewer that is genuinely distinct from the
Claude-family lead and the existing Google/Gemini reviewer. The route must be
chosen from live evidence, not preference, and must record provenance honestly:
agentic only when the runtime has tool access, bounded read permissions,
durable transcript refs, and replayable hashes; text-only when it only sees a
prompt.

## Blocking Route Evidence

Evidence is attached under
`docs/dual-agent/reviewer-panel-second-reviewer-20260601/route-evidence/`.

- Cursor SDK route: `cursor-sdk-probe.json` probed `composer-2.5` and
  `gpt-5.5`. Both attempts had `api_key_present=true` and `cursor_sdk_import=ok`
  but returned `reviewer_infrastructure_unavailable` with the SDK error
  `internal: internal error`; no typed verdict was produced.
- GPT-family Codex CLI route: `codex-cli-readonly-probe-summary.json` records a
  `gpt-5.5` Codex CLI reviewer run with `--sandbox read-only`; the transcript
  shows the reviewer inspected `README.md` with a shell read and returned a
  typed `<dual_agent_outcome>` accept. The transcript is stored at
  `codex-cli-readonly-probe.jsonl` with a sha256 hash.
- Planning fallback: the MCP `start_codex_session` transport returned
  `Transport closed`, and a direct Codex planning sub-session was killed after
  it produced no planning files. This PRD keeps the route decision grounded in
  the live route probes above.

Decision: use the Codex CLI/GPT-family route as the second reviewer. It is not
Claude-family and not Gemini-family. It must be labeled `provider_family=openai`
and `assurance_grade=agentic` only when its transcript/hash evidence proves
tool-backed read-only execution.

## Solution

Extend the thin reviewer registry so the configured panel can yield two
reviewers:

1. Existing reviewer slot: the configured Cursor-compatible reviewer, normally
   Gemini/LiteLLM structured in the rigorous workflow.
2. New reviewer slot: a Codex CLI reviewer using `codex exec --json`,
   `--sandbox read-only`, and the configured GPT-family model.

The workflow should run both reviewers downstream of Claude gate acceptance,
record both per-reviewer results in `independent_reviewer_results[]`, and feed
that full list into the existing conservative panel evaluator. Legacy
`cursor_review` and `tri_agent_cursor_review` compatibility remains anchored to
the first reviewer, while `independent_reviewer_review` carries the full panel.

If one reviewer has a recoverable infrastructure failure, the unavailable
result remains a non-accepting/missing reviewer in the panel metadata. Existing
reviewer-unavailable recovery may still proceed degraded or escalate according
to policy, but a missing verdict is never counted as accept and a real
revise/deny from any available reviewer still blocks.

## User Stories

1. As a workflow operator, I want rigorous runs to capture two real independent
   reviewer verdicts, so that the panel has cross-lineage signal instead of a
   single-reviewer alias.
2. As an auditor, I want each reviewer result to name provider family, lineage,
   tool access, assurance grade, transcript refs, and hashes, so that
   provenance labels are replayable.
3. As a maintainer, I want the new reviewer to reuse the slice-1 registry
   boundary, so that panel expansion does not become a broad plugin framework.
4. As a reliability owner, I want one reviewer outage to be recorded honestly
   while preserving existing degraded/escalation recovery.
5. As a gate owner, I want the existing conservative rules to remain unchanged:
   real important/critical revise or deny blocks, missing verdicts do not count
   as accept, and low-confidence accepts only escalate when thresholded.

## PRD Promise Contracts

P1. two-real-reviewers-recorded

User-visible promise: A rigorous workflow with independent review enabled
records two real reviewer results in `independent_reviewer_results[]`.
Representative prompt or action: Run `run_dual_agent_workflow` with
`cursor_review=true` and the default rigorous reviewer panel.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: `independent_reviewer_results[]` contains
`independent-reviewer-0` and `independent-reviewer-1`, both with typed verdicts
on the happy path.
Forbidden outcomes: only one reviewer result is emitted, the second reviewer is
mock-only in production, or the full panel only appears in tests.
Related user stories: 1, 3

P2. second-reviewer-provenance-truthful

User-visible promise: The second reviewer result truthfully records lineage and
assurance.
Representative prompt or action: Inspect the `independent_reviewer_review`
event and replay artifacts after a rigorous run.
Public boundary: `independent_reviewer_review` event payload
Allowed outcomes: the Codex CLI reviewer records `provider_family=openai`,
lineage including `codex_cli` and its model, `tool_access=codebase_tools`,
`assurance_grade=agentic`, transcript refs, transcript sha256, and output
sha256.
Forbidden outcomes: a text-only route is labeled agentic, missing transcript
hashes still claim agentic assurance, or the reviewer is mislabeled as Gemini
or Claude.
Related user stories: 2

P3. conservative-panel-applies-to-both

User-visible promise: The existing conservative evaluator sees both reviewers.
Representative prompt or action: Run a workflow where both reviewers accept,
then run a fixture where the second reviewer returns revise.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: both accepts advance; a real second-reviewer revise/deny at
important or critical severity blocks via the existing conservative panel
decision.
Forbidden outcomes: the second reviewer is recorded but ignored by aggregation,
or majority-style voting outvotes a real important objection.
Related user stories: 1, 5

P4. single-reviewer-outage-degrades-honestly

User-visible promise: If one panel reviewer is unavailable, the workflow records
that outage as degraded/missing evidence and never counts it as accept.
Representative prompt or action: Run a fixture where reviewer 1 accepts and
reviewer 2 returns `reviewer_infrastructure_unavailable` under
`reviewer_unavailable_policy=proceed_degraded`.
Public boundary: `run_dual_agent_workflow`
Allowed outcomes: the gate may proceed only through the existing degraded
recovery policy, the recovery event names the unavailable reviewer, and the
missing verdict remains visible in the panel decision.
Forbidden outcomes: the unavailable reviewer is counted as accept, the outage
is hidden from artifacts, or degraded recovery overrides a real revise/deny.
Related user stories: 4, 5

P5. deterministic-replay-preserved

User-visible promise: Reviewer panel expansion remains deterministic and
replayable under tests and exported artifacts.
Representative prompt or action: Run focused workflow tests, the workflow
driver suite, and the full suite.
Public boundary: tests plus exported dual-agent replay artifacts
Allowed outcomes: fixed fixtures/cassettes drive reviewer outcomes in tests,
artifact exports include both reviewer results, and full suite remains green.
Forbidden outcomes: tests invoke live Codex/Cursor by default, replay depends on
ambient credentials, or exported artifacts omit the second reviewer.
Related user stories: 2, 3

## Implementation Decisions

- Keep the slice behind the existing thin reviewer registry boundary. The
  registry returns the legacy Cursor-compatible reviewer as reviewer 0 and a
  Codex CLI/GPT-family reviewer as reviewer 1.
- Run the Codex CLI reviewer with JSONL output and a read-only sandbox. The
  adapter parses typed outcomes from agent messages while retaining the raw
  JSONL transcript for hashing and replay evidence.
- Preserve legacy compatibility by keeping `cursor_review` and
  `tri_agent_cursor_review` anchored to reviewer 0. The full panel is exposed
  through `independent_reviewer_results[]` and the
  `independent_reviewer_review` event.
- Feed all reviewer results into the existing conservative panel evaluator.
  This slice does not introduce weighting, voting, or calibrated confidence
  semantics.
- Treat a recoverable infrastructure failure from any panel reviewer as a
  reviewer-unavailable condition. Available reviewer verdicts must still be
  real accepts before existing degraded recovery can proceed.

## Testing Decisions

- The first public-boundary tests exercise `run_dual_agent_workflow`, because
  that is the operator-facing path that must invoke both reviewers and apply
  the conservative panel result.
- Registry-level tests are allowed only after the workflow-boundary behavior is
  covered; they verify the adapter shape, provider lineage, and transcript hash
  metadata.
- Tests must inject fake Cursor-compatible and Codex CLI runners. Live route
  evidence is kept under `route-evidence/`; the automated suite must not depend
  on ambient credentials or network access.
- The outage regression must assert both degraded recovery metadata and the
  panel's missing-reviewer evidence so the unavailable reviewer is never
  counted as accept.
- Full validation requires focused reviewer-panel tests, the workflow driver
  suite, and the full repository test suite.

## Out of Scope

- Do not add calibrated weighting.
- Do not change conservative panel rules.
- Do not add a general plugin framework.
- Do not mislabel text-only reviewers as agentic.
- Do not make Cursor SDK success a prerequisite; live evidence shows it is
  unavailable in this environment.
