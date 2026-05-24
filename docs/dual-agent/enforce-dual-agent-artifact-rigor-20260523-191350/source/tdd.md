# TDD Plan: Enforce Dual-Agent Artifact Rigor

## Public Boundary

The public boundary is the Codex-facing MCP tool `start_dual_agent_gate`.

## Tests

- Strict outcome-review gate without planning artifacts returns `blocked`, does not launch Claude Code, writes a ledger result, and exports artifacts.
- Strict outcome-review gate with required planning artifacts runs and returns accepted.
- User-facing strict gate without screenshots blocks.
- User-facing strict gate with screenshots runs and copies screenshots into the artifact folder.
- Exported Markdown includes `artifact_rigor` details for blocked runs.
- Skill documentation names `artifact_policy="strict"`, `user_facing=True`, and `required_artifacts_missing`.

## Verification

- Focused tests for MCP, artifacts, and skill docs.
- Full pytest suite.
- Compile check and whitespace check.
- Live dual-agent outcome-review gate after the code tests pass.
