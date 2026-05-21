# Claude Supervisor Telegram TDD Grill Findings

## Status

All findings are resolved in the issue pack and current tests.

## Findings

### CT1. Telegram Chat Tests Must Start at Ingress

Finding: Testing only the Claude runtime adapter would miss Telegram routing,
authorized chat checks, and response delivery.

Resolution: The first Telegram chat test calls the poller update boundary and
asserts non-slash text reaches the supervisor chat handler and sends a reply.

### CT2. Resolver Tests Must Prove No Slack Default

Finding: A resolver helper could pass while still encoding Slack-specific
assumptions.

Resolution: The Vela resolver test states that "Vela chat bot" is a Codex
session and resolves from local rollout/run data.

### CT3. Tool API Tests Must Verify Redaction

Finding: Claude supervisor tools could accidentally expose raw hook payloads or
secrets even if storage redaction exists.

Resolution: The supervisor tool API test reads events, hooks, and actions and
asserts secret material is absent from the full returned blob.

### CT4. Replay Tests Must Tripwire Live I/O

Finding: Supervisor-turn replay could silently call live Telegram, target, or
model APIs.

Resolution: The replay test patches httpx and subprocess below the boundary and
uses frozen tool/model outputs.

### CT5. Mutating Natural Language Needs Separate Action Tests

Finding: A chat response test is not enough to prove mode-safe execution.

Resolution: Mode-safe action execution remains covered by the existing
`action_executor` tests. Telegram chat may propose actions, but execution stays
behind that boundary.

Follow-up closure: `test_telegram_steer_request_creates_approval_action_not_direct_injection`
now starts at Telegram chat ingress and proves a steer request creates a
pending approval action, while `test_execute_action_inject_steering_runs_version_gated_resume`
proves approved delivery happens through `CodexAdapter`.
The TDD plan also records that mode changes are deferred and no
`request_mode_change` tool is exposed.
