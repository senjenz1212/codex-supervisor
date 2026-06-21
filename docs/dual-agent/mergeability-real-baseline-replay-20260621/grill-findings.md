# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 836309 `prd_review`: Low-severity: P3 replayable-evidence partially GREEN at PRD time - candidate_hash/task_hash/candidate_pool_sha256 already exported at :1021/:1325/:1373; TDD must pin net-new producer identity/prompt-hash/model/provider/budget/unavailable-reason (PRD line 28), not re-assert existing hash export
- event_id 836309 `prd_review`: Meta: this is the 5th prd_review of an unchanged immutable artifact; pipeline should advance to downstream gates (tdd/implplan/outcome) rather than re-running prd_review
- event_id 836310 `prd_review`: both agents accepted
- event_id 836331 `issues_review`: Low severity: issues.md is byte-identical to the R3-accepted issues_review gate from earlier today; the artifact under review is unchanged and pre-accepted. The supervisor is re-running the gate. Decomposition remains valid and is now more fully backed by source, so this does not warrant revise/deny.
- event_id 836332 `issues_review`: both agents accepted
- event_id 836503 `tdd_review`: both agents accepted
- event_id 836658 `implementation_plan`: independent_reviewer_non_accept: independent-reviewer-1
- event_id 836683 `implementation_plan`: Independent-reviewer-1 exact objection not provided in packet; could not confirm 1:1 it is addressed, though every enumerated intent requirement was verified satisfied
- event_id 836683 `implementation_plan`: Tests statically traced as non-vacuous but unexecuted (pytest approval-blocked) -> test_status unknown
- event_id 836845 `implementation_plan`: cursor_review_failed: independent-reviewer-1 producer dict() crash path still present and reproduced end-to-end; Claude accept overclaims fail-closed coverage for malformed producer types; No boundary test covers non-Mapping producer crash case; Plan step 7 full-file pytest rerun not evidenced in supervisor packet
- event_id 836851 `implementation_plan`: Independent-reviewer-1 exact objection not provided in packet; could not confirm 1:1 it is addressed, though every enumerated intent requirement was verified satisfied
- event_id 836851 `implementation_plan`: Tests statically traced as non-vacuous but unexecuted (pytest approval-blocked) -> test_status unknown
- event_id 836872 `implementation_plan`: CRITICAL: producer non-Mapping crash still present. supervisor/mergeability_bench.py:2887 (and :2851/:2858/:2866) run dict(raw.get('producer') or {}); a truthy non-Mapping producer (e.g. string 'alice' or list) yields dict('alice') -> ValueError. A hash-matching row with producer='alice' enters the missing_replay_fields branch and crashes the whole powered factorial evaluation rather than returning an unavailable/malformed decision. Violates the fail-closed intent and reproduces the cursor objection end-to-end.
- event_id 836872 `implementation_plan`: TEST GAP: no boundary test exercises a truthy non-Mapping producer. test_powered_factorial_baseline_missing_replay_fields_is_unavailable omits the producer key (None -> dict({}) is safe), so the crash path is untested. A RED test must supply producer as a non-empty string/list and assert single_agent_baseline_unavailable is True with a malformed reason, not an exception.
- event_id 836872 `implementation_plan`: EVIDENCE GAP: Plan Step 7 mandates a full mergeability test-file pytest rerun; the supervisor packet contains no pytest output. test_status cannot be confirmed (FM-2.4 information-withholding lesson applies). Fix: include full-file pytest run output in the packet.
- event_id 836873 `implementation_plan`: agents have not both accepted yet; revise and continue
- event_id 836875 `implementation_plan`: CRITICAL: producer non-Mapping crash still present. supervisor/mergeability_bench.py:2887 (and :2851/:2858/:2866) run dict(raw.get('producer') or {}); a truthy non-Mapping producer (e.g. string 'alice' or list) yields dict('alice') -> ValueError. A hash-matching row with producer='alice' enters the missing_replay_fields branch and crashes the whole powered factorial evaluation rather than returning an unavailable/malformed decision. Violates the fail-closed intent and reproduces the cursor objection end-to-end.
- event_id 836875 `implementation_plan`: TEST GAP: no boundary test exercises a truthy non-Mapping producer. test_powered_factorial_baseline_missing_replay_fields_is_unavailable omits the producer key (None -> dict({}) is safe), so the crash path is untested. A RED test must supply producer as a non-empty string/list and assert single_agent_baseline_unavailable is True with a malformed reason, not an exception.
- event_id 836875 `implementation_plan`: EVIDENCE GAP: Plan Step 7 mandates a full mergeability test-file pytest rerun; the supervisor packet contains no pytest output. test_status cannot be confirmed (FM-2.4 information-withholding lesson applies). Fix: include full-file pytest run output in the packet.
- event_id 836883 `implementation_plan`: Independent reviewer's exact objection is not present in the handoff packet (FM-2.4 information withholding); addressed by enumerating concrete line refs for every plausible objection category but cannot 100% confirm the specific concern is resolved
- event_id 836883 `implementation_plan`: Working tree is byte-identical to prior round (+443/-16, plan sha and HEAD unchanged) so the gate borders step-repetition; mitigated by independent live-source re-verification this round and absence of any substantive defect
- event_id 837004 `implementation_plan`: both agents accepted
- event_id 837035 `execution`: both agents accepted
- event_id 837174 `outcome_review`: both agents accepted
