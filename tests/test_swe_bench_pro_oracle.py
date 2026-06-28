from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

import supervisor.swe_bench_official_oracle as official_oracle
from supervisor.swe_bench_mergeability import (
    _interpret_oracle_outcome,
    _normalise_oracle_adapter_outcome,
)
from supervisor.swe_bench_official_oracle import run_swe_bench_pro_oracle


def _write_pro_scripts(tmp_path: Path, instance_id: str) -> Path:
    scripts_dir = tmp_path / "run_scripts"
    instance_dir = scripts_dir / instance_id
    instance_dir.mkdir(parents=True)
    (instance_dir / "run_script.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'running %s\\n' \"$@\"\n",
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
        "frozen_decisions_path": str(tmp_path / "frozen_decisions.json"),
        "frozen_decisions_sha256": "frozen-sha",
        "fail_to_pass": ["tests/test_parser.py::test_hidden"],
        "pass_to_pass": ["tests/test_parser.py::test_existing"],
        "before_repo_set_cmd": "git clean -fd\ngit checkout abc123 -- tests/test_parser.py",
        "selected_test_files_to_run": ["tests/test_parser.py"],
        "swe_bench_pro_scripts_dir": str(_write_pro_scripts(tmp_path, instance_id)),
    }


def _fake_docker_runner(tmp_path: Path, output_payload: dict):
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        calls.append(list(command))
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "pull ok", "")
        if command[:2] == ["docker", "run"]:
            volume_arg = command[command.index("-v") + 1]
            workspace = Path(volume_arg.split(":", 1)[0])
            (workspace / "patch_apply.json").write_text(
                json.dumps({"patch_applied": True}),
                encoding="utf-8",
            )
            (workspace / "stdout.log").write_text("test stdout\n", encoding="utf-8")
            (workspace / "stderr.log").write_text("", encoding="utf-8")
            (workspace / "output.json").write_text(
                json.dumps(output_payload),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "run ok", "")
        raise AssertionError(f"unexpected command: {command}")

    return calls, fake_run


def _fake_docker_runner_with_test_command_receipt(
    tmp_path: Path,
    output_payload: dict,
    *,
    test_command_return_code: int,
    docker_return_code: int = 0,
):
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        calls.append(list(command))
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "pull ok", "")
        if command[:2] == ["docker", "run"]:
            volume_arg = command[command.index("-v") + 1]
            workspace = Path(volume_arg.split(":", 1)[0])
            (workspace / "patch_apply.json").write_text(
                json.dumps({"patch_applied": True}),
                encoding="utf-8",
            )
            (workspace / "stdout.log").write_text("test stdout\n", encoding="utf-8")
            (workspace / "stderr.log").write_text("", encoding="utf-8")
            (workspace / "test_command.json").write_text(
                json.dumps({"test_command_return_code": test_command_return_code}),
                encoding="utf-8",
            )
            (workspace / "output.json").write_text(
                json.dumps(output_payload),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, docker_return_code, "run ok", "")
        raise AssertionError(f"unexpected command: {command}")

    return calls, fake_run


