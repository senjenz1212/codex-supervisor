"""Whitelisted live policy overlay for supervisor auto-evolution."""
from __future__ import annotations

import errno
import os
import secrets
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover - Unix is the supported runtime.
    fcntl = None


POLICY_OVERLAY_SCHEMA_VERSION = "supervisor-policy-overlay/v1"
POLICY_OVERLAY_SNAPSHOT_SCHEMA_VERSION = "supervisor-policy-overlay-snapshot/v1"
POLICY_REGRESSION_SCHEMA_VERSION = "supervisor-policy-regression/v1"
POLICY_ROLLBACK_DRAFT_SCHEMA_VERSION = "supervisor-policy-rollback-draft/v1"
POLICY_OVERLAY_PATH = ".supervisor/policy-overlay.yaml"
POLICY_OVERLAY_BLOCK_HEADER = "Supervisor policy overlay guidance"
EMPTY_POLICY_OVERLAY_HASH = sha256(b"").hexdigest()
ALLOWED_OVERLAY_KEYS = frozenset({
    "schema_version",
    "active_proposal_id",
    "instruction_guidance_blocks",
    "task_class_overlays",
    "frozen_task_classes",
    "lesson_limit",
    "rubric_thresholds",
})
ALLOWED_TASK_CLASS_OVERLAY_KEYS = frozenset({
    "instruction_guidance_blocks",
    "lesson_limit",
    "rubric_thresholds",
})


class PolicyOverlayError(RuntimeError):
    """Raised when a policy overlay or target is outside the safe surface."""


@dataclass(frozen=True)
class PolicyOverlaySnapshot:
    path: str
    exists: bool
    content_hash: str
    proposal_id: str
    lesson_limit: int
    guidance_blocks: Mapping[str, Any]
    task_class_overlays: Mapping[str, Any]
    frozen_task_classes: tuple[str, ...]
    rubric_thresholds: Mapping[str, Any]
    raw: Mapping[str, Any]

    def instruction_block(self, *, gate: str, task_class: str | None = None) -> str:
        return render_policy_overlay_block(self.raw, gate=gate, task_class=task_class)

    def block_hash(self, *, gate: str, task_class: str | None = None) -> str:
        return sha256(self.instruction_block(gate=gate, task_class=task_class).encode("utf-8")).hexdigest()

    def is_frozen(self, *, task_class: str | None = None) -> bool:
        normalized = _normalise_task_class(task_class)
        return bool(normalized and normalized in self.frozen_task_classes)

    def task_class_overlay_hash(self, *, task_class: str | None = None) -> str:
        normalized = _normalise_task_class(task_class)
        slot = self.task_class_overlays.get(normalized) if normalized else None
        if not isinstance(slot, Mapping):
            return sha256(b"").hexdigest()
        return sha256(yaml.safe_dump(dict(slot), sort_keys=True).encode("utf-8")).hexdigest()

    def to_event_payload(self, *, gate: str, task_class: str | None = None) -> dict[str, Any]:
        normalized_task_class = _normalise_task_class(task_class)
        frozen = self.is_frozen(task_class=normalized_task_class)
        block = "" if frozen else self.instruction_block(gate=gate, task_class=normalized_task_class)
        return {
            "schema_version": POLICY_OVERLAY_SNAPSHOT_SCHEMA_VERSION,
            "gate": gate,
            "task_class": normalized_task_class,
            "overlay_path": self.path,
            "exists": self.exists,
            "overlay_hash": self.content_hash,
            "policy_overlay_hash": self.content_hash,
            "proposal_id": self.proposal_id,
            "policy_proposal_id": self.proposal_id,
            "lesson_limit": self.lesson_limit,
            "block": block,
            "block_sha256": sha256(block.encode("utf-8")).hexdigest(),
            "overlay_frozen": frozen,
            "frozen_task_classes": list(self.frozen_task_classes),
            "task_class_overlay_hash": self.task_class_overlay_hash(task_class=normalized_task_class),
            "whitelisted_keys": sorted(ALLOWED_OVERLAY_KEYS),
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_authority": "unchanged",
        }


def normalise_overlay_target(path: str | Path, *, repo_root: str | Path) -> str:
    """Return a repo-relative path and reject non-overlay policy targets."""
    repo_root_path = _real_repo_root(repo_root)
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        raise PolicyOverlayError("policy overlay target path is required")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        for resolved in (
            Path(os.path.abspath(candidate)),
            Path(os.path.realpath(candidate)),
        ):
            try:
                raw = resolved.relative_to(repo_root_path).as_posix()
                break
            except ValueError:
                continue
        else:
            raise PolicyOverlayError(
                f"policy overlay target is outside repo root: {path}"
            )
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise PolicyOverlayError(f"policy overlay target traversal is not allowed: {path}")
        parts.append(part)
    rel = "/".join(parts)
    if rel != POLICY_OVERLAY_PATH:
        raise PolicyOverlayError(
            f"policy evolution may only target {POLICY_OVERLAY_PATH}; observed {rel}"
        )
    assert_repo_local_path(
        repo_root_path / rel,
        repo_root=repo_root_path,
        label="policy overlay target",
    )
    return rel


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | _NOFOLLOW | _DIRECTORY | _CLOEXEC
_READ_FLAGS = os.O_RDONLY | _NOFOLLOW | _CLOEXEC
_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | _NOFOLLOW
    | _CLOEXEC
)
_POLICY_PROCESS_LOCK = threading.RLock()


