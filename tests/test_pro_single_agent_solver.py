from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from supervisor.mergeability_bench import _resolve_powered_baseline_decision
from supervisor.swe_bench_mergeability import (
    ORACLE_BAD_LABEL,
    ORACLE_GOOD_LABEL,
    SwebenchMergeabilityFixtureRunnerError,
    build_swe_bench_pro_candidate_corpus,
)
from supervisor.swe_bench_mergeability_cli import _command_generator
from supervisor.swe_bench_solver import solver_attempts_for_candidate_corpus


def _write_public_worktree(tmp_path: Path) -> Path:
    public = tmp_path / "public-worktree"
    public.mkdir()
    (public / "module.py").write_text("value = 0\n", encoding="utf-8")
    return public


def _generator_input(public_worktree: Path) -> dict:
    return {
        "schema_version": "supervisor-swebench-mergeability-live-generator-input/v1",
        "arm": "baseline",
        "instance_id": "instance_demo__repo-123",
        "repo": "demo/repo",
        "base_commit": "public-base",
        "problem_statement": "Change the value.",
        "public_worktree_ref": str(public_worktree),
        "public_worktree_sha256": "public-sha",
        "public_worktree_manifest": [
            {
                "path": "module.py",
                "sha256": sha256((public_worktree / "module.py").read_bytes()).hexdigest(),
            }
        ],
        "generation_config": {
            "model": "fixture-model",
            "provider": "fixture-provider",
            "timeout_s": 10,
        },
        "prompt_hash": sha256(b"fixture-prompt").hexdigest(),
        "generator_input_hash": sha256(b"fixture-generator-input").hexdigest(),
    }


def _write_fake_runner(tmp_path: Path) -> Path:
    runner = tmp_path / "fake_single_agent.py"
    runner.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "attempt = int(os.environ['SWEBENCH_SOLVER_ATTEMPT_INDEX'])\n"
        "calls = Path(os.environ['SWEBENCH_SOLVER_FAKE_CALLS'])\n"
        "records = []\n"
        "if calls.exists():\n"
        "    records = json.loads(calls.read_text(encoding='utf-8'))\n"
        "records.append({\n"
        "    'attempt_index': attempt,\n"
        "    'solver': os.environ['SWEBENCH_SOLVER_SOLVER'],\n"
        "    'model': os.environ['SWEBENCH_SOLVER_MODEL'],\n"
        "    'provider': os.environ['SWEBENCH_SOLVER_PROVIDER'],\n"
        "    'worktree': str(Path.cwd()),\n"
        "})\n"
        "calls.write_text(json.dumps(records, sort_keys=True), encoding='utf-8')\n"
        "Path('module.py').write_text(f'value = {attempt}\\n', encoding='utf-8')\n"
        "if attempt == 2:\n"
        "    Path('new_file.py').write_text('created = True\\n', encoding='utf-8')\n"
        "output = Path(os.environ['SWEBENCH_SOLVER_ATTEMPT_OUTPUT'])\n"
        "output.write_text(json.dumps({\n"
        "    'accept': attempt == 1,\n"
        "    'cost_usd': 0.01,\n"
        "    'token_usage': {'input_tokens': attempt, 'output_tokens': attempt + 10},\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return runner


