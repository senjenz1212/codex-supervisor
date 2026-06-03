#!/usr/bin/env python3
"""Mine review-staged agentic eval corpus candidates from workflow results."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from supervisor.agentic_eval_corpus import (
    DEFAULT_CURATED_CORPUS_PATH,
    mine_agentic_eval_cases,
    write_agentic_eval_candidate_set,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", default=".handoff", help="Directory containing *-workflow-result.json files.")
    parser.add_argument(
        "--output",
        default=".scratch/agentic-eval/candidates.json",
        help="Review staging output path. Must not be the curated corpus path.",
    )
    parser.add_argument("--repo-root", default=".", help="Repo root for relative evidence refs.")
    parser.add_argument(
        "--curated-corpus",
        default=str(DEFAULT_CURATED_CORPUS_PATH),
        help="Curated corpus path to protect from automatic writes.",
    )
    parser.add_argument("--clean-accept-limit", type=int, default=2)
    parser.add_argument("--total-tokens", type=int, default=12000)
    parser.add_argument("--total-usd", type=float, default=3.5)
    args = parser.parse_args(argv)

    try:
        candidate_set = mine_agentic_eval_cases(
            handoff_dir=Path(args.handoff_dir),
            repo_root=Path(args.repo_root),
            clean_accept_limit=args.clean_accept_limit,
            total_tokens=args.total_tokens,
            total_usd=args.total_usd,
        )
        output = write_agentic_eval_candidate_set(
            candidate_set,
            Path(args.output),
            curated_corpus_path=Path(args.curated_corpus),
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "staged",
                "curation_required": True,
                "output": str(output),
                "candidate_count": len(candidate_set["tasks"]),
                "roster_sha256": candidate_set["roster_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
