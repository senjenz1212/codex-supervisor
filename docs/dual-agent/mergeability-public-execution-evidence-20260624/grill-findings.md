# PRD Grill Findings

## Finding 1: Do not add a second execution path

Status: resolved.

The PRD could have implied rerunning tests inside the reviewer packet builder. That would risk drift between S_probe and the reviewer evidence. The implementation must reuse existing public review receipts and patch-apply receipts.

## Finding 2: Avoid naming forbidden oracle keys inside "excluded material" lists

Status: resolved.

The SWE-bench leak scanner treats forbidden key names as leaks even if they appear in a list of excluded fields. The evidence block must describe excluded categories without spelling hidden oracle keys such as the held-out test-set field names.

## Finding 3: Keep fixture evidence labeled as calibration

Status: resolved.

The new evidence block improves auditability but does not make fixture results a real-world improvement claim.

