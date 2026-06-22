### Finding 1

Status: resolved

Question: Does the TDD plan begin at the operator-visible report boundary?

Resolution: Yes. The first test calls the diagnostic runner with official-shaped inputs and checks the persisted report shape, rather than asserting private helper internals.

### Finding 2

Status: resolved

Question: Are unavailable paths tested separately from quality reject paths?

Resolution: Yes. The full roster happy path permits a quality reject for the bad candidate while the S_full-unavailable test covers missing reviewer infrastructure as a separate blocked state.

### Finding 3

Status: resolved

Question: Do tests prove that a populated all-arms report is still non-applyable?

Resolution: Yes. The completed diagnostic test asserts every policy and improvement flag remains false, and the unavailable-path tests repeat the same no-claim invariant.

### Finding 4

Status: resolved

Question: Does the TDD plan prove hidden leak blocking, not only the clean isolation path?

Resolution: Yes. The hidden-field regression uses a forbidden SWE-bench marker in a public candidate identifier and asserts the diagnostic becomes unavailable with hidden_field_leak_detected.
