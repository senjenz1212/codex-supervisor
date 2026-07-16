from __future__ import annotations

from pathlib import Path

from supervisor.provider_boundaries import (
    PROVIDER_EDGE_ALLOWLIST,
    provider_boundary_violations,
)

EXPECTED_PROVIDER_EDGE_ALLOWLIST = {
    "daemon.py",
    "mcp_tools/codex_supervisor_stdio.py",
    "mcp_tools/codex_tools.py",
    "mcp_tools/supervisor_tools.py",
    "mcp_tools/telegram_tools.py",
    "scripts/run_harness_v1_runtime_smoke.py",
    "scripts/swebench_pro_claude_code_runner.py",
    "supervisor/agent_runtime.py",
    "supervisor/agentic_legacy_provider_edge.py",
    "supervisor/claude_sdk_runtime.py",
    "supervisor/cursor_agent.py",
    "supervisor/dual_agent_legacy_claude.py",
    "supervisor/harness_tracer.py",
    "supervisor/provider_clients.py",
    "supervisor/provider_boundaries.py",
    "supervisor/reviewer_legacy_provider_edge.py",
    "supervisor/swe_bench_mergeability_cli.py",
    "supervisor/target/codex.py",
    "supervisor/target/codex_app_server.py",
    "supervisor/target/codex_desktop_ipc.py",
}


def test_provider_specific_execution_is_confined_to_explicit_edges() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert set(PROVIDER_EDGE_ALLOWLIST) == EXPECTED_PROVIDER_EDGE_ALLOWLIST
    missing_edges = sorted(
        path
        for path in PROVIDER_EDGE_ALLOWLIST
        if not (repo_root / path).is_file()
    )
    assert missing_edges == []

    assert provider_boundary_violations(repo_root) == ()


def test_provider_edge_allowlist_has_an_auditable_reason() -> None:
    assert all(
        path.endswith(".py") and reason.strip()
        for path, reason in PROVIDER_EDGE_ALLOWLIST.items()
    )


def test_provider_guard_scans_scripts_and_literal_subprocess_calls(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "probe.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import subprocess\n"
        "subprocess.run(['claude', '-p', 'hello'])\n",
        encoding="utf-8",
    )

    violations = provider_boundary_violations(tmp_path)

    assert len(violations) == 1
    assert "scripts/probe.py:2 launches provider executable claude" in violations[0]


def test_provider_guard_detects_dynamic_import_and_returned_argv(
    tmp_path: Path,
) -> None:
    module = tmp_path / "supervisor" / "probe.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "import importlib\n"
        "client = importlib.import_module('openai')\n"
        "def command():\n"
        "    return ['codex', 'exec']\n",
        encoding="utf-8",
    )

    violations = provider_boundary_violations(tmp_path)

    assert any("dynamically imports provider SDK openai" in item for item in violations)
    assert any("returns direct provider argv codex" in item for item in violations)


def test_provider_guard_detects_variable_absolute_and_runtime_launches(
    tmp_path: Path,
) -> None:
    module = tmp_path / "supervisor" / "probe.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "import subprocess\n"
        "from supervisor.agent_runtime import ClaudeCodeRuntime\n"
        "claude_bin = '/opt/provider/bin/claude'\n"
        "subprocess.run([claude_bin, '--version'])\n"
        "subprocess.run(['/usr/local/bin/codex', 'exec'])\n"
        "runtime = ClaudeCodeRuntime(binary=claude_bin)\n",
        encoding="utf-8",
    )

    violations = provider_boundary_violations(tmp_path)

    assert any(
        "launches provider executable claude" in item
        for item in violations
    )
    assert any(
        "launches provider executable codex" in item
        for item in violations
    )
    assert any(
        "constructs provider runtime ClaudeCodeRuntime" in item
        for item in violations
    )
