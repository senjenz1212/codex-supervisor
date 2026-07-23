# Pi Default Executor + Fable Lead at xhigh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `PiRuntime` executor adapter for the pi coding agent, make it the default delegated executor (running `claude-fable-5` at `xhigh` thinking), keep Codex selectable via config, and thread an explicit `effort` through the dual-agent lead path with the high-effort-gate default raised to `xhigh`.

**Architecture:** `PiRuntime` subclasses `CommandAgentRuntime` (same seam as `CodexRuntime`). A new `executor:` config block selects the executor at the `CodexSupervisorMcpAPI` composition root; injected codex fakes keep executor=codex for back-compat. Lead effort threads `DualAgentGateSpec` → `_lead_request` → `LeadInvocationRequest` and surfaces on the `start_dual_agent_gate` MCP tool.

**Tech Stack:** Python 3.12, pydantic config models, pytest (`.venv/bin/pytest` — bare `pytest` is NOT on PATH in this repo), pi CLI (`@mariozechner/pi-coding-agent`, JSONL event stream).

**Spec:** `docs/superpowers/specs/2026-07-22-pi-executor-fable-xhigh-design.md`

**Repo:** `/Users/sam.zhang/Documents/codex-supervisor` (run all commands from the repo root; branch `codex/harness-v1-evidence-kernel-20260712`).

**Line numbers** below were verified on 2026-07-22. If an anchor drifted, locate by the quoted symbol name, not the number.

---

### Task 1: `PiRuntime` argv/env/manifest + provider boundary registration

**Files:**
- Modify: `supervisor/agent_runtime.py` (env keys near line 120; new class after `CodexRuntime`, which ends near line 1057)
- Modify: `supervisor/provider_boundaries.py:26,63,65-69`
- Test: `tests/test_agent_runtime.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_agent_runtime.py` (it already imports `AgentTask` and `CodexRuntime` from `supervisor.agent_runtime`; extend that import with `PiRuntime`):

```python
def test_pi_runtime_start_argv_defaults_to_fable_xhigh(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-argv",
        instruction="implement the fix",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
    )
    assert runtime.preview_start_argv(task) == (
        "pi",
        "-p",
        "--mode",
        "json",
        "--model",
        "anthropic/claude-fable-5",
        "--thinking",
        "xhigh",
        "implement the fix",
    )


def test_pi_runtime_argv_honors_effort_metadata_and_provider_prefix(
    tmp_path: Path,
) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-argv-effort",
        instruction="review",
        cwd=tmp_path,
        model="anthropic/claude-fable-5",
        timeout_s=60,
        metadata={"effort": "high"},
    )
    argv = runtime.preview_start_argv(task)
    assert argv[argv.index("--model") + 1] == "anthropic/claude-fable-5"
    assert argv[argv.index("--thinking") + 1] == "high"
    task_reasoning = AgentTask(
        task_id="pi-argv-reasoning",
        instruction="review",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
        metadata={"reasoning_effort": "medium", "effort": "low"},
    )
    argv = runtime.preview_start_argv(task_reasoning)
    assert argv[argv.index("--thinking") + 1] == "medium"


def test_pi_runtime_read_only_review_restricts_tools(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-read-only",
        instruction="review only",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
        metadata={"read_only_review": True},
    )
    argv = runtime.preview_start_argv(task)
    assert argv[argv.index("--tools") + 1] == "read,grep,find,ls"
    resume = runtime._resume_argv(
        task, session_id="sess-1", instruction="continue"
    )
    assert resume[resume.index("--tools") + 1] == "read,grep,find,ls"


def test_pi_runtime_resume_argv_uses_session_flag(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-resume",
        instruction="start",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
    )
    assert runtime._resume_argv(
        task, session_id="sess-42", instruction="continue"
    ) == (
        "pi",
        "--session",
        "sess-42",
        "-p",
        "--mode",
        "json",
        "--thinking",
        "xhigh",
        "continue",
    )


def test_pi_runtime_env_forwards_only_anthropic_credentials(
    tmp_path: Path,
) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-env",
        instruction="x",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
        env={
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "OPENAI_API_KEY": "leaked-openai",
            "CODEX_HOME": "/tmp/codex-home",
            "CLAUDE_CODE_EXTRA_BODY": "{}",
        },
        inherit_env=False,
    )
    env = runtime._runtime_env(task)
    assert env.get("ANTHROPIC_API_KEY") == "anthropic-secret"
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_HOME" not in env
    assert "CLAUDE_CODE_EXTRA_BODY" not in env


def test_pi_runtime_route_identity_manifest_is_complete(tmp_path: Path) -> None:
    runtime = PiRuntime(binary="pi")
    task = AgentTask(
        task_id="pi-route",
        instruction="x",
        cwd=tmp_path,
        model="claude-fable-5",
        timeout_s=60,
    )
    manifest = runtime._route_identity_manifest(task)
    assert manifest["provider"] == "anthropic"
    assert manifest["route_kind"] == "pi_cli"
    assert manifest["endpoint"] == "pi-cli-configured-route"
    assert manifest["sandbox_posture"] == "none"
    assert manifest["complete"] is True
```

