# PRD Grill Findings

### Finding 1: Corpus Must Not Become a Runner

- Risk: including arm-level workflow results in the corpus would duplicate the
  runner fixture format and bake policy comparisons into the golden set.
- Resolution: the PRD pins one schema,
  `agentic-lead-eval-labeled-set/v1`, with task-level budgets only. The runner
  remains a separate consumer.
Status: resolved

### Finding 2: Evidence Refs Need File Resolution

- Risk: `probe_receipt` strings could become uncheckable assertions.
- Resolution: every `evidence_ref` is required to resolve on disk. Probe
  receipts are represented as cassette or artifact files, not bare labels.
Status: resolved

### Finding 3: Miner Must Pause for Curation

- Risk: an automatic miner could pollute the curated fixture with noisy cases.
- Resolution: the miner writes only to a staging path and refuses the curated
  fixture path. Human curation remains outside the script.
Status: resolved

### Finding 4: Golden Set Must Be Class-Balanced

- Risk: accept-only seed data would optimize the eval toward permissive fan-out.
- Resolution: acceptance requires clean accept, revise, deny, artifact-only, and
  multi-file cases.
Status: resolved

### Finding 5: Avoid Policy and Gate Drift

- Risk: corpus work could accidentally change `agentic_lead_policy` or gate
  behavior.
- Resolution: PRD P5 makes report-only invariants explicit and the TDD plan
  checks that no production policy/default files are touched.
Status: resolved
