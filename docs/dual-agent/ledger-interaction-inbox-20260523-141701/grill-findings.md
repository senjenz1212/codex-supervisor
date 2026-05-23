# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 143336 `prd_review`: Inbox naming without ack - docs must call out no-ack contract explicitly
- event_id 143336 `prd_review`: Rule 4 actor heuristic is editorial, will mislabel rounds in UI
- event_id 143336 `prd_review`: Rule 5 'P2/P3 red' not anchored to typed payload path
- event_id 143336 `prd_review`: Rule 3 contradicts Rules 6/7/8 on ordering of synthetic items
- event_id 143336 `prd_review`: Rule 9 'ordinal' undefined
- event_id 143336 `prd_review`: Rule 6 'latest usable event' ts source ambiguous
- event_id 143336 `prd_review`: Rule 7/10 overlap on no_dual_agent_events warning
- event_id 143336 `prd_review`: Snapshot-wins conflict rule for severity/status divergence missing
- event_id 143336 `prd_review`: limit scope vs warnings/blocker not specified
- event_id 143336 `prd_review`: items/warnings should be tuples for frozen safety
- event_id 143336 `prd_review`: Missing tests: inbox.status==snapshot.status, idempotence/byte-equal re-projection
- event_id 143337 `prd_review`: Product: 'Inbox' naming without ack must be loudly documented; Rule 4 'actor' heuristic is editorial and will mislead UI consumers (rename to 'attributed_to' or document); Rule 5 'P2/P3 red' is undefined and must be anchored to a typed payload path. Reviewer: Rule 3 ('synthetic after causing event') contradicts Rules 6/7/8 ('after all event-derived items') - pin global order explicitly; 'ordinal' in Rule 9 is undefined; Rule 6 'latest usable event' ambiguous (name the snapshot field); Rule 7 vs Rule 10 overlap on 'no_dual_agent_events' - clarify ownership; add explicit snapshot-wins rule for severity/status divergence; confirm limit truncates items only (never warnings or blocker); pin items/warnings as tuples not lists; add tests for snapshot-equality and idempotence.
- event_id 143356 `prd_review`: Product: now_s passthrough, P2/P3 non-green vs red, limit last-N audit gap, action_required asymmetry
- event_id 143356 `prd_review`: Reviewer: ledger row shape, ':0' suffix semantics, missing decision values, blocker title None case, escalation field precedence, snapshot DTO version coupling
- event_id 143357 `prd_review`: Product: P1 now_s passthrough breaks idempotence promise; P2 non-green vs red inconsistency leaves yellow probes ambiguous; P3 limit last-N truncates early objections in a 20-round dispute; P4 action_required asymmetric between blocked-round (false without objection) and blocked-result (always true) for same severity. Reviewer: R1 State.read_dual_agent_gate_events row shape not pinned (which attribute is ts); R2 ':0' item_id suffix is cargo unless multi-item-per-event semantics defined; R3 missing/unknown round decision values uncovered; R4 blocker with both code and source None yields 'Blocked: None'; R5 escalation.reason vs message precedence undefined; R6 dependency on AgentInteractionSnapshot DTO version not pinned.
- event_id 143376 `prd_review`: Reviewer: TDD should pin attributed_to for synthetic items, SnapshotWarning.event_id source, dead 'info' branch handling, no_dual_agent_events provenance, and the static-guard mechanism.
- event_id 143399 `tdd_review`: Test 4 head+tail limit=3 semantics is invented behavior without PRD grounding
- event_id 143399 `tdd_review`: Test 8 item_id 'warning:<event_id>:0' contradicts the spec that warning items have event_id=None
- event_id 143399 `tdd_review`: Test 5 round title '<pair>' decision-string format is not pinned
- event_id 143399 `tdd_review`: Test 7 'compact JSON' serialization is not specified
- event_id 143399 `tdd_review`: Test 10 single-time-call assertion is hand-wavy and needs a deterministic call_count mechanism
- event_id 143399 `tdd_review`: Round attributed_to matrix is missing deny/deny, revise/revise, and claude-deny cases
- event_id 143399 `tdd_review`: Test 6 does not pin kind=result for result items
- event_id 143399 `tdd_review`: Test 2 does not pin the shape of snapshot.warnings entries
- event_id 143399 `tdd_review`: Warning action_required allowlist needs exhaustiveness confirmation
- event_id 143399 `tdd_review`: Test 11 '.lingtai' appears to be a typo - confirm exact banned substring list
- event_id 143400 `tdd_review`: TestLead: Test 4 limit=3 'first + last 2' is invented head+tail semantics with no PRD grounding; Test 5 title '<pair>' format is undefined; Test 7 'compact JSON' lacks concrete serialization (separators/sort_keys); Test 10 'time function that would fail if called twice if feasible' is hand-wavy - replace with deterministic call_count monkeypatch; Test 8 warning item_id 'warning:<event_id>:0' contradicts the same test's assertion that warning items have event_id=None - resolve which event_id sources the id segment. Reviewer: Round attributed_to matrix omits deny/deny, revise/revise, and claude-deny cases; codex-deny no-objection skipping the objection-required rule needs explicit PRD support; Test 6 does not pin kind=result; Test 2 does not pin the shape of snapshot.warnings entries (string vs dict) so 'tuple(snapshot.warnings)' is ambiguous; warning action_required allowlist (corrupt_event_skipped/unexpected_event_shape) needs exhaustiveness confirmation against snapshot.warnings taxonomy; '.lingtai' in Test 11 appears to be a typo for the banned mailbox path token - exact banned substrings list must be confirmed; Test 13 should state whether docs file currently exists or doc edit is part of GREEN.
- event_id 143422 `tdd_review`: Test 4: limit=1 case conflicts with stated 'first+tail' formula; declare it a special case or change to items[0].
- event_id 143422 `tdd_review`: Test 1: enumerate exact Literal members for InboxItemKind/InboxActor/InboxSeverity.
- event_id 143422 `tdd_review`: Test 8: replace 'Blocked: <code/source/unknown>' with explicit precedence (code, then source, then literal 'unknown').
- event_id 143422 `tdd_review`: Test 12: list the exact State mutation methods to monkeypatch.
- event_id 143422 `tdd_review`: Test 13: replace 'any wording that says mailbox is authoritative' with a concrete forbidden-substring set.
- event_id 143422 `tdd_review`: Test 4: add limit=2 and limit==count boundary cases to pin formula.
- event_id 143422 `tdd_review`: Test 5: state action_required rule (any deny OR objection present) explicitly.
- event_id 143423 `tdd_review`: TestLead: Test 4 contradicts itself: limit=1 case ('only latest event-derived item' = items[-1]) does not satisfy the stated PRD v3 first+tail formula, which for limit=1 yields items[0] (first + tail(0)). Either restate limit=1 as an explicit special case in the plan, or fix the case to return items[0]. Without resolution the test cannot be written deterministically. Also: Test 1 'Literal aliases exact values' never enumerates the value sets - list them (e.g., InboxItemKind: round|result|blocker|warning|handoff; InboxActor: codex|claude|supervisor|system; InboxSeverity: success|warning|blocked) so the assertion is closed. Test 12 must enumerate the exact State mutation methods to monkeypatch rather than referring to 'snapshot guard'. Reviewer: Plan claims 'no ambiguous assertions remain' and 'implementation can proceed without guessing', but: (1) Test 8 blocker title 'Blocked: <code/source/unknown>' uses placeholder notation - replace with explicit precedence rule 'code if present else source if present else literal unknown'; (2) Test 13 forbids 'any wording that says mailbox is authoritative' - unassertable, must be a concrete forbidden-substring list (e.g., 'mailbox is authoritative', 'filesystem mailbox truth layer', 'mailbox source of truth'); (3) Test 4 limit boundary coverage stops at {None,0,1,3,<0} - add limit=2 and limit==count to pin the first+tail formula edges; (4) Test 5 derives action_required by induction across parametrized cases - explicitly state the rule (any-deny OR objection-present) in the plan body so the test writer doesn't backsolve it.
- event_id 143587 `outcome_review`: Independent diff and test inspection was not performed; relying solely on Codex self-report violates lead verification rules
- event_id 143590 `outcome_review`: Claude outcome review could not inspect filesystem evidence; rerun with diff and test output embedded.
