"""Fail-closed process containment for agent subprocess trees.

POSIX process groups are useful but incomplete: a descendant can call
``setsid()`` and leave the original group.  Every process launched through this
module therefore receives an opaque containment id in its environment.  The id
is inherited across normal forks/execs and lets a restarted supervisor find
same-user descendants even after re-parenting or session changes.

This is cooperative same-user containment, not a security boundary against a
malicious process that deliberately removes the environment marker.  Callers
must keep stronger OS sandbox/container boundaries for hostile workloads.
"""
from __future__ import annotations

import os
import signal
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

import psutil


CONTAINMENT_ENV_VAR = "CODEX_SUPERVISOR_CONTAINMENT_ID"


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    started_at: float


@dataclass(frozen=True)
class ContainmentSnapshot:
    processes: tuple[ProcessIdentity, ...]
    scan_complete: bool
    errors: tuple[str, ...] = ()


def new_containment_id() -> str:
    return uuid.uuid4().hex


def containment_environment(
    base: Mapping[str, str] | None,
    containment_id: str,
) -> dict[str, str]:
    normalized = str(containment_id).strip()
    if not normalized:
        raise ValueError("containment_id is required")
    env: MutableMapping[str, str] = dict(os.environ if base is None else base)
    env[CONTAINMENT_ENV_VAR] = normalized
    return dict(env)


def process_identity(pid: int) -> ProcessIdentity | None:
    try:
        process = psutil.Process(int(pid))
        return ProcessIdentity(
            pid=int(pid),
            started_at=float(process.create_time()),
        )
    except (
        psutil.AccessDenied,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
    ):
        return None


def same_process(identity: ProcessIdentity) -> bool:
    observed = process_identity(identity.pid)
    return (
        observed is not None
        and abs(observed.started_at - identity.started_at) <= 0.001
        and _process_alive(identity.pid)
    )


def scan_containment(
    containment_id: str,
    *,
    root_identity: ProcessIdentity | None = None,
    known_identities: tuple[ProcessIdentity, ...] = (),
    unreadable_scope: str = "same_user",
) -> ContainmentSnapshot:
    """Find all same-user processes that inherited ``containment_id``.

    A complete scan is required before a caller may claim that the containment
    is empty. Access-denied results fail closed when the process cannot be
    ruled out: it is already known to belong to the containment, structurally
    descended from a known process, or started at or after the containment
    root, so a re-parented descendant that denies environment reads degrades
    the proof instead of silently passing. Same-user processes that predate
    the root may deny environment access on macOS and are not evidence that
    this containment is incomplete.

    ``unreadable_scope`` narrows which access-denied processes degrade the
    proof. The default ``"same_user"`` keeps the behaviour above. With
    ``"containment_tree"`` an unreadable process only fails the scan closed
    when it is known to the containment or structurally descends from a known
    process; unrelated same-user processes that merely started at or after
    the root (common on macOS, where ``environ()`` reads are denied for many
    unrelated same-user processes) are out of scope.
    """
    if unreadable_scope not in {"same_user", "containment_tree"}:
        raise ValueError(
            f"unknown containment unreadable_scope: {unreadable_scope!r}"
        )
    normalized = str(containment_id).strip()
    if not normalized:
        return ContainmentSnapshot(
            processes=(),
            scan_complete=False,
            errors=("missing_containment_id",),
        )
    try:
        current_username = psutil.Process().username()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        current_username = ""
    earliest = (
        root_identity.started_at - 0.001
        if root_identity is not None
        else None
    )
    known_by_pid = {
        identity.pid: identity
        for identity in (
            *((root_identity,) if root_identity is not None else ()),
            *known_identities,
        )
    }
    found: dict[tuple[int, float], ProcessIdentity] = {}
    errors: list[str] = []
    processes = list(
        psutil.process_iter(
            ["pid", "ppid", "username", "create_time"],
        )
    )
    parent_by_pid = {
        int(process.info["pid"]): int(process.info.get("ppid") or 0)
        for process in processes
        if process.info.get("pid") is not None
    }
    known_pids = set(known_by_pid)
    for process in processes:
        try:
            pid = int(process.info["pid"])
            if pid == os.getpid():
                continue
            username = str(process.info.get("username") or "")
            if current_username and username and username != current_username:
                continue
            started_at = float(
                process.info.get("create_time")
                or process.create_time()
            )
            if earliest is not None and started_at < earliest:
                continue
            environment = process.environ()
            if environment.get(CONTAINMENT_ENV_VAR) != normalized:
                continue
            identity = ProcessIdentity(pid=pid, started_at=started_at)
            if same_process(identity):
                found[(identity.pid, identity.started_at)] = identity
        except (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError):
            continue
        except psutil.AccessDenied:
            if _unreadable_in_scope(
                int(process.pid),
                unreadable_scope=unreadable_scope,
                earliest=earliest,
                parent_by_pid=parent_by_pid,
                known_pids=known_pids,
            ):
                errors.append(f"access_denied:{process.pid}")
        except (OSError, ValueError) as exc:
            if _unreadable_in_scope(
                int(process.pid),
                unreadable_scope=unreadable_scope,
                earliest=earliest,
                parent_by_pid=parent_by_pid,
                known_pids=known_pids,
            ):
                errors.append(
                    f"scan_error:{process.pid}:{type(exc).__name__}"
                )
    if root_identity is not None and same_process(root_identity):
        found[(root_identity.pid, root_identity.started_at)] = root_identity
    return ContainmentSnapshot(
        processes=tuple(sorted(found.values())),
        scan_complete=not errors,
        errors=tuple(sorted(set(errors))),
    )


