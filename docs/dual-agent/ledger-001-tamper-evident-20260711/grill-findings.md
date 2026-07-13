# PRD Grill Findings: LEDGER-001

1. **Accepted:** append-only SQL triggers are necessary but do not detect
   database rollback or replacement.
2. **Accepted:** a chain verifies the observed prefix; a separately trusted
   checkpoint is required to prove the expected tail.
3. **Accepted:** artifact manifests must be hashed into events rather than
   merely stored beside them.
4. **Accepted:** SQLite and PostgreSQL behavior must share one conformance
   contract.
5. **Accepted:** local filesystem pins are useful for hermetic composition but
   cannot be described as rollback-independent production anchoring.
6. **Residual operational gate:** terminal and periodic checkpoint emission
   must be wired to an externally managed signer and independent pin store
   before authoritative production evidence can be claimed.
