# PRD Grill Findings

Verdict: accepted after revisions.

## Findings

1. Original framing risk: "all arms populated" was ambiguous because execution availability and verified independent measurement were collapsed.
   Resolution: add `arm_execution_populated` separately and make `all_arms_populated` require reviewer roster verification.

2. Original adapter risk: LiteLLM through an OpenAI-compatible API can look like `openai_compatible`, which is not a proven provider family.
   Resolution: require explicit `litellm_provider_family` for proxy models when model text is not self-identifying.

3. Original self-preference risk: generator disjointness was report-only and only warned about sole same-family decisive reviewers.
   Resolution: official verification blocks on any available proven reviewer family matching a known baseline producer family.

4. Original authority risk: diagnostic readiness could be mistaken for auto-evolve permission.
   Resolution: keep all authority flags false and leave policy promotion out of scope.

## Gate

Status: accepted.

Required changes folded into PRD:

- P1 names the reviewer adapter seam.
- P2 names the all-arms report boundary.
- Same-family and unproven provenance are forbidden outcomes.
