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

import math
import os
import signal
import threading
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
class UnreadableProcessIdentity:
    pid: int
    started_at: float | None
    relation: str
    error: str

    @property
    def identity(self) -> ProcessIdentity | None:
        if self.started_at is None:
            return None
        return ProcessIdentity(pid=self.pid, started_at=self.started_at)


@dataclass(frozen=True)
class ContainmentSnapshot:
    processes: tuple[ProcessIdentity, ...]
    scan_complete: bool
    errors: tuple[str, ...] = ()
    unreadable_identities: tuple[UnreadableProcessIdentity, ...] = ()
    root_process_group_verified: bool = False


@dataclass(frozen=True)
class _ProcessPrincipal:
    uid: int | None
    username: str


@dataclass
class _TerminationScan:
    snapshot: ContainmentSnapshot
    pidfds: dict[tuple[int, float], int]


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


def _current_principal() -> _ProcessPrincipal:
    uid: int | None = None
    getuid = getattr(os, "geteuid", None) or getattr(os, "getuid", None)
    if callable(getuid):
        try:
            uid = int(getuid())
        except (OSError, TypeError, ValueError):
            uid = None
    try:
        username = str(psutil.Process().username() or "")
    except (
        psutil.AccessDenied,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
        OSError,
    ):
        username = ""
    return _ProcessPrincipal(uid=uid, username=username)


def _process_principal(process: Any) -> _ProcessPrincipal:
    try:
        username = str(process.info.get("username") or "")
    except (AttributeError, KeyError, TypeError, ValueError):
        username = ""
    if not username:
        username_method = getattr(process, "username", None)
        if callable(username_method):
            try:
                username = str(username_method() or "")
            except (
                psutil.AccessDenied,
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
                ProcessLookupError,
                OSError,
            ):
                username = ""
    uid: int | None = None
    try:
        uids = process.info.get("uids")
    except (AttributeError, KeyError, TypeError):
        uids = None
    if uids is None:
        uids_method = getattr(process, "uids", None)
        if callable(uids_method):
            try:
                uids = uids_method()
            except (
                psutil.AccessDenied,
                psutil.NoSuchProcess,
                psutil.ZombieProcess,
                ProcessLookupError,
                OSError,
            ):
                uids = None
    if uids is not None:
        try:
            if hasattr(uids, "effective"):
                uid = int(uids.effective)
            elif hasattr(uids, "real"):
                uid = int(uids.real)
            else:
                uid = int(uids[0])
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
            OverflowError,
        ):
            uid = None
    return _ProcessPrincipal(uid=uid, username=username)


def _principal_relation(
    current: _ProcessPrincipal,
    candidate: _ProcessPrincipal,
) -> str:
    if current.uid is not None and candidate.uid is not None:
        return "same" if current.uid == candidate.uid else "different"
    if current.username and candidate.username:
        return (
            "same"
            if current.username == candidate.username
            else "different"
        )
    return "unknown"


def _pid_principal_matches_current(pid: int) -> bool:
    try:
        process = psutil.Process(int(pid))
    except (
        psutil.AccessDenied,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
        OSError,
    ):
        return False
    return (
        _principal_relation(
            _current_principal(),
            _process_principal(process),
        )
        == "same"
    )


