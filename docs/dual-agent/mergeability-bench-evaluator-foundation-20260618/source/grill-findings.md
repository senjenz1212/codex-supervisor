# PRD Grill Findings

### Finding 1: Keep Human-Like Mergeability Secondary

Status: resolved

The PRD initially risked blending deterministic mergeability checks with maintainer-style review. That would reintroduce judge circularity before the bench has a stable oracle. The solution makes hidden tests, reverse tests, scope checks, and lint or build commands primary blockers while recording weighted rubric fields as secondary evidence only.

Resolution: incorporated in P2, P3, and the Implementation Decisions section.

### Finding 2: Make Calibration Controls Mandatory

Status: resolved

The bench is only useful if it can distinguish no-op, harmful, and known-good candidates. Without this, AutoResearch could optimize for a saturated all-pass metric and still satisfy report mechanics. The PRD now requires calibration controls and names them as part of P4.

Resolution: incorporated in P4 and Testing Decisions.

### Finding 3: Avoid External Benchmark Claims

Status: resolved

The slice could accidentally overclaim by referencing FrontierCode or public SWE-style benchmarks as if they were wired locally. The PRD limits implementation to a local fixture corpus and explicitly leaves powered studies and external benchmark execution out of scope.

Resolution: incorporated in Solution, Implementation Decisions, and Out of Scope.
