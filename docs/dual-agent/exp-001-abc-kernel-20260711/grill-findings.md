# PRD Grill Findings: EXP-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. **Accepted:** B versus C is primary because it isolates harness structure
   from extra compute.
2. **Accepted:** one unique task is the experimental unit; attempts are not
   observations.
3. **Accepted:** assignment is persisted before execution and retries remain
   inside the assigned arm.
4. **Accepted:** treatment-specific failures remain intention-to-treat
   failures.
5. **Accepted:** verification occurs on a recursively blinded frozen result.
6. **Residual gap:** kernel correctness is not evidence that B improves real
   task outcomes.