Note: mirror the assertion style of the neighbouring codex env test
`test_codex_runtime_never_forwards_unrelated_host_credentials`
(`tests/test_agent_runtime.py:1640`) — if its harness builds env differently
(e.g. through `runtime.start` with a fake transport), copy that harness
instead of calling `_runtime_env` directly, keeping the same key assertions.

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_agent_runtime.py -k "pi_runtime" -v
```

Expected: FAIL / ERROR with `ImportError: cannot import name 'PiRuntime'`.

- [ ] **Step 3: Implement `PiRuntime`**

In `supervisor/agent_runtime.py`, next to `_CODEX_ENV_KEYS` (~line 120) add:

```python
_PI_ENV_KEYS = frozenset({
    *_PROCESS_ENV_KEYS,
    *_SUPERVISOR_LAUNCH_ENV_KEYS,
    *_ISOLATION_NAMESPACE_ENV_KEYS,
    "ANTHROPIC_API_KEY",
})
```

Immediately after the `CodexRuntime` class (its `_resume_argv` ends ~line 1057) add:

```python
DEFAULT_PI_THINKING = "xhigh"
_PI_READ_ONLY_TOOLS = "read,grep,find,ls"


class PiRuntime(CommandAgentRuntime):
    kind = "pi"
    capabilities = {
        "resume": True,
        "cancel": True,
        "stream": True,
        "cost_reporting": False,
        "subagents": False,
        "images": True,
    }

    def __init__(
        self,
        *,
        transport: RuntimeTransport | None = None,
        binary: str = "pi",
    ) -> None:
        super().__init__(transport=transport, binary=binary)

    def _route_identity_manifest(
        self,
        task: AgentTask,
    ) -> Mapping[str, Any]:
        route = task.metadata.get("provider_route")
        if not isinstance(route, Mapping):
            route = {}
        provider = str(route.get("provider") or "anthropic").strip()
        route_kind = str(route.get("route_kind") or "pi_cli").strip()
        endpoint = str(
            route.get("endpoint") or "pi-cli-configured-route"
        ).strip()
        return {
            "provider": provider,
            "route_kind": route_kind,
            "endpoint": endpoint,
            "model_request": task.model,
            "sandbox_posture": self._sandbox_posture(task),
            "configuration_sha256": str(
                route.get("configuration_sha256") or ""
            ).strip(),
            "complete": bool(
                provider
                and route_kind
                and endpoint
                and str(task.model or "").strip()
            ),
        }

    def _sandbox_posture(self, task: AgentTask) -> str:
        """Pi has no OS sandbox flag; receipts must say what contained it."""
        if task.metadata.get("read_only_review"):
            return "tools-allowlist"
        if _filesystem_isolation_policy(
            task.metadata,
            cwd=Path(task.cwd).resolve(),
        ) is not None:
            return "worktree-only"
        return "none"

    def _runtime_env(self, task: AgentTask) -> dict[str, str]:
        return _allowlisted_environment(
            super()._runtime_env(task),
            allowed_keys=_PI_ENV_KEYS,
        )

    def _thinking_level(self, task: AgentTask) -> str:
        level = _validated_reasoning_effort(task.metadata)
        if level:
            return level
        fallback = str(task.metadata.get("effort") or "").strip()
        if fallback:
            if not _REASONING_EFFORT_PATTERN.fullmatch(fallback):
                raise ValueError(
                    "effort must match ^[A-Za-z0-9_-]+$: "
                    f"{fallback!r}"
                )
            return fallback
        return DEFAULT_PI_THINKING

    def _model_flag(self, task: AgentTask) -> str:
        model = str(task.model or "").strip()
        return model if "/" in model else f"anthropic/{model}"

    def _start_argv(self, task: AgentTask) -> tuple[str, ...]:
        argv = [
            self._binary,
            "-p",
            "--mode",
            "json",
            "--model",
            self._model_flag(task),
            "--thinking",
            self._thinking_level(task),
        ]
        if task.metadata.get("read_only_review"):
            argv.extend(("--tools", _PI_READ_ONLY_TOOLS))
        argv.append(task.instruction)
        return tuple(argv)

    def _resume_argv(
        self,
        task: AgentTask,
        *,
        session_id: str,
        instruction: str,
    ) -> tuple[str, ...]:
        argv = [
            self._binary,
            "--session",
            session_id,
            "-p",
            "--mode",
            "json",
            "--thinking",
            self._thinking_level(task),
        ]
        if task.metadata.get("read_only_review"):
            argv.extend(("--tools", _PI_READ_ONLY_TOOLS))
        argv.append(instruction)
        return tuple(argv)
