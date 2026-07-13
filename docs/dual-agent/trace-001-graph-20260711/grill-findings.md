# PRD Grill Findings: TRACE-001

This is a retrospective integration trace, not a synthetic skill receipt.

1. **Accepted:** logical IDs need namespaces; bare reused IDs such as `P1` are
   ambiguous.
2. **Accepted:** revision hash and instance identity are separate concepts.
3. **Accepted:** closure must follow the canonical objective-to-promotion
   chain, not accept any graph path.
4. **Accepted:** waivers are signed, exact, and expiring.
5. **Residual gap:** a graph can be internally valid while a producer fails to
   emit nodes; TRACER-001 is the integration guard for that failure mode.
