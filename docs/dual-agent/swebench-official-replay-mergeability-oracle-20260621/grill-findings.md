### Finding 1

Status: resolved

Official replay could look more real than it is if the oracle adapter is a deterministic local equivalent rather than Docker. The PRD now requires every report to label oracle_adapter_kind and keep all metrics report-only, so a non-Docker adapter cannot travel as official benchmark proof.

### Finding 2

Status: resolved

The highest-risk mistranslation is leaking FAIL_TO_PASS or PASS_TO_PASS into the public packet while constructing official records. The TDD plan starts at the official replay public function and asserts hidden keys are absent from public packets, reviewer packets, and frozen decision rows.

### Finding 3

Status: resolved

Dataset fetching could surprise an operator or test environment. The PRD promise contract now requires explicit opt-in for default dataset loading, while injected deterministic loaders remain available for tests and offline replay.
