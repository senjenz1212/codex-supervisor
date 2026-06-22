## Slice 1 - Official Replay Opt-In And Public Bundles

Scope: Add the official replay public interface that loads official-style SWE-bench records only with explicit opt-in or injected deterministic loader, then materializes public bundles at base_commit through a narrow materializer seam.

Acceptance Criteria:
- [ ] Public official replay refuses dataset loading without explicit opt-in when no injected loader is supplied.
- [ ] Official records with repo, base_commit, problem_statement, patch, test_patch, FAIL_TO_PASS, and PASS_TO_PASS become public bundles without hidden oracle content.
- [ ] The generated replay manifest records dataset name, split, instance ids, materialization receipts, and report-only invariants.

Priority: High

## Slice 2 - Freeze Decisions Before Official-Style Oracle

Scope: Extend replay execution so an official-style oracle adapter can run after frozen baseline, S_probe, and S_full decisions without exposing hidden fields to public decisions.

Acceptance Criteria:
- [ ] Frozen decisions are written before the oracle adapter receives hidden FAIL_TO_PASS or PASS_TO_PASS fields.
- [ ] Reviewer packets and public packets exclude patch, test_patch, FAIL_TO_PASS, PASS_TO_PASS, oracle_accept, and final_score strings.
- [ ] The report labels oracle_adapter_kind and keeps metric_applyable, improvement_claim_allowed, default_change_allowed, policy_mutated, and gate_advanced false.

Priority: High