```

Notes:
- No `-C`/cwd flag: the transport already starts the subprocess with
  `cwd=Path(task.cwd).resolve()` (see `CommandAgentRuntime.start`).
- `_REASONING_EFFORT_PATTERN`, `_validated_reasoning_effort`,
  `_allowlisted_environment`, and `_filesystem_isolation_policy` already exist
  in this module — do not redefine them. If `_filesystem_isolation_policy`
  (defined ~line 2558) is below the new class, that is fine — it is only
  referenced at call time.

- [ ] **Step 4: Register pi in the provider boundaries**

In `supervisor/provider_boundaries.py`:

```python
PROVIDER_EXECUTABLES = frozenset({"claude", "codex", "cursor", "pi"})

PROVIDER_RUNTIME_CONSTRUCTORS: dict[str, str] = {
    "ClaudeCodeRuntime": "claude",
    "CodexRuntime": "codex",
    "CursorRuntime": "cursor",
    "PiRuntime": "pi",
}
```

And update the existing allowlist description (line 26):

```python
    "supervisor/agent_runtime.py": "Claude Code, Codex, and Pi runtime adapters",
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_agent_runtime.py -k "pi_runtime" -v
.venv/bin/pytest tests/test_provider_edge_guard.py -v
```

Expected: all PASS (the edge guard verifies the new constructor/executable
registration is consistent).

- [ ] **Step 6: Commit**

```bash
git add supervisor/agent_runtime.py supervisor/provider_boundaries.py tests/test_agent_runtime.py
git commit -m "feat: add PiRuntime executor adapter behind the runtime seam"
```

---

### Task 2: Pi event normalization + session-id capture

**Files:**
- Modify: `supervisor/agent_runtime.py` (`normalize_runtime_event` ~line 1753, `_session_id` ~line 1867)
- Test: `tests/test_agent_runtime.py`

- [ ] **Step 1: Write the failing test**

Mirror the structure of
`test_codex_runtime_normalizes_captured_live_jsonl_and_marks_missing_provenance`
(`tests/test_agent_runtime.py:1014`) — a fake shell script standing in for the
pi binary:

```python
@pytest.mark.asyncio
async def test_pi_runtime_normalizes_documented_jsonl_stream(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = tmp_path / "captured-pi"
    script.write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        '{"type":"session","id":"pi-sess-1","version":"0.0.0"}\n'
        '{"type":"agent_start"}\n'
        '{"type":"turn_start"}\n'
        '{"type":"tool_execution_start","toolName":"read"}\n'
        '{"type":"tool_execution_end","toolName":"read","isError":false}\n'
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"OK done"}]}}\n'
        '{"type":"turn_end"}\n'
        '{"type":"agent_end"}\n'
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    runtime = PiRuntime(binary=str(script))
    handle = await runtime.start(
        AgentTask(
            task_id="pi-live-jsonl",
            instruction="reply",
            cwd=repo,
            model="claude-fable-5",
            timeout_s=30,
        )
    )
    kinds = [event.kind async for event in runtime.stream(handle)]
    assert "run.started" in kinds
    assert "turn.started" in kinds
    assert "tool.started" in kinds
    assert "tool.completed" in kinds
    assert "agent.message" in kinds
    assert "turn.completed" in kinds
    assert "run.completed" in kinds
    result = await runtime.collect(handle)
    assert result.status == "completed"
    assert "OK done" in result.output
    # Session id from pi's stream header must drive resume.
    assert runtime._session_ids[handle.run_id] == "pi-sess-1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_agent_runtime.py::test_pi_runtime_normalizes_documented_jsonl_stream -v
