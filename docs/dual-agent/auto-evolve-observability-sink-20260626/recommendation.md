# Langfuse vs Opik Recommendation

## Choice

Prefer Langfuse for Slice 2 if the next implementation is LiteLLM-native tracing plus read-only artifact links.

## Why

- Langfuse has a documented LiteLLM integration path for tracing LLM calls.
- The local repo already treats benchmark reports and ledgers as source-of-truth artifacts; the sink only needs trace and dashboard references.
- The trust-path boundary is simpler when the sink stores links to artifacts rather than judging or deriving benchmark authority.

## Opik Fit

Use Opik instead if the next product slice centers on an evaluation and human annotation workflow before tracing. That should remain a separate packet after this read-only boundary is proven.

## Infra Note

Self-hosting either sink adds operator burden. Do not introduce the deployment until the local ingest contract is stable and the AEB-0 artifact stream exists.

## Trust-Path Invariant

Langfuse/Opik data is an observability mirror. It cannot satisfy benchmark evidence, evaluator provenance, reviewer independence, or policy proposal requirements.