def scan_containment(
    containment_id: str,
    *,
    root_identity: ProcessIdentity | None = None,
    started_at_lower_bound: float | None = None,
    containment_only_recovery: bool = False,
    expected_process_group_id: int | None = None,
    known_identities: tuple[ProcessIdentity, ...] = (),
    known_unreadable_identities: tuple[
        UnreadableProcessIdentity,
        ...,
    ] = (),
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
        if started_at_lower_bound is not None:
            started_at_lower_bound = float(started_at_lower_bound)
            if not math.isfinite(started_at_lower_bound):
                raise ValueError("scan start must be finite")
    except (TypeError, ValueError, OverflowError):
        return ContainmentSnapshot(
            processes=(),
            scan_complete=False,
            errors=("invalid_containment_scan_start",),
        )
    supplied_identities = (
        *((root_identity,) if root_identity is not None else ()),
        *known_identities,
    )
    for identity in supplied_identities:
        try:
            if (
                int(identity.pid) <= 0
                or not math.isfinite(float(identity.started_at))
            ):
                raise ValueError("invalid process identity")
        except (TypeError, ValueError, OverflowError):
            return ContainmentSnapshot(
                processes=(),
                scan_complete=False,
                errors=(f"invalid_process_identity:{identity.pid}",),
            )
    for identity in known_unreadable_identities:
        if identity.started_at is None:
            continue
        try:
            if (
                int(identity.pid) <= 0
                or not math.isfinite(float(identity.started_at))
            ):
                raise ValueError("invalid unreadable identity")
        except (TypeError, ValueError, OverflowError):
            return ContainmentSnapshot(
                processes=(),
                scan_complete=False,
                errors=(
                    f"invalid_unreadable_process_identity:{identity.pid}",
                ),
            )
    current_principal = _current_principal()
    lower_bounds = [
        value
        for value in (
            (
                root_identity.started_at - 0.001
                if root_identity is not None
                else None
            ),
            started_at_lower_bound,
        )
        if value is not None
    ]
    earliest = min(lower_bounds) if lower_bounds else None
    known_by_pid = {
        identity.pid: identity
        for identity in (
            *((root_identity,) if root_identity is not None else ()),
            *known_identities,
        )
    }
    found: dict[tuple[int, float], ProcessIdentity] = {}
    root_process_group_verified = False
    errors: list[str] = []
    unreadable: dict[
        tuple[int, float | None],
        UnreadableProcessIdentity,
    ] = {}
    previous_unreadable_by_pid = {
        unreadable_identity.pid: unreadable_identity
        for unreadable_identity in known_unreadable_identities
    }
    resolved_unreadable: set[tuple[int, float | None]] = set()
    processes = list(
        psutil.process_iter(
            ["pid", "ppid", "username", "uids", "create_time"],
        )
    )
    parent_by_pid: dict[int, int] = {}
    identity_by_pid: dict[int, ProcessIdentity] = {}
    for process in processes:
        try:
            pid = int(process.info["pid"])
            started_at = float(
                process.info.get("create_time")
                or process.create_time()
            )
            parent_pid = int(process.info.get("ppid") or 0)
        except (
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            psutil.AccessDenied,
            psutil.NoSuchProcess,
            psutil.ZombieProcess,
            ProcessLookupError,
            OSError,
        ):
            continue
        parent_by_pid[pid] = parent_pid
        identity_by_pid[pid] = ProcessIdentity(
            pid=pid,
            started_at=started_at,
        )
    for process in processes:
        try:
            pid = int(process.info["pid"])
            if pid == os.getpid():
                continue
            candidate_principal = _process_principal(process)
            candidate_principal_relation = _principal_relation(
                current_principal,
                candidate_principal,
            )
            if candidate_principal_relation == "different":
                continue
            started_at = float(
                process.info.get("create_time")
                or process.create_time()
            )
            if earliest is not None and started_at < earliest:
                continue
            identity = ProcessIdentity(pid=pid, started_at=started_at)
            known = known_by_pid.get(pid)
            exact_known_identity = (
                known is not None
                and _same_start(identity.started_at, known.started_at)
            )
            environment = process.environ()
            previous_unreadable = _matching_unreadable_identity(
                identity,
                previous_unreadable_by_pid,
            )
            if previous_unreadable is not None:
                resolved_unreadable.add(
                    (
                        previous_unreadable.pid,
                        previous_unreadable.started_at,
                    )
                )
            if (
                environment.get(CONTAINMENT_ENV_VAR) != normalized
                and not exact_known_identity
            ):
                continue
            if candidate_principal_relation != "same":
                error = f"unknown_principal:{pid}"
                unreadable_identity = UnreadableProcessIdentity(
                    pid=identity.pid,
                    started_at=identity.started_at,
                    relation="unknown_principal",
                    error=error,
                )
                unreadable[
                    (identity.pid, identity.started_at)
                ] = unreadable_identity
                errors.append(error)
                continue
            if not _observed_process_alive(process, identity):
                continue
            found[(identity.pid, identity.started_at)] = identity
            if (
                root_identity is not None
                and expected_process_group_id is not None
                and identity == root_identity
                and same_process(identity)
            ):
                try:
                    root_process_group_verified = (
                        int(os.getpgid(identity.pid))
                        == int(expected_process_group_id)
                    )
                except (OSError, ProcessLookupError):
                    root_process_group_verified = False
        except (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError):
            identity = identity_by_pid.get(int(process.pid))
            previous_unreadable = _matching_unreadable_identity(
                identity,
                previous_unreadable_by_pid,
            )
            if previous_unreadable is not None:
                resolved_unreadable.add(
                    (
                        previous_unreadable.pid,
                        previous_unreadable.started_at,
                    )
                )
            continue
        except psutil.AccessDenied:
            _record_unreadable_scan_process(
                process,
                error=f"access_denied:{int(process.pid)}",
                current_principal=current_principal,
                containment_only_recovery=containment_only_recovery,
                earliest=earliest,
                unreadable_scope=unreadable_scope,
                parent_by_pid=parent_by_pid,
                identity_by_pid=identity_by_pid,
                previous_unreadable_by_pid=previous_unreadable_by_pid,
                known_by_pid=known_by_pid,
                unreadable=unreadable,
                errors=errors,
            )
        except (OSError, ValueError) as exc:
            _record_unreadable_scan_process(
                process,
                error=f"scan_error:{int(process.pid)}:{type(exc).__name__}",
                current_principal=current_principal,
                containment_only_recovery=containment_only_recovery,
                earliest=earliest,
                unreadable_scope=unreadable_scope,
                parent_by_pid=parent_by_pid,
                identity_by_pid=identity_by_pid,
                previous_unreadable_by_pid=previous_unreadable_by_pid,
                known_by_pid=known_by_pid,
                unreadable=unreadable,
                errors=errors,
            )
    for known in known_by_pid.values():
        key = (known.pid, known.started_at)
        if key in found or key in unreadable:
            continue
        iterated = identity_by_pid.get(known.pid)
        if iterated is not None:
            continue
        process: Any = None
        try:
            process = psutil.Process(known.pid)
            observed_started_at = float(process.create_time())
            if not _same_start(observed_started_at, known.started_at):
                continue
            candidate_principal_relation = _principal_relation(
                current_principal,
                _process_principal(process),
            )
            if candidate_principal_relation == "different":
                continue
            # Once observed inside the containment, this exact PID generation
            # remains owned even if a later execve removes the cooperative tag.
            # The create-time and principal checks above fence PID reuse.
            process.environ()
            if candidate_principal_relation != "same":
                error = f"unknown_principal:{known.pid}"
                unreadable[key] = UnreadableProcessIdentity(
                    pid=known.pid,
                    started_at=known.started_at,
                    relation="unknown_principal",
                    error=error,
                )
                errors.append(error)
                continue
            if not _observed_process_alive(process, known):
                continue
            found[key] = known
            if (
                root_identity is not None
                and expected_process_group_id is not None
                and known == root_identity
                and same_process(known)
            ):
                try:
                    root_process_group_verified = (
                        int(os.getpgid(known.pid))
                        == int(expected_process_group_id)
                    )
                except (OSError, ProcessLookupError):
                    root_process_group_verified = False
        except (psutil.NoSuchProcess, psutil.ZombieProcess, ProcessLookupError):
            continue
        except AttributeError:
            continue
        except psutil.AccessDenied:
            error = f"access_denied:{known.pid}"
            candidate_principal_relation = _principal_relation(
                current_principal,
                _process_principal(process),
            )
            relation = (
                "known_identity"
                if candidate_principal_relation == "same"
                else "unknown_principal"
            )
            unreadable[key] = UnreadableProcessIdentity(
                pid=known.pid,
                started_at=known.started_at,
                relation=relation,
                error=error,
            )
            errors.append(error)
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            error = f"scan_error:{known.pid}:{type(exc).__name__}"
            unreadable[key] = UnreadableProcessIdentity(
                pid=known.pid,
                started_at=known.started_at,
                relation="known_identity",
                error=error,
            )
            errors.append(error)
    for previous in known_unreadable_identities:
        key = (previous.pid, previous.started_at)
        if key in unreadable or key in resolved_unreadable:
            continue
        observed = identity_by_pid.get(previous.pid)
        if (
            observed is not None
            and previous.started_at is not None
            and not _same_start(observed.started_at, previous.started_at)
        ):
            continue
        previous_identity = previous.identity
        if (
            observed is None
            and previous_identity is not None
            and _exact_identity_resolution(previous_identity)
            in {"exited", "reused"}
        ):
            continue
        unreadable[key] = previous
        errors.append(previous.error)
    return ContainmentSnapshot(
        processes=tuple(sorted(found.values())),
        scan_complete=not errors and not unreadable,
        errors=tuple(sorted(set(errors))),
        unreadable_identities=_sorted_unreadable(unreadable.values()),
        root_process_group_verified=root_process_group_verified,
    )


def terminate_containment(
    *,
    root_pid: int | None,
    expected_root_started_at: float | None,
    expected_process_group_id: int | None,
    containment_id: str,
    process: Any = None,
    root_pid_reused: bool = False,
    scan_started_at: float | None = None,
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
    launched containment tree degrade the proof. When only ``containment_id``
    survives recovery, ``root_pid`` and ``expected_process_group_id`` may both
    be ``None``; only exact tagged identities are then signalled.
    """
    normalized_containment_id = str(containment_id).strip()
    containment_only = root_pid is None
    if containment_only:
        root_pid = 0
    else:
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
            if (
                normalized_containment_id
                and expected_root_started_at is None
                and expected_process_group_id is None
            ):
                containment_only = True
                root_pid = 0
            else:
                return _result(
                    status="invalid_worker_pid",
                    safe=False,
                    root_pid=root_pid,
                    pgid=expected_process_group_id,
                    containment_id=containment_id,
                )
    if scan_started_at is not None:
        try:
            scan_started_at = float(scan_started_at)
            if not math.isfinite(scan_started_at):
                raise ValueError("scan start must be finite")
        except (TypeError, ValueError, OverflowError):
            return _result(
                status="invalid_containment_scan_start",
                safe=False,
                root_pid=root_pid,
                pgid=expected_process_group_id,
                containment_id=containment_id,
            )
    if containment_only and expected_root_started_at is not None:
        return _result(
            status="invalid_worker_start_identity",
            safe=False,
            root_pid=root_pid,
            pgid=None,
            containment_id=containment_id,
        )
    if expected_root_started_at is not None:
        try:
            expected_root_started_at = float(expected_root_started_at)
            if not math.isfinite(expected_root_started_at):
                raise ValueError("worker start identity must be finite")
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
    if expected_process_group_id is None and containment_only:
        expected_pgid = None
    elif expected_process_group_id is None:
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
    if expected_pgid is not None and expected_pgid <= 0:
        return _result(
            status="invalid_worker_process_group",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
        )
    observed_root = (
        process_identity(root_pid)
        if not containment_only
        else None
    )
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
    if (
        expected_pgid is not None
        and expected_pgid == os.getpgrp()
        and not root_pid_reused
    ):
        return _result(
            status="worker_process_group_matches_supervisor",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
        )
    if root_pid_reused and not normalized_containment_id:
        return _result(
            status="worker_identity_mismatch_pid_reused",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            root_pid_reused=True,
        )
    if (
        not normalized_containment_id
        and observed_root is None
        and process is not None
        and process.poll() is not None
        and expected_pgid is not None
        and not _process_group_exists(expected_pgid)
    ):
        return _result(
            status="worker_already_reaped",
            safe=True,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
        )
    if not normalized_containment_id:
        return _result(
            status="worker_containment_identity_missing",
            safe=False,
            root_pid=root_pid,
            pgid=expected_process_group_id,
            containment_id=containment_id,
        )

    if (
        not root_pid_reused
        and not containment_only
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
    live: dict[tuple[int, float], ProcessIdentity] = {}
    unresolved: dict[
        tuple[int, float | None],
        UnreadableProcessIdentity,
    ] = {}
    all_seen: set[int] = set()
    scan_errors: set[str] = set()
    scan_root_identity = None if root_pid_reused else root_identity
    protected_pids = frozenset({root_pid}) if root_pid_reused else frozenset()
    root_pids = {root_pid} if root_pid > 0 else set()
    term_deadline = time.monotonic() + max(0.0, term_timeout_s)
    term_outcome = _terminate_phase(
        sig=signal.SIGTERM,
        root_identity=scan_root_identity,
        expected_pgid=expected_pgid,
        containment_id=containment_id,
        tracked=tracked,
        live=live,
        unresolved=unresolved,
        all_seen=all_seen,
        scan_errors=scan_errors,
        signal_process_group=(
            expected_pgid is not None and not root_pid_reused
        ),
        protected_pids=protected_pids,
        deadline=term_deadline,
        quiescence_s=quiescence_s,
        poll_s=poll_s,
        process=process,
        unreadable_scope=unreadable_scope,
        scan_started_at=scan_started_at,
        containment_only_recovery=containment_only,
    )
    if term_outcome is True:
        return _result(
            status="worker_tree_terminated",
            safe=True,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            descendants=all_seen - root_pids,
            unresolved=unresolved,
            root_pid_reused=root_pid_reused,
        )
    if term_outcome is None:
        survivors = sorted({
            *(
                identity.pid
                for identity in live.values()
            ),
            *(identity.pid for identity in unresolved.values()),
        })
        return _result(
            status="worker_containment_scan_incomplete",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            descendants=all_seen - root_pids,
            survivors=survivors,
            scan_errors=scan_errors,
            unresolved=unresolved,
            root_pid_reused=root_pid_reused,
        )

    kill_deadline = time.monotonic() + max(0.0, kill_timeout_s)
    kill_outcome = _terminate_phase(
        sig=signal.SIGKILL,
        root_identity=scan_root_identity,
        expected_pgid=expected_pgid,
        containment_id=containment_id,
        tracked=tracked,
        live=live,
        unresolved=unresolved,
        all_seen=all_seen,
        scan_errors=scan_errors,
        signal_process_group=(
            expected_pgid is not None and not root_pid_reused
        ),
        protected_pids=protected_pids,
        deadline=kill_deadline,
        quiescence_s=quiescence_s,
        poll_s=poll_s,
        process=process,
        unreadable_scope=unreadable_scope,
        scan_started_at=scan_started_at,
        containment_only_recovery=containment_only,
    )
    if kill_outcome is True:
        return _result(
            status="worker_tree_killed",
            safe=True,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            descendants=all_seen - root_pids,
            unresolved=unresolved,
            root_pid_reused=root_pid_reused,
        )
    if kill_outcome is None:
        survivors = sorted({
            *(
                identity.pid
                for identity in live.values()
            ),
            *(identity.pid for identity in unresolved.values()),
        })
        return _result(
            status="worker_containment_scan_incomplete",
            safe=False,
            root_pid=root_pid,
            pgid=expected_pgid,
            containment_id=containment_id,
            descendants=all_seen - root_pids,
            survivors=survivors,
            scan_errors=scan_errors,
            unresolved=unresolved,
            root_pid_reused=root_pid_reused,
        )

    survivors = sorted({
        *(
            identity.pid
            for identity in live.values()
        ),
        *(identity.pid for identity in unresolved.values()),
    })
    if "generation_bound_signal_unavailable" in scan_errors:
        status = "generation_bound_signal_unavailable"
    elif scan_errors or unresolved:
        status = "worker_containment_scan_incomplete"
    else:
        status = "worker_tree_survived_sigkill"
    return _result(
        status=status,
        safe=False,
        root_pid=root_pid,
        pgid=expected_pgid,
        containment_id=containment_id,
        descendants=all_seen - root_pids,
        survivors=survivors,
        scan_errors=scan_errors,
        unresolved=unresolved,
        root_pid_reused=root_pid_reused,
    )


def _terminate_phase(
    *,
    sig: signal.Signals,
    root_identity: ProcessIdentity | None,
    expected_pgid: int | None,
    containment_id: str,
    tracked: dict[tuple[int, float], ProcessIdentity],
    live: dict[tuple[int, float], ProcessIdentity],
    unresolved: dict[
        tuple[int, float | None],
        UnreadableProcessIdentity,
    ],
    all_seen: set[int],
    scan_errors: set[str],
    signal_process_group: bool,
    protected_pids: frozenset[int],
    deadline: float,
    quiescence_s: float,
    poll_s: float,
    process: Any,
    unreadable_scope: str = "same_user",
    scan_started_at: float | None = None,
    containment_only_recovery: bool = False,
) -> bool | None:
    quiet_since: float | None = None
    scan_attempted = False
    phase_signal_sent = False
    while True:
        if scan_attempted:
            remaining = deadline - time.monotonic()
            if remaining <= max(0.05, poll_s):
                return False
        termination_scan, scan_timed_out = _scan_before_deadline(
            deadline,
            containment_id=containment_id,
            root_identity=root_identity,
            started_at_lower_bound=scan_started_at,
            containment_only_recovery=containment_only_recovery,
            expected_process_group_id=expected_pgid,
            known_identities=tuple(tracked.values()),
            known_unreadable_identities=tuple(
                identity
                for identity in unresolved.values()
                if identity.relation != "unverified_popen"
            ),
            unreadable_scope=unreadable_scope,
        )
        snapshot = termination_scan.snapshot
        scan_errors.update(snapshot.errors)
        scan_attempted = True
        if scan_timed_out:
            _close_pidfds(termination_scan.pidfds)
            return (
                False
                if sig == signal.SIGTERM and phase_signal_sent
                else None
            )
        unresolved.clear()
        unresolved.update({
            (identity.pid, identity.started_at): identity
            for identity in snapshot.unreadable_identities
        })
        popen_blocker = _unverified_popen_blocker(
            process,
            root_identity=root_identity,
        )
        if popen_blocker is not None:
            unresolved[
                (popen_blocker.pid, popen_blocker.started_at)
            ] = popen_blocker
            scan_errors.add(popen_blocker.error)
        for identity in snapshot.processes:
            if identity.pid in protected_pids:
                continue
            tracked[(identity.pid, identity.started_at)] = identity
            all_seen.add(identity.pid)
        live.clear()
        live.update({
            (identity.pid, identity.started_at): identity
            for identity in snapshot.processes
            if identity.pid not in protected_pids
        })
        original_child_live = True
        if process is not None and root_identity is not None:
            try:
                process_pid = getattr(process, "pid", None)
                original_child_live = (
                    (
                        process_pid is None
                        or int(process_pid) == root_identity.pid
                    )
                    and process.poll() is None
                )
            except Exception:
                original_child_live = False
        group_owned = (
            signal_process_group
            and expected_pgid is not None
            and snapshot.root_process_group_verified
            and original_child_live
            and _root_process_group_still_owned(
                root_identity=root_identity,
                expected_pgid=expected_pgid,
                containment_id=containment_id,
                process=process,
            )
        )
        signal_sent = False
        if group_owned:
            try:
                os.killpg(expected_pgid, sig)
                signal_sent = True
            except (ProcessLookupError, PermissionError):
                pass
        for (pid, _started_at), pidfd in termination_scan.pidfds.items():
            if pid in protected_pids:
                continue
            signal_sent = _send_pidfd_signal(pidfd, sig) or signal_sent
        if not _pidfd_signalling_available():
            for identity in _exact_signal_targets(snapshot):
                if (
                    identity.pid in protected_pids
                    or identity.pid <= 0
                    or identity.pid == os.getpid()
                ):
                    continue
                signal_sent = (
                    _send_verified_pid_signal(identity, sig)
                    or signal_sent
                )
        _close_pidfds(termination_scan.pidfds)
        phase_signal_sent = phase_signal_sent or signal_sent

        _reap_process(process, root_identity.pid if root_identity else 0)
        if (live or unresolved) and not signal_sent:
            has_generation_bound_target = bool(live) or any(
                identity.relation
                in {"known_identity", "structural_descendant"}
                for identity in unresolved.values()
            )
            if has_generation_bound_target:
                scan_errors.add("generation_bound_signal_unavailable")
            return False
        now = time.monotonic()
        if not live and snapshot.scan_complete and not unresolved:
            if quiet_since is None:
                quiet_since = now
            if now - quiet_since >= max(0.0, quiescence_s):
                final_scan, final_scan_timed_out = _scan_before_deadline(
                    deadline,
                    containment_id=containment_id,
                    root_identity=root_identity,
                    started_at_lower_bound=scan_started_at,
                    containment_only_recovery=containment_only_recovery,
                    expected_process_group_id=expected_pgid,
                    known_identities=tuple(tracked.values()),
                    known_unreadable_identities=tuple(
                        identity
                        for identity in unresolved.values()
                        if identity.relation != "unverified_popen"
                    ),
                    unreadable_scope=unreadable_scope,
                )
                final = final_scan.snapshot
                scan_errors.update(final.errors)
                if final_scan_timed_out:
                    _close_pidfds(final_scan.pidfds)
                    return (
                        False
                        if sig == signal.SIGTERM and phase_signal_sent
                        else None
                    )
                unresolved.clear()
                unresolved.update({
                    (identity.pid, identity.started_at): identity
                    for identity in final.unreadable_identities
                })
                popen_blocker = _unverified_popen_blocker(
                    process,
                    root_identity=root_identity,
                )
                if popen_blocker is not None:
                    unresolved[
                        (popen_blocker.pid, popen_blocker.started_at)
                    ] = popen_blocker
                    scan_errors.add(popen_blocker.error)
                if (
                    final.scan_complete
                    and not final.processes
                    and not unresolved
                ):
                    live.clear()
                    _close_pidfds(final_scan.pidfds)
                    return True
                live.clear()
                live.update({
                    (identity.pid, identity.started_at): identity
                    for identity in final.processes
                    if identity.pid not in protected_pids
                })
                _close_pidfds(final_scan.pidfds)
                quiet_since = None
        else:
            quiet_since = None
        if now >= deadline:
            return False
        time.sleep(min(max(0.001, poll_s), max(0.001, deadline - now)))


def _scan_before_deadline(
    deadline: float,
    *,
    containment_id: str,
    **scan_kwargs: Any,
) -> tuple[_TerminationScan, bool]:
    """Run one potentially blocking process scan in a bounded daemon."""
    remaining = max(0.0, deadline - time.monotonic())
    if remaining <= 0:
        return (
            _TerminationScan(
                snapshot=ContainmentSnapshot(
                    processes=(),
                    scan_complete=False,
                    errors=("scan_timeout",),
                ),
                pidfds={},
            ),
            True,
        )

    done = threading.Event()
    lock = threading.Lock()
    cancelled = False
    result: list[_TerminationScan] = []

    def scan_worker() -> None:
        nonlocal cancelled
        pidfds: dict[tuple[int, float], int] = {}
        try:
            snapshot = scan_containment(
                containment_id,
                **scan_kwargs,
            )
            for identity in _exact_signal_targets(snapshot):
                pidfd = _open_verified_pidfd(identity)
                if pidfd is not None:
                    pidfds[(identity.pid, identity.started_at)] = pidfd
            termination_scan = _TerminationScan(
                snapshot=snapshot,
                pidfds=pidfds,
            )
        except Exception as exc:
            _close_pidfds(pidfds)
            termination_scan = _TerminationScan(
                snapshot=ContainmentSnapshot(
                    processes=(),
                    scan_complete=False,
                    errors=(f"scan_error:{type(exc).__name__}",),
                ),
                pidfds={},
            )
        with lock:
            if cancelled:
                _close_pidfds(termination_scan.pidfds)
            else:
                result.append(termination_scan)
        done.set()

    worker = threading.Thread(
        target=scan_worker,
        name="containment-scan",
        daemon=True,
    )
    worker.start()
    if not done.wait(remaining):
        with lock:
            if result:
                return result[0], False
            cancelled = True
        return (
            _TerminationScan(
                snapshot=ContainmentSnapshot(
                    processes=(),
                    scan_complete=False,
                    errors=("scan_timeout",),
                ),
                pidfds={},
            ),
            True,
        )
    with lock:
        if result:
            return result[0], False
    return (
        _TerminationScan(
            snapshot=ContainmentSnapshot(
                processes=(),
                scan_complete=False,
                errors=("scan_error:worker_no_result",),
            ),
            pidfds={},
        ),
        False,
    )


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
    unresolved: Mapping[
        tuple[int, float | None],
        UnreadableProcessIdentity,
    ] | None = None,
    root_pid_reused: bool = False,
) -> dict[str, Any]:
    unresolved_identities = _sorted_unreadable(
        (unresolved or {}).values()
    )
    return {
        "status": status,
        "safe_to_finalize": bool(safe and not unresolved_identities),
        "pid": root_pid,
        "pgid": pgid,
        "containment_id": str(containment_id),
        "descendant_pids": sorted(descendants or set()),
        "surviving_pids": list(survivors or []),
        "scan_errors": sorted(scan_errors or set()),
        "unresolved_process_identities": [
            {
                "pid": identity.pid,
                "started_at": identity.started_at,
                "relation": identity.relation,
                "error": identity.error,
            }
            for identity in unresolved_identities
        ],
        "containment_kind": "inherited_environment_same_user",
        "root_pid_reused": bool(root_pid_reused),
    }


def _same_start(observed: float, expected: float) -> bool:
    return abs(float(observed) - float(expected)) <= 0.001


def _sorted_unreadable(
    identities: Any,
) -> tuple[UnreadableProcessIdentity, ...]:
    return tuple(
        sorted(
            identities,
            key=lambda identity: (
                identity.pid,
                (
                    float("-inf")
                    if identity.started_at is None
                    else identity.started_at
                ),
                identity.relation,
                identity.error,
            ),
        )
    )


def _unverified_popen_blocker(
    process: Any,
    *,
    root_identity: ProcessIdentity | None,
) -> UnreadableProcessIdentity | None:
    if process is None or root_identity is not None:
        return None
    try:
        pid = int(getattr(process, "pid", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        pid = 0
    try:
        if process.poll() is not None:
            return None
    except Exception:
        pass
    error = f"unverified_popen_live:{pid}"
    return UnreadableProcessIdentity(
        pid=pid,
        started_at=None,
        relation="unverified_popen",
        error=error,
    )


def _matching_unreadable_identity(
    identity: ProcessIdentity | None,
    previous_unreadable_by_pid: Mapping[int, UnreadableProcessIdentity],
) -> UnreadableProcessIdentity | None:
    if identity is None:
        return None
    previous = previous_unreadable_by_pid.get(identity.pid)
    if (
        previous is not None
        and previous.started_at is not None
        and _same_start(identity.started_at, previous.started_at)
    ):
        return previous
    return None


def _identity_for_unreadable_error(
    pid: int,
    *,
    identity_by_pid: Mapping[int, ProcessIdentity],
    previous_unreadable_by_pid: Mapping[int, UnreadableProcessIdentity],
    known_by_pid: Mapping[int, ProcessIdentity],
) -> ProcessIdentity | None:
    scanned = identity_by_pid.get(pid)
    if scanned is not None:
        return scanned
    previous = previous_unreadable_by_pid.get(pid)
    if previous is not None:
        return previous.identity
    return known_by_pid.get(pid)


def _exact_identity_resolution(identity: ProcessIdentity) -> str:
    """Classify an exact identity without treating access denial as exit."""
    try:
        process = psutil.Process(identity.pid)
        observed_started_at = float(process.create_time())
    except (
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
    ):
        return "exited"
    except (psutil.AccessDenied, PermissionError, OSError, ValueError):
        return "unreadable"
    if not _same_start(observed_started_at, identity.started_at):
        return "reused"
    try:
        if (
            not process.is_running()
            or process.status() == psutil.STATUS_ZOMBIE
        ):
            return "exited"
    except (
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
    ):
        return "exited"
    except (psutil.AccessDenied, PermissionError, OSError):
        pass
    return "same"


def _open_verified_pidfd(identity: ProcessIdentity) -> int | None:
    """Open a pidfd and verify that it binds the scanned generation/principal."""
    pidfd_open = getattr(os, "pidfd_open", None)
    if not callable(pidfd_open):
        return None
    pidfd: int | None = None
    try:
        pidfd = int(pidfd_open(identity.pid, 0))
        observed = process_identity(identity.pid)
        if (
            observed is None
            or not _same_start(observed.started_at, identity.started_at)
            or not _pid_principal_matches_current(identity.pid)
        ):
            try:
                os.close(pidfd)
            except OSError:
                pass
            return None
    except (
        ProcessLookupError,
        PermissionError,
        OSError,
        TypeError,
        ValueError,
    ):
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass
        return None
    return pidfd


def _pidfd_signalling_available() -> bool:
    return callable(getattr(os, "pidfd_open", None)) and callable(
        getattr(signal, "pidfd_send_signal", None)
    )


def _exact_signal_targets(
    snapshot: ContainmentSnapshot,
) -> tuple[ProcessIdentity, ...]:
    targets: dict[tuple[int, float], ProcessIdentity] = {
        (identity.pid, identity.started_at): identity
        for identity in snapshot.processes
    }
    for unreadable_identity in snapshot.unreadable_identities:
        if unreadable_identity.relation not in {
            "known_identity",
            "structural_descendant",
        }:
            continue
        exact_identity = unreadable_identity.identity
        if exact_identity is not None:
            targets.setdefault(
                (exact_identity.pid, exact_identity.started_at),
                exact_identity,
            )
    return tuple(targets.values())


def _send_verified_pid_signal(
    identity: ProcessIdentity,
    sig: signal.Signals,
) -> bool:
    observed = process_identity(identity.pid)
    if (
        observed is None
        or not _same_start(observed.started_at, identity.started_at)
        or not _pid_principal_matches_current(identity.pid)
    ):
        return False
    try:
        os.kill(identity.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _send_pidfd_signal(pidfd: int, sig: signal.Signals) -> bool:
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if not callable(pidfd_send_signal):
        return False
    try:
        pidfd_send_signal(int(pidfd), sig, None, 0)
    except (
        ProcessLookupError,
        PermissionError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False
    return True


def _pid_has_containment_id(pid: int, containment_id: str) -> bool:
    normalized = str(containment_id).strip()
    if not normalized:
        return False
    try:
        return (
            str(
                psutil.Process(int(pid)).environ().get(
                    CONTAINMENT_ENV_VAR,
                    "",
                )
            )
            == normalized
        )
    except (
        OSError,
        psutil.AccessDenied,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
        TypeError,
        ValueError,
    ):
        return False


def _root_process_group_still_owned(
    *,
    root_identity: ProcessIdentity | None,
    expected_pgid: int,
    containment_id: str,
    process: Any,
) -> bool:
    """Revalidate group ownership immediately before a group signal."""
    if root_identity is None or int(expected_pgid) == os.getpgrp():
        return False
    observed = process_identity(root_identity.pid)
    if (
        observed is None
        or not _same_start(observed.started_at, root_identity.started_at)
        or not _pid_principal_matches_current(root_identity.pid)
        or not _pid_has_containment_id(
            root_identity.pid,
            containment_id,
        )
    ):
        return False
    try:
        if int(os.getpgid(root_identity.pid)) != int(expected_pgid):
            return False
        if process is not None:
            process_pid = getattr(process, "pid", None)
            if (
                process_pid is not None
                and int(process_pid) != root_identity.pid
            ):
                return False
            if process.poll() is not None:
                return False
    except (
        OSError,
        ProcessLookupError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        return False
    return True


def _close_pidfds(pidfds: Mapping[tuple[int, float], int]) -> None:
    for pidfd in pidfds.values():
        try:
            os.close(int(pidfd))
        except OSError:
            pass


def _record_unreadable_scan_process(
    process: Any,
    *,
    error: str,
    current_principal: _ProcessPrincipal,
    containment_only_recovery: bool,
    earliest: float | None,
    unreadable_scope: str,
    parent_by_pid: Mapping[int, int],
    identity_by_pid: Mapping[int, ProcessIdentity],
    previous_unreadable_by_pid: Mapping[int, UnreadableProcessIdentity],
    known_by_pid: Mapping[int, ProcessIdentity],
    unreadable: MutableMapping[
        tuple[int, float | None],
        UnreadableProcessIdentity,
    ],
    errors: list[str],
) -> None:
    pid = int(process.pid)
    identity = _identity_for_unreadable_error(
        pid,
        identity_by_pid=identity_by_pid,
        previous_unreadable_by_pid=previous_unreadable_by_pid,
        known_by_pid=known_by_pid,
    )
    candidate_principal_relation = _principal_relation(
        current_principal,
        _process_principal(process),
    )
    if candidate_principal_relation == "different":
        return
    if identity is None:
        if (
            containment_only_recovery
            or earliest is not None
            or unreadable_scope == "same_user"
        ):
            relation = (
                "unknown_principal"
                if candidate_principal_relation != "same"
                else "non_identifiable"
            )
            unreadable[(pid, None)] = UnreadableProcessIdentity(
                pid=pid,
                started_at=None,
                relation=relation,
                error=error,
            )
            errors.append(error)
        return
    relation = _retained_or_observed_unreadable_relation(
        identity,
        previous_unreadable_by_pid=previous_unreadable_by_pid,
        unreadable_scope=unreadable_scope,
        earliest=earliest,
        parent_by_pid=parent_by_pid,
        identity_by_pid=identity_by_pid,
        known_by_pid=known_by_pid,
    )
    if (
        relation is None
        and containment_only_recovery
        and earliest is not None
    ):
        relation = "same_user_after_root"
    if relation is not None and candidate_principal_relation != "same":
        relation = "unknown_principal"
    if relation is not None:
        unreadable[
            (identity.pid, identity.started_at)
        ] = UnreadableProcessIdentity(
            pid=identity.pid,
            started_at=identity.started_at,
            relation=relation,
            error=error,
        )
        errors.append(error)


def _retained_or_observed_unreadable_relation(
    identity: ProcessIdentity | None,
    *,
    previous_unreadable_by_pid: Mapping[int, UnreadableProcessIdentity],
    unreadable_scope: str,
    earliest: float | None,
    parent_by_pid: Mapping[int, int],
    identity_by_pid: Mapping[int, ProcessIdentity],
    known_by_pid: Mapping[int, ProcessIdentity],
) -> str | None:
    if identity is None:
        return None
    previous = _matching_unreadable_identity(
        identity,
        previous_unreadable_by_pid,
    )
    if previous is not None:
        return previous.relation
    return _observed_unreadable_relation(
        identity,
        unreadable_scope=unreadable_scope,
        earliest=earliest,
        parent_by_pid=parent_by_pid,
        identity_by_pid=identity_by_pid,
        known_by_pid=known_by_pid,
    )


def _observed_unreadable_relation(
    identity: ProcessIdentity,
    *,
    unreadable_scope: str,
    earliest: float | None,
    parent_by_pid: Mapping[int, int],
    identity_by_pid: Mapping[int, ProcessIdentity],
    known_by_pid: Mapping[int, ProcessIdentity],
) -> str | None:
    """Classify why an unreadable exact identity is in containment scope."""
    known = known_by_pid.get(identity.pid)
    if (
        known is not None
        and _same_start(identity.started_at, known.started_at)
    ):
        return "known_identity"
    if _is_structurally_related(
        identity,
        parent_by_pid=parent_by_pid,
        identity_by_pid=identity_by_pid,
        known_by_pid=known_by_pid,
    ):
        return "structural_descendant"
    if unreadable_scope == "same_user":
        if earliest is not None:
            return "same_user_after_root"
        return "same_user_unbounded"
    return None


def _is_structurally_related(
    identity: ProcessIdentity,
    *,
    parent_by_pid: Mapping[int, int],
    identity_by_pid: Mapping[int, ProcessIdentity],
    known_by_pid: Mapping[int, ProcessIdentity],
) -> bool:
    """Return whether ``pid`` is known or descends from a known process."""
    current = identity
    visited: set[tuple[int, float]] = set()
    while current.pid > 0:
        key = (current.pid, current.started_at)
        if key in visited:
            return False
        known = known_by_pid.get(current.pid)
        if (
            known is not None
            and _same_start(current.started_at, known.started_at)
        ):
            return True
        visited.add(key)
        parent_pid = int(parent_by_pid.get(current.pid, 0))
        parent_identity = identity_by_pid.get(parent_pid)
        if parent_identity is None:
            return False
        current = parent_identity
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


def _observed_process_alive(
    process: Any,
    identity: ProcessIdentity,
) -> bool:
    try:
        return (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except AttributeError:
        return same_process(identity)
    except (
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
        ProcessLookupError,
    ):
        return False
    except (psutil.AccessDenied, PermissionError, OSError):
        return True


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
