#!/usr/bin/env python
"""Run the fixture-only Supervisor AutoResearch foundation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from supervisor.autoresearch.orchestrator import run_autoresearch_fixture
from supervisor.state import State


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="Path to AutoResearch fixture JSON.")
    parser.add_argument("--output-dir", required=True, help="Directory for report.json.")
    parser.add_argument("--state-path", default="", help="Optional SQLite state path.")
    parser.add_argument("--run-id", default="autoresearch-fixture-run", help="Ledger run id.")
    parser.add_argument("--repo-root", default=".", help="Repository root for artifact validation.")
    parser.add_argument("--execution-mode", default="fixture_replay")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()

    if args.execution_mode != "fixture_replay" and not args.allow_live:
        raise RuntimeError("Supervisor AutoResearch live execution is disabled by default; pass --allow-live explicitly")

    output_dir = Path(args.output_dir)
    state_path = Path(args.state_path) if args.state_path else output_dir / "autoresearch-state.db"
    state = State(str(state_path))
    report = run_autoresearch_fixture(
        fixture_path=args.fixture,
        state=state,
        run_id=args.run_id,
        repo_root=args.repo_root,
        output_dir=output_dir,
    )
    print(json.dumps({
        "report_path": str(output_dir / "report.json"),
        "report_sha256": report["report_sha256"],
        "default_change_allowed": report["default_change_allowed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
