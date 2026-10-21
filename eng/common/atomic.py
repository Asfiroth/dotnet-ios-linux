#!/usr/bin/env python3
"""Linux x86-64 atomic directory publication primitives."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
from pathlib import Path


AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
SYS_RENAMEAT2_X86_64 = 316


def fsync_directory(path: Path) -> None:
    """Persist directory entries previously changed below *path*."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_file(path: Path) -> None:
    """Persist one regular file without following a final symlink."""
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_path_state(path: Path) -> None:
    """Persist the current presence, absence, or contents of one path."""
    if not os.path.lexists(path):
        current = path.parent
        while not os.path.lexists(current):
            if current == current.parent:
                break
            current = current.parent
        if not current.is_dir() or current.is_symlink():
            raise NotADirectoryError(f"path ancestor is unsafe: {current}")
        fsync_directory(current)
        return
    if path.is_symlink():
        fsync_directory(path.parent)
        return
    if path.is_dir():
        fsync_directory(path)
        fsync_directory(path.parent)
        return
    if path.is_file():
        fsync_file(path)
        fsync_directory(path.parent)
        return
    raise ValueError(f"cannot persist unsupported path type: {path}")


def mkdir_durable(path: Path, *, mode: int = 0o777) -> None:
    """Create a directory chain and persist every newly published entry."""
    missing: list[Path] = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    if not current.is_dir() or current.is_symlink():
        raise NotADirectoryError(f"directory ancestor is unsafe: {current}")
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        fsync_directory(directory)
        fsync_directory(directory.parent)


def fsync_tree(root: Path) -> None:
    """Persist a staged tree before publishing a journal that references it."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"fsync tree root is not a real directory: {root}")
    directories: list[Path] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        directories.append(current)
        for name in [*names, *files]:
            path = current / name
            if path.is_symlink():
                continue
            if path.is_file():
                fsync_file(path)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)


def durable_remove(path: Path, tombstone: Path) -> None:
    """Atomically hide *path*, then idempotently garbage-collect it."""
    if path.parent != tombstone.parent:
        raise ValueError("removal tombstone must share the target parent")
    if os.path.lexists(path) and os.path.lexists(tombstone):
        raise FileExistsError(f"both removal target and tombstone exist: {path}")
    if os.path.lexists(path):
        os.replace(path, tombstone)
        fsync_directory(path.parent)
    if not os.path.lexists(tombstone):
        fsync_path_state(path)
        return
    if tombstone.is_dir() and not tombstone.is_symlink():
        shutil.rmtree(tombstone)
    else:
        tombstone.unlink()
    fsync_directory(tombstone.parent)


def _renameat2(source: Path, destination: Path, flags: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is not None:
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        result = renameat2(AT_FDCWD, source_bytes, AT_FDCWD, destination_bytes, flags)
    else:
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
        result = syscall(
            SYS_RENAMEAT2_X86_64,
            AT_FDCWD,
            source_bytes,
            AT_FDCWD,
            destination_bytes,
            flags,
        )
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), destination)
        raise OSError(error, os.strerror(error), source, destination)


def publish_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically move a staged directory into an absent destination."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"publication source is not a real directory: {source}")
    fsync_tree(source)
    _renameat2(source, destination, RENAME_NOREPLACE)
    fsync_directory(destination.parent)


def exchange_directories(first: Path, second: Path) -> None:
    """Atomically exchange two existing directories on one filesystem."""
    if first.is_symlink() or second.is_symlink() or not first.is_dir() or not second.is_dir():
        raise ValueError("exchange operands must be real directories")
    _renameat2(first, second, RENAME_EXCHANGE)
    parents = {first.parent, second.parent}
    for parent in parents:
        fsync_directory(parent)
