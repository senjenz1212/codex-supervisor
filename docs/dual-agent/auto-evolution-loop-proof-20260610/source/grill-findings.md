# PRD Grill Findings

### Finding P1: The proof must exercise the real operator surface.

Status: resolved

- Risk: A helper-only proof could bypass AXI activation or approval semantics.
- Resolution: The PRD requires AXI for both human touchpoints and treats helper-only activation as forbidden.

### Finding P2: The daemon runner must be part of the proof.

Status: resolved

- Risk: Inline fixture execution would not prove the live runner cadence.
- Resolution: The proof uses `AutoResearchRunnerTask.tick_once` and verifies evaluator_execution provenance.

### Finding P3: Rollback must remain draft-only.

Status: resolved

- Risk: A complete loop proof could accidentally normalize automatic policy mutation.
- Resolution: The PRD explicitly requires rollback proposal drafting without automatic apply.

### Finding P4: Documentation must be evidence-backed.

Status: resolved

- Risk: `docs/LOOP.md` could become aspirational text.
- Resolution: The doc must reference the generated manifest and tests must verify the artifact references.
