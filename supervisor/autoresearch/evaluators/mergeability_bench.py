#!/usr/bin/env python
"""Hash-pinned AutoResearch evaluator for the local mergeability bench."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-worktree", required=True)
    parser.add_argument("--trial-index", required=True, type=int)
    parser.add_argument("--metric-name", required=True)
    parser.add_argument("--attempt-json", required=True)
    args = parser.parse_args()

    source_root = Path(os.environ.get("AUTORESEARCH_SOURCE_ROOT") or ".").resolve()
    sys.path.insert(0, str(source_root))

    from supervisor.mergeability_bench import (  # pylint: disable=import-outside-toplevel
        grade_mergeability_candidate,
        load_mergeability_candidate,
        load_mergeability_task,
        result_receipt,
    )

    attempt = json.loads(Path(args.attempt_json).read_text(encoding="utf-8"))
    bench_root = Path(os.environ.get("MERGEABILITY_BENCH_ROOT") or source_root / "tests/fixtures/mergeability_bench").resolve()
    task_id = str(
        attempt.get("mergeability_task_id")
        or os.environ.get("MERGEABILITY_TASK_ID")
        or attempt.get("task_id")
        or "calculator-addition"
    )
    control = attempt.get("evaluator_quality_control") or {}
    control_kind = str(control.get("kind") or os.environ.get("AUTORESEARCH_CONTROL_KIND") or "")
    candidate_ref = _candidate_ref_for_attempt(
        attempt,
        control_kind=control_kind,
        bench_root=bench_root,
    )

    task = load_mergeability_task(bench_root, task_id)
    candidate = load_mergeability_candidate(candidate_ref)
    output_dir = Path(os.environ.get("MERGEABILITY_OUTPUT_DIR") or Path(args.attempt_json).parent / "mergeability-results")
    result = grade_mergeability_candidate(
        task,
        candidate,
        bench_root=bench_root,
        output_dir=output_dir,
        timeout_s=float(os.environ.get("MERGEABILITY_TIMEOUT_S") or 30.0),
    )
    result_ref = (output_dir / f"{result.task_id}-{result.candidate_id}.json").as_posix()
    receipt = result_receipt(result, result_ref=result_ref)
    print(json.dumps({
        "metric_name": args.metric_name,
        "metric_value": float(result.final_score),
        "metrics": {
            "mergeability_score": float(result.final_score),
            "blocker_status": result.blocker_status,
            "hidden_test_status": result.hidden_test_status,
            "reverse_test_status": result.reverse_test_status,
            "scope_status": result.scope_status,
            "lint_build_status": result.lint_build_status,
        },
        "evidence_refs": [f"mergeability_result:{result_ref}"],
        "receipts": [receipt],
        "runtime_native_receipt": receipt,
        "cost_usd": 0.0,
        "trial_index": args.trial_index,
    }, sort_keys=True))
    return 0


def _candidate_ref_for_attempt(
    attempt: dict[str, Any],
    *,
    control_kind: str,
    bench_root: Path,
) -> Path:
    if control_kind in {"noop", "harmful", "known_good", "determinism"}:
        candidate_name = "known_good" if control_kind == "determinism" else control_kind
        if candidate_name == "harmful":
            candidate_name = "known_bad"
        return bench_root / "candidates" / f"{candidate_name}.json"
    raw = (
        attempt.get("mergeability_candidate_ref")
        or attempt.get("patch_ref")
        or os.environ.get("MERGEABILITY_CANDIDATE_REF")
        or "candidates/known_good.json"
    )
    candidate = Path(str(raw))
    if candidate.is_absolute():
        return candidate
    attempt_ref = Path(os.environ.get("AUTORESEARCH_ATTEMPT_JSON") or "").parent / candidate
    if attempt_ref.exists():
        return attempt_ref
    source_root = Path(os.environ.get("AUTORESEARCH_SOURCE_ROOT") or ".").resolve()
    source_ref = source_root / candidate
    if source_ref.exists():
        return source_ref
    return bench_root / candidate


if __name__ == "__main__":
    raise SystemExit(main())