```

Expected: FAIL — pi event kinds pass through unmapped (no `run.started`
etc.), and the session id stays the run uuid.

- [ ] **Step 3: Extend event normalization**

In `normalize_runtime_event` (`supervisor/agent_runtime.py:1753`):

1. Add a `message_end` branch to the existing `elif` chain, after the
   `elif raw_kind == "result":` branch:

```python
    elif raw_kind == "message_end":
        message = (
            raw.get("message")
            if isinstance(raw.get("message"), Mapping)
            else {}
        )
        if str(message.get("role") or "assistant") == "assistant":
            raw_kind = "agent_message"
```

2. Add the pi vocabulary to the `aliases` dict (keep existing entries):

```python
        "session": "run.started",
        "agent_start": "run.started",
        "agent_end": "run.completed",
        "turn_start": "turn.started",
        "turn_end": "turn.completed",
        "tool_execution_start": "tool.started",
        "tool_execution_end": "tool.completed",
```

3. Extend `_session_id` (~line 1867) to read pi's session header:

```python
def _session_id(raw: Mapping[str, Any]) -> str:
    nested = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
    value = str(
        raw.get("session_id")
        or raw.get("thread_id")
        or nested.get("session_id")
        or nested.get("thread_id")
        or ""
    ).strip()
    if value:
        return value
    if str(raw.get("type") or "") == "session":
        return str(raw.get("id") or "").strip()
    return ""
```

The final assistant text is already extracted by `_runtime_message_text`
(it walks the nested `message` mapping and its `content` block list), so no
change is needed there.

- [ ] **Step 4: Run tests to verify they pass, plus non-regression**

```bash
.venv/bin/pytest tests/test_agent_runtime.py -v
```

Expected: all PASS, including the pre-existing codex/claude normalization
tests (the alias additions must not change their mappings).

- [ ] **Step 5: Commit**

```bash
git add supervisor/agent_runtime.py tests/test_agent_runtime.py
git commit -m "feat: normalize pi JSONL events and capture pi session ids"
```

---

### Task 3: `ExecutorCfg` config model

**Files:**
- Modify: `supervisor/config.py` (models near `TargetCfg` ~line 105; `Config` ~line 311; `Config.load` ~line 340)
- Test: `tests/test_target_config_load.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_target_config_load.py` (it already loads
`tests/fixtures/config_claude_code_minimal.yaml` via `Config.load`; reuse its
imports):

```python
def test_executor_config_defaults_to_pi():
    cfg = Config.load("tests/fixtures/config_claude_code_minimal.yaml")
    assert cfg.executor.kind == "pi"
    assert cfg.executor.pi_cli_command == "pi"


def test_executor_config_accepts_codex_kind_and_expands_cli_path(tmp_path):
    base = Path("tests/fixtures/config_claude_code_minimal.yaml").read_text()
    path = tmp_path / "config.yaml"
    path.write_text(
        base + "\nexecutor:\n  kind: codex\n  pi_cli_command: ~/bin/pi\n"
    )
    cfg = Config.load(path)
    assert cfg.executor.kind == "codex"
    assert cfg.executor.pi_cli_command == str(Path("~/bin/pi").expanduser())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_target_config_load.py -k "executor" -v
```

Expected: FAIL with `AttributeError: 'Config' object has no attribute 'executor'`.

- [ ] **Step 3: Implement the config model**

In `supervisor/config.py`, after `TargetCfg` (~line 110):

```python
class ExecutorCfg(BaseModel):
    """Delegated implementor/executor runtime selection."""

    kind: Literal["pi", "codex"] = "pi"
    pi_cli_command: str = "pi"
```

In `Config` (~line 311), after the `supervisor: SupervisorCfg` field:

```python
    executor: ExecutorCfg = Field(default_factory=ExecutorCfg)
