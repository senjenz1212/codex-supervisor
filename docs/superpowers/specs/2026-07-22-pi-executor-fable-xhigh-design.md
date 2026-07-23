# Pi as Default Implementor Executor + Fable Lead at xhigh — Design

Date: 2026-07-22
Status: approved (design), pending implementation plan

## Goal

1. Add the pi coding agent (`@mariozechner/pi-coding-agent`) as a first-class
   implementor/executor runtime and make it the default delegated executor for
   the dual-agent workflow. Codex remains fully supported and selectable via
   config; it is not deprecated.
2. Run implementation work on `claude-fable-5` at `xhigh` reasoning:
   - pi executor tasks default to model `claude-fable-5` with pi thinking
     level `xhigh`;
   - the Claude lead's effort becomes threadable end-to-end, with the
     high-effort-gate default raised from `high` to `xhigh`.

## Background (verified against the repo)

- Implementor/executor CLIs are `CommandAgentRuntime` subclasses in
  `supervisor/agent_runtime.py`; only `ClaudeCodeRuntime` (line 873) and
  `CodexRuntime` (line 956) exist. `cursor_agent.py` is a reviewer adapter,
  not an executor.
- The dual-agent composition root hardcodes the Codex executor:
  `mcp_tools/codex_supervisor_stdio.py` (~line 530) builds
  `codex_runtime_factory = lambda: CodexRuntime(binary=codex_cli)`.
- `supervisor/provider_boundaries.py` is the enforced allowlist:
  `PROVIDER_EXECUTABLES` (line 63), `PROVIDER_RUNTIME_CONSTRUCTORS`
  (lines 65-69), `PROVIDER_EDGE_ALLOWLIST` (lines 14-43). A static boundary
  test fails if a module launches a provider binary without registration.
- Lead effort today: `select_lead_effort` (`supervisor/dual_agent_lead.py:271`)
  returns explicit effort if given, else `high` for `HIGH_EFFORT_GATES`, else
  `medium`. Nothing populates `LeadInvocationRequest.effort` (line 130):
  `DualAgentGateSpec` (`supervisor/dual_agent_runner.py:128`) has only
  `quality`/`model`, and the `start_dual_agent_gate` MCP tool exposes no
  effort parameter. Downstream, `--effort` (CLI) and `effort=` (SDK) pass the
  string through unvalidated, so `xhigh`/`max` are not clamped anywhere in
  this repo.
- `claude-fable-5` is already `DEFAULT_ANTHROPIC_MODEL`
  (`supervisor/provider_routing.py:9`).
- Codex executor already defaults to `reasoning_effort="xhigh"`
  (`mcp_tools/codex_supervisor_stdio.py`, `DEFAULT_CODEX_REASONING_EFFORT`).
  Pi at xhigh is symmetric with this.

## Pi CLI facts the design relies on

- Print mode `-p`, JSONL event stream `--mode json` (events: `agent_start/end`,
  `turn_start/end`, `message_start/update/end`, `tool_execution_start/update/
  end`; session header carries the session id).
- `--model <pattern>` accepts `provider/id` and a thinking suffix
  (`anthropic/claude-fable-5:xhigh`); `--thinking` accepts
  `off|minimal|low|medium|high|xhigh|max`.
- Session resume via `--session <path|id>`; `--no-session` for ephemeral runs.
- Tool restriction via `--tools <list>` (built-ins: `read`, `bash`, `edit`,
  `write`, `grep`, `find`, `ls`). No OS-level sandbox flag.
- Auth via `ANTHROPIC_API_KEY` env.

## Design

### 1. Executor selection (pi default, codex optional)

- New config block in `supervisor/config.py`:

  ```yaml
  executor:
    kind: pi          # pi | codex; default pi
    pi_cli_command: pi
    # optional search paths, mirroring target.codex.cli_search_paths
  ```

- The composition root (`mcp_tools/codex_supervisor_stdio.py`) builds an
  `executor_runtime_factory` from `executor.kind`:
  - `pi` → `PiRuntime(binary=pi_cli)`
  - `codex` → `CodexRuntime(binary=codex_cli)` (existing behavior)
- Existing `codex_runtime_factory` / `codex_runtime_runner` injection
  parameters are preserved for tests and replay; when injected they win.
- Switching back to codex is one config line (`executor.kind: codex`).

### 2. `PiRuntime(CommandAgentRuntime)` in `supervisor/agent_runtime.py`

- `kind = "pi"`, `binary = "pi"`; capabilities: resume/cancel/stream true,
  cost_reporting false (revisit once pi usage events are confirmed),
  subagents false, images true.
- `_start_argv`:
  `pi -p --mode json --model anthropic/<task.model> --thinking <level>
  <instruction>`, with `--tools read,grep,find,ls` appended when
  `metadata["read_only_review"]` is set (excludes `bash`/`edit`/`write`).
- Thinking level source: `metadata["reasoning_effort"]`, falling back to
  `metadata["effort"]`, defaulting to `xhigh`. Reuses the existing
  `_validated_reasoning_effort` charset check.
