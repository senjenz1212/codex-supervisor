"""Adapter selection. Reads `cfg.target.kind` and constructs the right adapter.

Imports are placed inside the branches so that, e.g., choosing Claude Code does
not pull in Codex-only modules — which the PRD forbids (P1 forbidden outcomes).
"""
from __future__ import annotations
from typing import Any

from .protocol import TargetAgentAdapter


def build_target_adapter(cfg: Any) -> TargetAgentAdapter:
    """Construct the configured target adapter.

    Expects `cfg.target.kind` to be set (Config infers it for back-compat).
    """
    kind = getattr(getattr(cfg, "target", None), "kind", None)
    if kind == "claude_code":
        from .claude_code import ClaudeCodeAdapter
        sub = getattr(cfg.target, "claude_code", None)
        return ClaudeCodeAdapter(sub.model_dump() if sub is not None else None)
    if kind == "codex":
        from .codex import CodexAdapter
        sub = getattr(cfg.target, "codex", None)
        legacy = getattr(cfg, "codex", None)
        # Prefer target.codex; fall back to legacy top-level `codex:` block.
        sub_dict: dict[str, Any] | None = None
        if sub is not None:
            sub_dict = sub.model_dump()
        elif legacy is not None:
            sub_dict = legacy.model_dump()
        return CodexAdapter(sub_dict)
    raise ValueError(f"unknown target.kind: {kind!r}")
