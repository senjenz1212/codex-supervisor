"""Invokes the Claude Agent SDK with the appropriate skill per decision kind.

Each invocation is bounded (max_turns), runs to completion, and terminates.
This is what gives us predictable cost — the agent doesn't loop forever.
"""
from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .config import Config
from .state import State, Decision

log = logging.getLogger(__name__)

ClaudeSDKClient: Any | None = None
ClaudeAgentOptions: Any | None = None


class MissingClaudeAgentSdk(RuntimeError):
    """Raised when the optional Claude Agent SDK is needed but unavailable."""


SKILL_FOR_DECISION = {
    "adjudicate_drift": "drift-watch",
    "evaluate_run":     "evaluate-run",
    "plan_recovery":    "plan-recovery",
    "review_updates":   "review-updates",
}


class AgentInvoker:
    def __init__(self, cfg: Config, state: State, skills_dir: Path,
                 codex_mcp_server, telegram_mcp_server):
        self.cfg = cfg
        self.state = state
        self.skills_dir = skills_dir
        self.codex_mcp = codex_mcp_server
        self.telegram_mcp = telegram_mcp_server

    async def run(self) -> None:
        log.info("AgentInvoker: starting decision loop")
        while True:
            decision = await self.state.next_decision()
            try:
                await self._handle(decision)
            except Exception as e:
                log.exception("decision %s failed: %s", decision.kind, e)

    async def _handle(self, d: Decision) -> None:
        skill_name = SKILL_FOR_DECISION.get(d.kind)
        if not skill_name:
            log.warning("unknown decision kind: %s", d.kind)
            return
        skill_text = (self.skills_dir / f"{skill_name}.md").read_text()

        model = self._model_for(d.kind)
        client_cls, options_cls = _load_claude_agent_sdk()
        options = options_cls(
            system_prompt=skill_text,
            model=model,
            max_turns=12,
            mcp_servers={
                "codex": self.codex_mcp,
                "telegram": self.telegram_mcp,
            },
            allowed_tools=self._allowed_tools_for(d.kind),
        )

        user_message = self._format_decision(d)
        log.info("invoking agent: kind=%s run=%s skill=%s",
                 d.kind, d.run_id, skill_name)

        async with client_cls(options=options) as client:
            await client.query(user_message)
            outputs: list[str] = []
            async for msg in client.receive_response():
                if hasattr(msg, "content"):
                    for block in getattr(msg, "content", []) or []:
                        text = getattr(block, "text", None)
                        if text:
                            outputs.append(text)

        self.state.write_verdict(
            run_id=d.run_id, phase=d.kind, layer="L4" if d.kind == "adjudicate_drift" else None,
            model=model, output={"agent_outputs": outputs},
            latency_ms=0,
        )

    def _model_for(self, kind: str) -> str:
        if kind == "adjudicate_drift":
            return self.cfg.models.drift_l4_model
        if kind in ("evaluate_run", "review_updates"):
            return self.cfg.models.post_run_eval_model
        return self.cfg.models.drift_l4_model

    @staticmethod
    def _allowed_tools_for(kind: str) -> list[str]:
        if kind == "review_updates":
            return [
                "mcp__codex__read_rollout",
                "mcp__codex__get_run_metadata",
                "mcp__codex__read_workspace_snapshot",
                "mcp__codex__read_workspace_file",
                "mcp__telegram__send_message",
            ]
        return [
            "mcp__codex__read_rollout",
            "mcp__codex__get_run_metadata",
            "mcp__codex__inject_steering",
            "mcp__codex__list_active_runs",
            "mcp__telegram__send_message",
            "mcp__telegram__ask_user",
        ]

    def _format_decision(self, d: Decision) -> str:
        return (
            f"Decision request:\n\n"
            f"kind: {d.kind}\n"
            f"run_id: {d.run_id}\n"
            f"context:\n{json.dumps(d.payload, indent=2, default=str)[:8000]}\n\n"
            f"Follow the procedure in your system prompt. Use your MCP tools as needed. "
            f"End your response with a single JSON block summarizing the action you took."
        )


def _load_claude_agent_sdk() -> tuple[Any, Any]:
    global ClaudeSDKClient, ClaudeAgentOptions
    if ClaudeSDKClient is not None and ClaudeAgentOptions is not None:
        return ClaudeSDKClient, ClaudeAgentOptions
    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            ClaudeAgentOptions as _ClaudeAgentOptions,
            ClaudeSDKClient as _ClaudeSDKClient,
        )
    except ModuleNotFoundError as e:
        if e.name == "claude_agent_sdk":
            raise MissingClaudeAgentSdk(
                "claude_agent_sdk is optional; install codex-supervisor[agent] "
                "to run AgentInvoker decisions."
            ) from e
        raise
    ClaudeSDKClient = _ClaudeSDKClient
    ClaudeAgentOptions = _ClaudeAgentOptions
    return ClaudeSDKClient, ClaudeAgentOptions
