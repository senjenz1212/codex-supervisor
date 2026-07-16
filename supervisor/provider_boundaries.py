"""Auditable provider-edge allowlist for production Python.

Files not listed here may depend on provider-neutral protocols and result
types, but they may not import provider SDKs, construct provider SDK clients,
or launch model-provider CLIs directly.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, Mapping


PROVIDER_EDGE_ALLOWLIST: dict[str, str] = {
    "daemon.py": "application composition root",
    "mcp_tools/codex_supervisor_stdio.py": "MCP composition root",
    "mcp_tools/codex_tools.py": "Claude SDK MCP adapter edge",
    "mcp_tools/supervisor_tools.py": "Claude SDK MCP adapter edge",
    "mcp_tools/telegram_tools.py": "Claude SDK MCP adapter edge",
    "scripts/run_harness_v1_runtime_smoke.py": (
        "operator compatibility-smoke composition edge"
    ),
    "scripts/swebench_pro_claude_code_runner.py": (
        "SWE-bench Claude Code runtime composition edge"
    ),
    "supervisor/agent_runtime.py": "Claude Code and Codex runtime adapters",
    "supervisor/agentic_legacy_provider_edge.py": "legacy agentic subprocess edge",
    "supervisor/claude_sdk_runtime.py": "Claude Agent SDK transport edge",
    "supervisor/cursor_agent.py": "Cursor SDK and structured gateway edge",
    "supervisor/dual_agent_legacy_claude.py": "legacy lead subprocess edge",
    "supervisor/harness_tracer.py": "hermetic tracer runtime composition edge",
    "supervisor/provider_clients.py": "provider ModelClient adapters",
    "supervisor/provider_boundaries.py": "provider-edge static guard definition",
    "supervisor/reviewer_legacy_provider_edge.py": (
        "legacy Codex reviewer subprocess edge"
    ),
    "supervisor/swe_bench_mergeability_cli.py": (
        "public benchmark runtime composition edge"
    ),
    "supervisor/target/codex.py": "Codex target steering adapter",
    "supervisor/target/codex_app_server.py": "Codex app-server transport edge",
    "supervisor/target/codex_desktop_ipc.py": "Codex desktop IPC edge",
}

PROVIDER_SDK_ROOTS = frozenset({
    "anthropic",
    "claude_agent_sdk",
    "cursor_sdk",
    "openai",
})

PROVIDER_CLIENT_CONSTRUCTORS = frozenset({
    "Agent",
    "Anthropic",
    "AsyncAnthropic",
    "AsyncOpenAI",
    "ClaudeAgentOptions",
    "ClaudeSDKClient",
    "LocalAgentOptions",
    "OpenAI",
})

PROVIDER_EXECUTABLES = frozenset({"claude", "codex", "cursor"})

PROVIDER_RUNTIME_CONSTRUCTORS: dict[str, str] = {
    "ClaudeCodeRuntime": "claude",
    "CodexRuntime": "codex",
    "CursorRuntime": "cursor",
}

PRODUCTION_PYTHON_ROOTS = (
    "daemon.py",
    "mcp_tools",
    "scripts",
    "supervisor",
)


def production_python_files(repo_root: str | Path) -> Iterator[Path]:
    root = Path(repo_root)
    for relative in PRODUCTION_PYTHON_ROOTS:
        path = root / relative
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))


def provider_boundary_violations(
    repo_root: str | Path,
) -> tuple[str, ...]:
    root = Path(repo_root).resolve()
    violations: list[str] = []
    for path in production_python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in PROVIDER_EDGE_ALLOWLIST:
            continue
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=relative,
        )
        symbols = _provider_symbol_bindings(tree)
        for node in ast.walk(tree):
            violation = provider_ast_violation(node, symbols=symbols)
            if violation is not None:
                violations.append(
                    f"{relative}:{getattr(node, 'lineno', 0)} {violation}"
                )
    return tuple(violations)


def provider_ast_violation(
    node: ast.AST,
    *,
    symbols: Mapping[str, str] | None = None,
) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in PROVIDER_SDK_ROOTS:
                return f"imports provider SDK {alias.name}"
    if isinstance(node, ast.ImportFrom) and node.module:
        root = node.module.split(".", 1)[0]
        if root in PROVIDER_SDK_ROOTS:
            return f"imports provider SDK {node.module}"
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in PROVIDER_CLIENT_CONSTRUCTORS:
            return f"constructs provider client {name}"
        if name in PROVIDER_RUNTIME_CONSTRUCTORS:
            executable = PROVIDER_RUNTIME_CONSTRUCTORS[name]
            return (
                f"constructs provider runtime {name} "
                f"for executable {executable}"
            )
        if name in {"__import__", "import_module"} and node.args:
            imported = _constant_string(node.args[0])
            if imported and imported.split(".", 1)[0] in PROVIDER_SDK_ROOTS:
                return f"dynamically imports provider SDK {imported}"
        if name in {
            "Popen",
            "call",
            "check_call",
            "check_output",
            "create_subprocess_exec",
            "run",
        }:
            executable = _provider_executable_from_call(
                node,
                symbols=symbols,
            )
            if executable:
                return f"launches provider executable {executable}"
        if name.endswith("Runtime"):
            for keyword in node.keywords:
                if keyword.arg in {"binary", "command", "executable"}:
                    executable = _provider_executable_from_expr(
                        keyword.value,
                        symbols=symbols,
                    )
                    if executable:
                        return (
                            f"constructs runtime {name} for provider "
                            f"executable {executable}"
                        )
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target_names = _assignment_target_names(node)
        executable = _provider_executable_from_argv_expr(
            node.value,
            symbols=symbols,
        )
        if (
            executable
            and any(
                marker in name.casefold()
                for name in target_names
                for marker in ("argv", "command", "cmd")
            )
        ):
            return f"constructs direct provider argv {executable}"
    if isinstance(node, ast.Return):
        executable = _provider_executable_from_argv_expr(
            node.value,
            symbols=symbols,
        )
        if executable:
            return f"returns direct provider argv {executable}"
    return None


def _provider_executable_from_call(
    node: ast.Call,
    *,
    symbols: Mapping[str, str] | None = None,
) -> str:
    if not node.args:
        return ""
    name = _call_name(node.func)
    if name == "create_subprocess_exec":
        return _provider_executable_from_expr(
            node.args[0],
            symbols=symbols,
        )
    return _provider_executable_from_expr(
        node.args[0],
        symbols=symbols,
    )


def _provider_executable_from_argv_expr(
    node: ast.AST | None,
    *,
    symbols: Mapping[str, str] | None = None,
) -> str:
    if isinstance(node, (ast.List, ast.Tuple)):
        return _provider_executable_from_expr(node, symbols=symbols)
    return ""


def _provider_executable_from_expr(
    node: ast.AST | None,
    *,
    symbols: Mapping[str, str] | None = None,
) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts = node.value.strip().split(maxsplit=1)
        if not parts:
            return ""
        return _provider_executable_from_path(parts[0])
    if isinstance(node, ast.Name):
        if symbols and node.id in symbols:
            return symbols[node.id]
        return _provider_executable_from_identifier(node.id)
    if isinstance(node, ast.Attribute):
        return _provider_executable_from_identifier(node.attr)
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _provider_executable_from_expr(
            node.elts[0],
            symbols=symbols,
        )
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in {"Path", "PurePath", "which"} and node.args:
            return _provider_executable_from_expr(
                node.args[0],
                symbols=symbols,
            )
    return ""


def _provider_symbol_bindings(tree: ast.AST) -> dict[str, str]:
    bindings: dict[str, str] = {}
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            executable = _provider_executable_from_expr(
                assignment.value,
                symbols=bindings,
            )
            if not executable:
                continue
            for target in _assignment_target_names(assignment):
                if bindings.get(target) == executable:
                    continue
                bindings[target] = executable
                changed = True
        if not changed:
            break
    return bindings


def _provider_executable_from_identifier(identifier: str) -> str:
    normalized = identifier.casefold().replace("-", "_")
    tokens = tuple(token for token in normalized.split("_") if token)
    for executable in PROVIDER_EXECUTABLES:
        if executable in tokens:
            return executable
    return ""


def _provider_executable_from_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    basename = normalized.rsplit("/", 1)[-1]
    return basename if basename in PROVIDER_EXECUTABLES else ""


def _constant_string(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _assignment_target_names(
    node: ast.Assign | ast.AnnAssign,
) -> list[str]:
    raw_targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in raw_targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(
                item.id
                for item in target.elts
                if isinstance(item, ast.Name)
            )
    return names


__all__ = [
    "PRODUCTION_PYTHON_ROOTS",
    "PROVIDER_CLIENT_CONSTRUCTORS",
    "PROVIDER_EDGE_ALLOWLIST",
    "PROVIDER_EXECUTABLES",
    "PROVIDER_RUNTIME_CONSTRUCTORS",
    "PROVIDER_SDK_ROOTS",
    "production_python_files",
    "provider_ast_violation",
    "provider_boundary_violations",
]
