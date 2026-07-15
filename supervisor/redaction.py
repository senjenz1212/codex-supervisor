"""Secret redaction.

A pure function `redact()` walks dict/list/str structures and rewrites secret
patterns in string values and JSON object keys into `[REDACTED_*]` markers.
Applied at every storage and notification boundary (state writes + Telegram).

Patterns are ordered specific-first so the most informative marker wins:
  PEM blocks → Bearer/Basic auth → Anthropic keys → OpenAI keys →
  GitHub tokens → generic KEY=VALUE where KEY ends in _KEY/_TOKEN/_SECRET/
  _PASSWORD/PASSWORD.

Markers ARE deliberate — reviewers should see redaction happened, not just
think the field was empty.

Ledger rules are versioned: v1 preserves the historical values-only behavior;
v2 also redacts object keys and rejects key collisions.
"""
from __future__ import annotations
import re
from typing import Any, Callable


_COMMON_LEADING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
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
)
_COMMON_TRAILING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # GitHub personal access tokens.
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
     "[REDACTED_GITHUB_TOKEN]"),
    # Cursor API keys.
    (re.compile(r"crsr_[A-Za-z0-9]{16,}"),
     "[REDACTED_CURSOR_KEY]"),
    # KEY=VALUE / KEY: VALUE where KEY ends in a secret-like suffix.
    (re.compile(
        r"((?i:[A-Z][A-Z0-9_]*?(?:_KEY|_TOKEN|_SECRET|_PASSWORD|PASSWORD))"
        r"\s*[=:]\s*)\S+"),
     r"\1[REDACTED]"),
)

# v1 must reproduce the original values-only rules byte-for-byte, including
# their historical tendency to redact an embedded ``sk-`` substring.
_V1_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    *_COMMON_LEADING_PATTERNS,
    # Anthropic-style API keys: sk-ant-...
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),
     "[REDACTED_API_KEY]"),
    # OpenAI-style keys: sk-... and sk-proj-...
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{6,}"),
     "[REDACTED_API_KEY]"),
    *_COMMON_TRAILING_PATTERNS,
)
_V2_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    *_COMMON_LEADING_PATTERNS,
    # Avoid corrupting ordinary identifiers that merely contain ``sk-``.
    (re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{6,}"),
     "[REDACTED_API_KEY]"),
    (re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_\-]{6,}"),
     "[REDACTED_API_KEY]"),
    *_COMMON_TRAILING_PATTERNS,
)
_PATTERNS = _V2_PATTERNS


def _redact_str_with_patterns(
    s: str,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
) -> str:
    out = s
    for pat, repl in patterns:
        out = pat.sub(repl, out)
    return out


def _disambiguate_redacted_key(
    key: str,
    existing: dict[Any, Any],
) -> str:
    suffix = 2
    while True:
        candidate = (
            f"{key[:-1]}_{suffix}]"
            if key.endswith("]")
            else f"{key}_{suffix}"
        )
        if candidate not in existing:
            return candidate
        suffix += 1


def _redact_with_patterns(
    value: Any,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
    *,
    redact_keys: bool,
) -> Any:
    if isinstance(value, str):
        return _redact_str_with_patterns(value, patterns)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            redacted_key = (
                _redact_str_with_patterns(key, patterns)
                if redact_keys and isinstance(key, str)
                else key
            )
            if redacted_key in redacted:
                redacted_key = _disambiguate_redacted_key(
                    str(redacted_key),
                    redacted,
                )
            redacted[redacted_key] = _redact_with_patterns(
                item,
                patterns,
                redact_keys=redact_keys,
            )
        return redacted
    if isinstance(value, list):
        return [
            _redact_with_patterns(
                item,
                patterns,
                redact_keys=redact_keys,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _redact_with_patterns(
                item,
                patterns,
                redact_keys=redact_keys,
            )
            for item in value
        )
    return value


def _build_frozen_redactor(
    patterns: tuple[tuple[re.Pattern[str], str], ...],
    *,
    redact_keys: bool,
) -> Callable[[Any], Any]:
    frozen_patterns = tuple(patterns)
    frozen_redact_keys = bool(redact_keys)

    def apply(value: Any) -> Any:
        if isinstance(value, str):
            out = value
            for pattern, replacement in frozen_patterns:
                out = pattern.sub(replacement, out)
            return out
        if isinstance(value, dict):
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                redacted_key = (
                    apply(key)
                    if frozen_redact_keys and isinstance(key, str)
                    else key
                )
                if redacted_key in redacted:
                    raise ValueError(
                        "redaction produced duplicate object key: "
                        f"{redacted_key!r}"
                    )
                redacted[redacted_key] = apply(item)
            return redacted
        if isinstance(value, list):
            return [apply(item) for item in value]
        if isinstance(value, tuple):
            return tuple(apply(item) for item in value)
        return value

    return apply


redact_v1 = _build_frozen_redactor(
    _V1_PATTERNS,
    redact_keys=False,
)
redact_v1.__name__ = "redact_v1"
redact_v1.__doc__ = (
    "Apply the frozen v1 rules used to validate persisted ledgers."
)
redact_v2 = _build_frozen_redactor(
    _V2_PATTERNS,
    redact_keys=True,
)
redact_v2.__name__ = "redact_v2"
redact_v2.__doc__ = (
    "Apply the frozen v2 rules, including JSON object-key redaction."
)


def redact(value: Any) -> Any:
    """Recursively redact secrets without mutating the input."""
    return _redact_with_patterns(
        value,
        _PATTERNS,
        redact_keys=True,
    )


def redact_for_telegram(text: str) -> str:
    """Redact a single text string bound for Telegram delivery."""
    return _redact_str_with_patterns(text, _PATTERNS)
