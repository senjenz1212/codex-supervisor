# Issues

Task id: `pro-runscripts-coverage-20260626`

## Issue 1: Add Pro Run-Script Preflight

PRD promise: P1, P2.

What to build: a public preflight that accepts selected SWE-bench Pro
`instance_id` values and checks each has `run_script.sh` and `parser.py` under
the resolved scripts root.

Acceptance criteria:

- Missing instances are reported with every missing `instance_id`.
- Present instances return resolved script paths and hash evidence.
- No generic script content is generated.

## Issue 2: Wire Batch Official Replay Fail-Fast

PRD promise: P2.

What to build: run the preflight inside official replay after selection and
before replay/oracle execution whenever Pro scripts are configured or the Pro
oracle adapter is used.

Acceptance criteria:

- Multiple missing selected IDs are reported in one failure.
- Materializer and oracle runner are not called when preflight fails.
- `SWEBENCH_PRO_ORACLE_SCRIPTS_DIR` is treated as the authoritative scripts
  root.

## Issue 3: Preserve Script Provenance

PRD promise: P1.

What to build: record the source script paths, byte counts, and sha256 values
in the Pro adapter receipt.

Acceptance criteria:

- Adapter tests prove workspace scripts are copied verbatim from source.
- Receipts contain source script hash evidence.
- Missing scripts still return unavailable before Docker.