```

In `Config.load` (~line 346, alongside the other `_expanduser` calls):

```python
        cfg.executor.pi_cli_command = (
            _expanduser(cfg.executor.pi_cli_command)
            or cfg.executor.pi_cli_command
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_target_config_load.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add supervisor/config.py tests/test_target_config_load.py
git commit -m "feat: add executor config block (pi default, codex optional)"
```

---

### Task 4: Executor selection at the MCP composition root

**Files:**
- Modify: `mcp_tools/codex_supervisor_stdio.py` (imports line 172; constants ~line 217; `CodexSupervisorMcpAPI.__init__` ~lines 490-540; `start_codex_session` ~lines 4440-4535; module-level builder ~lines 6012-6039; outer `start_codex_session` wrapper ~line 6612)
- Test: `tests/test_codex_supervisor_mcp_stdio.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_codex_supervisor_mcp_stdio.py`, reusing its existing
`_cfg(tmp_path)` helper and `State` import (mirror the setup of
`test_codex_supervisor_mcp_start_codex_session_can_dry_run_or_execute_with_runner`
at line 3083):

```python
def test_start_codex_session_dry_run_defaults_to_pi_fable_xhigh(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(_cfg(tmp_path), state)

    dry_run = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=False,
    )

    assert dry_run["status"] == "dry_run"
    assert dry_run["runtime"] == "pi"
    argv = dry_run["argv"]
    assert argv[0] == "pi"
    assert argv[argv.index("--model") + 1] == "anthropic/claude-fable-5"
    assert argv[argv.index("--thinking") + 1] == "xhigh"


def test_start_codex_session_honors_executor_kind_codex(tmp_path):
    from mcp_tools.codex_supervisor_stdio import CodexSupervisorMcpAPI

    cfg = _cfg(tmp_path)
    cfg.executor.kind = "codex"
    state = State(str(tmp_path / "state.db"))
    api = CodexSupervisorMcpAPI(cfg, state)

    dry_run = api.start_codex_session(
        prompt="Implement the slice.",
        cwd=str(tmp_path),
        execute=False,
    )

    assert dry_run["runtime"] == "codex"
    argv = dry_run["argv"]
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("-m") + 1] == "gpt-5.5"
    assert 'reasoning_effort="xhigh"' in " ".join(argv)
```

If `_cfg` returns a config without an `executor` attribute (it builds
`Config` from the minimal fixture, so `executor` should default), adjust the
second test to rebuild the config with `executor.kind = "codex"` however the
helper allows — the assertion targets stay the same.

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_codex_supervisor_mcp_stdio.py -k "executor_kind or defaults_to_pi" -v
```

Expected: FAIL — `dry_run["runtime"] == "codex"` in the first test (pi not
wired yet).

- [ ] **Step 3: Implement executor selection**

In `mcp_tools/codex_supervisor_stdio.py`:

1. Extend the import (line 172):

```python
from supervisor.agent_runtime import (
    AgentTask,
    ClaudeCodeRuntime,
    CodexRuntime,
    PiRuntime,
)
```

2. Next to `DEFAULT_CODEX_MODEL` (~line 217):

```python
DEFAULT_PI_MODEL = "claude-fable-5"
DEFAULT_PI_REASONING_EFFORT = "xhigh"
```

3. Add two keyword-only parameters to `CodexSupervisorMcpAPI.__init__`
   (~line 498, next to `codex_runtime_factory`/`codex_runtime_runner`):

```python
        executor_runtime_factory: Callable[[], CommandAgentRuntime] | None = None,
        executor_runtime_runner: RuntimeTaskRunner | None = None,
```

(Use whatever runtime base type the module already imports for typing; if
none fits, `Callable[[], Any]` matches the local style of loose factory
typing.)

4. After the existing `self.codex_runtime_runner = (...)` block (~line 537):

```python
        executor_cfg = getattr(cfg, "executor", None)
        executor_kind = (
            executor_cfg.kind if executor_cfg is not None else "pi"
        )
        pi_cli = (
            executor_cfg.pi_cli_command
            if executor_cfg is not None
            else "pi"
        )
        # Injected codex fakes (tests, replay) keep the executor on codex.
        codex_injected = (
            codex_runtime_factory is not None
            or codex_runtime_runner is not None
        )
        executor_is_codex = executor_runtime_factory is None and (
            executor_kind == "codex" or codex_injected
        )
        if executor_runtime_factory is not None:
            self.executor_runtime_factory = executor_runtime_factory
        elif executor_is_codex:
            self.executor_runtime_factory = self.codex_runtime_factory
        else:
            self.executor_runtime_factory = (
                lambda: PiRuntime(binary=pi_cli)
            )
        if executor_runtime_runner is not None:
            self.executor_runtime_runner = executor_runtime_runner
        elif executor_is_codex:
            self.executor_runtime_runner = self.codex_runtime_runner
        else:
            self.executor_runtime_runner = runtime_task_runner(
                self.executor_runtime_factory
            )
        self.executor_default_model = (
            DEFAULT_CODEX_MODEL if executor_is_codex else DEFAULT_PI_MODEL
        )
        self.executor_default_reasoning_effort = (
            DEFAULT_CODEX_REASONING_EFFORT
            if executor_is_codex
            else DEFAULT_PI_REASONING_EFFORT
        )
```