def _solver_command(runner: Path, *, k: int = 3, budget: float = 1.0) -> str:
    runner_command = f"{sys.executable} {runner}"
    parts = [
        sys.executable,
        "-m",
        "supervisor.swe_bench_solver",
        "--solver",
        "fixture-agent",
        "--runner-command",
        runner_command,
        "--allow-live",
        "--max-budget-usd",
        f"{budget:.2f}",
        "--k",
        str(k),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _run_solver_generator(tmp_path: Path, *, k: int = 3) -> tuple[dict, Path, Path]:
    public = _write_public_worktree(tmp_path)
    runner = _write_fake_runner(tmp_path)
    calls = tmp_path / "runner-calls.json"
    os.environ["SWEBENCH_SOLVER_FAKE_CALLS"] = str(calls)
    generator = _command_generator(
        _solver_command(runner, k=k),
        output_dir=tmp_path / "generator-out",
        arm="baseline",
    )
    return dict(generator(_generator_input(public))), public, calls


def test_generator_emits_k_attempts_with_diffs(tmp_path):
    payload, public, calls = _run_solver_generator(tmp_path, k=3)

    assert (public / "module.py").read_text(encoding="utf-8") == "value = 0\n"
    call_records = json.loads(calls.read_text(encoding="utf-8"))
    assert [record["attempt_index"] for record in call_records] == [1, 2, 3]
    assert {record["solver"] for record in call_records} == {"fixture-agent"}
    assert {record["model"] for record in call_records} == {"fixture-model"}
    assert {record["provider"] for record in call_records} == {"fixture-provider"}

    attempts = payload["attempts"]
    assert len(attempts) == 3
    assert payload["model_patch"] == attempts[0]["model_patch"]
    assert payload["candidate_id"] == attempts[0]["candidate_id"]
    assert payload["single_agent_baseline_decision"] == attempts[0][
        "single_agent_baseline_decision"
    ]

    patches = [attempt["model_patch"] for attempt in attempts]
    assert len(set(patches)) == 3
    assert all("-value = 0" in patch for patch in patches)
    assert "+value = 1" in patches[0]
    assert "+value = 2" in patches[1]
    assert "+value = 3" in patches[2]
    assert "new_file.py" in patches[1]
    assert all("swebench_solver" not in patch for patch in patches)

    for index, attempt in enumerate(attempts, start=1):
        patch_hash = sha256(attempt["model_patch"].encode("utf-8")).hexdigest()
        receipt = attempt["single_agent_baseline_decision"]
        assert attempt["candidate_artifact_hash"] == patch_hash
        assert receipt["candidate_artifact_hash"] == patch_hash
        assert receipt["candidate_id"] == attempt["candidate_id"]
        assert receipt["decision_source"] == "single_agent_candidate_generation"
        assert receipt["accept"] is (index == 1)
        assert receipt["producer"] == {
            "model": "fixture-model",
            "provider": "fixture-provider",
            "runner_label": "fixture-agent",
        }
        assert receipt["prompt_sha256"]


def test_baseline_receipt_passes_resolver(tmp_path):
    payload, _public, _calls = _run_solver_generator(tmp_path, k=2)
    attempt = payload["attempts"][0]
    receipt = attempt["single_agent_baseline_decision"]

    decision = _resolve_powered_baseline_decision(
        raw=receipt,
        expected_candidate_artifact_hash=attempt["candidate_artifact_hash"],
        expected_candidate_id=attempt["candidate_id"],
    )

    assert decision["unavailable"] is False
    assert decision["accept"] is True
    assert decision["decision_source"] == "single_agent_candidate_generation"
    assert decision["producer"]["provider"] == "fixture-provider"


def test_baseline_receipt_rejected_on_hash_mismatch(tmp_path):
    payload, _public, _calls = _run_solver_generator(tmp_path, k=2)
    attempt = payload["attempts"][0]
    receipt = deepcopy(attempt["single_agent_baseline_decision"])
    receipt["candidate_artifact_hash"] = "0" * 64

    decision = _resolve_powered_baseline_decision(
        raw=receipt,
        expected_candidate_artifact_hash=attempt["candidate_artifact_hash"],
        expected_candidate_id=attempt["candidate_id"],
    )

    assert decision["unavailable"] is True
    assert decision["accept"] is False
    assert decision["evidence_kind"] == "hash_mismatch"
    assert decision["unavailable_reason"] == "candidate_artifact_hash_mismatch"


@pytest.mark.parametrize(
    "missing_field",
    ["candidate_id", "model", "provider", "runner_label"],
)
def test_baseline_receipt_rejected_when_replay_evidence_missing(
    tmp_path,
    missing_field,
):
    payload, _public, _calls = _run_solver_generator(tmp_path, k=2)
    attempt = payload["attempts"][0]
    receipt = deepcopy(attempt["single_agent_baseline_decision"])
    if missing_field == "candidate_id":
        receipt.pop("candidate_id")
    else:
        receipt["producer"].pop(missing_field)

    decision = _resolve_powered_baseline_decision(
        raw=receipt,
        expected_candidate_artifact_hash=attempt["candidate_artifact_hash"],
        expected_candidate_id=attempt["candidate_id"],
    )

    assert decision["unavailable"] is True
    assert decision["evidence_kind"] == "malformed"
    assert missing_field in decision["unavailable_reason"]


def test_runner_subprocess_env_scrubs_oracle_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_DOCKERHUB_USERNAME", "leaky-user")
    monkeypatch.setenv("FAIL_TO_PASS", "hidden::test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaky-key")
    monkeypatch.setenv("SWEBENCH_SOLVER_KEEP_THIS", "ok-token")
    public = _write_public_worktree(tmp_path)
    runner = tmp_path / "env_check_runner.py"
    env_dump = tmp_path / "env-dump.json"
    runner.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        f"Path({str(env_dump)!r}).write_text(json.dumps(dict(os.environ)), encoding='utf-8')\n"
        "Path('module.py').write_text('value = 1\\n', encoding='utf-8')\n"
        "output = Path(os.environ['SWEBENCH_SOLVER_ATTEMPT_OUTPUT'])\n"
        "output.write_text(json.dumps({'accept': True, 'cost_usd': 0.0,"
        " 'token_usage': {'input_tokens': 1, 'output_tokens': 1}}),"
        " encoding='utf-8')\n",
        encoding="utf-8",
    )
    generator = _command_generator(
        _solver_command(runner, k=2),
        output_dir=tmp_path / "generator-out",
        arm="baseline",
    )
    generator(_generator_input(public))

    dumped = json.loads(env_dump.read_text(encoding="utf-8"))
    assert "SWEBENCH_PRO_ORACLE_DOCKERHUB_USERNAME" not in dumped
    assert "FAIL_TO_PASS" not in dumped
    assert "ANTHROPIC_API_KEY" not in dumped
    assert dumped.get("SWEBENCH_SOLVER_KEEP_THIS") == "ok-token"
    assert dumped.get("SWEBENCH_SOLVER_ATTEMPT_OUTPUT")


