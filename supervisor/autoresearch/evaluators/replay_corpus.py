#!/usr/bin/env python
"""Default replay-corpus evaluator for report-only AutoResearch experiments."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an attempt against the pinned agentic eval corpus.")
    parser.add_argument("--attempt-worktree", required=True)
    parser.add_argument("--trial-index", required=True, type=int)
    parser.add_argument("--metric-name", required=True)
    parser.add_argument("--attempt-json", required=True)
    args = parser.parse_args()

    source_root = Path(os.environ.get("AUTORESEARCH_SOURCE_ROOT") or ".").resolve()
    attempt_worktree = Path(args.attempt_worktree).resolve()
    sys.path.insert(0, str(source_root))

    from supervisor.agentic_eval_corpus import (  # pylint: disable=import-outside-toplevel
        DEFAULT_CURATED_CORPUS_PATH,
        load_agentic_eval_labeled_set,
    )
    from supervisor.replay import replay_from_fixtures  # pylint: disable=import-outside-toplevel

    corpus_path = Path(os.environ.get("AUTORESEARCH_CORPUS_PATH") or DEFAULT_CURATED_CORPUS_PATH)
    if not corpus_path.is_absolute():
        corpus_path = source_root / corpus_path
    corpus = load_agentic_eval_labeled_set(corpus_path, repo_root=source_root)
    tasks = _selected_tasks(corpus["tasks"])
    passed = [
        task for task in tasks
        if _task_replay_evidence_passes(
            task,
            source_root=source_root,
            attempt_worktree=attempt_worktree,
            replay_from_fixtures=replay_from_fixtures,
        )
    ]
    pass_rate = (len(passed) / len(tasks)) if tasks else 0.0
    print(json.dumps({
        "metric_name": args.metric_name,
        "metric_value": round(float(pass_rate), 12),
        "metrics": {
            "pass_rate": round(float(pass_rate), 12),
            "passed_cases": len(passed),
            "total_cases": len(tasks),
        },
        "cost_usd": 0.0,
        "trial_index": args.trial_index,
    }, sort_keys=True))
    return 0


def _selected_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selection = os.environ.get("AUTORESEARCH_CORPUS_TASK_IDS", "").strip()
    if not selection:
        return tasks
    selected_ids = {item.strip() for item in selection.split(",") if item.strip()}
    return [task for task in tasks if str(task.get("task_id")) in selected_ids]


def _task_replay_evidence_passes(
    task: dict[str, Any],
    *,
    source_root: Path,
    attempt_worktree: Path,
    replay_from_fixtures: Any,
) -> bool:
    for verdict in task.get("required_verdicts") or []:
        ref = str(verdict.get("evidence_ref") or "")
        if not _ref_exists(ref, source_root=source_root, attempt_worktree=attempt_worktree):
            return False
    for transcript in task.get("transcript_refs") or []:
        if _is_replay_fixture_ref(transcript):
            if not _replay_fixture_passes(
                transcript,
                source_root=source_root,
                attempt_worktree=attempt_worktree,
                replay_from_fixtures=replay_from_fixtures,
            ):
                return False
            continue
        ref = str(transcript.get("ref") or "")
        if not _ref_exists(ref, source_root=source_root, attempt_worktree=attempt_worktree):
            return False
    return True


def _is_replay_fixture_ref(transcript: dict[str, Any]) -> bool:
    kind = str(transcript.get("kind") or "")
    ref = transcript.get("ref")
    return kind in {"replay_fixture", "supervisor_replay"} and isinstance(ref, dict)


def _replay_fixture_passes(
    transcript: dict[str, Any],
    *,
    source_root: Path,
    attempt_worktree: Path,
    replay_from_fixtures: Any,
) -> bool:
    ref = transcript.get("ref")
    if not isinstance(ref, dict):
        return False
    try:
        replay_from_fixtures(
            snapshot_path=_resolve_path(str(ref["snapshot"]), source_root=source_root, attempt_worktree=attempt_worktree),
            events_path=_resolve_path(str(ref["events"]), source_root=source_root, attempt_worktree=attempt_worktree),
            model_outputs_path=_resolve_path(str(ref["model_outputs"]), source_root=source_root, attempt_worktree=attempt_worktree),
        )
        return True
    except Exception:
        return False


def _ref_exists(ref: str, *, source_root: Path, attempt_worktree: Path) -> bool:
    if not ref:
        return False
    path_ref = ref.split(":", 1)[0] if ":" in ref else ref
    return _resolve_path(path_ref, source_root=source_root, attempt_worktree=attempt_worktree).exists()


def _resolve_path(ref: str, *, source_root: Path, attempt_worktree: Path) -> Path:
    candidate = Path(ref)
    if candidate.is_absolute():
        return candidate
    worktree_candidate = attempt_worktree / candidate
    if worktree_candidate.exists():
        return worktree_candidate
    return source_root / candidate


if __name__ == "__main__":
    raise SystemExit(main())
