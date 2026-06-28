from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import supervisor.swe_bench_official_oracle as official_oracle
from supervisor.swe_bench_official_oracle import run_swe_bench_pro_oracle


PACKET_DIR = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "dual-agent"
    / "pro-oracle-gold-proof-20260626"
)
MANIFEST_PATH = PACKET_DIR / "artifacts" / "gold-proof-manifest.json"


def _write_pro_scripts(tmp_path: Path, instance_id: str) -> Path:
    scripts_dir = tmp_path / "run_scripts"
    instance_dir = scripts_dir / instance_id
    instance_dir.mkdir(parents=True)
    (instance_dir / "run_script.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'running selected tests\\n'\n",
        encoding="utf-8",
    )
    (instance_dir / "parser.py").write_text(
        "import json, sys\njson.dump({'tests': []}, open(sys.argv[3], 'w'))\n",
        encoding="utf-8",
    )
    return scripts_dir


def _pro_context(tmp_path: Path, *, instance_id: str = "instance_NodeBB__NodeBB-demo") -> dict:
    model_patch = "diff --git a/app.js b/app.js\n--- a/app.js\n+++ b/app.js\n"
    return {
        "instance_id": instance_id,
        "candidate_id": "gold-candidate",
        "repo": "NodeBB/NodeBB",
        "base_commit": "abc123",
        "dockerhub_tag": "nodebb.nodebb-demo",
        "model_patch": model_patch,
        "model_patch_sha256": sha256(model_patch.encode("utf-8")).hexdigest(),
        "fail_to_pass": ["tests/test_parser.py::test_hidden"],
        "pass_to_pass": ["tests/test_parser.py::test_existing"],
        "selected_test_files_to_run": ["tests/test_parser.py"],
        "swe_bench_pro_scripts_dir": str(_write_pro_scripts(tmp_path, instance_id)),
    }


def _artifact_json(manifest: dict, key: str) -> dict:
    rel_path = manifest["artifacts"][key]["path"]
    return json.loads((PACKET_DIR / rel_path).read_text(encoding="utf-8"))


def test_gold_run_receipt_is_oracle_good():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    receipt = _artifact_json(manifest, "result")
    output = _artifact_json(manifest, "output_json")
    patch_apply = _artifact_json(manifest, "patch_apply")
    test_command = _artifact_json(manifest, "test_command")

    assert manifest["status"] == "completed"
    assert manifest["authority_flags"] == {
        "metric_applyable": False,
        "improvement_claim_allowed": False,
        "powered_improvement_claim_allowed": False,
        "human_mergeability_claim_allowed": False,
        "default_change_allowed": False,
        "policy_mutated": False,
        "gate_advanced": False,
    }
    assert manifest["dataset_row"]["fail_to_pass_count"] > 0
    assert manifest["dataset_row"]["pass_to_pass_count"] > 0
    assert manifest["dataset_row"]["selected_test_count"] > 0
    assert manifest["parsed_test_count"] > 0

    assert receipt["fail_to_pass_status"] == "pass"
    assert receipt["pass_to_pass_status"] == "pass"
    assert receipt["patch_applied"] is True
    adapter_receipt = receipt["oracle_adapter_receipt"]
    assert adapter_receipt["test_command_return_code"] == 0
    assert adapter_receipt["patch_applied"] is True
    assert patch_apply["patch_applied"] is True
    assert test_command["test_command_return_code"] == 0
    assert output["tests"]


def test_empty_parser_output_is_unavailable_not_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "pull ok", "")
        if command[:2] == ["docker", "run"]:
            volume_arg = command[command.index("-v") + 1]
            workspace = Path(volume_arg.split(":", 1)[0])
            (workspace / "patch_apply.json").write_text(
                json.dumps({"patch_applied": True}),
                encoding="utf-8",
            )
            (workspace / "test_command.json").write_text(
                json.dumps({"test_command_return_code": 1}),
                encoding="utf-8",
            )
            (workspace / "output.json").write_text(
                json.dumps({"tests": []}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "run ok", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "pro_parser_output_empty"
    assert result["patch_applied"] is True
    receipt = result["oracle_adapter_receipt"]
    assert receipt["test_command_return_code"] == 1
    assert receipt["unavailable_reason"] == "pro_parser_output_empty"


def test_missing_patch_apply_receipt_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "pull ok", "")
        if command[:2] == ["docker", "run"]:
            volume_arg = command[command.index("-v") + 1]
            workspace = Path(volume_arg.split(":", 1)[0])
            (workspace / "test_command.json").write_text(
                json.dumps({"test_command_return_code": 0}),
                encoding="utf-8",
            )
            (workspace / "output.json").write_text(
                json.dumps({
                    "tests": [
                        {
                            "name": "tests/test_parser.py::test_hidden",
                            "status": "PASSED",
                        },
                        {
                            "name": "tests/test_parser.py::test_existing",
                            "status": "PASSED",
                        },
                    ]
                }),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "run ok", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "patch_apply_receipt_missing_or_malformed"
    assert result["patch_applied"] is None


def test_before_repo_setup_does_not_erase_patch_before_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    captured_entryscript: dict[str, str] = {}

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "pull ok", "")
        if command[:2] == ["docker", "run"]:
            volume_arg = command[command.index("-v") + 1]
            workspace = Path(volume_arg.split(":", 1)[0])
            captured_entryscript["text"] = (workspace / "entryscript.sh").read_text(
                encoding="utf-8"
            )
            (workspace / "patch_apply.json").write_text(
                json.dumps({"patch_applied": True}),
                encoding="utf-8",
            )
            (workspace / "test_command.json").write_text(
                json.dumps({"test_command_return_code": 0}),
                encoding="utf-8",
            )
            (workspace / "output.json").write_text(
                json.dumps({
                    "tests": [
                        {
                            "name": "tests/test_parser.py::test_hidden",
                            "status": "PASSED",
                        },
                        {
                            "name": "tests/test_parser.py::test_existing",
                            "status": "PASSED",
                        },
                    ]
                }),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "run ok", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)
    context = _pro_context(tmp_path)
    context["before_repo_set_cmd"] = "\n".join([
        "git reset --hard abc123",
        "git clean -fd",
        "git checkout abc123",
        "git checkout issue456 -- tests/test_parser.py",
    ])

    result = run_swe_bench_pro_oracle(context)

    assert result["fail_to_pass_status"] == "pass"
    script = captured_entryscript["text"]
    reset_index = script.index("git reset --hard abc123")
    clean_index = script.index("git clean -fd")
    base_checkout_index = script.index("git checkout abc123")
    apply_index = script.index("git apply -v /workspace/patch.diff")
    hidden_checkout_index = script.index(
        "git checkout issue456 -- tests/test_parser.py"
    )
    run_index = script.index("if bash /workspace/run_script.sh")

    assert reset_index < clean_index < base_checkout_index < apply_index
    assert apply_index < hidden_checkout_index < run_index
