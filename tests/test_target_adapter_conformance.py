"""Ticket 01, next TDD cycle: target_adapter_conformance (PRD promises P1, P2).

Shared conformance suite. The same assertions run against both
ClaudeCodeAdapter and CodexAdapter — that's the proof that the adapter
boundary is real (T5 grill finding: Codex compatibility is a conformance
test, not a special case).

What's tested:
  - health() returns a TargetHealth with a documented state and capability map
  - normalize_hook() maps all five documented Codex hook events to normalized HookKind
  - normalize_hook() degrades unknown event names to hook_kind="unknown" without raising
  - supports_feature() returns False for unknown features without raising
  - execute_action() returns delivered=False, reason ∈ {"not_supported", ...} without raising

Forbidden outcomes this guards against:
  - "Permission requests are treated as generic unknown events"
  - adapter raising to signal degraded state (must return TargetHealth instead)
"""
from __future__ import annotations
import typing

import pytest

from supervisor.target.claude_code import ClaudeCodeAdapter
from supervisor.target.codex import CodexAdapter
from supervisor.target.types import (
    HookEvent, TargetAction, TargetHealth,
)


# Each adapter under test runs the full suite. If you add a new adapter,
# add a fixture entry here and the entire suite must pass for it too.
ADAPTERS = [
    pytest.param(ClaudeCodeAdapter, id="claude_code"),
    pytest.param(CodexAdapter,      id="codex"),
]


VALID_HEALTH_STATES = {"healthy", "degraded", "unreachable", "unknown"}

# Documented hook events. The PRD says permission_request must NOT be treated
# as generic unknown. SessionStart/SessionEnd are normal lifecycle mappings,
# not edge cases — adapter must handle them now (ticket 03 AC).
DOCUMENTED_HOOKS = {
    "PreToolUse":        "pre_tool_use",
    "PostToolUse":       "post_tool_use",
    "PermissionRequest": "permission_request",
    "UserPromptSubmit":  "user_prompt_submit",
    "Stop":              "stop",
    "SessionStart":      "session_start",
    "SessionEnd":        "session_end",
}


# ---------- health() ----------

@pytest.mark.parametrize("adapter_cls", ADAPTERS)
@pytest.mark.asyncio
async def test_health_returns_valid_target_health(adapter_cls):
    adapter = adapter_cls()
    health = await adapter.health()
    assert isinstance(health, TargetHealth)
    assert health.state in VALID_HEALTH_STATES, (
        f"{adapter_cls.__name__}: health.state must be one of {VALID_HEALTH_STATES}"
    )
    assert isinstance(health.detail, str) and health.detail
    assert isinstance(health.capabilities, dict)
    # Capabilities must include the canonical feature names.
    for required_feature in ("hooks", "permission_hook", "inject_steering"):
        assert required_feature in health.capabilities, (
            f"{adapter_cls.__name__}: health.capabilities missing '{required_feature}'"
        )


# ---------- normalize_hook() ----------

@pytest.mark.parametrize("adapter_cls", ADAPTERS)
@pytest.mark.parametrize("raw_kind,normalized", list(DOCUMENTED_HOOKS.items()))
@pytest.mark.asyncio
async def test_normalize_hook_maps_documented_events(adapter_cls, raw_kind, normalized):
    """Forbidden outcome: permission requests treated as generic unknown events."""
    adapter = adapter_cls()
    payload = {
        "hook_event": raw_kind,
        "session_id": "sess-conformance",
        "tool_name":  "shell",
        "tool_args":  {"cmd": "echo hi"},
    }
    ev = await adapter.normalize_hook(payload)
    assert isinstance(ev, HookEvent)
    assert ev.source_target == adapter.kind
    assert ev.hook_kind == normalized, (
        f"{adapter_cls.__name__}: {raw_kind!r} must normalize to {normalized!r}, "
        f"not {ev.hook_kind!r}"
    )
    assert ev.session_id == "sess-conformance"
    assert ev.tool_name == "shell"
    assert ev.tool_args == {"cmd": "echo hi"}
    # Raw payload preserved for audit (P3).
    assert ev.raw_payload == payload


@pytest.mark.parametrize("adapter_cls", ADAPTERS)
@pytest.mark.asyncio
async def test_normalize_hook_unknown_kind_degrades_safely(adapter_cls):
    """Adapters must degrade, not raise, on novel hook event names."""
    adapter = adapter_cls()
    ev = await adapter.normalize_hook({
        "hook_event": "SomeNewHookEvent",
        "session_id": "sess-x",
    })
    assert ev.hook_kind == "unknown"
    assert ev.source_target == adapter.kind


# ---------- supports_feature() ----------

@pytest.mark.parametrize("adapter_cls", ADAPTERS)
def test_supports_feature_unknown_is_false_not_raise(adapter_cls):
    adapter = adapter_cls()
    assert adapter.supports_feature("hooks") is True, (
        f"{adapter_cls.__name__}: 'hooks' must be supported"
    )
    # Unknown feature: False, no exception.
    assert adapter.supports_feature("teleportation") is False


# ---------- execute_action() ----------

@pytest.mark.parametrize("adapter_cls", ADAPTERS)
@pytest.mark.asyncio
async def test_execute_action_unsupported_returns_not_supported(adapter_cls):
    """Adapters that don't support a feature must return delivered=False with
    reason 'not_supported' (T1-grill: don't special-case in core; the adapter
    is honest about what it can't do)."""
    adapter = adapter_cls()
    action = TargetAction(kind="teleportation",
                          session_id="sess-x",
                          payload={})
    result = await adapter.execute_action(action)
    assert isinstance(result, dict)
    assert result.get("delivered") is False
    assert result.get("reason") == "not_supported"
    assert result.get("feature") == "teleportation"
    assert result.get("target") == adapter.kind


# ---------- protocol conformance ----------

@pytest.mark.parametrize("adapter_cls", ADAPTERS)
def test_adapter_satisfies_protocol(adapter_cls):
    """Runtime-checkable Protocol: each adapter must satisfy TargetAgentAdapter."""
    from supervisor.target.protocol import TargetAgentAdapter
    adapter = adapter_cls()
    assert isinstance(adapter, TargetAgentAdapter), (
        f"{adapter_cls.__name__} does not satisfy TargetAgentAdapter"
    )