def test_pro_scripts_empty_env_uses_vendored_default(monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_SCRIPTS_DIR", " ")

    scripts_dir = official_oracle.swe_bench_pro_oracle_scripts_dir({})

    assert scripts_dir == (
        Path(official_oracle.__file__).resolve().parent
        / "vendor"
        / "swe_bench_pro"
        / "run_scripts"
    )


def test_pro_dockerhub_tag_strips_instance_repo_prefix():
    assert official_oracle._pro_dockerhub_tag(
        instance_id="instance_qutebrowser__qutebrowser-f91ace-1234",
        repo="qutebrowser/qutebrowser",
    ) == "qutebrowser.qutebrowser-f91ace-1234"


def test_pro_runner_returns_pass_status_on_gold_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    output_payload = {
        "tests": [
            {"name": "tests/test_parser.py::test_hidden", "status": "PASSED"},
            {"name": "tests/test_parser.py::test_existing", "status": "PASSED"},
        ]
    }
    calls, fake_run = _fake_docker_runner(tmp_path, output_payload)
    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert [call[:2] for call in calls] == [["docker", "pull"], ["docker", "run"]]
    assert result["fail_to_pass_status"] == "pass"
    assert result["pass_to_pass_status"] == "pass"
    assert result["patch_applied"] is True
    receipt = result["oracle_adapter_receipt"]
    assert receipt["return_code"] == 0
    assert receipt["patch_applied"] is True
    assert receipt["docker"]["image"] == "jefzda/sweap-images:nodebb.nodebb-demo"
    assert receipt["harness"]["name"] == "swe-bench-pro-local-docker"
    source = receipt["source_run_scripts"]
    assert source["instance_id"] == "instance_NodeBB__NodeBB-demo"
    assert source["run_script_sha256"] == sha256(
        Path(source["run_script"]).read_bytes()
    ).hexdigest()
    assert source["parser_sha256"] == sha256(
        Path(source["parser"]).read_bytes()
    ).hexdigest()
    assert Path(receipt["artifact_paths"]["output_json"]).exists()


def test_pro_runner_missing_scripts_fails_before_docker(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    context = _pro_context(tmp_path)
    context["swe_bench_pro_scripts_dir"] = str(tmp_path / "empty-run-scripts")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("docker must not run when Pro scripts are missing")

    monkeypatch.setattr(official_oracle.subprocess, "run", fail_if_called)

    result = run_swe_bench_pro_oracle(context)

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == (
        "pro_script_missing:"
        "instance_NodeBB__NodeBB-demo(parser.py,run_script.sh)"
    )
    receipt = result["oracle_adapter_receipt"]
    assert receipt["command"] == []
    assert receipt["unavailable_reason"] == result["oracle_unavailable_reason"]


def test_pro_runner_copies_source_scripts_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    instance_id = "instance_NodeBB__NodeBB-demo"
    context = _pro_context(tmp_path, instance_id=instance_id)
    instance_dir = Path(context["swe_bench_pro_scripts_dir"]) / instance_id
    run_script_text = "#!/usr/bin/env bash\necho bespoke-run-script\n"
    parser_text = "import json, sys\njson.dump({'tests': []}, open(sys.argv[3], 'w'))\n"
    (instance_dir / "run_script.sh").write_text(run_script_text, encoding="utf-8")
    (instance_dir / "parser.py").write_text(parser_text, encoding="utf-8")
    output_payload = {
        "tests": [
            {"name": "tests/test_parser.py::test_hidden", "status": "PASSED"},
            {"name": "tests/test_parser.py::test_existing", "status": "PASSED"},
        ]
    }
    _calls, fake_run = _fake_docker_runner(tmp_path, output_payload)
    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(context)

    receipt = result["oracle_adapter_receipt"]
    workspace_run_script = Path(receipt["artifact_paths"]["run_script"])
    workspace_parser = Path(receipt["artifact_paths"]["parser"])
    assert workspace_run_script.read_text(encoding="utf-8") == run_script_text
    assert workspace_parser.read_text(encoding="utf-8") == parser_text
    assert receipt["source_run_scripts"]["run_script_sha256"] == sha256(
        run_script_text.encode("utf-8")
    ).hexdigest()
    assert receipt["source_run_scripts"]["parser_sha256"] == sha256(
        parser_text.encode("utf-8")
    ).hexdigest()


def test_pro_entryscript_runs_parser_after_test_command_failure():
    script = official_oracle._pro_entryscript(
        base_commit="abc123",
        before_repo_set_cmd="",
        selected_tests=["tests/test_parser.py"],
    )

    run_index = script.index("if bash /workspace/run_script.sh")
    receipt_index = script.index("/workspace/test_command.json")
    parser_index = script.index("python /workspace/parser.py")

    assert run_index < receipt_index < parser_index
    assert "test_command_exit=$?" in script


def test_pro_entryscript_uses_last_line_of_before_repo_set_cmd():
    script = official_oracle._pro_entryscript(
        base_commit="abc123",
        before_repo_set_cmd="pip install foo\npip install bar",
        selected_tests=["tests/test_parser.py"],
    )

    apply_index = script.index("git apply -v /workspace/patch.diff")
    last_line_index = script.index("pip install bar")

    assert "pip install foo" not in script
    assert last_line_index > apply_index


def test_pro_entryscript_hoists_pre_patch_repo_setup_carveout():
    script = official_oracle._pro_entryscript(
        base_commit="abc123",
        before_repo_set_cmd="echo ignored\ngit reset --hard otherhash",
        selected_tests=["tests/test_parser.py"],
    )

    apply_index = script.index("git apply -v /workspace/patch.diff")
    hoisted_index = script.index("git reset --hard otherhash")

    assert "echo ignored" not in script
    assert hoisted_index < apply_index


def test_pro_entryscript_does_not_split_chained_last_line():
    script = official_oracle._pro_entryscript(
        base_commit="abc123",
        before_repo_set_cmd="pip install foo && pip install bar || true",
        selected_tests=["tests/test_parser.py"],
    )

    apply_index = script.index("git apply -v /workspace/patch.diff")
    chain_index = script.index("pip install foo && pip install bar || true")

    assert chain_index > apply_index
    assert script.count("pip install foo") == 1


def test_pro_entryscript_hardcodes_upstream_reset_and_checkout():
    script = official_oracle._pro_entryscript(
        base_commit="abc123",
        before_repo_set_cmd="",
        selected_tests=["tests/test_parser.py"],
    )

    reset_index = script.index("git reset --hard abc123")
    checkout_index = script.index("git checkout abc123")
    apply_index = script.index("git apply -v /workspace/patch.diff")

    assert reset_index < checkout_index < apply_index


def test_pro_runner_unavailable_on_pull_or_parse_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        return subprocess.CompletedProcess(command, 1, "", "pull denied")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "docker_pull_failed"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["return_code"] == 1
    assert receipt["unavailable_reason"] == "docker_pull_failed"


def test_pro_runner_preserves_test_command_receipt_when_parser_output_missing(
    tmp_path,
    monkeypatch,
):
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
            return subprocess.CompletedProcess(command, 1, "run failed", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "pro_parser_output_missing"
    receipt = result["oracle_adapter_receipt"]
    assert receipt["test_command_return_code"] == 1
    assert receipt["patch_applied"] is True


def test_pro_runner_unavailable_when_patch_does_not_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))

    def fake_run(command, *, cwd, env, text, capture_output, check, timeout):
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "pull ok", "")
        if command[:2] == ["docker", "run"]:
            volume_arg = command[command.index("-v") + 1]
            workspace = Path(volume_arg.split(":", 1)[0])
            (workspace / "patch_apply.json").write_text(
                json.dumps({"patch_applied": False, "return_code": 1}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 1, "", "patch failed")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["fail_to_pass_status"] == "unavailable"
    assert result["pass_to_pass_status"] == "unavailable"
    assert result["oracle_unavailable"] is True
    assert result["oracle_unavailable_reason"] == "patch_apply_failed"
    assert result["patch_applied"] is False
    receipt = result["oracle_adapter_receipt"]
    assert receipt["unavailable_reason"] == "patch_apply_failed"
    assert receipt["patch_applied"] is False


@pytest.mark.parametrize(
    ("output_payload", "expected_fail_to_pass", "expected_pass_to_pass"),
    [
        (
            {
                "tests": [
                    {"name": "tests/test_parser.py::test_hidden", "status": "FAILED"},
                    {"name": "tests/test_parser.py::test_existing", "status": "PASSED"},
                ]
            },
            "fail",
            "pass",
        ),
        (
            {
                "tests": [
                    {"name": "tests/test_parser.py::test_hidden", "status": "PASSED"},
                    {"name": "tests/test_parser.py::test_existing", "status": "FAILED"},
                ]
            },
            "pass",
            "fail",
        ),
    ],
)
def test_pro_runner_classifies_fail_to_pass_and_pass_to_pass_independently(
    tmp_path,
    monkeypatch,
    output_payload,
    expected_fail_to_pass,
    expected_pass_to_pass,
):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    _calls, fake_run = _fake_docker_runner(tmp_path, output_payload)
    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["fail_to_pass_status"] == expected_fail_to_pass
    assert result["pass_to_pass_status"] == expected_pass_to_pass


def test_pro_runner_classifies_failed_test_command_when_parser_output_exists(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    output_payload = {
        "tests": [
            {"name": "tests/test_parser.py::test_hidden", "status": "FAILED"},
            {"name": "tests/test_parser.py::test_existing", "status": "PASSED"},
        ]
    }
    _calls, fake_run = _fake_docker_runner_with_test_command_receipt(
        tmp_path,
        output_payload,
        test_command_return_code=1,
    )
    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))

    assert result["fail_to_pass_status"] == "fail"
    assert result["pass_to_pass_status"] == "pass"
    assert "oracle_unavailable" not in result
    receipt = result["oracle_adapter_receipt"]
    assert receipt["return_code"] == 0
    assert receipt["test_command_return_code"] == 1


def test_pro_runner_outcome_feeds_interpret_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("SWEBENCH_PRO_ORACLE_ARTIFACT_DIR", str(tmp_path / "oracle"))
    output_payload = {
        "tests": [
            {"name": "tests/test_parser.py::test_hidden", "status": "PASSED"},
            {"name": "tests/test_parser.py::test_existing", "status": "PASSED"},
        ]
    }
    _calls, fake_run = _fake_docker_runner(tmp_path, output_payload)
    monkeypatch.setattr(official_oracle.subprocess, "run", fake_run)

    result = run_swe_bench_pro_oracle(_pro_context(tmp_path))
    outcome, receipt = _normalise_oracle_adapter_outcome(result)
    interpreted = _interpret_oracle_outcome(outcome)

    assert outcome == {
        "fail_to_pass_status": "pass",
        "pass_to_pass_status": "pass",
        "oracle_unavailable": False,
        "oracle_unavailable_reason": "",
        "patch_applied": True,
    }
    assert receipt["fail_to_pass_status"] == "pass"
    assert interpreted["oracle_accept"] is True
