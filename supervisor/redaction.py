"""Secret redaction.

A pure function `redact()` walks dict/list/str structures and rewrites any
strings matching known secret patterns into `[REDACTED_*]` markers. Applied
at every storage and notification boundary (state writes + Telegram).

Patterns are ordered specific-first so the most informative marker wins:
  PEM blocks → Bearer/Basic auth → Anthropic keys → OpenAI keys →
  GitHub tokens → generic KEY=VALUE where KEY ends in _KEY/_TOKEN/_SECRET/
  _PASSWORD/PASSWORD.

Markers ARE deliberate — reviewers should see redaction happened, not just
think the field was empty.
"""
from __future__ import annotations
import re
from typing import Any


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM-encoded private/public keys (multi-line).
    (re.compile(r"-----BEGIN [A-Z ]+?KEY-----.*?-----END [A-Z ]+?KEY-----",
                re.DOTALL),
     "[REDACTED_PEM]"),
    # HTTP Authorization headers (Bearer, Basic).
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+"),
     r"\1[REDACTED_BEARER]"),
    # Standalone bearer token snippets inside JSON values or exception text.
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
     r"\1[REDACTED_BEARER]"),
    # Anthropic-style API keys: sk-ant-...
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
     "[REDACTED_API_KEY]"),
    # OpenAI-style keys: sk-... and sk-proj-...
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{6,}"),
     "[REDACTED_API_KEY]"),
    # GitHub personal access tokens.
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
     "[REDACTED_GITHUB_TOKEN]"),
    # KEY=VALUE / KEY: VALUE where KEY ends in a secret-like suffix.
    (re.compile(
        r"((?i:[A-Z][A-Z0-9_]*?(?:_KEY|_TOKEN|_SECRET|_PASSWORD|PASSWORD))"
        r"\s*[=:]\s*)\S+"),
     r"\1[REDACTED]"),
]


def _redact_str(s: str) -> str:
    out = s
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact(value: Any) -> Any:
    """Recursively redact secrets from strings inside dict/list/tuple/str values.
    Returns a new structure; the input is not mutated."""
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    return value


def redact_for_telegram(text: str) -> str:
    """Redact a single text string bound for Telegram delivery."""
    return _redact_str(text)
