"""Trusted pytest instrumentation for projection-registry proof gates."""
from __future__ import annotations

import functools
import importlib
import inspect
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest


PROOF_RECEIPT_SCHEMA_VERSION = "harness-projection-proof-receipt/v2"
_BINDINGS_ENV = "CODEX_PROJECTION_PROOF_BINDINGS"
_RECEIPT_ENV = "CODEX_PROJECTION_PROOF_RECEIPT"

_collected_node_ids: list[str] = []
_reports: list[dict[str, str]] = []
_binding_references: list[str] = []
_binding_counts_by_node_id: dict[str, dict[str, int]] = {}
_binding_count_lock = threading.Lock()
_active_node_id: str | None = None


def _resolve_owner(reference: str) -> tuple[Any, str, Any]:
    module_name, qualified_name = reference.split(":", 1)
    owner: Any = importlib.import_module(module_name)
    segments = qualified_name.split(".")
    for segment in segments[:-1]:
        owner = getattr(owner, segment)
    attribute = segments[-1]
    return owner, attribute, getattr(owner, attribute)


def _instrument(reference: str) -> None:
    owner, attribute, original = _resolve_owner(reference)
    if not callable(original):
        raise TypeError(f"projection proof binding is not callable: {reference}")

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _record_binding(reference)
            return await original(*args, **kwargs)

        replacement = async_wrapper
    else:

        @functools.wraps(original)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _record_binding(reference)
            return original(*args, **kwargs)

        replacement = wrapper
    setattr(owner, attribute, replacement)


def _record_binding(reference: str) -> None:
    node_id = _active_node_id
    if node_id is None:
        return
    with _binding_count_lock:
        node_counts = _binding_counts_by_node_id.get(node_id)
        if node_counts is not None:
            node_counts[reference] += 1


def pytest_configure(config: Any) -> None:
    del config
    global _active_node_id
    _active_node_id = None
    _collected_node_ids.clear()
    _reports.clear()
    _binding_references.clear()
    _binding_counts_by_node_id.clear()
    raw_bindings = os.environ.get(_BINDINGS_ENV, "[]")
    bindings = json.loads(raw_bindings)
    if not isinstance(bindings, list) or any(
        not isinstance(reference, str) or ":" not in reference
        for reference in bindings
    ):
        raise ValueError("projection proof bindings must be symbol references")
    _binding_references.extend(dict.fromkeys(bindings))
    for reference in _binding_references:
        _instrument(reference)


def pytest_collection_finish(session: Any) -> None:
    _collected_node_ids.extend(item.nodeid for item in session.items)
    _binding_counts_by_node_id.update(
        {
            item.nodeid: {
                reference: 0
                for reference in _binding_references
            }
            for item in session.items
        }
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: Any, nextitem: Any):
    del nextitem
    global _active_node_id
    previous_node_id = _active_node_id
    _active_node_id = str(item.nodeid)
    try:
        yield
    finally:
        _active_node_id = previous_node_id


def pytest_runtest_logreport(report: Any) -> None:
    _reports.append(
        {
            "nodeid": str(report.nodeid),
            "when": str(report.when),
            "outcome": str(report.outcome),
        }
    )


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    receipt_path = os.environ.get(_RECEIPT_ENV)
    if not receipt_path:
        raise RuntimeError("projection proof receipt path is required")
    payload = {
        "schema_version": PROOF_RECEIPT_SCHEMA_VERSION,
        "exit_status": int(exitstatus),
        "collected_node_ids": list(_collected_node_ids),
        "reports": list(_reports),
        "binding_counts_by_node_id": {
            node_id: dict(counts)
            for node_id, counts in _binding_counts_by_node_id.items()
        },
    }
    target = Path(receipt_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