def terminate_containment(
    *,
    root_pid: int,
    expected_root_started_at: float | None,
    expected_process_group_id: int | None,
    containment_id: str,
    process: Any = None,
    root_pid_reused: bool = False,
    term_timeout_s: float = 2.0,
    kill_timeout_s: float = 2.0,
    quiescence_s: float = 0.25,
    poll_s: float = 0.02,
    unreadable_scope: str = "same_user",
) -> dict[str, Any]:
    """Terminate a tagged process tree and prove a quiescent empty scan.

    New descendants discovered while SIGTERM/SIGKILL is in flight are signalled
    in the same phase. If the root PID was reused, the reused root and recorded
    process group are never signalled; only processes carrying the recorded
    containment id are targeted. An incomplete or non-empty final scan never
    returns a successful reap proof. ``unreadable_scope`` is forwarded to
    ``scan_containment`` and controls whether unreadable processes outside the
    launched containment tree degrade the proof.
    """
    try:
        root_pid = int(root_pid)
    except (TypeError, ValueError, OverflowError):
        return _result(
            status="invalid_worker_pid",
            safe=False,
            root_pid=0,
            pgid=None,
            containment_id=containment_id,
        )
    if root_pid <= 0:
        return _result(
            status="invalid_worker_pid",
            safe=False,
            root_pid=root_pid,
            pgid=expected_process_group_id,
            containment_id=containment_id,
        )
    if expected_root_started_at is not None:
        try:
            expected_root_started_at = float(expected_root_started_at)
        except (TypeError, ValueError, OverflowError):
            return _result(
                status="invalid_worker_start_identity",
                safe=False,
                root_pid=root_pid,
                pgid=None,
                containment_id=containment_id,
            )
    root_identity = (
        ProcessIdentity(root_pid, expected_root_started_at)
        if expected_root_started_at is not None
        else None
    )
    if expected_process_group_id is None:
        expected_pgid = root_pid
    else:
        try:
            expected_pgid = int(expected_process_group_id)
        except (TypeError, ValueError, OverflowError):
            return _result(
                status="invalid_worker_process_group",
                safe=False,
                root_pid=root_pid,
                pgid=None,
                containment_id=containment_id,
            )
    if expected_pgid <= 0:
        return _result(
            status="invalid_worker_process_group",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
        )
    observed_root = process_identity(root_pid)
    root_pid_reused = bool(
        root_pid_reused
        or (
            root_identity is not None
            and observed_root is not None
            and not _same_start(
                observed_root.started_at,
                root_identity.started_at,
            )
        )
    )
    if expected_pgid == os.getpgrp() and not root_pid_reused:
        return _result(
            status="worker_process_group_matches_supervisor",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
        )
    if root_pid_reused and not str(containment_id).strip():
        return _result(
            status="worker_identity_mismatch_pid_reused",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            root_pid_reused=True,
        )
    if (
        not str(containment_id).strip()
        and observed_root is None
        and process is not None
        and process.poll() is not None
        and not _process_group_exists(expected_pgid)
    ):
        return _result(
            status="worker_already_reaped",
            safe=True,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
        )
    if not str(containment_id).strip():
        return _result(
            status="worker_containment_identity_missing",
            safe=False,
            root_pid=root_pid,
            pgid=expected_process_group_id,
            containment_id=containment_id,
        )

    if (
        not root_pid_reused
        and observed_root is not None
        and expected_process_group_id is not None
    ):
        try:
            observed_pgid = os.getpgid(root_pid)
        except OSError:
            observed_pgid = None
        if observed_pgid is not None and observed_pgid != expected_pgid:
            return _result(
                status="worker_identity_mismatch_process_group",
                safe=False,
                root_pid=root_pid,
                pgid=expected_pgid,
                containment_id=containment_id,
            )

    tracked: dict[tuple[int, float], ProcessIdentity] = {}
    all_seen: set[int] = set()
    scan_errors: set[str] = set()
    scan_root_identity = None if root_pid_reused else root_identity
    protected_pids = frozenset({root_pid}) if root_pid_reused else frozenset()
    term_deadline = time.monotonic() + max(0.0, term_timeout_s)
    if _terminate_phase(
        sig=signal.SIGTERM,
        root_identity=scan_root_identity,
        expected_pgid=expected_pgid,
        containment_id=containment_id,
        tracked=tracked,
        all_seen=all_seen,
        scan_errors=scan_errors,
        signal_process_group=not root_pid_reused,
        protected_pids=protected_pids,
        deadline=term_deadline,
        quiescence_s=quiescence_s,
        poll_s=poll_s,
        process=process,
        unreadable_scope=unreadable_scope,
    ):
        return _result(
            status="worker_tree_terminated",
            safe=True,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            descendants=all_seen - {root_pid},
            root_pid_reused=root_pid_reused,
        )

    kill_deadline = time.monotonic() + max(0.0, kill_timeout_s)
    if _terminate_phase(
        sig=signal.SIGKILL,
        root_identity=scan_root_identity,
        expected_pgid=expected_pgid,
        containment_id=containment_id,
        tracked=tracked,
        all_seen=all_seen,
        scan_errors=scan_errors,
        signal_process_group=not root_pid_reused,
        protected_pids=protected_pids,
        deadline=kill_deadline,
        quiescence_s=quiescence_s,
        poll_s=poll_s,
        process=process,
        unreadable_scope=unreadable_scope,
    ):
        return _result(
            status="worker_tree_killed",
            safe=True,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            descendants=all_seen - {root_pid},
            root_pid_reused=root_pid_reused,
        )

    survivors = sorted(
        identity.pid for identity in tracked.values() if same_process(identity)
    )
    status = (
        "worker_containment_scan_incomplete"
        if scan_errors
        else "worker_tree_survived_sigkill"
    )
    return _result(
        status=status,
        safe=False,
        root_pid=root_pid,
        pgid=expected_pgid,
        containment_id=containment_id,
        descendants=all_seen - {root_pid},
        survivors=survivors,
        scan_errors=scan_errors,
        root_pid_reused=root_pid_reused,
    )


