"""Deterministic deny rules for destructive shell commands.

The detector is intentionally narrow: only patterns where the action is almost
always wrong. The LLM-critique path that handles ambiguous cases is deferred
to a later ticket.

Rule shape: each entry is `(label, compiled_regex)`. A match short-circuits.
"""
from __future__ import annotations
import json
import re
from typing import Any


DESTRUCTIVE_RULES: list[tuple[str, re.Pattern[str]]] = [
    # rm -rf with any modifier order (rm -fr, rm -Rf, rm -rfv ...).
    ("rm_recursive_force",
     re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\b", re.IGNORECASE)),
    ("rm_recursive_force_alt",
     re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\b", re.IGNORECASE)),
    # git push --force / -f.
    ("git_push_force",
     re.compile(r"\bgit\s+push\s+(--force\b|-f\b|--force-with-lease\b)",
                re.IGNORECASE)),
    # Credential / secret-file deletion.
    ("rm_credential_file",
     re.compile(r"\brm\s+(-\w+\s+)*[^\s;|&]*(?:\.env(?:\.\w+)?|\.pem|\.key"
                r"|credentials(?:\.\w+)?|id_rsa|id_ed25519)\b",
                re.IGNORECASE)),
    # SQL DROP — table or database.
    ("sql_drop",
     re.compile(r"\bdrop\s+(?:table|database)\b", re.IGNORECASE)),
    # Filesystem-level destruction.
    ("mkfs",     re.compile(r"\bmkfs(?:\.\w+)?\b", re.IGNORECASE)),
    ("dd_write", re.compile(r"\bdd\s+if=\S+\s+of=/dev/", re.IGNORECASE)),
    # Hard reset that discards local commits.
    ("git_hard_reset_to_origin",
     re.compile(r"\bgit\s+reset\s+--hard\s+origin", re.IGNORECASE)),
]


def detect_destructive(tool_args: Any) -> str | None:
    """Return a rule label if the args contain a destructive command, else None."""
    if not tool_args:
        return None

    # Common shapes: {"command": "..."}, {"cmd": "..."}, {"script": "..."}.
    candidates: list[str] = []
    if isinstance(tool_args, dict):
        for key in ("command", "cmd", "script"):
            v = tool_args.get(key)
            if isinstance(v, str):
                candidates.append(v)
        # Fall back to JSON dump if no canonical field was present.
        if not candidates:
            try:
                candidates.append(json.dumps(tool_args))
            except Exception:
                candidates.append(str(tool_args))
    elif isinstance(tool_args, str):
        candidates.append(tool_args)
    else:
        candidates.append(str(tool_args))

    for cmd in candidates:
        for label, pat in DESTRUCTIVE_RULES:
            if pat.search(cmd):
                return label
    return None
