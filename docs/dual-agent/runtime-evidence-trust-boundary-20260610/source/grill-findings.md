# PRD Grill Findings

### Finding 1

Status: resolved

Concern: The first version could be read as trusting `source="supervisor"` whenever the receipt dictionary contained that string. That would preserve the exact forgeability problem.

Resolution: The PRD now requires current in-process runtime receipt ids to be passed into verification. Caller provenance is downgraded before merge and forged internal markers are stripped unless the receipt id comes from the current runtime evidence result.

### Finding 2

Status: resolved

Concern: Test confinement could accidentally become a broad command runner if `python -m anything` or package-manager scripts were accepted.

Resolution: The PRD limits execution to pytest module invocations and narrow make-test targets, rejects shell metacharacters, and records non-allowlisted declarations as failed runtime evidence instead of executing them.

### Finding 3

Status: resolved

Concern: A broken validation environment could make the runtime floor look green by falling back to changed-file checks alone.

Resolution: The PRD names `runtime_test_environment_unavailable` as a blocking failure. The TDD plan includes a direct missing-pytest simulation so this cannot regress silently.
