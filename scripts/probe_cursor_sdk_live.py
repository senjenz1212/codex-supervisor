#!/usr/bin/env python3
"""Run or record the live Cursor SDK tri-agent probe.

The script always writes an inspectable JSON fixture. If CURSOR_API_KEY is not
present, the fixture records a skipped diagnostic instead of pretending the live
probe ran.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from supervisor.cursor_agent import CursorInvocationRequest, invoke_cursor_agent
from supervisor.redaction import redact


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe live Cursor SDK review boundary.")
    parser.add_argument("--output-dir", default="", help="Directory for summary.json and transcript.txt.")
    parser.add_argument("--timeout-s", type=int, default=300)
    args = parser.parse_args()

    output_dir = Path(args.output_dir or _default_output_dir()).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "probe": "live_cursor_sdk_probe",
        "created_at": int(time.time()),
        "cursor_sdk_import": "ok" if importlib.util.find_spec("cursor_sdk") else "missing",
        "api_key_present": bool(os.environ.get("CURSOR_API_KEY")),
    }
    if not summary["api_key_present"]:
        summary.update({
            "status": "skipped",
            "reason": "missing_cursor_api_key",
        })
        _write_fixture(output_dir, summary, transcript="")
        print(json.dumps(summary, sort_keys=True))
        return 0

    with tempfile.TemporaryDirectory(prefix="codex-supervisor-cursor-probe-") as tmp:
        sandbox = Path(tmp) / "sandbox-repo"
        sandbox.mkdir()
        subprocess.run(["git", "init"], cwd=sandbox, check=True, capture_output=True, text=True)
        (sandbox / "README.md").write_text(
            "# Cursor SDK Live Probe\n\nCursor should review this repository without editing it.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "README.md"], cwd=sandbox, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", "seed cursor probe"],
            cwd=sandbox,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Probe", "GIT_AUTHOR_EMAIL": "probe@example.com", "GIT_COMMITTER_NAME": "Probe", "GIT_COMMITTER_EMAIL": "probe@example.com"},
        )
        request = CursorInvocationRequest(
            task_id="live-cursor-sdk-probe",
            gate="outcome_review",
            instruction=(
                "Review this tiny repository read-only. Accept if README.md exists "
                "and no implementation is required. Do not edit files."
            ),
            cwd=sandbox,
            claude_outcome={
                "task_id": "live-cursor-sdk-probe",
                "summary": "Claude outcome fixture says the README exists.",
                "decisions": ["accept"],
                "claims": ["README.md exists"],
            },
            timeout_s=max(1, int(args.timeout_s)),
            expected_decisions=("accept",),
        )
        result = invoke_cursor_agent(request)
        summary.update({
            "status": "completed" if result.probe.ok else "blocked",
            "probe": result.probe.__dict__,
            "accepted": bool(result.outcome and result.probe.ok),
            "agent_id": result.agent_id,
            "run_id": result.run_id,
            "cursor_status": result.status,
            "model": result.model,
            "duration_ms": result.duration_ms,
            "outcome": result.outcome.model_dump() if result.outcome is not None else None,
        })
        _write_fixture(output_dir, summary, transcript=result.transcript)
        print(json.dumps(redact(summary), sort_keys=True))
        return 0 if result.probe.ok else 1


def _default_output_dir() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"docs/dual-agent/live-cursor-sdk-probe-{stamp}"


def _write_fixture(output_dir: Path, summary: dict, *, transcript: str) -> None:
    (output_dir / "summary.json").write_text(
        json.dumps(redact(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if transcript:
        (output_dir / "transcript.txt").write_text(redact(transcript), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
