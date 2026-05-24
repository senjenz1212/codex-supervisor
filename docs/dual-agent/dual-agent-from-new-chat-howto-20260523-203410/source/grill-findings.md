# Grill Findings

## Finding 1: Do Not Imply Chat Memory Is The Source Of Truth

The new chat must read the supervisor ledger and artifacts, not infer state from prior conversation.

Resolution: the how-to starts with `read_gate_transcript`, `read_outcome`, and `interactions.md`.

## Finding 2: Non-MCP Chats Cannot Continue Gates

Artifact-only review should not be described as a live dual-agent run.

Resolution: the how-to explicitly says non-MCP chats can only review exported artifacts.
