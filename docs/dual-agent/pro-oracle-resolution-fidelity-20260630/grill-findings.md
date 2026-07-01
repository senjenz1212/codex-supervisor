# PRD Grill Findings

Task id: `pro-oracle-resolution-fidelity-20260630`

## Finding 1: Empty PASS_TO_PASS could be overgeneralized.

Concern: The product request is not "ignore PASS_TO_PASS." It is "empty PASS_TO_PASS is vacuous pass when FAIL_TO_PASS exists and passes."

Resolution: P1 and P3 split the empty and non-empty regression cases. P3 explicitly preserves non-empty `PASS_TO_PASS` regression failure.

Status: resolved.

## Finding 2: Empty FAIL_TO_PASS could accidentally ride the same vacuous path.

Concern: Upstream's vacuous regression bucket is not permission to accept records with no bug-fix tests.

Resolution: P2 requires `pro_oracle_bucket_empty:fail_to_pass` to stay unavailable and makes empty `FAIL_TO_PASS` a forbidden outcome.

Status: resolved.

## Finding 3: rc-nonzero acceptance could hide harness instability.

Concern: Accepting non-zero return codes can be abused if the parser output is empty or malformed.

Resolution: P4 requires `patch_applied=true`, `tests_count>0`, and both parsed statuses `pass`; the rc is retained and `rc_nonzero_resolved=true` is disclosed.

Status: resolved.

## Finding 4: Disclosure could stop at the receipt and disappear from summaries.

Concern: A field buried in an adapter receipt is not operator-visible when reviewing corpus and powered artifacts.

Resolution: P5 requires row-level fields plus corpus summary counts, powered report metadata counts, and DoD evidence.

Status: resolved.

## Finding 5: Fixing Phase 0 could be mistaken as permission to spend solver budget.

Concern: The curation rerun is still only Phase 0. It must not start solver/model execution automatically.

Resolution: P6 keeps the rerun dry-gold-only and requires operator review before solver spend even if the prereg bar passes.

Status: resolved.