5. In `start_codex_session` (~line 4440): change the signature default
   `reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT` to
   `reasoning_effort: str | None = None`, then in the body:

```python
            model=model or self.executor_default_model,
```

```python
                "reasoning_effort": (
                    reasoning_effort
                    or self.executor_default_reasoning_effort
                ),
```

```python
        preview_runtime = self.executor_runtime_factory()
```

```python
            execution = self.executor_runtime_runner(agent_task)
```

(Four one-line substitutions: the `DEFAULT_CODEX_MODEL` fallback, the
metadata `reasoning_effort` fallback, the `self.codex_runtime_factory()`
call, and the `self.codex_runtime_runner(agent_task)` call. Leave
`self.codex_runtime_factory`/`self.codex_runtime_runner` themselves intact —
the codex reviewer path at ~line 1892 still uses
`runtime_runner=self.codex_runtime_runner` and must keep doing so.)

6. In the module-level builder (~lines 6012-6039): add the same two optional
   parameters and pass them through to `CodexSupervisorMcpAPI(...)`. In the
   outer `start_codex_session` wrapper (~line 6612): change
   `reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT` to
   `reasoning_effort: str | None = None` (it already forwards the value).

- [ ] **Step 4: Run tests to verify they pass, including back-compat**

```bash
.venv/bin/pytest tests/test_codex_supervisor_mcp_stdio.py -k "start_codex_session or executor" -v
```

Expected: all PASS — critically including the pre-existing
`test_codex_supervisor_mcp_start_codex_session_can_dry_run_or_execute_with_runner`,
which injects only `codex_runtime_runner` and must still see codex argv and
`gpt-5.5`/`xhigh` defaults (that is what the `codex_injected` rule preserves).

If the execute path fails on launch-receipt/workflow validation with
`target_kind` "pi": `target_kind` is a free string end-to-end
(`supervisor/run_registry.py:118` and `_validate_run_registration_authority`
only check presence/equality), so register the test workflow with
`target_kind="pi"` to match `preview_runtime.kind`.

- [ ] **Step 5: Commit**

```bash
git add mcp_tools/codex_supervisor_stdio.py tests/test_codex_supervisor_mcp_stdio.py
git commit -m "feat: select pi as default executor at the MCP composition root"
```

---

### Task 5: Lead effort threading + xhigh default

**Files:**
- Modify: `supervisor/provider_routing.py:11` (add constant below)
- Modify: `supervisor/dual_agent_lead.py:44-45,281-282`
- Modify: `supervisor/dual_agent_runner.py:46,128-139,1943-1974`
- Modify: `mcp_tools/codex_supervisor_stdio.py` (`start_dual_agent_gate` method ~line 594, spec build ~line 5820, wrapper ~lines 6057-6110)
- Test: `tests/test_dual_agent_lead_invoker.py:207-210`, `tests/test_dual_agent_runner.py`

- [ ] **Step 1: Update/write the failing tests**

In `tests/test_dual_agent_lead_invoker.py` replace lines 207-210 with:

```python
    assert select_lead_effort("intent", quality="best") == "medium"
    assert select_lead_effort("prd_review", quality="best") == "xhigh"
    assert select_lead_effort("execution", quality="balanced") == "xhigh"
    assert select_lead_effort("prd_review", quality="cheap") == "low"
    assert (
        select_lead_effort("execution", quality="best", explicit_effort="max")
        == "max"
    )
```

Add to `tests/test_dual_agent_runner.py`:

```python
def test_gate_spec_threads_effort_to_lead_request():
    from supervisor.dual_agent_runner import DualAgentGateSpec, _lead_request

    spec = DualAgentGateSpec(
        task_id="t-effort",
        run_id="r-effort",
        gate="execution",
        instruction="do the work",
        cwd="/tmp",
        effort="xhigh",
    )
    request = _lead_request(spec)
    assert request.effort == "xhigh"

    default_spec = DualAgentGateSpec(
        task_id="t-default",
        run_id="r-default",
        gate="execution",
        instruction="do the work",
        cwd="/tmp",
    )
    assert _lead_request(default_spec).effort is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_dual_agent_lead_invoker.py tests/test_dual_agent_runner.py::test_gate_spec_threads_effort_to_lead_request -v
```

Expected: FAIL — `select_lead_effort("prd_review", ...)` returns `"high"`,
and `DualAgentGateSpec` rejects the unknown `effort` kwarg.

- [ ] **Step 3: Implement**

1. `supervisor/provider_routing.py` (below line 11):

```python
XHIGH_ANTHROPIC_EFFORT: Literal["xhigh"] = "xhigh"
```

2. `supervisor/dual_agent_lead.py`: add `XHIGH_ANTHROPIC_EFFORT` to the
   `provider_routing` import block (lines 44-45), then in
   `select_lead_effort` change the high-effort branch (~line 281):

```python
    if gate in HIGH_EFFORT_GATES:
        return XHIGH_ANTHROPIC_EFFORT
```

Leave `COMPLEX_ANTHROPIC_EFFORT` itself untouched (other consumers keep
"high"); remove its import from `dual_agent_lead.py` only if this was its
sole use in the module (check with
`grep -n COMPLEX_ANTHROPIC_EFFORT supervisor/dual_agent_lead.py`).

3. `supervisor/dual_agent_runner.py`: extend the import at line 46 area to
   also bring `ClaudeEffort` from `supervisor.dual_agent_lead`. In
   `DualAgentGateSpec` (line 128), after the `model: str | None = None`
   field add:

```python
    effort: ClaudeEffort | None = None
```

In `_lead_request` (~line 1950), after `model=spec.model,` add:

```python
        effort=spec.effort,
```

4. `mcp_tools/codex_supervisor_stdio.py`: mirror how `model` flows from the
   tool surface to the spec — add the parameter at each of the same hops:
   - `CodexSupervisorMcpAPI.start_dual_agent_gate` signature (~line 605,
     next to `model: str | None = None`): add `effort: str | None = None,`
     and validate right after the existing canonicalization calls:

```python
        if effort is not None and effort not in (
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError(
                "effort must be one of low|medium|high|xhigh|max: "
                f"{effort!r}"
            )
```

   - every intermediate hop that forwards `model=` between the method and
     the `DualAgentGateSpec(` construction at ~line 5820 (find them with
     `grep -n "model=model" mcp_tools/codex_supervisor_stdio.py` and
     `grep -n "quality=quality" mcp_tools/codex_supervisor_stdio.py`):
     forward `effort=effort,` alongside.
   - the `DualAgentGateSpec(` build (~line 5820): add `effort=effort,`
     (with `# type: ignore[arg-type]` only if the surrounding style does
     that for `gate`).
   - the module-level wrapper `start_dual_agent_gate` (~line 6057): add
     `effort: str | None = None,` to its signature and `effort=effort,` to
     the `tool_api.start_dual_agent_gate(...)` call (~line 6094).

- [ ] **Step 4: Run tests and sweep for stale effort expectations**

```bash
.venv/bin/pytest tests/test_dual_agent_lead_invoker.py tests/test_dual_agent_runner.py -v
grep -rn '"high"' tests/test_dual_agent_lead_invoker.py tests/test_dual_agent_workflow_driver.py tests/test_claude_sdk_routing.py | grep -i effort
```

Expected: the two named files PASS; for any other test the grep surfaces
that asserts a high-effort gate produced `--effort high` (or
`metadata["effort"] == "high"`), update the expectation to `xhigh` — the
behavior change is intentional.

- [ ] **Step 5: Commit**

```bash
git add supervisor/provider_routing.py supervisor/dual_agent_lead.py supervisor/dual_agent_runner.py mcp_tools/codex_supervisor_stdio.py tests/test_dual_agent_lead_invoker.py tests/test_dual_agent_runner.py
git commit -m "feat: thread lead effort end-to-end and default high-effort gates to xhigh"
```

---

### Task 6: Operator docs