def assert_repo_local_path(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str = "path",
) -> Path:
    """Return a lexical repo-local path after rejecting existing links."""
    root, candidate, relative = _repo_relative_path(
        path,
        repo_root=repo_root,
        label=label,
    )
    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError as exc:
            raise PolicyOverlayError(
                f"{label} could not be inspected safely: {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise PolicyOverlayError(
                f"{label} contains a symlink component: {current}"
            )
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(
            metadata.st_mode
        ):
            raise PolicyOverlayError(
                f"{label} contains a non-directory component: {current}"
            )
    return candidate


@contextmanager
def repo_root_lock_no_follow(
    repo_root: str | Path,
    *,
    label: str = "repository",
) -> Iterator[None]:
    """Serialize policy transactions without creating an attacker path."""
    if fcntl is None:
        raise PolicyOverlayError(
            f"{label} cross-process filesystem locking is unavailable"
        )
    _POLICY_PROCESS_LOCK.acquire()
    try:
        root = _real_repo_root(repo_root)
        try:
            descriptor = os.open(root, _DIRECTORY_FLAGS)
        except OSError as exc:
            raise _secure_open_error(label=f"{label} root", error=exc) from exc
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    break
                except OSError as exc:
                    if exc.errno == errno.EINTR:
                        continue
                    raise PolicyOverlayError(
                        f"{label} cross-process lock failed: {exc}"
                    ) from exc
            try:
                yield
            finally:
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                        break
                    except OSError as exc:
                        if exc.errno == errno.EINTR:
                            continue
                        raise PolicyOverlayError(
                            f"{label} cross-process unlock failed: {exc}"
                        ) from exc
        finally:
            os.close(descriptor)
    finally:
        _POLICY_PROCESS_LOCK.release()


def read_repo_file_no_follow(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str = "path",
    missing_ok: bool = False,
    max_bytes: int | None = None,
) -> bytes | None:
    """Read one regular repo-local file without following any links."""
    opened = _open_repo_parent(
        path,
        repo_root=repo_root,
        label=label,
        create=False,
        missing_ok=missing_ok,
    )
    if opened is None:
        return None
    _target, parent_fd, name = opened
    try:
        return _read_regular_file_at(
            parent_fd,
            name,
            label=label,
            missing_ok=missing_ok,
            max_bytes=max_bytes,
        )
    finally:
        os.close(parent_fd)


