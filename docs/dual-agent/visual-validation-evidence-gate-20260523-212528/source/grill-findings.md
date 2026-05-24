# Grill Findings: Visual Validation Evidence Gate

## G1: Screenshot Existence Is Not Evidence

Finding: Accepting any existing screenshot path still lets user-facing review pass on a fake or unrelated file.

Resolution: `_valid_screenshot_paths` now checks that the file has a known image header. `_visual_validation_evidence` separately requires capture source and passed review metadata.

## G2: Provenance Alone Is Not Review

Finding: A screenshot with `source=computer_use` does not prove Codex inspected it against acceptance criteria.

Resolution: User-facing gates require a passed validation status. Artifact export includes validation notes so the operator can audit what was reviewed.

## G3: Supervisor Cannot Prove Tool Origin Cryptographically

Finding: The supervisor cannot currently verify that Browser or Computer Use produced the file rather than Codex hand-writing metadata.

Resolution: Treat this as an enforced evidence contract, not a signed attestation system. The gate blocks missing/invalid evidence, and the limitation remains documented.

## G4: Non-UI Work Must Not Inherit UI Burden

Finding: Requiring visual evidence for every outcome-review gate would slow non-UI changes and create false blocks.

Resolution: Visual validation is required only when `user_facing=True`.
