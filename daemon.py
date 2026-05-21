"""Entry point. launchd starts this; it wires the subsystems together and runs forever.

Subsystems (all run as concurrent asyncio tasks where supported by target):
  - RolloutWatcher   — tails ~/.codex/sessions/.../rollout-*.jsonl (Codex only)
  - DriftDetector    — periodic L1–L3 checks; enqueues L4 decisions
  - ProcessMonitor   — Codex desktop process + activity heartbeat
  - TelegramPoller   — long-polls getUpdates; routes button callbacks
  - HookServer       — FastAPI on :9001 for synchronous target hooks
  - AgentInvoker     — consumes the decisions queue, runs Agent SDK sessions
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

# Add this directory to the path so `supervisor` and `mcp_tools` import cleanly.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from supervisor.config import Config
from supervisor.state import State
from supervisor.drift_detector import DriftDetector
from supervisor.hook_server import build_app, serve
from supervisor.target.factory import build_target_adapter
from supervisor.telegram import TelegramNotifier, TelegramPoller, telegram_enabled


def _setup_logging(cfg: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(cfg.logging.file),
            logging.StreamHandler(),
        ],
    )
    # httpx logs full URLs, which would include Telegram bot tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> int:
    cfg_path = os.environ.get(
        "CODEX_SUPERVISOR_CONFIG",
        str(Path.home() / ".codex-supervisor" / "config.yaml"),
    )
    cfg = Config.load(cfg_path)
    _setup_logging(cfg)
    log = logging.getLogger("daemon")
    target_adapter = build_target_adapter(cfg)
    target_kind = target_adapter.kind
    log.info("starting codex-supervisor target=%s (config=%s)", target_kind, cfg_path)

    state = State(cfg.supervisor.state_db)

    anthropic_key = (
        cfg.models.anthropic_api_key
        or cfg.models.anthropic_auth_token
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    )
    anthropic_base_url = (
        cfg.models.anthropic_base_url
        or os.environ.get("ANTHROPIC_BASE_URL", "")
    )
    anthropic_kwargs = {"api_key": anthropic_key}
    if anthropic_base_url:
        anthropic_kwargs["base_url"] = anthropic_base_url
    anthropic = AsyncAnthropic(**anthropic_kwargs) if anthropic_key else None

    openai_key = cfg.models.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    openai_base_url = cfg.models.openai_base_url or os.environ.get("OPENAI_BASE_URL", "")
    openai_kwargs = {"api_key": openai_key}
    if openai_base_url:
        openai_kwargs["base_url"] = openai_base_url
    oai = AsyncOpenAI(**openai_kwargs) if openai_key else None

    invoker = None
    notifier = TelegramNotifier(cfg) if telegram_enabled(cfg) else None
    progress_streamer = None
    if notifier is not None:
        from supervisor.telegram_progress import TelegramProgressStreamer
        progress_streamer = TelegramProgressStreamer(
            state=state,
            notifier=notifier,
            target_adapter=target_adapter,
            desktop_status_mode=cfg.modes.desktop_status_sync,
        )
    chat_supervisor = None
    if notifier is not None:
        try:
            from supervisor.telegram_supervisor import TelegramChatSupervisor
            chat_supervisor = TelegramChatSupervisor(
                cfg,
                state,
                target_adapter=target_adapter,
                telegram_sender=notifier.approval_sender(),
            )
        except ModuleNotFoundError as e:
            if e.name != "claude_agent_sdk":
                raise
            log.warning(
                "claude_agent_sdk not installed; Telegram slash commands still run "
                "but conversational supervisor chat is disabled."
            )
    telegram_poller = (
        TelegramPoller(
            cfg,
            state,
            target_adapter=target_adapter,
            chat_handler=chat_supervisor,
        )
        if notifier is not None else None
    )
    hook_critic = None
    if cfg.supervisor.hook_critique_strategy == "model_first":
        try:
            import claude_agent_sdk  # noqa: F401
            from supervisor.hook_critic import ClaudeAgentSDKHookCritic
            hook_critic = ClaudeAgentSDKHookCritic(cfg)
            log.info("hook critique strategy: model_first via Claude Agent SDK")
        except ModuleNotFoundError as e:
            if e.name != "claude_agent_sdk":
                raise
            log.warning(
                "hook_critique_strategy=model_first but claude_agent_sdk is not "
                "installed; falling back to deterministic hook rules."
            )
    if notifier is not None:
        try:
            from supervisor.agent_invoker import AgentInvoker
            from mcp_tools.codex_tools import build_codex_mcp_server
            from mcp_tools.telegram_tools import build_telegram_mcp_server

            codex_mcp = build_codex_mcp_server(cfg, state)
            telegram_mcp = build_telegram_mcp_server(cfg, state)
            skills_dir = HERE / "skills"
            invoker = AgentInvoker(cfg, state, skills_dir, codex_mcp, telegram_mcp)
        except ModuleNotFoundError as e:
            if e.name != "claude_agent_sdk":
                raise
            log.warning(
                "claude_agent_sdk not installed; decision runtime disabled. "
                "Telegram polling still runs when configured."
            )
    else:
        log.warning(
            "Telegram is not configured; Telegram poller and AgentInvoker "
            "decision loop are disabled. Hook audit and rollout monitoring still run."
        )

    drift_detector = DriftDetector(cfg, state, anthropic, oai)

    process_monitor = None
    if target_kind == "codex":
        from supervisor.process_monitor import ProcessMonitor
        process_monitor = ProcessMonitor(cfg, state)

    # Hook server (with a closure that ticks the process_monitor heartbeat when
    # the target has a monitor wired).
    app = build_app(
        cfg,
        state,
        target_adapter=target_adapter,
        anthropic=anthropic,
        telegram_notifier=notifier,
        hook_critic=hook_critic,
        on_hook_seen=process_monitor.mark_hook_seen if process_monitor else None,
    )

    tasks = [
        asyncio.create_task(drift_detector.run(), name="drift_detector"),
        asyncio.create_task(serve(cfg, app), name="hook_server"),
    ]
    if telegram_poller is not None:
        tasks.append(asyncio.create_task(telegram_poller.run(), name="telegram_poller"))
    if notifier is not None:
        tasks.append(asyncio.create_task(
            notifier.send_startup(
                target=target_kind,
                port=cfg.supervisor.hook_server_port,
            ),
            name="telegram_startup_notice",
        ))
    if invoker is not None:
        tasks.append(asyncio.create_task(invoker.run(), name="agent_invoker"))
    codex_cfg = (
        cfg.target.codex
        if cfg.target is not None and cfg.target.codex is not None
        else cfg.codex
    )
    if target_kind == "codex" and codex_cfg is not None:
        from supervisor.rollout_watcher import RolloutWatcher
        rollout_watcher = RolloutWatcher(
            sessions_root=codex_cfg.sessions_root,
            registry_dir=cfg.orchestrator.run_registry_dir,
            state=state,
            on_event=progress_streamer.handle_event if progress_streamer else None,
            sweep_interval_s=cfg.supervisor.rollout_sweep_interval_s,
        )
        tasks.append(asyncio.create_task(rollout_watcher.run(), name="rollout_watcher"))
        if process_monitor:
            tasks.append(asyncio.create_task(process_monitor.run(), name="process_monitor"))
    else:
        log.info("target=%s has no live event tailer wired yet; hook server only", target_kind)

    log.info("all subsystems started; %d tasks", len(tasks))
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        try:
            t.result()
        except Exception as e:
            log.exception("subsystem %s died: %s", t.get_name(), e)
    for t in pending:
        t.cancel()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
