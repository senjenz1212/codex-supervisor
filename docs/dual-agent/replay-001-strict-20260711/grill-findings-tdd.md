# TDD Grill Findings: REPLAY-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. Negative tests cover missing and unknown schema declarations.
2. The recorded-snapshot test mutates the live tree and proves the historical
   result is unchanged.
3. Manifest tests require resolved models and hashed component categories
   before reporting completeness.
4. Missing-checkout behavior is tested as incompatible rather than falling
   back.
5. External artifact availability and third-party service replay remain
   operational evidence, not unit-test claims.
