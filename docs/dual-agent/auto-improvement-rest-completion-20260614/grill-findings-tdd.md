# TDD Grill Findings

## T1: Queue drain must be tested through AXI
Resolved: the first test is an AXI command test, not a helper-only state update.

## T2: Overlay scope must be observed through composed lead instructions
Resolved: the overlay tests call `_workflow_gate_start_kwargs`, the same boundary used at gate start.

## T3: Empty-floor checks must preserve report-only invariants
Resolved: tests require non-applyable proposals to avoid policy mutation and gate advancement.

## T4: Trend decision status must not encode a product decision
Resolved: tests assert `insufficient_data`, not "CLI wins" or "TOON wins".

