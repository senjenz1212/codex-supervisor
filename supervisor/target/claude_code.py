"""ClaudeCodeAdapter — v1 production target.

What's implemented here in ticket 01:
  - kind identity
  - health() returning a structured (degraded, stub) state
  - normalize_hook() mapping Claude Code's hook event names to normalized HookKind
  - execute_action() honestly reporting `not_supported` / `not_implemented`
  - supports_feature() capability probe

Deferred to later tickets:
  - real liveness probing of a Claude Code session
  - transcript JSONL tailing → normalized event stream (event ingestion API)
  - steering injection through the Claude Code SDK
"""
from __future__ import annotations
from typing import Any

from .types import HookEvent, TargetAction, TargetHealth


# Capability flags the supervisor checks before constructing an action.
# False here means execute_action() will return delivered=False, reason="not_supported".
_CAPABILITIES: dict[str, bool] = {
    "hooks":            True,
    "permission_hook":  True,
    "rollout_tail":     False,   # transcripts wired in a later ticket
    "inject_steering":  False,   # SDK path wired in a later ticket
    "kill_session":     False,
    "restart":          False,
}

# Claude Code hook event name → normalized HookKind.
_HOOK_KIND_MAP: dict[str, str] = {
    "PreToolUse":        "pre_tool_use",
    "PostToolUse":       "post_tool_use",
    "PermissionRequest": "permission_request",
    "UserPromptSubmit":  "user_prompt_submit",
    "Stop":              "stop",
    "SessionStart":      "session_start",
    "SessionEnd":        "session_end",
}


class ClaudeCodeAdapter:
    """Target adapter for Claude Code. v1 production target."""

    kind = "claude_code"

    def __init__(self, target_cfg: dict[str, Any] | None = None) -> None:
        self._cfg = dict(target_cfg or {})

    async def health(self) -> TargetHealth:
        # Until liveness probing is wired, report degraded with full capability map
        # so callers can make accurate "not_supported" decisions at the boundary.
        return TargetHealth(
            state="degraded",
            detail="claude_code_adapter_v1_skeleton",
            capabilities=dict(_CAPABILITIES),
        )

    async def normalize_hook(self, raw_payload: dict[str, Any]) -> HookEvent:
        raw_kind = (raw_payload.get("hook_event")
                    or raw_payload.get("event")
                    or raw_payload.get("type")
                    or "unknown")
        hook_kind = _HOOK_KIND_MAP.get(raw_kind, "unknown")
        return HookEvent(
            source_target=self.kind,
            hook_kind=hook_kind,                                 # type: ignore[arg-type]
            session_id=str(raw_payload.get("session_id", "")),
            tool_name=raw_payload.get("tool_name"),
            tool_args=(raw_payload.get("tool_args")
                       or raw_payload.get("arguments")
                       or {}),
            raw_payload=raw_payload,
        )

    async def execute_action(self, action: TargetAction) -> dict[str, Any]:
        if not self.supports_feature(action.kind):
            return {"delivered": False,
                    "reason": "not_supported",
                    "feature": action.kind,
                    "target": self.kind}
        # Feature is enabled in capabilities but not yet wired:
        return {"delivered": False,
                "reason": "not_implemented",
                "feature": action.kind,
                "target": self.kind}

    def supports_feature(self, feature: str) -> bool:
        return _CAPABILITIES.get(feature, False)
