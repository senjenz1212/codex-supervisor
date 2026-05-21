"""Read-only workspace grounding for Claude review suggestions."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .redaction import redact
from .state import State


SECRET_PATH_PARTS = {
    ".env",
    ".env.local",
    ".git",
}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SECRET_NAME_PARTS = ("credential", "secret", "token")


def workspace_snapshot(
    state: State,
    *,
    run_id: str,
    max_diff_chars: int = 12_000,
) -> dict[str, Any]:
    root = resolve_workspace_root(state, run_id=run_id)
    if root is None:
        return {"status": "not_found", "workspace": None}
    status = _git(root, "status", "--short")
    changed = _changed_files(root)
    diff = _git(root, "diff", "--", *changed) if changed else ""
    if len(diff) > max_diff_chars:
        diff = diff[: max(0, max_diff_chars - 20)].rstrip() + "\n...[truncated]"
    return redact({
        "status": "ok",
        "workspace": {
            "root": str(root),
            "git_status": status,
            "changed_files": changed,
            "diff": diff,
        },
    })


def read_workspace_file(
    state: State,
    *,
    run_id: str,
    path: str,
    max_bytes: int = 20_000,
) -> dict[str, Any]:
    root = resolve_workspace_root(state, run_id=run_id)
    if root is None:
        return {"status": "not_found", "file": None}
    if _is_secret_path(path):
        return {"status": "denied", "reason": "secret_path"}
    try:
        target = (root / path).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return {"status": "denied", "reason": "outside_workspace"}
    if not target.is_file():
        return {"status": "not_found", "file": None}
    try:
        data = target.read_bytes()[:max(1, int(max_bytes))]
    except OSError as e:
        return {"status": "error", "error": str(e)}
    if b"\x00" in data:
        return {"status": "denied", "reason": "binary_file"}
    text = data.decode("utf-8", errors="replace")
    truncated = target.stat().st_size > len(data)
    return redact({
        "status": "ok",
        "file": {
            "root": str(root),
            "path": str(target.relative_to(root)),
            "content": text,
            "truncated": truncated,
        },
    })


def resolve_workspace_root(state: State, *, run_id: str) -> Path | None:
    for ev in reversed(state.recent_events(run_id, n=200)):
        for candidate in _workspace_candidates(ev):
            path = Path(candidate).expanduser()
            if path.exists() and path.is_dir():
                return path.resolve()
    return None


def _workspace_candidates(event: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            out.append(value.strip())

    add(event.get("cwd"))
    add(event.get("workdir"))
    payload = event.get("payload")
    if isinstance(payload, dict):
        add(payload.get("cwd"))
        add(payload.get("workdir"))
        args = payload.get("arguments")
        parsed = _parse_json(args)
        if isinstance(parsed, dict):
            add(parsed.get("cwd"))
            add(parsed.get("workdir"))
    return out


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    return out.stdout.rstrip()


def _changed_files(root: Path) -> list[str]:
    names = _git(root, "diff", "--name-only").splitlines()
    status_names: list[str] = []
    for line in _git(root, "status", "--short").splitlines():
        if len(line) >= 4:
            status_names.append(line[3:].strip())
    return _dedupe([n for n in [*names, *status_names] if n and not _is_secret_path(n)])


def _is_secret_path(path: str) -> bool:
    p = Path(path)
    parts = {part.casefold() for part in p.parts}
    name = p.name.casefold()
    return (
        bool(parts & SECRET_PATH_PARTS)
        or name.startswith(".env")
        or name.endswith(SECRET_SUFFIXES)
        or any(part in name for part in SECRET_NAME_PARTS)
    )


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
