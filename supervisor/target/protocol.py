"""TargetAgentAdapter protocol. The boundary between supervisor core and
target-specific behavior (Claude Code, Codex, future targets).

All target-specific I/O — hook payload shapes, session-file conventions,
resume command construction, version detection — lives in implementations
of this protocol. Supervisor core may consume only the typed surfaces
declared here.
"""
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

from .types import HookEvent, TargetAction, TargetHealth


@runtime_checkable
class TargetAgentAdapter(Protocol):
    """Stable boundary used by supervisor core to interact with a target agent."""

    @property
    def kind(self) -> str:
        """Adapter identity. One of: 'claude_code', 'codex'."""
        ...

    async def health(self) -> TargetHealth:
        """Probe the target. Return a TargetHealth even on degraded / unreachable —
        adapters never raise to communicate degraded state."""
        ...

    async def normalize_hook(self, raw_payload: dict[str, Any]) -> HookEvent:
        """Normalize a raw hook payload from this target into a `HookEvent`.
        Unknown hook kinds normalize to `hook_kind='unknown'` rather than raising."""
        ...

    async def execute_action(self, action: TargetAction) -> dict[str, Any]:
        """Execute a `TargetAction`. Return delivery metadata — never raise to
        communicate 'not supported' or 'temporary failure'."""
        ...

    def supports_feature(self, feature: str) -> bool:
        """Capability probe. Used by callers to short-circuit before constructing
        an action that would just degrade to `not_supported`."""
        ...
