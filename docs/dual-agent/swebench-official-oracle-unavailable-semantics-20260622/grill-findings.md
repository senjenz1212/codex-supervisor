# Grill Findings

### Finding 1

status: resolved

The original failure mode would silently turn harness infrastructure failures into fail/fail oracle labels, corrupting false-accept and true-accept denominators. The PRD now requires unavailable labels, explicit reasons, receipt preservation, and metric suppression or exclusion before any FAR/TAR/FRR number can travel.

### Finding 2

status: resolved

The unavailable path could become too broad and hide real benchmark failures. The PRD and TDD plan keep a valid unresolved official report as fail/pass or fail/fail according to official harness output, which preserves real negative oracle evidence.

### Finding 3

status: resolved

Receipt validation could become permissive after unavailable support. The slice keeps command metadata mandatory and only relaxes return-code and artifact-path expectations for unavailable rows with an explicit unavailable reason.
