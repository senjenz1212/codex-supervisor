"""Pure scope-policy evaluator (drift L1).

Inputs:
  scope_contract: dict with allowed_paths / related_paths / protected_paths /
                  never_touch_patterns. (Or any object with .to_dict().)
  events:         iterable of normalized event dicts. Each must have `id` and
                  `kind`. Writes must carry `path`.

Output:
  list of finding dicts:
    {event_id, path, classification, severity}

Rules:
  - Only WRITE-like events generate findings. Reads, messages, plans, etc.
    never count as drift (a forbidden outcome in PRD §7).
  - Most-restrictive classification wins:
        never_touch  > protected  > allowed  > related  > out_of_scope
  - Findings are emitted ONLY for violations (out_of_scope, protected,
    never_touch). Allowed/related writes are silent.

This module is deliberately small and pure: no I/O, no model calls, no
SQLite. Ticket 04 cycle 4 wires the same function into the live
DriftDetector; ticket 05 already wires it into the replay path.
"""
from __future__ import annotations
import fnmatch
from typing import Any, Iterable


# Event kinds that count as writes. Add new shapes here when adapters surface
# them, NOT inside drift detector logic.
WRITE_KINDS: frozenset[str] = frozenset({
    "item.file_change",
    "file_change",
    "item.file_write",
    "patch",
    "apply_patch",
    "write_file",
    "edit",
})


SEVERITY: dict[str, str] = {
    "out_of_scope": "warn",
    "protected":    "severe",
    "never_touch":  "severe",
}


def _matches_pattern(path: str, pattern: str) -> bool:
    """fnmatch with `**/...` workaround.

    fnmatch's `*` does match `/`, so `**/.env*` against `src/auth/.env.local`
    works. But it also requires the `/` to be present, so root-level
    `.env.production` won't match `**/.env*`. We try both the full pattern and
    the prefix-stripped form to cover both placements.
    """
    if fnmatch.fnmatch(path, pattern):
        return True
    stripped = pattern
    while stripped.startswith("**/"):
        stripped = stripped[3:]
    if stripped != pattern and fnmatch.fnmatch(path, stripped):
        return True
    return False


def _classify_path(
    path: str,
    *,
    allowed: tuple[str, ...],
    related: tuple[str, ...],
    protected: tuple[str, ...],
    never_touch: tuple[str, ...],
) -> str:
    # Most-restrictive wins.
    for pat in never_touch:
        if _matches_pattern(path, pat):
            return "never_touch"
    for p in protected:
        if path.startswith(p):
            return "protected"
    for p in allowed:
        if path.startswith(p):
            return "allowed"
    for p in related:
        if path.startswith(p):
            return "related"
    return "out_of_scope"


def _as_contract_dict(scope_contract: Any) -> dict[str, Any]:
    if isinstance(scope_contract, dict):
        return scope_contract
    if hasattr(scope_contract, "to_dict"):
        return scope_contract.to_dict()
    return dict(getattr(scope_contract, "__dict__", {}))


def evaluate_scope(
    scope_contract: dict[str, Any] | Any,
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return scope findings for a stream of normalized events.

    Pure function — same inputs always produce the same output. Suitable for
    replay; suitable for unit tests; will be called by the live DriftDetector
    once it's rewired to operate over normalized events (ticket 04 cycle 4).
    """
    sc = _as_contract_dict(scope_contract)
    allowed   = tuple(sc.get("allowed_paths")        or ())
    related   = tuple(sc.get("related_paths")        or ())
    protected = tuple(sc.get("protected_paths")      or ())
    never     = tuple(sc.get("never_touch_patterns") or ())

    findings: list[dict[str, Any]] = []
    for ev in events:
        kind = ev.get("kind", "")
        if kind not in WRITE_KINDS:
            continue
        path = ev.get("path") or ev.get("file") or ev.get("filename")
        if not isinstance(path, str) or not path:
            continue
        cls = _classify_path(
            path,
            allowed=allowed, related=related,
            protected=protected, never_touch=never,
        )
        if cls in SEVERITY:
            findings.append({
                "event_id":       ev.get("id"),
                "path":           path,
                "classification": cls,
                "severity":       SEVERITY[cls],
            })
    return findings
