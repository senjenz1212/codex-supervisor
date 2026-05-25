# TDD Plan: Dual-Agent Fast Triage Artifacts

## RED 1 - Triage Export

Boundary: `export_dual_agent_run_artifacts`
Test file: `tests/test_dual_agent_artifacts.py`

Behavior:

- build a blocked outcome-review event with MAST taxonomy and claim failures
- export artifacts
- assert `triage.md` exists and names the blocker, status split, event id,
  next safe action, and top tool calls

## RED 2 - Workspace Snapshot Manifest

Boundary: `export_dual_agent_run_artifacts`
Test file: `tests/test_dual_agent_artifacts.py`

Behavior:

- create a temporary git workspace with a handoff packet and source artifacts
- export artifacts
- assert `replay/manifest.json` contains current HEAD, git status, diff hash,
  file-tree hash, and source artifact hashes

## RED 3 - Tool Call Forensics

Boundary: `run_dual_agent_gate`
Test file: `tests/test_dual_agent_runner.py`

Behavior:

- run fixture-backed direct gate
- assert trace-envelope tool calls include timing plus safe `args` and
  `result_summary`

## RED 4 - Exception Metadata

Boundary: `timed_tool_call`
Test file: `tests/test_failure_taxonomy.py`

Behavior:

- raise inside `timed_tool_call`
- assert `error.type` and `error.message` are recorded before re-raise

## RED 5 - Live Probe Status Split

Boundary: `scripts/probe_live_failure_mode.py`
Test file: live probe refresh plus fixture summary inspection

Behavior:

- rerun live tri-agent probe
- assert summary and transcript expose `claude_gate_status=accepted` and
  `supervisor_final_status=blocked`

## RED 6 - Timeout Primary Failure

Boundary: `scripts/probe_live_failure_mode.py`
Test file: `tests/test_dual_agent_live_lead_fixture.py`

Behavior:

- build a blocked gate result with `P2/P3 lead_invocation_timeout` and no
  outcome
- assert final classification chooses `P2/lead_invocation_timeout`
- assert the expected P11 receipt-block predicate remains false
