"""Target-independent types consumed by supervisor core.

These are the only types `drift_detector`, `replay`, `action_executor`, and
`runtime` are allowed to depend on. Adapters normalize raw target payloads
into these shapes; core logic must never read raw Claude Code or Codex
payloads directly (see forbidden-outcomes.md).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal


HookKind = Literal[
    "pre_tool_use",
    "post_tool_use",
    "permission_request",
    "user_prompt_submit",
    "stop",
    "session_start",
    "session_end",
    "unknown",
]


HealthState = Literal["healthy", "degraded", "unreachable", "unknown"]


@dataclass(frozen=True)
class NormalizedEvent:
    """A target-independent event derived from a target's raw observation stream."""
    source_target: str
    raw_kind: str
    kind: str
    session_id: str
    ts: int
    payload: dict[str, Any]
    redacted: bool = False


@dataclass(frozen=True)
class HookEvent:
    """A hook-time event from a target. The supervisor reacts to these in
    real-time within a tight latency budget."""
    source_target: str
    hook_kind: HookKind
    session_id: str
    tool_name: str | None
    tool_args: dict[str, Any]
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class TargetAction:
    """An action the supervisor wants the target to take.

    `kind` values mirror feature flags on the adapter: inject_steering,
    kill_session, restart, etc. Adapters that don't support a feature must
    return `delivered=False, reason="not_supported"` rather than raising.
    """
    kind: str
    session_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ScopeContract:
    """Immutable per-run contract describing what the run is allowed to touch."""
    allowed_paths: tuple[str, ...] = ()
    related_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    never_touch_patterns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "allowed_paths":        list(self.allowed_paths),
            "related_paths":        list(self.related_paths),
            "protected_paths":      list(self.protected_paths),
            "never_touch_patterns": list(self.never_touch_patterns),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScopeContract":
        return cls(
            allowed_paths=tuple(d.get("allowed_paths") or ()),
            related_paths=tuple(d.get("related_paths") or ()),
            protected_paths=tuple(d.get("protected_paths") or ()),
            never_touch_patterns=tuple(d.get("never_touch_patterns") or ()),
        )


@dataclass(frozen=True)
class TargetHealth:
    """Adapter health snapshot."""
    state: HealthState
    detail: str
    capabilities: dict[str, bool] = field(default_factory=dict)
