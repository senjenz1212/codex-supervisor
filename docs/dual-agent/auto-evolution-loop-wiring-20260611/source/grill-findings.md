# PRD Grill Findings

### Finding G1: Autonomy Must Stop At Draft

Status: resolved

- Risk: Wiring the loop could accidentally self-activate experiments or self-apply policy.
- Resolution: Every autonomous path writes draft/report-only output. Activation and proposal decisions remain explicit operator actions.

### Finding G2: Request Paths Must Stay Non-Executing

Status: resolved

- Risk: Adding AXI/MCP verbs could reintroduce the transport timeout class by running phases inline.
- Resolution: New verbs call ledger APIs only. A guard test monkeypatches dispatcher and spawn seams.

### Finding G3: Daemon Cadence Needs A Testable Seam

Status: resolved

- Risk: A launchd-only implementation would be difficult to verify in unit tests.
- Resolution: Daemon tasks expose `tick_once` and are registered in `daemon.py` as restartable subsystems.

### Finding G4: Derive-On-Acceptance Needs A Narrow Trigger

Status: resolved

- Risk: Calling derivation for every report creates noisy skipped events and hides real proposal candidates.
- Resolution: The orchestrator calls derivation only when the report contains a derivable accepted record.

### Finding G5: Lessons Cannot Become Gate Authority

Status: resolved

- Risk: Lesson injection and feedback could be mistaken for acceptance evidence.
- Resolution: Lesson records remain advisory. Gates, typed outcomes, runtime evidence, and reviewers remain authoritative.

### Finding G6: Weekly Audit Must Be Observational

Status: resolved

- Risk: A trend audit could accidentally block or mutate policy.
- Resolution: Weekly P11 audit rows are read-only observations and never gate workflow advancement.

## Resolution Status

All findings are incorporated into the issues and TDD plan. No finding is waived.
