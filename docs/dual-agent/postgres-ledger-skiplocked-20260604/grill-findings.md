# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 491181 `prd_review`: both agents accepted
- event_id 491215 `issues_review`: both agents accepted
- event_id 491432 `tdd_review`: both agents accepted
- event_id 491465 `implementation_plan`: shasum verification of planning artifacts was approval-declined; hashes self-reported per packet required_evidence_grade=self_reported
- event_id 491465 `implementation_plan`: pytest not executed this gate; test existence and shape verified statically, not pass/fail
- event_id 491465 `implementation_plan`: Dual-schema source-of-truth risk (inline POSTGRES_SCHEMA_SQL vs alembic migration) mitigated only by a structural-equivalence test, not single-source generation
- event_id 491632 `implementation_plan`: both agents accepted
- event_id 491667 `execution`: Core DB-behavior guarantees (SKIP LOCKED disjointness, multi-writer double-submit, compare-and-set, catch-up) are exercised only by tests gated on CODEX_SUPERVISOR_POSTGRES_TEST_DSN, so default CI does not run them; mitigated by 6 always-on tests enforcing SQL shape + inline/migration structural equivalence and explicit PRD/policy acceptance of self_reported grade.
- event_id 491668 `execution`: both agents accepted
- event_id 491911 `outcome_review`: both agents accepted
