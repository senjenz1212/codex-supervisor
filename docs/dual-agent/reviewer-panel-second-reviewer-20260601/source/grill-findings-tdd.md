# TDD Grill Findings: Reviewer Panel Second Reviewer

### Finding 1: Tests must not call live Codex by default

Concern: Adding a Codex CLI reviewer could make normal unit tests depend on
ambient credentials and live model access.

Resolution: TDD requires fake runner injection for the Codex reviewer. Live
route proof stays in route-evidence artifacts; deterministic tests use cassettes
or fake JSONL.

Status: resolved

### Finding 2: Workflow tests must hit the public boundary

Concern: Helper-only registry tests would miss whether `run_dual_agent_workflow`
actually runs both reviewers and feeds both into the conservative panel.

Resolution: TDD includes public-boundary workflow tests for two accepts, second
reviewer revise, and second reviewer outage.

Status: resolved

### Finding 3: Degraded outage test must keep missing verdict visible

Concern: A proceed-degraded test could pass while hiding the unavailable
reviewer from panel metadata.

Resolution: TDD requires the outage test to assert recovery metadata and the
panel's missing/unavailable reviewer evidence.

Status: resolved
