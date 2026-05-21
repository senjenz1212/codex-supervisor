from __future__ import annotations

import json
import subprocess

from supervisor.state import State
from supervisor.supervisor_tools import SupervisorToolAPI
from supervisor.target.types import ScopeContract


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("def run():\n    return 'old'\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=t@example.test",
         "commit", "-m", "initial")
    (repo / "src" / "app.py").write_text(
        "def run():\n    return 'new'\n# Bearer secret-token\n"
    )
    return repo


def _make_state(tmp_path, repo) -> State:
    state = State(str(tmp_path / "state.db"))
    state.register_run(
        run_id="run-vela",
        session_id="session-vela",
        rollout_path=str(tmp_path / "rollout.jsonl"),
        task="Review Vela changes",
        scope=ScopeContract(allowed_paths=("src",)),
        target_kind="codex",
    )
    state.write_event(
        run_id="run-vela",
        source="rollout",
        kind="turn_context",
        payload={"payload": {"cwd": str(tmp_path / "older-cwd")}},
    )
    state.write_event(
        run_id="run-vela",
        source="rollout",
        kind="response_item",
        payload={
            "payload": {
                "type": "function_call",
                "arguments": json.dumps({
                    "cmd": "pytest -q",
                    "workdir": str(repo),
                }),
            },
        },
    )
    return state


def test_supervisor_tool_api_reads_grounded_workspace_snapshot(tmp_path):
    repo = _make_repo(tmp_path)
    state = _make_state(tmp_path, repo)
    api = SupervisorToolAPI(state)

    snapshot = api.read_workspace_snapshot(run_id="run-vela", max_diff_chars=4000)

    blob = json.dumps(snapshot)
    assert snapshot["status"] == "ok"
    assert snapshot["workspace"]["root"] == str(repo)
    assert "src/app.py" in snapshot["workspace"]["changed_files"]
    assert "return 'new'" in snapshot["workspace"]["diff"]
    assert "secret-token" not in blob
    assert "[REDACTED" in blob


def test_supervisor_tool_api_reads_safe_workspace_files_only(tmp_path):
    repo = _make_repo(tmp_path)
    state = _make_state(tmp_path, repo)
    api = SupervisorToolAPI(state)

    ok = api.read_workspace_file(run_id="run-vela", path="src/app.py", max_bytes=2000)
    escape = api.read_workspace_file(run_id="run-vela", path="../outside.txt")

    assert ok["status"] == "ok"
    assert "return 'new'" in ok["file"]["content"]
    assert "secret-token" not in json.dumps(ok)
    assert escape["status"] == "denied"


def test_workspace_grounding_denies_dotenv_variants(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ".env.production").write_text("SERVICE_TOKEN=prod-secret\n")
    state = _make_state(tmp_path, repo)
    api = SupervisorToolAPI(state)

    snapshot = api.read_workspace_snapshot(run_id="run-vela", max_diff_chars=4000)
    denied = api.read_workspace_file(run_id="run-vela", path=".env.production")

    assert ".env.production" not in snapshot["workspace"]["changed_files"]
    assert "prod-secret" not in json.dumps(snapshot)
    assert denied == {"status": "denied", "reason": "secret_path"}
