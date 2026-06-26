# Read-Only Observability Sink PRD

## Product Intent

Operators need a place to inspect AEB-0, powered benchmark, ledger, and future annotation artifacts without letting dashboard traces or annotations become benchmark evidence. Slice 1 creates a local read-only ingest contract and a separate packet for a future Langfuse or Opik integration.

## Recommendation

Use Langfuse first for read-only artifact/trace ingest if this repo wants LiteLLM-native tracing and accepts the additional self-hosting infra footprint. Keep Opik as an alternate if the product center of gravity becomes evaluation/annotation workflow first.

References:

- Langfuse LiteLLM integration: https://langfuse.com/docs/integrations/litellm
- Langfuse self-hosting: https://langfuse.com/self-hosting
- Opik self-hosting: https://www.comet.com/docs/opik/self-host/self_hosting
- Opik LiteLLM integration: https://www.comet.com/docs/opik/tracing/integrations/litellm

## Promise Contracts

P-SINK-1: The sink requires an AEB-0 artifact reference before ingest.

P-SINK-2: The sink may ingest completed or blocked artifacts but must preserve their blocked/report-only status.

P-SINK-3: Sink-origin fields cannot set authority flags or satisfy evaluator provenance.

P-SINK-4: Sink annotations cannot affect reviewer roster, pairwise oracle-error overlap, effective vote, or policy derivation.

P-SINK-5: The sink writes operator-facing evidence and optional ledger rows only.

## Allowed Outcomes

- Missing AEB-0 artifact ref returns `status=blocked`.
- Existing blocked AEB-0 artifact can be ingested with sink `status=reported`, while AEB-0 remains blocked.
- Sink payload fields like trace IDs, scores, annotations, and dashboard URLs are report-only metadata.
- All authority flags remain false.

## Forbidden Outcomes

- Dashboard score or annotation becomes `evaluator_run_ref`, `evaluator_run_hash`, quality-control evidence, empty-floor evidence, or candidate overlay evidence.
- Sink payload sets `metric_applyable`, `improvement_claim_allowed`, `policy_mutated`, or `gate_advanced`.
- Sink annotations change roster selection or effective-vote computation.
- Sink output enters policy derivation.
