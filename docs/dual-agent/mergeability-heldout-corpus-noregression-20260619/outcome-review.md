# Outcome Review Gate

## event_id: 820414

- ts: `1781918745`
- kind: `supervisor_policy_overlay_snapshot`
- gate: `outcome_review`
- status: `None`
- attempts: `None`
- handoff_packet_path: `None`

### Supervisor Block

Claude Code was not invoked.

- reason: `unknown`

### Probes

- None recorded.

### Artifact Rigor

- None recorded.

## event_id: 820415

- ts: `1781918745`
- kind: `supervisor_lesson_injection`
- gate: `outcome_review`
- status: `None`
- attempts: `None`
- handoff_packet_path: `None`

### Supervisor Block

Claude Code was not invoked.

- reason: `unknown`

### Probes

- None recorded.

### Artifact Rigor

- None recorded.

## event_id: 820416

- ts: `1781918745`
- kind: `dual_agent_gate_result`
- gate: `outcome_review`
- status: `blocked`
- attempts: `0`
- handoff_packet_path: `None`

### Supervisor Block

Claude Code was not invoked.

- reason: `required_artifacts_missing`
- claude_gate_status: `not_invoked`

### Probes

- None recorded.

### Artifact Rigor

- status: `blocked`
- reason: `required_artifacts_missing`
- artifact_policy: `strict`
- required_artifacts: `prd`, `tdd_plan`, `grill_findings`, `issues`, `implementation_plan`
- present_artifacts: `grill_findings`, `implementation_plan`, `issues`, `prd`, `tdd_plan`
- missing_artifacts: `screenshots`
- missing_artifact_paths: `[]`
- required_prerequisite_gates: `execution`
- accepted_prerequisite_gates: `execution`
- missing_prerequisite_gates: `[]`
- gate_statuses: `{"execution": "accepted", "implementation_plan": "accepted", "issues_review": "accepted", "prd_review": "accepted", "tdd_review": "accepted"}`
- user_facing: `True`
- screenshots: `[]`
- missing_screenshot_paths: `[]`
- visual_validation: `{"allowed_sources": ["browser", "browser-use", "browser_use", "computer", "computer-use", "computer_use"], "evidence": [], "failures": [], "reason": "no_visual_evidence", "status": "blocked"}`

### Trace Envelope

- policy_verdict: `blocked`
- failure_category: `system_design`
- failure_subcategory: `artifact_quality`
- failure_code: `required_artifacts_missing`
- mast_code: `FM-1.1`
- mast_mode: `Disobey task specification`
- mast_category: `Specification Issues`
