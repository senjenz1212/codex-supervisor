"""CS20 Codex Desktop IPC capture/diff helper.

This module intentionally writes sanitized IPC frames only. It preserves method
names, stream patch paths, and discovery parameter keys, but drops prompt text,
patch values, raw params, and other payload values before anything reaches
disk.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

from .target.codex_desktop_ipc import (
    CodexDesktopIpcClient,
    CodexDesktopIpcError,
    diff_desktop_ipc_captures,
    sanitize_desktop_ipc_message,
    summarize_desktop_ipc_capture,
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            count = capture_sanitized_ipc(
                output_path=args.output,
                limit=args.limit,
                duration_s=args.duration_s,
                socket_path=args.socket_path,
                timeout_s=args.timeout_s,
            )
            print(f"captured={count} output={args.output}")
            return 0
        if args.command == "summary":
            summary = summarize_desktop_ipc_capture(args.input, phase=args.phase)
            _write_json(args.output, summary)
            return 0
        if args.command == "diff":
            diff = diff_desktop_ipc_captures(args.cold_start, args.normal_turn)
            _write_json(args.output, diff)
            return 0
    except (OSError, CodexDesktopIpcError) as e:
        print(f"desktop-ipc-probe failed: {e}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2


def capture_sanitized_ipc(
    *,
    output_path: str | Path,
    limit: int,
    duration_s: float,
    socket_path: str | None = None,
    timeout_s: int = 2,
) -> int:
    """Capture sanitized Desktop IPC frames for a bounded duration."""
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    client = CodexDesktopIpcClient(socket_path=socket_path, timeout_s=timeout_s)
    captured = 0
    deadline = time.monotonic() + max(0.0, float(duration_s))
    try:
        with path.open("w", encoding="utf-8") as f:
            while captured < limit and time.monotonic() < deadline:
                try:
                    message = client.recv_message()
                except socket.timeout:
                    continue
                except CodexDesktopIpcError as e:
                    if "timed out" in str(e).lower():
                        continue
                    raise
                f.write(json.dumps(
                    sanitize_desktop_ipc_message(message),
                    separators=(",", ":"),
                    sort_keys=True,
                ))
                f.write("\n")
                captured += 1
    finally:
        client.close()
    return captured


def _write_json(output_path: str | Path | None, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        print(text, end="")
        return
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="capture sanitized Desktop IPC frames")
    capture.add_argument("--phase", choices=["cold_start", "normal_turn"], required=True)
    capture.add_argument("--output", required=True)
    capture.add_argument("--limit", type=int, default=200)
    capture.add_argument("--duration-s", type=float, default=30)
    capture.add_argument("--socket-path")
    capture.add_argument("--timeout-s", type=int, default=2)

    summary = subparsers.add_parser("summary", help="summarize a sanitized or raw capture")
    summary.add_argument("--phase", choices=["cold_start", "normal_turn"], required=True)
    summary.add_argument("--input", required=True)
    summary.add_argument("--output")

    diff = subparsers.add_parser("diff", help="diff cold-start and normal-turn captures")
    diff.add_argument("--cold-start", required=True)
    diff.add_argument("--normal-turn", required=True)
    diff.add_argument("--output")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
