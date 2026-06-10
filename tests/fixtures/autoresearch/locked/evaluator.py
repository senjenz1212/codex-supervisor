#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic AutoResearch evaluator fixture.")
    parser.add_argument("--attempt-worktree", required=True)
    parser.add_argument("--trial-index", required=True, type=int)
    parser.add_argument("--metric-name", required=True)
    parser.add_argument("--attempt-json", required=True)
    args = parser.parse_args()

    worktree = Path(args.attempt_worktree)
    trial_path = worktree / "skills" / "reviewer-rubrics" / f"trial-{args.trial_index}.txt"
    trial_path.parent.mkdir(parents=True, exist_ok=True)
    trial_path.write_text(f"trial {args.trial_index}\n", encoding="utf-8")

    print(json.dumps({
        "metric_name": args.metric_name,
        "metric_value": 0.86 + (args.trial_index * 0.01),
        "cost_usd": 0.001,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
