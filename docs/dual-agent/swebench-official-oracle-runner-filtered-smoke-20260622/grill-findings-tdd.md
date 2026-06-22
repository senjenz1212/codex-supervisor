### Finding 1

Status: resolved

Concern: A helper-only test could pass while the CLI still fails to pass oracle_runner into the executable replay path.

Resolution: The first two TDD cases invoke the official replay CLI boundary and assert failure without adapter plus successful fake-runner report writing.

### Finding 2

Status: resolved

Concern: Filtering tests could validate only list slicing and miss the prediction-coverage ordering bug.

Resolution: The TDD plan requires a selected-instance case where non-selected rows lack predictions and must not fail coverage.

### Finding 3

Status: resolved

Concern: Receipt tests could prove receipt shape without proving oracle isolation ordering.

Resolution: The TDD plan combines frozen_decisions_path ordering with hidden-field leak checks across public artifacts.
