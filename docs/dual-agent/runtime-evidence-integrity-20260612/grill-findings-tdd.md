# TDD Grill Findings

## Findings

### TG1. The TDD floor must include an unresolved-name test

- Risk: A missing-test-only test can still silently skip ambiguous or typoed names.
- Resolution: Added `test_runtime_evidence_fails_when_tdd_test_name_is_unresolved`.

### TG2. The era output test must cover default and JSON formats

- Risk: A JSON-only assertion would leave TOON-lite operator output blind.
- Resolution: `test_axi_trends_surfaces_by_era_in_json_and_toon` covers both paths.

### TG3. Format metrics must be emitted by the live default output path

- Risk: Existing seeded metric tests prove aggregation, not emission.
- Resolution: `test_axi_toon_poll_records_format_metric` invokes AXI poll without `--json`.

### TG4. Public-boundary poll failure must cover both ingress surfaces

- Risk: AXI-only coverage can leave MCP shim behavior stale.
- Resolution: `test_transport_incident_events_are_written_from_public_axi_and_mcp_poll_failure_boundaries` covers both AXI and MCP missing-job polling.

## Translation Audit

- Every PRD promise P1-P6 has an issue claimant.
- Every issue names a first public-boundary RED test.
- The first tests touch runtime/workflow/CLI/MCP/Postgres boundaries before private helpers.
- Forbidden outcomes from the PRD are represented in the test plan.
- No live model, live Cursor, Telegram, or network dependency is required for local RED/GREEN tests.

