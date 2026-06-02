# TDD Grill Findings: Reviewer Route Access Fix

### Finding 1: The first red test must hit invocation, not a helper only

Status: resolved

Evidence: The failure appears at the reviewer invocation boundary: exceptions
from LiteLLM/OpenAI-compatible review are converted into supervisor reviewer
results.

Resolution: `test_structured_litellm_access_denied_classifies_distinctly_without_retry`
uses `invoke_cursor_agent`, not a private classifier alone. Helper detection is
covered through that public invocation result.

### Finding 2: Workflow recovery must be tested separately from classification

Status: resolved

Evidence: A correct cursor-agent classification is not enough if
`run_dual_agent_workflow` still treats it as reviewer-unavailable recovery.

Resolution: `test_reviewer_access_denied_blocks_without_degraded_recovery`
asserts the workflow gate blocks and that no
`dual_agent_reviewer_unavailable_recovery` event is written.

### Finding 3: Prompt compaction must preserve the contract

Status: resolved

Evidence: Compacting too aggressively could make Gemini faster while removing
the typed outcome or critical-review requirements that make the verdict useful.

Resolution: `test_structured_fallback_prompt_compacts_large_receipts` checks both
truncation and retention of the typed outcome contract and critical review text.

### Finding 4: Existing degraded recovery remains a regression target

Status: resolved

Evidence: The product explicitly keeps recovery-to-proceed for genuine
transient both-down reviewer failures.

Resolution: Existing workflow-driver tests for reviewer-unavailable
`proceed_degraded`, default escalation, high-stakes escalation, and real
reviewer rejection stay in the focused test command.
