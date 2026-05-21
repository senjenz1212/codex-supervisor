"""Target-aware FastAPI hook server (v0.3, ticket 03 cycle 1).

Public boundary: `hook_http_api`. Single endpoint `/hook/claude-code` accepts
raw Claude Code hook payloads, normalizes them through the configured target
adapter, audits the call into `hook_requests` with the raw payload redacted,
and returns a response shaped to the target's hook contract.

Operating mode is read from `cfg.modes.hook_blocking`:

  shadow → never deny; always return {"action": "allow"}; audit row records
           what we would have done (mode column = "shadow")
  advise → never deny; (next cycle: also send a Telegram FYI)
  enforce → (next cycle: deterministic deny rules then LLM critique)
  off    → bypass entirely (still allows; no audit row beyond the request line)

Deliberately NOT in this cycle (per ticket 03 TDD plan):
  - Deterministic destructive-command deny rules (next cycle)
  - LLM critique (next cycle, depends on anthropic and a mode_policy gate)
  - Telegram FYIs from advise mode (next cycle)
  - Malformed payload safe default with audit (next cycle)
"""
from __future__ import annotations
import logging
import time
import asyncio
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, Request

from .config import Config
from .state import State
from .target.protocol import TargetAgentAdapter
from .hook_rules import detect_destructive
from .desktop_visibility import desktop_status_health

if TYPE_CHECKING:
    # anthropic is only needed by future LLM critique paths. Importing it
    # under TYPE_CHECKING keeps the hook server importable in test/sandbox
    # environments that don't have the SDK installed.
    from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)


def build_app(cfg: Config, state: State, *,
              target_adapter: TargetAgentAdapter,
              anthropic: "AsyncAnthropic | None" = None,
              telegram_notifier: Any | None = None,
              hook_critic: Any | None = None,
              on_hook_seen=None) -> FastAPI:
    """Build the FastAPI app. `target_adapter` is required — the server is
    target-aware by construction. `anthropic` is reserved for the LLM-critique
    path added in a later cycle."""
    app = FastAPI(title="agent-supervisor hooks")

    async def _handle_hook(req: Request) -> dict[str, Any]:
        if on_hook_seen:
            on_hook_seen()

        t0 = time.monotonic()
        try:
            payload = await req.json()
            parse_error: str | None = None
        except Exception as e:
            # Malformed payload still goes through the audit path. Empty payload
            # normalizes to hook_kind=unknown via the adapter.
            payload = {}
            parse_error = str(e)[:200]

        # P3: normalize through the target adapter.
        hook_ev = await target_adapter.normalize_hook(payload)

        run_row = state.get_run_by_session(hook_ev.session_id)
        run_id = run_row["run_id"] if run_row else None

        mode = cfg.modes.hook_blocking

        # Deterministic destructive-command detector (no LLM in this path).
        # Only relevant for pre-tool-use; other hook kinds always allow.
        destructive_label: str | None = None
        if hook_ev.hook_kind == "pre_tool_use":
            destructive_label = detect_destructive(hook_ev.tool_args)

        model_verdict: dict[str, Any] | None = None
        critic_error: str | None = None
        if (
            cfg.supervisor.hook_critique_strategy == "model_first"
            and hook_critic is not None
        ):
            try:
                model_verdict = await asyncio.wait_for(
                    hook_critic.critique(
                        hook_ev,
                        raw_payload=payload,
                    ),
                    timeout=float(getattr(hook_critic, "timeout_s", 30.0)),
                )
            except Exception as e:
                critic_error = str(e)[:300]
                log.warning("hook critic failed; falling back to deterministic rules: %s", e)

        # P4 mode policy:
        #   shadow  → never deny; record what we would have done.
        #   advise  → never deny; record a recommendation in `actions` so
        #             ticket 06's Telegram path can pick it up.
        #   enforce → deny on destructive match; otherwise allow.
        #   off     → bypass; allow.
        model_deny_reason = None
        if model_verdict and model_verdict.get("action") == "deny":
            model_deny_reason = str(model_verdict.get("reason") or "model_denied")

        deny_reason = (
            model_deny_reason
            or (f"destructive_command:{destructive_label}" if destructive_label else None)
        )

        response: dict[str, Any]
        if deny_reason and mode == "enforce":
            response = {
                "action": "deny",
                "reason": deny_reason,
            }
        else:
            response = {"action": "allow"}

        audit_response = dict(response)
        if model_verdict is not None:
            audit_response["model_verdict"] = model_verdict
        if critic_error is not None:
            audit_response["model_critic_error"] = critic_error

        if deny_reason and mode == "advise":
            reason = deny_reason
            state.record_action(
                run_id=run_id or "unknown",
                action_type="recommend_block",
                requested_by="hook_critique_model" if model_deny_reason else "hook_critique",
                payload={
                    "reason": reason,
                    "model_verdict": model_verdict,
                    "hook_event": hook_ev.hook_kind,
                    "tool_name": hook_ev.tool_name,
                    "tool_args": hook_ev.tool_args,
                },
                status="recorded",
            )
            if telegram_notifier is not None:
                asyncio.create_task(
                    telegram_notifier.send_hook_recommendation(
                        session_id=hook_ev.session_id,
                        run_id=run_id,
                        reason=reason,
                        tool_name=hook_ev.tool_name,
                    )
                )

        latency = int((time.monotonic() - t0) * 1000)

        # If parsing failed, surface the error in the stored payload so the
        # audit row is informative.
        audited_payload: dict[str, Any] = (
            {"_parse_error": parse_error} if parse_error
            else payload
        )

        # P3: every hook call is audited with redacted raw payload + normalized
        # metadata. Redaction is applied inside state.write_hook_request.
        state.write_hook_request(
            run_id=run_id,
            hook_event=hook_ev.hook_kind,        # normalized — not raw
            tool_name=hook_ev.tool_name,
            payload=audited_payload,
            response=audit_response,
            latency_ms=latency,
            mode=mode,
        )
        if parse_error:
            response = {**response, "note": "supervisor_malformed_payload"}
        return response

    @app.post("/hook/claude-code")
    async def hook_claude_code(req: Request) -> dict[str, Any]:
        return await _handle_hook(req)

    @app.post("/hook/codex")
    async def hook_codex(req: Request) -> dict[str, Any]:
        return await _handle_hook(req)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "ts": int(time.time()),
            "target": target_adapter.kind,
            "modes": cfg.modes.model_dump(),
            **desktop_status_health(cfg.modes.desktop_status_sync),
        }

    return app


async def serve(cfg: Config, app: FastAPI) -> None:
    """Run the app via uvicorn. Imported lazily so tests can use TestClient
    without pulling in the full uvicorn boot path."""
    import uvicorn
    server_cfg = uvicorn.Config(
        app, host="127.0.0.1",
        port=cfg.supervisor.hook_server_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_cfg)
    await server.serve()