def test_generator_reads_only_public_packet(tmp_path):
    public = _write_public_worktree(tmp_path)
    runner = _write_fake_runner(tmp_path)
    calls = tmp_path / "runner-calls.json"
    os.environ["SWEBENCH_SOLVER_FAKE_CALLS"] = str(calls)
    generator_input = _generator_input(public)
    generator_input["fail_to_pass"] = ["hidden::test"]
    generator = _command_generator(
        _solver_command(runner, k=2),
        output_dir=tmp_path / "generator-out",
        arm="baseline",
    )

    with pytest.raises(SwebenchMergeabilityFixtureRunnerError, match="forbidden"):
        generator(generator_input)

    assert not calls.exists()


def test_generator_attempts_feed_candidate_corpus_without_filtering_rejected_diffs(tmp_path):
    payload, _public, _calls = _run_solver_generator(tmp_path, k=3)
    attempts = solver_attempts_for_candidate_corpus(payload)
    assert [attempt["single_agent_baseline_decision"]["accept"] for attempt in attempts] == [
        True,
        False,
        False,
    ]

    def oracle_runner(attempt):
        return {
            "fail_to_pass_status": "pass"
            if str(attempt["candidate_id"]).endswith("attempt-1")
            else "fail",
            "pass_to_pass_status": "pass",
            "patch_applied": True,
        }

    report = build_swe_bench_pro_candidate_corpus(
        attempts=attempts,
        output_path=tmp_path / "pro-predictions.jsonl",
        oracle_runner=oracle_runner,
    )

    assert report["metric_applyable"] is False
    assert len(report["rows"]) == 3
    labels = [row["oracle_label"] for row in report["rows"]]
    assert labels == [ORACLE_GOOD_LABEL, ORACLE_BAD_LABEL, ORACLE_BAD_LABEL]
    assert report["rows"][1]["single_agent_baseline_decision"]["accept"] is False


def test_solver_refuses_without_live_consent(tmp_path, monkeypatch):
    public = _write_public_worktree(tmp_path)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(_generator_input(public)), encoding="utf-8")
    monkeypatch.setenv("SWEBENCH_MERGEABILITY_GENERATOR_INPUT", str(input_path))
    monkeypatch.setenv("SWEBENCH_MERGEABILITY_GENERATOR_OUTPUT", str(output_path))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "supervisor.swe_bench_solver",
            "--solver",
            "fixture-agent",
            "--runner-command",
            sys.executable,
            "--max-budget-usd",
            "1.0",
            "--k",
            "2",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "without --allow-live" in result.stderr
    assert not output_path.exists()