- `_resume_argv`: `pi --session <session_id> -p --mode json <instruction>`;
  session id captured from pi's JSONL session header in `stream`/`collect`.
- `_runtime_env`: allowlisted env (`_PI_ENV_KEYS`: `ANTHROPIC_API_KEY`,
  `HOME`, `PATH`, `TMPDIR`, containment id, plus whatever `_CODEX_ENV_KEYS`
  patterns apply), mirroring the Codex approach.
- `_route_identity_manifest`: provider `anthropic`, route_kind `pi_cli`,
  endpoint `pi-cli-configured-route`, plus a `sandbox` posture field (see §3)
  so receipts record pi's weaker containment.
- Event parsing: extend `normalize_runtime_event`
  (`supervisor/agent_runtime.py:1753`) to map pi's event vocabulary onto
  `RuntimeEvent`s; final assistant output comes from the `agent_end` messages
  array.
- Executor task defaults when `executor.kind == "pi"`: model
  `claude-fable-5`, thinking `xhigh`.

### 3. Sandbox posture (decision: match existing posture, no OS sandbox now)

- Read-only tasks: `--tools read,grep,find,ls` approximates Codex
  `--sandbox read-only`.
- Write tasks under a filesystem-isolation policy: rely on worktree cwd
  isolation (existing `_filesystem_isolation_policy` machinery decides
  applicability); pi has no `workspace-write` equivalent, so the blast radius
  is the worktree, enforced by convention rather than the OS.
- `process_containment.py` (cooperative process-tree containment) applies to
  pi like every other runtime, via the containment env id.
- The provenance manifest records `sandbox: "tools-allowlist"` /
  `"worktree-only"` / `"none"` so evidence reviews can see the posture.
- Out of scope (explicit non-goal for this slice): OS-level sandboxing
  (e.g. a macOS seatbelt/`sandbox-exec` argv wrapper). Argv construction is
  centralized in the transport, so this can be added later without redesign.

### 4. Lead fable at xhigh

- Thread `effort` end-to-end:
  - add `effort: ClaudeEffort | None = None` to `DualAgentGateSpec`
    (`supervisor/dual_agent_runner.py:128`);
  - pass it in `_lead_request` (~line 1943) into
    `LeadInvocationRequest(effort=...)`;
  - expose `effort` as an optional parameter on the `start_dual_agent_gate`
    MCP tool (both definitions in `mcp_tools/codex_supervisor_stdio.py`).
- Raise the high-effort-gate default: `select_lead_effort` returns `xhigh`
  (instead of `high`) for `HIGH_EFFORT_GATES`. Explicit effort still wins, so
  `max` (already in `ClaudeEffort`) remains reachable per-gate. `cheap`
  quality still maps to `low`; non-high-effort gates keep `medium`.

### 5. Provider boundaries, tests

- `supervisor/provider_boundaries.py`: add `"pi"` to `PROVIDER_EXECUTABLES`;
  add `"PiRuntime": "pi"` to `PROVIDER_RUNTIME_CONSTRUCTORS`. No SDK root
  (pure CLI integration; no pyproject change).
- Tests:
  - `tests/test_agent_runtime.py`-style suite for `PiRuntime`: start/resume
    argv construction (model/thinking flags, read-only tools flag), env
    allowlist, session-id capture, event normalization, route identity
    manifest completeness;
  - provider-boundary test expectations updated for `pi`;
  - executor-selection tests: config default `pi`, `kind: codex` fallback,
    injected factories still win;
  - lead-effort tests: spec→request threading, MCP parameter, new `xhigh`
    default for high-effort gates, explicit `max` override.

### 6. Prerequisites / live checks before implementation completes

- `npm i -g @mariozechner/pi-coding-agent` (pi is not installed on this host).
- `pi --models` includes `claude-fable-5` under the anthropic provider (else
  pin the full `anthropic/claude-fable-5` id and verify).
- One live `pi -p --mode json --model anthropic/claude-fable-5 --thinking
  xhigh` smoke (same argv shape as §2) to capture the real event stream
  (fixture for normalization tests).
- Claude CLI accepts `--effort xhigh` for fable (the historical
  `400 invalid effort level 'xhigh'` note in
  `docs/dual-agent/agentic-lead-executor-wiring-20260531/tdd.md:281` was the
  OpenAI/Codex reasoning API, not Claude — must be confirmed live).

## Non-goals

- Deprecating or removing the Codex executor.
- Pi as a reviewer (that is the `cursor_agent.py` seam; separate work).
- OS-level sandboxing for pi.
- Changing reviewer models/modes or supervisor decision models.

## Risks

- Pi's JSONL schema may drift between versions; the live-smoke fixture pins
  the version we integrate against (record the pi version in the manifest).
- `--effort xhigh`/fable acceptance by the Claude CLI is unverified until the
  live check; if rejected, the lead default stays `high` and the threading
  change still lands (explicit efforts become possible when supported).
- Pi write-task containment is weaker than Codex `workspace-write`; mitigated
  by worktree isolation and honest provenance labeling (§3).
