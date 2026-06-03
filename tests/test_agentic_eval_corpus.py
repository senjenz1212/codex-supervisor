from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from supervisor.agentic_eval_corpus import (
    LABELED_SET_SCHEMA_VERSION,
    compute_agentic_eval_roster_sha256,
    load_agentic_eval_labeled_set,
    mine_agentic_eval_cases,
)


SEED_FIXTURE = Path("tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml")
CURATED_PATH = Path("tests/fixtures/agentic_eval/agentic_lead_labeled_set.yaml")


def test_agentic_eval_labeled_set_loads_seed_fixture():
    corpus = load_agentic_eval_labeled_set(SEED_FIXTURE)

    assert corpus["schema_version"] == LABELED_SET_SCHEMA_VERSION
    assert corpus["execution_mode"] == "fixture_replay"
    assert 8 <= len(corpus["tasks"]) <= 12
    assert corpus["roster_sha256"] == compute_agentic_eval_roster_sha256(corpus["tasks"])
    assert corpus["provenance"]["curated"] is True

    task_ids = [task["task_id"] for task in corpus["tasks"]]
    assert len(task_ids) == len(set(task_ids))
    assert "clean-accept-runner-report" in task_ids
    assert "planning-artifact-deny" in task_ids

    expected_outcomes = {task["case"]["expected_outcome"] for task in corpus["tasks"]}
    assert {"accept", "revise", "deny"} <= expected_outcomes
    case_types = {case_type for task in corpus["tasks"] for case_type in task["case"].get("case_types", [])}
    assert {"artifact_only_gate", "multi_file_change"} <= case_types

    for task in corpus["tasks"]:
        assert set(task["budget"]) == {"total_tokens", "total_usd"}
        assert task["required_verdicts"]
        assert task["transcript_refs"]
        assert task["provenance"]["mined_from"]
        assert task["provenance"]["source_gate_result"] in {"accept", "revise", "deny"}


def test_agentic_eval_labeled_set_rejects_bad_schema_version(tmp_path):
    raw = _seed_raw()
    raw["schema_version"] = "agentic-lead-eval-labeled-set/v0"
    path = _write_yaml(tmp_path / "bad-schema.yaml", raw)

    with pytest.raises(ValueError, match="schema_version"):
        load_agentic_eval_labeled_set(path)


def test_agentic_eval_labeled_set_rejects_roster_sha_mismatch(tmp_path):
    raw = _seed_raw()
    raw["roster_sha256"] = "0" * 64
    path = _write_yaml(tmp_path / "bad-roster.yaml", raw)

    with pytest.raises(ValueError, match="roster_sha256"):
        load_agentic_eval_labeled_set(path)


def test_agentic_eval_labeled_set_rejects_missing_evidence_ref(tmp_path):
    raw = _seed_raw()
    raw["tasks"][0]["required_verdicts"][0]["evidence_ref"] = "tests/fixtures/agentic_eval/does-not-exist.json"
    raw["roster_sha256"] = compute_agentic_eval_roster_sha256(raw["tasks"])
    path = _write_yaml(tmp_path / "missing-evidence.yaml", raw)

    with pytest.raises(ValueError, match="missing evidence_ref"):
        load_agentic_eval_labeled_set(path)


def test_agentic_eval_labeled_set_rejects_missing_diff_hunk_label(tmp_path):
    raw = _seed_raw()
    multi_file_task = next(task for task in raw["tasks"] if task["task_id"] == "multi-file-change-accept")
    multi_file_task["required_verdicts"][0][
        "evidence_ref"
    ] = "tests/fixtures/agentic_eval/corpus_evidence/diff-hunks.patch:missing-hunk-label"
    raw["roster_sha256"] = compute_agentic_eval_roster_sha256(raw["tasks"])
    path = _write_yaml(tmp_path / "missing-diff-hunk-label.yaml", raw)

    with pytest.raises(ValueError, match="missing evidence_ref"):
        load_agentic_eval_labeled_set(path)


def test_agentic_eval_labeled_set_rejects_per_arm_budget(tmp_path):
    raw = _seed_raw()
    raw["tasks"][0]["budget"] = {
        "total_tokens": 12000,
        "total_usd": 3.5,
        "arms": {"lead_direct": {"total_tokens": 12000, "total_usd": 3.5}},
    }
    raw["roster_sha256"] = compute_agentic_eval_roster_sha256(raw["tasks"])
    path = _write_yaml(tmp_path / "per-arm-budget.yaml", raw)

    with pytest.raises(ValueError, match="budget"):
        load_agentic_eval_labeled_set(path)


