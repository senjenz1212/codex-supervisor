#!/usr/bin/env python3
"""Run repeated live failure-mode probes and aggregate cohort evidence."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from supervisor.probe_cohorts import summarize_probe_cohort
from supervisor.redaction import redact
from supervisor.stability_proposals import stability_proposals_for_cohort


def main(argv: list[str] | None = None) -> int:
    return main_with_args(argv)


def main_with_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a live failure-mode probe cohort.")
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output-root", default="docs/dual-agent")
    parser.add_argument("--fixture-root", default="tests/fixtures/dual_agent")
    parser.add_argument("--timeout-s", type=int, default=900)
    parser.add_argument("--budget-usd", type=float, default=100.0)
    parser.add_argument("--skip-cursor", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path.cwd().resolve()
    output_root = (repo_root / args.output_root).resolve()
    fixture_root = (repo_root / args.fixture_root).resolve()
    cohort_dir = output_root / args.cohort_id
    cohort_dir.mkdir(parents=True, exist_ok=True)

    trial_summaries: list[dict[str, Any]] = []
    for index in range(1, max(1, int(args.trials)) + 1):
        trial_id = f"{args.cohort_id}-trial-{index:02d}"
        output_dir = output_root / trial_id
        fixture_dir = fixture_root / trial_id.replace("-", "_")
        command = [
            sys.executable,
            str(repo_root / "scripts" / "probe_live_failure_mode.py"),
            "--task-id",
            trial_id,
            "--run-id",
            trial_id,
            "--output-dir",
            _path_arg(output_dir, repo_root),
            "--fixture-dir",
            _path_arg(fixture_dir, repo_root),
            "--timeout-s",
            str(max(1, int(args.timeout_s))),
            "--budget-usd",
            str(float(args.budget_usd)),
        ]
        if args.skip_cursor:
            command.append("--skip-cursor")
        result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
        summary_path = output_dir / "summary.json"
        summary = _read_summary(summary_path)
        summary["trial_id"] = trial_id
        summary["cohort_id"] = args.cohort_id
        summary["returncode"] = result.returncode
        summary["stdout_tail"] = result.stdout[-4000:]
        summary["stderr_tail"] = result.stderr[-4000:]
        if result.returncode != 0 and summary.get("status") != "unexpected":
            summary["status"] = "unexpected"
            summary.setdefault("final_failure", {
                "reason": "probe_subprocess_failed",
                "details": {"returncode": result.returncode},
            })
        trial_summaries.append(summary)

    cohort = summarize_probe_cohort(
        cohort_id=args.cohort_id,
        trial_summaries=trial_summaries,
    )
    proposals = stability_proposals_for_cohort(cohort)
    _write_json(cohort_dir / "cohort-summary.json", cohort)
    _write_json(cohort_dir / "stability-proposals.json", {"proposals": proposals})
    (cohort_dir / "cohort-summary.md").write_text(_render_cohort_markdown(cohort, proposals), encoding="utf-8")
    print(json.dumps(redact(cohort), sort_keys=True))
    return 0 if cohort["unexpected_count"] == 0 else 1


def _read_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "unexpected", "artifact_export": {"output_dir": str(path.parent)}}
    return json.loads(path.read_text(encoding="utf-8"))


def _path_arg(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render_cohort_markdown(cohort: dict[str, Any], proposals: list[dict[str, Any]]) -> str:
    lines = [
        f"# Probe Cohort {cohort['cohort_id']}",
        "",
        f"- Classification: `{cohort['classification']}`",
        f"- Trials: `{cohort['trial_count']}`",
        f"- Unexpected: `{cohort['unexpected_count']}`",
        f"- MAST failures: `{cohort['failure_counts_by_mast_code']}`",
        "",
        "## Trials",
        "",
        "| Trial | Status | MAST | Cost | Tokens In | Artifact |",
        "|---|---|---|---:|---:|---|",
    ]
    for trial in cohort["trials"]:
        lines.append(
            "| {trial_id} | {status} | {mast} | {cost:.6f} | {tokens} | {artifact} |".format(
                trial_id=trial["trial_id"],
                status=trial["status"],
                mast=trial.get("mast_code") or "",
                cost=float(trial.get("cost_usd") or 0.0),
                tokens=int(trial.get("tokens_in") or 0),
                artifact=trial.get("artifact_dir") or "",
            )
        )
    lines.extend(["", "## Stability Proposals", ""])
    if not proposals:
        lines.append("No stability proposals generated.")
    for proposal in proposals:
        lines.append(
            f"- `{proposal['proposal_id']}` `{proposal['mast_code']}`: {proposal['proposed_change']}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