**Files:**
- Modify: `config.example.yaml` (after the `target:` block, ~line 45)
- Modify: `README.md` (~line 501, the "Supervisor defaults Claude work to `claude-fable-5`" area)

- [ ] **Step 1: Add the executor block to `config.example.yaml`**

Insert after the commented alternate `target:` block (~line 45):

```yaml
# Delegated implementor/executor runtime for supervisor-launched worker
# sessions (start_codex_session). The default is the pi coding agent running
# claude-fable-5 at xhigh thinking; set kind: codex to restore the Codex CLI
# executor. Pi needs `npm i -g @mariozechner/pi-coding-agent` and
# ANTHROPIC_API_KEY. Pi has no OS sandbox flag: read-only work is restricted
# via --tools, write work relies on worktree isolation, and receipts record
# the sandbox posture.
executor:
  kind: pi
  pi_cli_command: pi
```

- [ ] **Step 2: Update the README model note**

Extend the sentence at README.md ~line 501 ("Supervisor defaults Claude work
to `claude-fable-5`.") with:

```markdown
The delegated executor defaults to the pi coding agent running
`claude-fable-5` at `xhigh` thinking (`executor.kind: codex` restores the
Codex CLI executor), and the dual-agent lead defaults to `xhigh` effort on
high-effort gates (`effort` on `start_dual_agent_gate` overrides per gate).
```

- [ ] **Step 3: Commit**

```bash
git add config.example.yaml README.md
git commit -m "docs: document pi executor selection and xhigh lead effort"
```

---

### Task 7: Live verification (pi install, model catalog, stream fixture, claude effort)

These checks validate the external assumptions the code was built on. They
need network + `ANTHROPIC_API_KEY`; run them on the host, not in a sandbox.

- [ ] **Step 1: Install pi and confirm the fable model exists**

```bash
npm i -g @mariozechner/pi-coding-agent
pi --version
pi --models 2>/dev/null | grep -i fable
```

Expected: a version prints, and the model list contains `claude-fable-5`
under the anthropic provider. If the bare id is missing but
`anthropic/claude-fable-5` resolves, no code change is needed
(`_model_flag` already emits the prefixed form).

- [ ] **Step 2: Capture a live JSONL stream and diff it against our aliases**

```bash
cd /tmp && pi --no-session -p --mode json --model anthropic/claude-fable-5 --thinking xhigh "Reply with exactly: OK" > /tmp/pi-live.jsonl; cat /tmp/pi-live.jsonl | head -30
```

Compare the `"type"` values in `/tmp/pi-live.jsonl` with the alias set added
in Task 2 (`session`, `agent_start`, `agent_end`, `turn_start`, `turn_end`,
`tool_execution_start`, `tool_execution_end`, `message_end`) and the session
header shape (`{"type":"session","id":...}`). If any name or the header
id-field differs, update `normalize_runtime_event`/`_session_id` and the
Task 2 test fixture to the observed names, and re-run:

```bash
.venv/bin/pytest tests/test_agent_runtime.py -k "pi_runtime" -v
```

- [ ] **Step 3: Verify the claude CLI accepts xhigh effort for fable**

```bash
claude -p --model claude-fable-5 --effort xhigh "Reply with exactly: OK"
```

Expected: "OK" with no `400 invalid effort level` error. If the CLI rejects
`xhigh`: revert only the `select_lead_effort` default (Task 5 step 3.2) to
`COMPLEX_ANTHROPIC_EFFORT`, keep the threading (explicit efforts become
usable the moment the CLI supports them), and update the two test
expectations back to `high`. Record the outcome either way.

- [ ] **Step 4: Full suite + commit any fixture adjustments**

```bash
.venv/bin/pytest -q
```

Expected: PASS (note: this suite is large; if pre-existing unrelated
failures block, record them and scope the assertion to the files touched by
Tasks 1-5).

```bash
git add -A tests/ supervisor/agent_runtime.py
git commit -m "test: pin pi live-stream fixture from captured run"
```

(Skip the commit if Step 2 required no changes.)

---

## Out of scope (per spec)

- Deprecating/removing the Codex executor or touching the codex reviewer
  path (`runtime_runner=self.codex_runtime_runner` at stdio ~line 1892).
- Pi as a reviewer (`cursor_agent.py` seam).
- OS-level sandboxing for pi.
- Reviewer models/modes, supervisor decision models.
