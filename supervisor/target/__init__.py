"""Target adapter boundary.

The supervisor's core depends only on the names re-exported here. Concrete
adapters (`claude_code`, `codex`) and any of their raw payload handling stay
inside this package.
"""
from .types import (
    NormalizedEvent, HookEvent, TargetAction,
    ScopeContract, TargetHealth, HookKind, HealthState,
)
from .protocol import TargetAgentAdapter
from .factory import build_target_adapter

__all__ = [
    "NormalizedEvent", "HookEvent", "TargetAction",
    "ScopeContract", "TargetHealth", "HookKind", "HealthState",
    "TargetAgentAdapter", "build_target_adapter",
]
