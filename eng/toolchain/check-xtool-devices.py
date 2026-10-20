#!/usr/bin/env python3
"""Run xtool USB discovery without emitting names or device identifiers."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path


REVISION = "f0a1f90efdbb0dc023e276ff529da92618da7a87"
MARKER_NAME = ".dotnet-ios-linux-xtool"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List xtool USB devices while suppressing all device-provided values."
    )
    parser.add_argument("--xtool-root", required=True, type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    arguments = parse_arguments()
    root = arguments.xtool_root
    executable = root / "xtool"
    marker_path = root / MARKER_NAME
    try:
        marker = marker_path.read_text(encoding="utf-8").splitlines()
        expected_hash = next(line.split("=", 1)[1] for line in marker if line.startswith("xtool_sha256="))
        expected_revision = next(
            line.split("=", 1)[1] for line in marker if line.startswith("xtool_revision=")
        )
    except (OSError, StopIteration, ValueError):
        print("xtool_stage=invalid")
        return 2

    if expected_revision != REVISION or sha256(executable) != expected_hash:
        print("xtool_stage=invalid")
        return 2

    environment = os.environ.copy()
    environment.pop("LD_LIBRARY_PATH", None)
    try:
        result = subprocess.run(
            [str(executable), "devices", "--usb", "--no-wait"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print("xtool_device_discovery=unavailable")
        print(f"error_type={type(error).__name__}")
        return 3

    if result.returncode != 0:
        print("xtool_device_discovery=failed")
        print(f"xtool_exit_code={result.returncode}")
        return 3

    # Each non-empty stdout line is one device. The line itself contains the
    # device name and UDID, so it must never be copied to this process's output.
    device_count = sum(1 for line in result.stdout.splitlines() if line.strip())
    print("xtool_device_discovery=available")
    print(f"usb_device_count={device_count}")
    print(f"stderr_suppressed={str(bool(result.stderr)).lower()}")
    return 0 if device_count else 4


if __name__ == "__main__":
    raise SystemExit(main())
