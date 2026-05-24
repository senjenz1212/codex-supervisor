# PRD: Document Codex / Claude Code Interactions

## Problem

The dual-agent artifact folder contains `transcript.md`, but that file is a raw ledger projection. It is useful for audit detail but not ideal for an operator who wants to quickly understand what Codex asked, what Claude Code answered, where they disagreed, and how each gate resolved.

## Goal

Every dual-agent artifact export should include a readable `interactions.md` file that documents the Codex / Claude Code interaction sequence.

## Acceptance Criteria

- Artifact exports include `interactions.md`.
- The index links to `interactions.md`.
- Round events show Codex decision, Claude Code decision, confidences, and objections.
- Gate result events show Claude Code outcome summary, decisions, specialists, objections, validation probes, and artifact rigor.
- Skill docs tell Codex to point operators at `interactions.md`.