def create_repo_file_no_follow(
    path: str | Path,
    data: bytes,
    *,
    repo_root: str | Path,
    label: str = "path",
    exist_ok_same: bool = False,
) -> Path:
    """Crash-atomically create one regular file without replacing an entry."""
    target, parent_fd, name = _require_open_repo_parent(
        path,
        repo_root=repo_root,
        label=label,
        create=True,
    )
    temporary_name = (
        f".policy-create-{sha256(name.encode('utf-8')).hexdigest()[:16]}-"
        f"{os.getpid()}-{secrets.token_hex(16)}"
    )
    temporary_created = False
    try:
        existing = _read_regular_file_at(
            parent_fd,
            name,
            label=label,
            missing_ok=True,
        )
        if existing is not None:
            if exist_ok_same and existing == data:
                return target
            raise PolicyOverlayError(
                f"{label} already exists with different content: {target}"
            )
        try:
            descriptor = os.open(
                temporary_name,
                _CREATE_FLAGS,
                0o600,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise _secure_open_error(
                label=f"{label} temporary file",
                error=exc,
            ) from exc
        temporary_created = True
        try:
            _write_all(descriptor, data, label=label)
            os.fsync(descriptor)
        except OSError as exc:
            raise PolicyOverlayError(
                f"{label} durable write failed: {target}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing = _read_regular_file_at(
                parent_fd,
                name,
                label=label,
                missing_ok=False,
            )
            if not (exist_ok_same and existing == data):
                raise PolicyOverlayError(
                    f"{label} already exists with different content: {target}"
                )
        except OSError as exc:
            raise _secure_open_error(label=label, error=exc) from exc
        else:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError as exc:
                try:
                    os.unlink(name, dir_fd=parent_fd)
                except OSError:
                    pass
                raise _secure_open_error(
                    label=f"{label} temporary publication",
                    error=exc,
                ) from exc
            temporary_created = False
        _fsync_directory(parent_fd, label=label)
        observed = _read_regular_file_at(
            parent_fd,
            name,
            label=label,
            missing_ok=False,
        )
        if observed != data:
            raise PolicyOverlayError(
                f"{label} post-create content mismatch: {target}"
            )
        return target
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(parent_fd)


def write_repo_file_no_follow(
    path: str | Path,
    data: bytes,
    *,
    repo_root: str | Path,
    label: str = "path",
) -> Path:
    """Atomically replace bytes without following links or truncating aliases."""
    target, parent_fd, name = _require_open_repo_parent(
        path,
        repo_root=repo_root,
        label=label,
        create=True,
    )
    temporary_name = f".policy-write-{os.getpid()}-{secrets.token_hex(16)}"
    try:
        _reject_unsafe_existing_entry(parent_fd, name, label=label)
        try:
            descriptor = os.open(
                temporary_name,
                _CREATE_FLAGS,
                0o600,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise _secure_open_error(
                label=f"{label} temporary file",
                error=exc,
            ) from exc
        try:
            _write_all(descriptor, data, label=label)
            os.fsync(descriptor)
        except OSError as exc:
            raise PolicyOverlayError(
                f"{label} durable write failed: {target}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)
        try:
            os.replace(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError as exc:
            raise _secure_open_error(label=label, error=exc) from exc
        _fsync_directory(parent_fd, label=label)
        return target
    finally:
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except (FileNotFoundError, OSError):
            pass
        os.close(parent_fd)


def commit_staged_repo_file_no_follow(
    target_path: str | Path,
    staged_path: str | Path | None,
    *,
    repo_root: str | Path,
    expected_hash: str,
    expected_exists: bool,
    desired_hash: str,
    desired_exists: bool,
    label: str = "path",
) -> Path:
    """Publish a staged sibling through an existence-and-hash CAS."""
    target, parent_fd, target_name = _require_open_repo_parent(
        target_path,
        repo_root=repo_root,
        label=label,
        create=False,
    )
    try:
        current = _read_regular_file_at(
            parent_fd,
            target_name,
            label=label,
            missing_ok=True,
        )
        current_exists = current is not None
        current_hash = sha256(current or b"").hexdigest()
        if current_exists == desired_exists and current_hash == desired_hash:
            if staged_path is not None:
                _remove_staged_sibling(
                    parent_fd,
                    target_path=target,
                    staged_path=staged_path,
                    repo_root=repo_root,
                    label=label,
                )
            return target
        if (
            current_exists != bool(expected_exists)
            or current_hash != str(expected_hash)
        ):
            raise PolicyOverlayError(
                f"{label} compare-and-set mismatch for {target}: "
                f"expected exists={bool(expected_exists)} hash={expected_hash}, "
                f"observed exists={current_exists} hash={current_hash}"
            )

        if desired_exists:
            if staged_path is None:
                raise PolicyOverlayError(
                    f"{label} staged artifact is required for publication"
                )
            staged_target, staged_parent_fd, staged_name = (
                _require_open_repo_parent(
                    staged_path,
                    repo_root=repo_root,
                    label=f"{label} staged artifact",
                    create=False,
                )
            )
            try:
                target_root, _, target_relative = _repo_relative_path(
                    target,
                    repo_root=repo_root,
                    label=label,
                )
                staged_root, _, staged_relative = _repo_relative_path(
                    staged_target,
                    repo_root=repo_root,
                    label=f"{label} staged artifact",
                )
                if (
                    target_root != staged_root
                    or target_relative.parent != staged_relative.parent
                ):
                    raise PolicyOverlayError(
                        f"{label} staged artifact must be a target sibling"
                    )
                staged_bytes = _read_regular_file_at(
                    staged_parent_fd,
                    staged_name,
                    label=f"{label} staged artifact",
                    missing_ok=False,
                )
                staged_hash = sha256(staged_bytes or b"").hexdigest()
                if staged_hash != desired_hash:
                    raise PolicyOverlayError(
                        f"{label} staged artifact hash mismatch: "
                        f"expected {desired_hash}, observed {staged_hash}"
                    )
                try:
                    os.replace(
                        staged_name,
                        target_name,
                        src_dir_fd=staged_parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                except OSError as exc:
                    raise _secure_open_error(label=label, error=exc) from exc
            finally:
                os.close(staged_parent_fd)
        else:
            try:
                os.unlink(target_name, dir_fd=parent_fd)
            except OSError as exc:
                raise _secure_open_error(label=label, error=exc) from exc
        _fsync_directory(parent_fd, label=label)
        observed = _read_regular_file_at(
            parent_fd,
            target_name,
            label=label,
            missing_ok=True,
        )
        observed_exists = observed is not None
        observed_hash = sha256(observed or b"").hexdigest()
        if (
            observed_exists != bool(desired_exists)
            or observed_hash != desired_hash
        ):
            raise PolicyOverlayError(
                f"{label} post-commit hash mismatch for {target}: "
                f"expected exists={bool(desired_exists)} hash={desired_hash}, "
                f"observed exists={observed_exists} hash={observed_hash}"
            )
        return target
    finally:
        os.close(parent_fd)


def remove_repo_file_no_follow(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str = "path",
    missing_ok: bool = True,
) -> bool:
    """Remove one regular file without following a swapped parent or target."""
    opened = _open_repo_parent(
        path,
        repo_root=repo_root,
        label=label,
        create=False,
        missing_ok=missing_ok,
    )
    if opened is None:
        return False
    _target, parent_fd, name = opened
    try:
        metadata = _entry_metadata_at(
            parent_fd,
            name,
            label=label,
            missing_ok=missing_ok,
        )
        if metadata is None:
            return False
        _require_regular_single_link(metadata, label=label)
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            if missing_ok:
                return False
            raise
        except OSError as exc:
            raise _secure_open_error(label=label, error=exc) from exc
        _fsync_directory(parent_fd, label=label)
        return True
    finally:
        os.close(parent_fd)


def list_repo_directory_no_follow(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str = "directory",
    missing_ok: bool = False,
) -> tuple[str, ...]:
    """List one repo-local directory through no-follow descriptors."""
    root, _candidate, relative = _repo_relative_path(
        path,
        repo_root=repo_root,
        label=label,
    )
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise _secure_open_error(label=f"{label} root", error=exc) from exc
    try:
        for component in relative.parts:
            try:
                child = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if missing_ok:
                    return ()
                raise PolicyOverlayError(f"{label} is missing: {path}")
            except OSError as exc:
                raise _secure_open_error(label=label, error=exc) from exc
            os.close(descriptor)
            descriptor = child
        try:
            return tuple(sorted(os.listdir(descriptor)))
        except OSError as exc:
            raise PolicyOverlayError(
                f"{label} could not be listed safely: {exc}"
            ) from exc
    finally:
        os.close(descriptor)


def _real_repo_root(repo_root: str | Path) -> Path:
    if not _NOFOLLOW or not _DIRECTORY:
        raise PolicyOverlayError(
            "secure no-follow filesystem operations are unavailable"
        )
    return Path(
        os.path.realpath(
            os.path.abspath(os.path.expanduser(os.fspath(repo_root)))
        )
    )


def _repo_relative_path(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str,
) -> tuple[Path, Path, Path]:
    root = _real_repo_root(repo_root)
    raw = Path(os.path.expanduser(os.fspath(path)))
    candidate = raw if raw.is_absolute() else root / raw
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PolicyOverlayError(f"{label} is outside repo root: {path}") from exc
    if not relative.parts:
        raise PolicyOverlayError(f"{label} must name a path inside repo root")
    return root, candidate, relative


def _secure_open_error(*, label: str, error: OSError) -> PolicyOverlayError:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return PolicyOverlayError(
            f"{label} contains a symlink or non-directory path component"
        )
    if error.errno == errno.ENOENT:
        return PolicyOverlayError(f"{label} is missing")
    return PolicyOverlayError(f"{label} could not be opened safely: {error}")


def _open_repo_parent(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str,
    create: bool,
    missing_ok: bool,
) -> tuple[Path, int, str] | None:
    root, target, relative = _repo_relative_path(
        path,
        repo_root=repo_root,
        label=label,
    )
    try:
        current_fd = os.open(root, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise _secure_open_error(label=f"{label} root", error=exc) from exc
    try:
        for component in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise _secure_open_error(label=label, error=exc) from exc
                else:
                    _fsync_directory(
                        current_fd,
                        label=f"{label} parent creation",
                    )
            try:
                child_fd = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                if missing_ok:
                    os.close(current_fd)
                    return None
                raise PolicyOverlayError(f"{label} is missing: {target}")
            except OSError as exc:
                raise _secure_open_error(label=label, error=exc) from exc
            os.close(current_fd)
            current_fd = child_fd
        return target, current_fd, relative.parts[-1]
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _require_open_repo_parent(
    path: str | Path,
    *,
    repo_root: str | Path,
    label: str,
    create: bool,
) -> tuple[Path, int, str]:
    opened = _open_repo_parent(
        path,
        repo_root=repo_root,
        label=label,
        create=create,
        missing_ok=False,
    )
    assert opened is not None
    return opened


def _entry_metadata_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
    missing_ok: bool,
) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise PolicyOverlayError(f"{label} is missing")
    except OSError as exc:
        raise _secure_open_error(label=label, error=exc) from exc


def _require_regular_single_link(
    metadata: os.stat_result,
    *,
    label: str,
) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise PolicyOverlayError(f"{label} is a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise PolicyOverlayError(f"{label} is not a regular file")
    if metadata.st_nlink != 1:
        raise PolicyOverlayError(f"{label} has unexpected hard links")


def _reject_unsafe_existing_entry(
    parent_fd: int,
    name: str,
    *,
    label: str,
) -> None:
    metadata = _entry_metadata_at(
        parent_fd,
        name,
        label=label,
        missing_ok=True,
    )
    if metadata is not None:
        _require_regular_single_link(metadata, label=label)


def _read_regular_file_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
    missing_ok: bool,
    max_bytes: int | None = None,
) -> bytes | None:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise PolicyOverlayError(f"{label} is missing")
    except OSError as exc:
        raise _secure_open_error(label=label, error=exc) from exc
    try:
        metadata = os.fstat(descriptor)
        _require_regular_single_link(metadata, label=label)
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = os.read(descriptor, 1024 * 1024)
            except OSError as exc:
                raise PolicyOverlayError(
                    f"{label} could not be read safely: {exc}"
                ) from exc
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise PolicyOverlayError(
                    f"{label} exceeds the maximum allowed size"
                )
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes, *, label: str) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError(f"{label} write made no progress")
        remaining = remaining[written:]


def _fsync_directory(descriptor: int, *, label: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise PolicyOverlayError(
            f"{label} directory fsync failed: {exc}"
        ) from exc


def _remove_staged_sibling(
    parent_fd: int,
    *,
    target_path: Path,
    staged_path: str | Path,
    repo_root: str | Path,
    label: str,
) -> None:
    target_root, _, target_relative = _repo_relative_path(
        target_path,
        repo_root=repo_root,
        label=label,
    )
    staged_root, _, staged_relative = _repo_relative_path(
        staged_path,
        repo_root=repo_root,
        label=f"{label} staged artifact",
    )
    if (
        target_root != staged_root
        or target_relative.parent != staged_relative.parent
    ):
        raise PolicyOverlayError(
            f"{label} staged artifact must be a target sibling"
        )
    metadata = _entry_metadata_at(
        parent_fd,
        staged_relative.name,
        label=f"{label} staged artifact",
        missing_ok=True,
    )
    if metadata is None:
        return
    _require_regular_single_link(
        metadata,
        label=f"{label} staged artifact",
    )
    try:
        os.unlink(staged_relative.name, dir_fd=parent_fd)
    except OSError as exc:
        raise _secure_open_error(
            label=f"{label} staged artifact",
            error=exc,
        ) from exc
    _fsync_directory(parent_fd, label=f"{label} staged cleanup")


def load_policy_overlay(repo_root: str | Path) -> PolicyOverlaySnapshot:
    data = read_repo_file_no_follow(
        POLICY_OVERLAY_PATH,
        repo_root=repo_root,
        label="policy overlay",
        missing_ok=True,
    )
    if data is None:
        return PolicyOverlaySnapshot(
            path=POLICY_OVERLAY_PATH,
            exists=False,
            content_hash=EMPTY_POLICY_OVERLAY_HASH,
            proposal_id="",
            lesson_limit=5,
            guidance_blocks={},
            task_class_overlays={},
            frozen_task_classes=(),
            rubric_thresholds={},
            raw={},
        )
    loaded = yaml.safe_load(data.decode("utf-8")) or {}
    if not isinstance(loaded, dict):
        raise PolicyOverlayError("policy overlay must be a mapping")
    unknown = sorted(set(str(key) for key in loaded) - ALLOWED_OVERLAY_KEYS)
    if unknown:
        raise PolicyOverlayError(f"policy overlay contains non-whitelisted keys: {unknown}")
    guidance = loaded.get("instruction_guidance_blocks") or {}
    if not isinstance(guidance, dict):
        raise PolicyOverlayError("instruction_guidance_blocks must be a mapping")
    rubric = loaded.get("rubric_thresholds") or {}
    if not isinstance(rubric, dict):
        raise PolicyOverlayError("rubric_thresholds must be a mapping")
    task_class_overlays = loaded.get("task_class_overlays") or {}
    if not isinstance(task_class_overlays, dict):
        raise PolicyOverlayError("task_class_overlays must be a mapping")
    _validate_task_class_overlays(task_class_overlays)
    frozen_task_classes = _normalise_frozen_task_classes(loaded.get("frozen_task_classes") or ())
    return PolicyOverlaySnapshot(
        path=POLICY_OVERLAY_PATH,
        exists=True,
        content_hash=sha256(data).hexdigest(),
        proposal_id=str(loaded.get("active_proposal_id") or ""),
        lesson_limit=_bounded_int(loaded.get("lesson_limit"), default=5, minimum=0, maximum=20),
        guidance_blocks=guidance,
        task_class_overlays=task_class_overlays,
        frozen_task_classes=frozen_task_classes,
        rubric_thresholds=rubric,
        raw=loaded,
    )


def apply_policy_overlay_to_instruction(
    instruction: str,
    *,
    overlay: PolicyOverlaySnapshot,
    gate: str,
    task_class: str | None = None,
) -> str:
    if overlay.is_frozen(task_class=task_class):
        return instruction
    block = overlay.instruction_block(gate=gate, task_class=task_class)
    if not block or block in instruction:
        return instruction
    return instruction.rstrip() + "\n\n" + block


def render_policy_overlay_block(
    overlay: Mapping[str, Any],
    *,
    gate: str,
    task_class: str | None = None,
) -> str:
    guidance = overlay.get("instruction_guidance_blocks")
    entries: list[str] = []
    if isinstance(guidance, Mapping):
        entries.extend(_guidance_entries(guidance, gate=gate))
    task_class_slot = _task_class_slot(overlay, task_class=task_class)
    if isinstance(task_class_slot, Mapping):
        scoped = task_class_slot.get("instruction_guidance_blocks")
        if isinstance(scoped, Mapping):
            entries.extend(_guidance_entries(scoped, gate=gate))
    if not entries:
        return ""
    lines = [
        POLICY_OVERLAY_BLOCK_HEADER,
        "This operator-approved overlay is advisory and cannot satisfy a gate by itself.",
    ]
    for index, entry in enumerate(entries, start=1):
        lines.append(f"{index}. {entry}")
    return "\n".join(lines)


def _guidance_entries(guidance: Mapping[str, Any], *, gate: str) -> list[str]:
    entries: list[str] = []
    for key in ("all", gate):
        value = guidance.get(key)
        if isinstance(value, str) and value.strip():
            entries.append(value.strip())
        elif isinstance(value, (list, tuple)):
            entries.extend(str(item).strip() for item in value if str(item).strip())
    return entries


def _task_class_slot(overlay: Mapping[str, Any], *, task_class: str | None = None) -> Mapping[str, Any] | None:
    normalized = _normalise_task_class(task_class)
    slots = overlay.get("task_class_overlays")
    if not normalized or not isinstance(slots, Mapping):
        return None
    slot = slots.get(normalized)
    return slot if isinstance(slot, Mapping) else None


def draft_policy_regression_rollback_if_needed(
    state: Any,
    *,
    run_id: str,
    proposal_id: str,
    rollback_pointer: Mapping[str, Any],
    repo_root: str | Path,
    task_class: str | None = None,
    gate: str | None = None,
    min_runs: int = 3,
    first_pass_drop_threshold: float = 0.05,
    false_accept_increase_threshold: float = 0.01,
    time_to_accept_increase_ratio: float = 0.25,
    now: int | None = None,
) -> dict[str, Any]:
    """Detect policy regression and draft exactly one rollback proposal."""
    proposal = str(proposal_id or "").strip()
    if not proposal:
        raise PolicyOverlayError("proposal_id is required for regression verification")
    _validate_rollback_pointer_targets(rollback_pointer, repo_root=repo_root)
    rows = state.list_quality_trend_rows(task_class=task_class, gate=gate)
    before = [row for row in rows if str(row.get("policy_proposal_id") or "") != proposal]
    after = [row for row in rows if str(row.get("policy_proposal_id") or "") == proposal]
    if len(after) < max(1, int(min_runs)) or not before:
        return {
            "status": "insufficient_data",
            "proposal_id": proposal,
            "after_count": len(after),
            "before_count": len(before),
            "rollback_drafted": False,
        }

    comparison = _compare_windows(before=before, after=after)
    reasons: list[str] = []
    if comparison["first_pass_rate_delta"] < -abs(float(first_pass_drop_threshold)):
        reasons.append("first_pass_rate_regressed")
    if comparison["false_accept_rate_delta"] > abs(float(false_accept_increase_threshold)):
        reasons.append("false_accept_rate_regressed")
    before_time = comparison["before_avg_time_to_accept_s"]
    after_time = comparison["after_avg_time_to_accept_s"]
    if before_time is not None and after_time is not None:
        allowed = before_time * (1.0 + max(0.0, float(time_to_accept_increase_ratio)))
        if after_time > allowed:
            reasons.append("time_to_accept_regressed")
    if not reasons:
        return {
            "status": "no_regression",
            "proposal_id": proposal,
            "comparison": comparison,
            "rollback_drafted": False,
        }

    existing = [
        event for event in state.read_events_since(run_id, after_event_id=0, limit=10_000)
        if event["kind"] == "autoresearch_policy_rollback_proposal_drafted"
        and str(event["payload"].get("proposal_id") or "") == proposal
    ]
    if existing:
        return {
            "status": "already_drafted",
            "proposal_id": proposal,
            "comparison": comparison,
            "rollback_drafted": True,
            "event_id": existing[-1]["event_id"],
        }

    timestamp = int(time.time()) if now is None else int(now)
    detection_payload = {
        "schema_version": POLICY_REGRESSION_SCHEMA_VERSION,
        "proposal_id": proposal,
        "task_class": task_class or "",
        "gate": gate or "",
        "reasons": reasons,
        "comparison": comparison,
        "detected_at": timestamp,
        "observational_only": True,
        "gate_authority": "unchanged",
    }
    detection_event_id = state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="policy_regression_detected",
        payload=detection_payload,
    )
    rollback_draft = {
        "schema_version": POLICY_ROLLBACK_DRAFT_SCHEMA_VERSION,
        "proposal_id": proposal,
        "status": "draft",
        "reason": "policy_regression_detected",
        "detected_event_id": detection_event_id,
        "rollback_pointer": dict(rollback_pointer),
        "comparison": comparison,
        "requires_operator_approval": True,
        "operator_approved": False,
        "default_change_allowed": False,
        "automatic_policy_mutation": False,
        "gate_advanced": False,
        "gate_authority": "unchanged",
    }
    draft_event_id = state.write_event(
        run_id=run_id,
        source="autoresearch",
        kind="autoresearch_policy_rollback_proposal_drafted",
        payload=rollback_draft,
    )
    return {
        "status": "rollback_drafted",
        "proposal_id": proposal,
        "comparison": comparison,
        "rollback_drafted": True,
        "policy_regression_event_id": detection_event_id,
        "rollback_draft_event_id": draft_event_id,
        "rollback_proposal": rollback_draft,
    }


def draft_policy_regression_rollbacks_for_trend_rows(
    state: Any,
    *,
    run_id: str,
    trend_rows: list[Mapping[str, Any]],
    repo_root: str | Path,
    min_runs: int = 3,
    first_pass_drop_threshold: float = 0.05,
    false_accept_increase_threshold: float = 0.01,
    time_to_accept_increase_ratio: float = 0.25,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Draft rollback proposals for newly recorded trend rows tied to live policy ids."""
    seen: set[tuple[str, str, str]] = set()
    results: list[dict[str, Any]] = []
    for row in trend_rows:
        proposal_id = str(row.get("policy_proposal_id") or "").strip()
        if not proposal_id:
            continue
        task_class = str(row.get("task_class") or "")
        gate = str(row.get("gate") or "")
        key = (proposal_id, task_class, gate)
        if key in seen:
            continue
        seen.add(key)
        rollback_pointer = _latest_rollback_pointer_for_proposal(state, proposal_id=proposal_id)
        if rollback_pointer is None:
            results.append({
                "status": "missing_rollback_pointer",
                "proposal_id": proposal_id,
                "task_class": task_class,
                "gate": gate,
                "rollback_drafted": False,
                "observational_only": True,
                "gate_authority": "unchanged",
            })
            continue
        result = draft_policy_regression_rollback_if_needed(
            state,
            run_id=run_id,
            proposal_id=proposal_id,
            rollback_pointer=rollback_pointer,
            repo_root=repo_root,
            task_class=task_class or None,
            gate=gate or None,
            min_runs=min_runs,
            first_pass_drop_threshold=first_pass_drop_threshold,
            false_accept_increase_threshold=false_accept_increase_threshold,
            time_to_accept_increase_ratio=time_to_accept_increase_ratio,
            now=now,
        )
        results.append({
            **result,
            "task_class": task_class,
            "gate": gate,
            "observational_only": True,
            "gate_authority": "unchanged",
        })
    return results


def schedule_empty_floor_rebaseline_if_due(
    state: Any,
    *,
    run_id: str,
    proposal_id: str,
    overlay_hash: str,
    task_class: str,
    gate: str,
    cadence_s: int = 604800,
    now: int | None = None,
) -> dict[str, Any]:
    """Emit an observational request to re-baseline a live overlay against empty."""
    timestamp = int(time.time()) if now is None else int(now)
    proposal = str(proposal_id or "").strip()
    overlay = str(overlay_hash or "").strip()
    task = _normalise_task_class(task_class)
    gate_name = str(gate or "").strip() or "unknown"
    existing = [
        event for event in state.read_events_since(run_id, after_event_id=0, limit=10_000)
        if event["kind"] == "policy_empty_floor_rebaseline_due"
        and str(event["payload"].get("proposal_id") or "") == proposal
        and str(event["payload"].get("task_class") or "") == task
        and str(event["payload"].get("gate") or "") == gate_name
    ]
    if existing:
        latest = max(int(event.get("ts") or 0) for event in existing)
        if timestamp - latest < max(1, int(cadence_s)):
            return {
                "status": "not_due",
                "proposal_id": proposal,
                "overlay_hash": overlay,
                "task_class": task,
                "gate": gate_name,
                "observational_only": True,
                "gate_authority": "unchanged",
            }
    event_id = state.write_event(
        run_id=run_id,
        source="supervisor",
        kind="policy_empty_floor_rebaseline_due",
        payload={
            "schema_version": "supervisor-policy-empty-floor-rebaseline/v1",
            "proposal_id": proposal,
            "overlay_hash": overlay,
            "task_class": task,
            "gate": gate_name,
            "scheduled_at": timestamp,
            "observational_only": True,
            "default_change_allowed": False,
            "automatic_policy_mutation": False,
            "gate_advanced": False,
            "gate_authority": "unchanged",
        },
    )
    return {
        "status": "scheduled",
        "event_id": event_id,
        "proposal_id": proposal,
        "overlay_hash": overlay,
        "task_class": task,
        "gate": gate_name,
        "observational_only": True,
        "gate_authority": "unchanged",
    }


def _latest_rollback_pointer_for_proposal(state: Any, *, proposal_id: str) -> dict[str, Any] | None:
    list_approvals = getattr(state, "list_policy_proposal_approval_events", None)
    if list_approvals is None:
        return None
    events = list_approvals(proposal_id=proposal_id, limit=10_000)
    for event in reversed(events):
        payload = event.get("payload") if isinstance(event, Mapping) else {}
        pointer = payload.get("rollback_pointer") if isinstance(payload, Mapping) else None
        if isinstance(pointer, Mapping) and pointer.get("files"):
            return dict(pointer)
    return None


def _validate_rollback_pointer_targets(
    rollback_pointer: Mapping[str, Any],
    *,
    repo_root: str | Path,
) -> None:
    files = rollback_pointer.get("files") if isinstance(rollback_pointer.get("files"), list) else []
    if not files:
        raise PolicyOverlayError("rollback pointer has no files")
    for item in files:
        if not isinstance(item, Mapping):
            raise PolicyOverlayError("rollback file entry must be an object")
        normalise_overlay_target(str(item.get("target_path") or ""), repo_root=repo_root)


def _compare_windows(*, before: list[Mapping[str, Any]], after: list[Mapping[str, Any]]) -> dict[str, Any]:
    before_first_pass = _rate(before, "first_pass_accepted")
    after_first_pass = _rate(after, "first_pass_accepted")
    before_false_accept = _false_accept_rate(before)
    after_false_accept = _false_accept_rate(after)
    before_time = _avg_time(before)
    after_time = _avg_time(after)
    return {
        "before_count": len(before),
        "after_count": len(after),
        "before_first_pass_rate": before_first_pass,
        "after_first_pass_rate": after_first_pass,
        "first_pass_rate_delta": after_first_pass - before_first_pass,
        "before_false_accept_rate": before_false_accept,
        "after_false_accept_rate": after_false_accept,
        "false_accept_rate_delta": after_false_accept - before_false_accept,
        "before_avg_time_to_accept_s": before_time,
        "after_avg_time_to_accept_s": after_time,
        "time_to_accept_delta_s": (
            after_time - before_time
            if before_time is not None and after_time is not None
            else None
        ),
    }


def _rate(rows: list[Mapping[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if bool(row.get(key))) / len(rows)


def _false_accept_rate(rows: list[Mapping[str, Any]]) -> float:
    denominator = sum(int(row.get("false_accept_denominator") or 0) for row in rows)
    if denominator <= 0:
        return 0.0
    false_count = sum(int(row.get("false_accept_count") or 0) for row in rows)
    return false_count / denominator


def _avg_time(rows: list[Mapping[str, Any]]) -> float | None:
    values = [
        float(row["time_to_accepted_outcome_s"])
        for row in rows
        if row.get("time_to_accepted_outcome_s") is not None
    ]
    return (sum(values) / len(values)) if values else None


def _validate_task_class_overlays(task_class_overlays: Mapping[str, Any]) -> None:
    for task_class, slot in task_class_overlays.items():
        normalized = _normalise_task_class(task_class)
        if not normalized:
            raise PolicyOverlayError("task_class_overlays keys must be non-empty")
        if not isinstance(slot, Mapping):
            raise PolicyOverlayError(f"task_class overlay must be a mapping: {task_class}")
        unknown = sorted(set(str(key) for key in slot) - ALLOWED_TASK_CLASS_OVERLAY_KEYS)
        if unknown:
            raise PolicyOverlayError(
                f"task_class overlay {task_class} contains non-whitelisted keys: {unknown}"
            )
        guidance = slot.get("instruction_guidance_blocks") or {}
        if not isinstance(guidance, Mapping):
            raise PolicyOverlayError(
                f"task_class overlay {task_class} instruction_guidance_blocks must be a mapping"
            )
        rubric = slot.get("rubric_thresholds") or {}
        if not isinstance(rubric, Mapping):
            raise PolicyOverlayError(
                f"task_class overlay {task_class} rubric_thresholds must be a mapping"
            )


def _normalise_frozen_task_classes(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raise PolicyOverlayError("frozen_task_classes must be a list")
    normalized = sorted({item for item in (_normalise_task_class(raw) for raw in raw_values) if item})
    return tuple(normalized)


def _normalise_task_class(value: Any) -> str:
    return str(value or "").strip().replace("-", "_")


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        observed = int(value)
    except (TypeError, ValueError):
        observed = int(default)
    return max(minimum, min(maximum, observed))