def test_mine_agentic_eval_cases_is_deterministic(tmp_path):
    handoff_dir = tmp_path / ".handoff"
    _write_handoff_fixture(handoff_dir)

    first = mine_agentic_eval_cases(handoff_dir=handoff_dir, repo_root=tmp_path)
    second = mine_agentic_eval_cases(handoff_dir=handoff_dir, repo_root=tmp_path)

    assert first == second
    assert first["schema_version"] == "agentic-lead-eval-candidate-set/v1"
    assert first["source_schema_version"] == LABELED_SET_SCHEMA_VERSION
    assert first["execution_mode"] == "fixture_replay"
    assert first["curation_required"] is True
    assert [task["task_id"] for task in first["tasks"]] == [
        "clean-accept",
        "needs-revise",
        "policy-deny",
    ]
    assert [task["provenance"]["source_gate_result"] for task in first["tasks"]] == [
        "accept",
        "revise",
        "deny",
    ]
    for task in first["tasks"]:
        assert Path(tmp_path, task["required_verdicts"][0]["evidence_ref"]).exists()
        assert task["transcript_refs"][0]["kind"] == "cassette"


def test_miner_stages_candidates_without_touching_curated_corpus(tmp_path):
    handoff_dir = tmp_path / ".handoff"
    _write_handoff_fixture(handoff_dir)
    staging = tmp_path / ".scratch" / "agentic-eval" / "candidates.json"
    curated = tmp_path / "tests" / "fixtures" / "agentic_eval" / "agentic_lead_labeled_set.yaml"
    curated.parent.mkdir(parents=True)
    curated.write_text("curated: keep\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/mine_agentic_eval_cases.py",
            "--handoff-dir",
            str(handoff_dir),
            "--output",
            str(staging),
            "--repo-root",
            str(tmp_path),
            "--curated-corpus",
            str(curated),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert "curation_required" in result.stdout
    assert staging.exists()
    assert curated.read_text(encoding="utf-8") == "curated: keep\n"

    blocked = subprocess.run(
        [
            sys.executable,
            "scripts/mine_agentic_eval_cases.py",
            "--handoff-dir",
            str(handoff_dir),
            "--output",
            str(curated),
            "--repo-root",
            str(tmp_path),
            "--curated-corpus",
            str(curated),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )
    assert blocked.returncode != 0
    assert "refusing to write curated corpus" in blocked.stderr
    assert curated.read_text(encoding="utf-8") == "curated: keep\n"


def test_agentic_eval_corpus_replay_does_not_call_workflow_runner():
    called = False

    def forbidden_runner(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("corpus replay must not call a workflow runner")

    corpus = load_agentic_eval_labeled_set(SEED_FIXTURE)

    assert corpus["execution_mode"] == "fixture_replay"
    assert called is False
    assert "workflow_runner" not in inspect.signature(load_agentic_eval_labeled_set).parameters
    with pytest.raises(ValueError, match="execution_mode"):
        load_agentic_eval_labeled_set(SEED_FIXTURE, execution_mode="live_workflow")
    assert called is False


def _seed_raw() -> dict:
    return yaml.safe_load(SEED_FIXTURE.read_text(encoding="utf-8"))


def _write_yaml(path: Path, raw: dict) -> Path:
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _write_handoff_fixture(handoff_dir: Path) -> None:
    handoff_dir.mkdir(parents=True)
    _write_workflow_result(handoff_dir / "b-needs-revise-workflow-result.json", "needs-revise", "revise")
    _write_workflow_result(handoff_dir / "a-clean-accept-workflow-result.json", "clean-accept", "accept")
    _write_workflow_result(handoff_dir / "c-policy-deny-workflow-result.json", "policy-deny", "deny")


def _write_workflow_result(path: Path, task_id: str, decision: str) -> None:
    accepted = decision == "accept"
    status = "accepted" if accepted else "blocked"
    gate_result_status = "accepted" if accepted else decision
    path.write_text(
        json.dumps(
            {
                "run_id": f"run-{task_id}",
                "task_id": task_id,
                "current_gate": "outcome_review",
                "status": status,
                "steps": [
                    {"gate": "prd_review", "status": "accepted"},
                    {"gate": "issues_review", "status": "accepted"},
                    {"gate": "tdd_review", "status": "accepted"},
                    {"gate": "implementation_plan", "status": "accepted"},
                    {"gate": "execution", "status": "accepted"},
                    {"gate": "outcome_review", "status": status},
                ],
                "final_gate_result": {
                    "gate": "outcome_review",
                    "status": gate_result_status,
                    "codex_decision": decision,
                    "claude_decision": decision,
                    "probes": {"P1": {"status": "green"}, "P2": {"status": "green"}, "P3": {"status": "green"}},
                },
                "artifact_export": {
                    "status": "ok",
                    "files": [str(path.relative_to(path.parents[1]))],
                },
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
