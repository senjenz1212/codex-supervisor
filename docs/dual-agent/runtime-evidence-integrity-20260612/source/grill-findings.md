# PRD Grill Findings

### Finding 1: Baseline persistence needs visible fallback evidence

Status: resolved

Risk: Swallowing marker write failure could make an `outcome_review` fallback look like normal persisted-baseline behavior.

Resolution: Require the fallback reason `fresh_fallback_no_persisted_execution_baseline` in runtime baseline receipts.

### Finding 2: TDD coverage must use supervisor execution receipts

Status: resolved

Risk: A dynamic agent can claim "tests passed" while omitting a TDD-named test.

Resolution: Add a runtime-native TDD coverage receipt that resolves TDD names and compares them to executed pytest nodeids.

### Finding 3: Operator trend output must show era data by default

Status: resolved

Risk: CLI migration decisions remain data-less if era data exists only inside hidden JSON details.

Resolution: Add era counts and rates to trend rows and include `transport_incident_by_era` in default TOON-lite trend fields.

### Finding 4: Parity tests must touch Postgres behavior

Status: resolved

Risk: JSON detail behavior can pass in SQLite while failing in the Postgres production lane.

Resolution: Add a Postgres lane parity test for trend detail roundtrip and incident aggregation.

