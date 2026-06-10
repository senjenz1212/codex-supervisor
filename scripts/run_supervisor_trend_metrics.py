#!/usr/bin/env python
"""Record, audit, and query supervisor quality trend metrics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from supervisor.quality_trends import (
    query_quality_trends,
    record_quality_trends_for_run,
    run_sampled_p11_false_accept_audit,
)
from supervisor.state import State


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", required=True, help="SQLite state DB path or Postgres DSN.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="Persist trend rows for one run.")
    record_parser.add_argument("--run-id", required=True)
    record_parser.add_argument("--task-id", default=None)
    record_parser.add_argument("--task-class", default=None)

    audit_parser = subparsers.add_parser("audit", help="Sample accepted P11 receipts and re-verify.")
    audit_parser.add_argument("--run-id", required=True)
    audit_parser.add_argument("--task-id", default=None)
    audit_parser.add_argument("--task-class", default=None)
    audit_parser.add_argument("--sample-size", type=int, default=10)
    audit_parser.add_argument("--test-timeout-s", type=int, default=120)

    query_parser = subparsers.add_parser("query", help="Read trend rows without writing.")
    query_parser.add_argument("--task-class", default=None)
    query_parser.add_argument("--gate", default=None)

    args = parser.parse_args()
    state = State(args.state_path)
    if args.command == "record":
        payload = {
            "rows": record_quality_trends_for_run(
                state,
                run_id=args.run_id,
                task_id=args.task_id,
                task_class=args.task_class,
            )
        }
    elif args.command == "audit":
        payload = run_sampled_p11_false_accept_audit(
            state,
            run_id=args.run_id,
            task_id=args.task_id,
            task_class=args.task_class,
            sample_size=args.sample_size,
            test_timeout_s=args.test_timeout_s,
        )
    else:
        payload = {
            "rows": query_quality_trends(
                state,
                task_class=args.task_class,
                gate=args.gate,
            ),
            "read_only": True,
        }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
