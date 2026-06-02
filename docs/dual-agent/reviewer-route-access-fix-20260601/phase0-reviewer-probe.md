# Phase 0 Reviewer Probe: reviewer-route-access-fix-20260601

## Purpose

Resolve the blocking open question with real runtime evidence before PRD/TDD:
which reviewer route can produce a real typed verdict in rigorous workflow, and
whether the fallback 403 is transient infrastructure or access/configuration.

## Runtime Setup

The probe was run from `/Users/sam.zhang/Documents/codex-supervisor` using the
same secret-loading paths as `codex-supervisor-workflow`:

- `.codex-supervisor/secrets.env` loaded `OPENAI_API_KEY` and
  `OPENAI_BASE_URL`.
- `.codex/config.toml` loaded `CURSOR_API_KEY` from the
  `mcp_servers.codex_supervisor.env` block.
- Secrets were not printed or written. The OpenAI-compatible key is an opaque
  non-JWT key, so token subject/scope cannot be locally introspected.
- Fallback route host: `uai-litellm.internal.unity.com`.
- Fallback model: `gemini-3.1-pro-preview`.

Raw redacted fixtures:

- `phase0-reviewer-probe.json`
- `phase0-litellm-bounded-probe.json`
- `phase0-cursor-bounded-probe.json`
- `phase0-cursor-tiny-live/summary.json`

## Fallback Route Evidence: LiteLLM / Gemini

### Large realistic packet

A representative rigorous packet built from prior workflow artifacts and the
previous accepted run produced:

- prompt chars: `134764`
- prompt sha256: `7cc7483a5a522756e017b79e7aa7fd5f2c76b1e13fc9de5a61b2270b58e28e05`
- prompt tokens reported by gateway: `43984`
- HTTP/auth result: request authenticated and returned a completion
- model: `gemini-3.1-pro-preview`
- finish reason: `length`
- typed verdict: unusable because completion content was truncated/empty

Conclusion: with the supervisor-style secrets loaded, this route did not return
403 in Phase 0. The route can authenticate, but the prompt/token budget can
still prevent a usable typed verdict if the reviewer packet is too large.

### Bounded representative packet

A bounded representative packet produced:

- prompt chars: `16168`
- runtime: `litellm_structured`
- model: `gemini-3.1-pro-preview`
- finish reason: `stop`
- probe reason: `cursor_review_ok`
- typed outcome decisions: `accept`

Conclusion: Gemini fallback is currently the viable reviewer route. It can
return a parseable typed `<dual_agent_outcome>` when the packet is scoped enough
for the structured response to finish.

## Primary Route Evidence: Cursor SDK

### Large representative packet

The Cursor SDK route was forced with the same large representative packet:

- prompt chars: `134764`
- attempts: `3`
- retry limit: `2`
- backoff: `[1.0, 2.0]`
- every attempt failed with `internal: internal error`
- final classification: `reviewer_infrastructure_unavailable`

### Bounded representative packet

The Cursor SDK route was forced with the bounded `16157` char packet:

- attempts: `3`
- every attempt failed with `internal: internal error`
- final classification: `reviewer_infrastructure_unavailable`

### Tiny control packet

`scripts/probe_cursor_sdk_live.py` was run against a tiny temporary repository:

- `cursor_sdk` import: ok
- `CURSOR_API_KEY`: present
- attempts: `3`
- every attempt failed with `internal: internal error`
- final classification: `reviewer_infrastructure_unavailable`

Conclusion: current evidence does not support a prompt-size-only Cursor fix.
Cursor SDK is failing even on a tiny packet, so this slice should keep the
bounded retry behavior from `01c0e66`, file an OQ for a dedicated Cursor route
follow-up, and ensure the Gemini fallback carries rigorous review when Cursor is
down.

## Decision

Implement this slice as a fallback-route hardening and access-denied
classification slice:

1. Keep Cursor primary and the bounded infrastructure retries.
2. Make LiteLLM/Gemini fallback capable of returning a real counted typed
   verdict by scoping/compacting reviewer packets when needed.
3. Classify HTTP 401/403 or access-denied gateway responses as
   `reviewer_access_denied`, not transient infrastructure.
4. Do not retry access-denied failures; retrying a 403 cannot fix token scope,
   model allowlist, VPN, or base URL configuration.
5. Surface `reviewer_access_denied` as a distinct hard configuration failure,
   not as proceed-degraded. Missing verdicts remain non-accepting.

## Open Question Filed

Cursor SDK currently returns `internal: internal error` for tiny, bounded, and
large packets despite a present key and importable SDK. That is out of scope for
this slice beyond preserving bounded retries. A dedicated Cursor follow-up
should inspect SDK version, account/model entitlements, API status, raw SDK
transport logs, and whether the key is valid for `composer-2.5` local agents.
