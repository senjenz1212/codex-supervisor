"""Config loader. Reads YAML, expands ${ENV_VAR}, returns a typed pydantic object.

v0.3 (ticket 01): added `target` section with `target.kind` selector; legacy
top-level `codex:` block is now optional. For back-compat, a config that has
`codex:` but no `target:` infers `target.kind = "codex"`.
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Any, Literal
import yaml
from pydantic import BaseModel, Field, model_validator


_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(obj: Any) -> Any:
    if isinstance(obj, str):
        def sub(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_RE.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj


def _expanduser(p: str | None) -> str | None:
    return None if p is None else str(Path(p).expanduser())


# ---- Operating modes (per-capability safety dial) ----

Mode = Literal["off", "shadow", "advise", "enforce", "ask_user"]


class ModesCfg(BaseModel):
    """Per-capability operating mode. Defaults follow the v0.3 control-plane
    philosophy: shadow before advise, advise before enforce, never enforce a
    destructive action without explicit user approval."""
    rollout_ingestion: Mode = "enforce"
    telegram_fyis: Mode = "advise"
    drift_l1_l3: Mode = "shadow"
    drift_l4: Mode = "advise"
    steering_injection: Mode = "advise"
    desktop_status_sync: Mode = "off"
    hook_blocking: Mode = "shadow"
    recovery_actions: Mode = "ask_user"

    @model_validator(mode="before")
    @classmethod
    def _normalize_yaml_boolean_modes(cls, data: Any) -> Any:
        """YAML 1.1 style parsing turns bare `off` into boolean False.

        Operators naturally write `mode: off`; accept that as the literal mode
        instead of failing launchd startup.
        """
        if not isinstance(data, dict):
            return data
        return {
            key: ("off" if value is False else value)
            for key, value in data.items()
        }


# ---- Target adapter config ----

class TargetClaudeCodeCfg(BaseModel):
    cli_command: str = "claude"
    transcripts_root: str | None = None        # set when transcript tailing lands


class TargetCodexCfg(BaseModel):
    sessions_root: str = "~/.codex/sessions"
    cli_command: str = "codex"
    steering_delivery: Literal["resume", "desktop_gui"] = "resume"
    resume_extra_args: list[str] = Field(default_factory=lambda: [
        "--skip-git-repo-check",
    ])
    resume_timeout_s: int = 60
    app_server_transport: Literal["proxy", "stdio"] = "proxy"
    app_server_socket_path: str | None = None
    app_server_load_thread_before_inject: bool = True
    app_server_timeout_s: int = 10
    desktop_gui_input_x: int | None = None
    desktop_gui_input_y: int | None = None
    desktop_gui_session_click_x: int | None = None
    desktop_gui_session_click_y: int | None = None
    desktop_gui_swift_command: str = "/usr/bin/swift"
    desktop_gui_submit_delay_ms: int = 2000
    cli_search_paths: list[str] = Field(default_factory=lambda: [
        "~/.npm-global/bin/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    ])
    desktop_process_names: list[str] = Field(default_factory=lambda: ["Codex"])


class TargetCfg(BaseModel):
    kind: Literal["claude_code", "codex"] = "claude_code"
    claude_code: TargetClaudeCodeCfg | None = None
    codex: TargetCodexCfg | None = None


# ---- Legacy + remaining config sections ----

class CodexCfg(BaseModel):
    """Deprecated top-level `codex:` block. Kept for back-compat with v0.2 configs.
    New configs should use `target.kind: codex` and `target.codex: { ... }`."""
    sessions_root: str
    desktop_process_names: list[str] = Field(default_factory=lambda: ["Codex"])
    resume_command: str = "codex"


class OrchestratorCfg(BaseModel):
    run_registry_dir: str


class SupervisorCfg(BaseModel):
    state_db: str
    hook_server_port: int = 9001
    hook_critique_strategy: Literal["deterministic_first", "model_first"] = "deterministic_first"
    rollout_sweep_interval_s: int = 10
    drift_check_interval_s: int = 30
    stall_threshold_s: int = 90
    nudge_cooldown_s: int = 300
    reviewer_unavailable_policy: Literal["block", "escalate", "proceed_degraded"] = "escalate"
    reviewer_model: str = "gemini-3.1-pro-preview"
    reviewer_output_mode: Literal["litellm_structured", "cursor_sdk"] = "cursor_sdk"
    reviewer_max_tokens: int = 4096
    reviewer_infra_retry_limit: int = 2
    reviewer_infra_retry_backoff_s: float = 1.0


class AgenticLeadCfg(BaseModel):
    policy: Literal["off", "allowed", "required"] = "off"
    min_subagents: int = 0
    required_roles: list[str] = Field(default_factory=list)
    solo_exception_for_artifact_only_gates: bool = False
    required_evidence_grade: Literal["self_reported", "lead_captured", "runtime_native"] = "self_reported"


class LocalFallbackCfg(BaseModel):
    enabled: bool = False
    base_url: str = "http://localhost:8000/v1"
    fast_model: str = ""
    deep_model: str = ""


class ModelsCfg(BaseModel):
    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""
    anthropic_base_url: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    realtime_critique_model: str
    drift_l3_model: str
    drift_l4_model: str
    post_run_eval_model: str
    embedding_model: str
    local_fallback: LocalFallbackCfg = Field(default_factory=LocalFallbackCfg)


class TelegramCfg(BaseModel):
    bot_token: str
    chat_id: str
    poll_interval_s: int = 2
    ask_timeout_s: int = 300


class DriftCfg(BaseModel):
    l1_scope_violation_threshold: int = 3
    l2_similarity_threshold: float = 0.4
    nudge_text_template: str = "Refocus on: {task}"


class ConnectorsCfg(BaseModel):
    """External MCP connectors exposed to the Telegram Claude supervisor.

    The supervisor always includes its local `supervisor` MCP server. External
    connectors are opt-in and tool-allowlisted so Telegram cannot become a broad
    write surface by accident.
    """
    enabled: bool = False
    import_from_claude_desktop: bool = False
    claude_desktop_config_path: str = "~/Library/Application Support/Claude/claude_desktop_config.json"
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)


class LoggingCfg(BaseModel):
    level: str = "INFO"
    file: str = "/tmp/codex-supervisor.log"


class Config(BaseModel):
    # New in v0.3: optional target block. Inferred for back-compat if absent.
    target: TargetCfg | None = None

    # Legacy: optional now. If present and target is absent → target.kind=codex.
    codex: CodexCfg | None = None

    orchestrator: OrchestratorCfg
    supervisor: SupervisorCfg
    agentic_lead: AgenticLeadCfg = Field(default_factory=AgenticLeadCfg)
    modes: ModesCfg = Field(default_factory=ModesCfg)
    models: ModelsCfg
    telegram: TelegramCfg
    drift: DriftCfg = Field(default_factory=DriftCfg)
    connectors: ConnectorsCfg = Field(default_factory=ConnectorsCfg)
    logging: LoggingCfg = Field(default_factory=LoggingCfg)

    @model_validator(mode="after")
    def _infer_target(self) -> "Config":
        if self.target is None:
            kind: Literal["claude_code", "codex"] = "codex" if self.codex is not None else "claude_code"
            self.target = TargetCfg(kind=kind)
        return self

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        with open(Path(path).expanduser()) as f:
            raw = yaml.safe_load(f)
        raw = _expand_env(raw)
        cfg = cls(**raw)
        # Expand ~ on path-like fields.
        if cfg.codex is not None:
            cfg.codex.sessions_root = _expanduser(cfg.codex.sessions_root) or cfg.codex.sessions_root
        if cfg.target and cfg.target.codex is not None:
            cfg.target.codex.sessions_root = _expanduser(cfg.target.codex.sessions_root) or cfg.target.codex.sessions_root
            cfg.target.codex.cli_command = _expanduser(cfg.target.codex.cli_command) or cfg.target.codex.cli_command
            cfg.target.codex.cli_search_paths = [
                _expanduser(p) or p for p in cfg.target.codex.cli_search_paths
            ]
            cfg.target.codex.app_server_socket_path = (
                _expanduser(cfg.target.codex.app_server_socket_path)
                if cfg.target.codex.app_server_socket_path is not None
                else None
            )
        cfg.orchestrator.run_registry_dir = _expanduser(cfg.orchestrator.run_registry_dir) or cfg.orchestrator.run_registry_dir
        cfg.supervisor.state_db = _expanduser(cfg.supervisor.state_db) or cfg.supervisor.state_db
        cfg.connectors.claude_desktop_config_path = (
            _expanduser(cfg.connectors.claude_desktop_config_path)
            or cfg.connectors.claude_desktop_config_path
        )
        return cfg
