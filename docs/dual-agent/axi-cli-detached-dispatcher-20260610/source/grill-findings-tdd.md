# TDD Grill Findings: Detached Dispatcher And AXI CLI

### Finding T1: Poll tests must fail on hidden dispatcher calls

Status: resolved

Risk: A latency assertion could pass even if poll still constructs a dispatcher. Resolution: tests monkeypatch the old dispatcher seam to raise and assert request files and pid remain absent after poll.

### Finding T2: AXI tests must hit the process boundary

Status: resolved

Risk: Helper-only formatter tests would not prove argparse, exit code, stdout, or console script behavior. Resolution: AXI tests invoke `main(argv)`, capture stdout, parse JSON, and read the package script registration.

### Finding T3: Dispatcher recovery must remain covered

Status: resolved

Risk: Removing poll-side recovery could strand result files. Resolution: dispatcher reaper tests and result-file recovery tests prove terminal outcome is persisted by dispatcher-owned paths before poll replays it.

### Finding T4: Idempotency and provenance need ledger assertions

Status: resolved

Risk: CLI output could look correct while storing unsafe request payloads. Resolution: AXI tests open the SQLite state and inspect the stored request JSON for downgraded forged receipts and one-row token deduplication.

### Finding T5: Documentation cannot be a vague note

Status: resolved

Risk: Operators need the exact daemon command and launchd path. Resolution: `docs/supervisor-axi-detached-dispatcher.md` names the dispatcher command, plist path, and phase ownership split.
