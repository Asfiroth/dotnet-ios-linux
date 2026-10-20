#!/usr/bin/env python3
"""Linux x86-64 atomic directory publication primitives."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path


AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
SYS_RENAMEAT2_X86_64 = 316


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
    _renameat2(source, destination, RENAME_NOREPLACE)


def exchange_directories(first: Path, second: Path) -> None:
    """Atomically exchange two existing directories on one filesystem."""
    if first.is_symlink() or second.is_symlink() or not first.is_dir() or not second.is_dir():
        raise ValueError("exchange operands must be real directories")
    _renameat2(first, second, RENAME_EXCHANGE)
