# TDD Grill Findings

## Finding 1: The first test could drift into helper-only validation.

Resolution: RED 1 must call the fixture measurement runner/report interface and assert exported report behavior. Manifest helper tests may follow only after the public boundary is covered.

## Finding 2: More positives could hide false-accept regression.

Resolution: RED 3 and RED 4 require retained negative controls, false-accept traps, separate denominators, and S_probe/S_full false-accept counts.

## Finding 3: Calibration could accidentally become promotion evidence.

Resolution: RED 5 explicitly guards all report-only flags and forbids best-of-K or held-out improvement labeling from this fixture-only slice.

## Finding 4: Oracle isolation could be assumed from old candidates.

Resolution: RED 2 rebuilds public packets or worktrees for the grown corpus and checks the hidden-oracle exclusion boundary directly.
