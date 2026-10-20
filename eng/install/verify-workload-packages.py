#!/usr/bin/env python3
"""Verify every package archive in the locked direct Linux iOS workload graph."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


EXPECTED_PACKAGES = {
    "ios-sdk-27": ("Microsoft.iOS.Sdk.net10.0_27.0", "27.0.10722", True, False),
    "ios-sdk-26": ("Microsoft.iOS.Sdk.net10.0_26.0", "26.0.11017", True, False),
    "ios-ref-27": ("Microsoft.iOS.Ref.net10.0_27.0", "27.0.10722", True, False),
    "ios-runtime-27": ("Microsoft.iOS.Runtime.ios.net10.0_27.0", "27.0.10722", True, False),
    "ios-runtime-arm64-27": (
        "Microsoft.iOS.Runtime.ios-arm64.net10.0_27.0",
        "27.0.10722",
        True,
        False,
    ),
    "ios-templates-27": ("Microsoft.iOS.Templates", "27.0.10722", True, False),
    "mono-runtime-ios-arm64": (
        "Microsoft.NETCore.App.Runtime.Mono.ios-arm64",
        "10.0.12",
        True,
        False,
    ),
    "mono-aot-task": ("Microsoft.NET.Runtime.MonoAOTCompiler.Task", "10.0.12", True, False),
    "mono-targets-sdk": ("Microsoft.NET.Runtime.MonoTargets.Sdk", "10.0.12", True, False),
    "project-sdk": ("DotnetIosLinux.Sdk", "0.1.0-preview.1", True, True),
    "project-aot": (
        "DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64",
        "0.1.0-preview.1",
        True,
        True,
    ),
    "derived-ios-manifest": (
        "Microsoft.NET.Sdk.iOS.Manifest-10.0.100",
        "27.0.10722",
        False,
        True,
    ),
}


def fail(message: str) -> "None":
    raise SystemExit(f"workload package verification failed: {message}")


def safe_relative(raw: str) -> Path:
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"unsafe package path in toolchain lock: {raw!r}")
    return Path(*value.parts)


def digests(path: Path) -> tuple[str, str]:
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(block)
            sha512.update(block)
    return sha256.hexdigest(), base64.b64encode(sha512.digest()).decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="canonical .NET root")
    parser.add_argument("--feed", type=Path, required=True, help="project-owned local workload feed")
    parser.add_argument("--lock", type=Path, required=True, help="toolchain.lock.json")
    parser.add_argument("--dotnet", type=Path, required=True, help="pinned dotnet executable")
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    feed = args.feed.expanduser().absolute()
    lock_path = args.lock.expanduser().resolve()
    dotnet = args.dotnet.expanduser().resolve()
    if not dotnet.is_file() or not os.access(dotnet, os.X_OK):
        fail(f"dotnet is not executable: {dotnet}")

    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        records = lock["components"]["linuxIosWorkload"]["packages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"cannot read package inventory from {lock_path}: {error}")
    if not isinstance(records, list):
        fail("package inventory is not a list")
    by_key: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("key"), str):
            fail("package inventory contains an invalid record")
        if record["key"] in by_key:
            fail(f"package inventory contains duplicate key {record['key']!r}")
        by_key[record["key"]] = record
    if set(by_key) != set(EXPECTED_PACKAGES):
        fail("package inventory does not exactly match the required direct workload graph")

    signed_official: list[Path] = []
    unsigned_local: list[Path] = []
    for key, (package_id, version, installed, local_feed) in EXPECTED_PACKAGES.items():
        record = by_key[key]
        if record.get("id") != package_id or record.get("version") != version:
            fail(f"package identity mismatch for {key}")
        locations: list[Path] = []
        if installed:
            raw = record.get("installedArchive")
            if not isinstance(raw, str):
                fail(f"installed package path is missing for {key}")
            locations.append(root / safe_relative(raw))
        elif "installedArchive" in record:
            fail(f"unexpected installed package path for {key}")
        if local_feed:
            raw = record.get("feedFile")
            if not isinstance(raw, str) or len(PurePosixPath(raw).parts) != 1:
                fail(f"local feed filename is invalid for {key}")
            locations.append(feed / safe_relative(raw))
        elif "feedFile" in record:
            fail(f"unexpected local feed filename for {key}")

        expected_sha256 = record.get("sha256")
        expected_sha512 = record.get("sha512Base64")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            fail(f"invalid SHA-256 lock for {key}")
        if not isinstance(expected_sha512, str):
            fail(f"invalid SHA-512 lock for {key}")
        for path in locations:
            if not path.is_file() or path.is_symlink():
                fail(f"package archive is missing or not a regular file: {path}")
            actual_sha256, actual_sha512 = digests(path)
            if actual_sha256 != expected_sha256 or actual_sha512 != expected_sha512:
                fail(f"package archive hash mismatch: {path}")
            if record.get("origin") == "nuget.org":
                signed_official.append(path)
            elif local_feed:
                unsigned_local.append(path)

    for path in unsigned_local:
        try:
            with zipfile.ZipFile(path) as archive:
                signed = any(name.lower() == ".signature.p7s" for name in archive.namelist())
        except (OSError, zipfile.BadZipFile) as error:
            fail(f"cannot inspect local package signature state {path}: {error}")
        if signed:
            fail(f"deterministic local package unexpectedly contains a NuGet signature: {path}")

    environment = os.environ.copy()
    environment["NUGET_CERT_REVOCATION_MODE"] = "offline"
    result = subprocess.run(
        [str(dotnet), "nuget", "verify", "--all", *(str(path) for path in signed_official)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        fail("one or more exact official archives failed NuGet author/repository signature verification")

    print(f"workload_packages_verified={len(by_key)}")
    print(f"official_nuget_signatures_verified={len(signed_official)}")
    print(f"expected_unsigned_local_archives={len(unsigned_local)}")


if __name__ == "__main__":
    main()