def _terminate_phase(
    *,
    sig: signal.Signals,
    root_identity: ProcessIdentity | None,
    expected_pgid: int,
    containment_id: str,
    tracked: dict[tuple[int, float], ProcessIdentity],
    all_seen: set[int],
    scan_errors: set[str],
    signal_process_group: bool,
    protected_pids: frozenset[int],
    deadline: float,
    quiescence_s: float,
    poll_s: float,
    process: Any,
    unreadable_scope: str = "same_user",
) -> bool:
    quiet_since: float | None = None
    while True:
        snapshot = scan_containment(
            containment_id,
            root_identity=root_identity,
            known_identities=tuple(tracked.values()),
            unreadable_scope=unreadable_scope,
        )
        scan_errors.update(snapshot.errors)
        for identity in snapshot.processes:
            if identity.pid in protected_pids:
                continue
            tracked[(identity.pid, identity.started_at)] = identity
            all_seen.add(identity.pid)

        live = [
            identity
            for identity in tracked.values()
            if same_process(identity)
        ]
        group_owned = (
            signal_process_group
            and any(
                _process_group(identity.pid) == expected_pgid
                for identity in live
            )
        )
        if group_owned:
            try:
                os.killpg(expected_pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        for identity in reversed(live):
            try:
                if _process_group(identity.pid) == expected_pgid and group_owned:
                    continue
                os.kill(identity.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                continue

        _reap_process(process, root_identity.pid if root_identity else 0)
        live_after = [
            identity
            for identity in tracked.values()
            if same_process(identity)
        ]
        now = time.monotonic()
        if not live_after and snapshot.scan_complete:
            if quiet_since is None:
                quiet_since = now
            elif now - quiet_since >= max(0.0, quiescence_s):
                final = scan_containment(
                    containment_id,
                    root_identity=root_identity,
                    known_identities=tuple(tracked.values()),
                    unreadable_scope=unreadable_scope,
                )
                scan_errors.update(final.errors)
                if final.scan_complete and not final.processes:
                    return True
                quiet_since = None
        else:
            quiet_since = None
        if now >= deadline:
            return False
        time.sleep(min(max(0.001, poll_s), max(0.001, deadline - now)))


def _result(
    *,
    status: str,
    safe: bool,
    root_pid: int,
    pgid: int | None,
    containment_id: str,
    descendants: set[int] | None = None,
    survivors: list[int] | None = None,
    scan_errors: set[str] | None = None,
    root_pid_reused: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "safe_to_finalize": bool(safe),
        "pid": root_pid,
        "pgid": pgid,
        "containment_id": str(containment_id),
        "descendant_pids": sorted(descendants or set()),
        "surviving_pids": list(survivors or []),
        "scan_errors": sorted(scan_errors or set()),
        "containment_kind": "inherited_environment_same_user",
        "root_pid_reused": bool(root_pid_reused),
    }


def _same_start(observed: float, expected: float) -> bool:
    return abs(float(observed) - float(expected)) <= 0.001


def _unreadable_in_scope(
    pid: int,
    *,
    unreadable_scope: str,
    earliest: float | None,
    parent_by_pid: Mapping[int, int],
    known_pids: set[int],
) -> bool:
    """Return whether an unreadable process must degrade the scan proof."""
    if _is_structurally_related(
        pid,
        parent_by_pid=parent_by_pid,
        known_pids=known_pids,
    ):
        return True
    return unreadable_scope == "same_user" and earliest is not None


def _is_structurally_related(
    pid: int,
    *,
    parent_by_pid: Mapping[int, int],
    known_pids: set[int],
) -> bool:
    """Return whether ``pid`` is known or descends from a known process."""
    current = int(pid)
    visited: set[int] = set()
    while current > 0 and current not in visited:
        if current in known_pids:
            return True
        visited.add(current)
        current = int(parent_by_pid.get(current, 0))
    return False


def _process_alive(pid: int) -> bool:
    try:
        process = psutil.Process(int(pid))
        return (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError):
        return False
    except psutil.AccessDenied:
        return True


def _process_group(pid: int) -> int | None:
    try:
        return int(os.getpgid(int(pid)))
    except (OSError, ProcessLookupError):
        return None


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(int(pgid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_process(process: Any, pid: int) -> None:
    if process is not None:
        poll = getattr(process, "poll", None)
        if callable(poll):
            try:
                poll()
            except Exception:
                return
            return
    if pid <= 0:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, ProcessLookupError):
        pass
