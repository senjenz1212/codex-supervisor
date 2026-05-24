# Grill Findings

## Finding 1: Transcript Is Too Raw For Operator Review

`transcript.md` exposes ledger details but makes operators scan event payloads to understand the interaction.

Resolution: add `interactions.md` as the readable projection.

## Finding 2: Do Not Replace The Ledger

A readable interaction projection must not become a second truth source.

Resolution: `interactions.md` is generated from the supervisor SQLite ledger and explicitly says it is a projection.
