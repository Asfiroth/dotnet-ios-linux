#!/usr/bin/env python3
"""Verify the selected locked Apple workload package graph."""

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


DIRECT_WORKLOADS = ("ios", "macos", "maccatalyst", "tvos")


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


def require_regular_path(path: Path, boundary: Path) -> None:
    """Reject a final archive or any ancestor symlink below its trust boundary."""

    try:
        relative = path.relative_to(boundary)
    except ValueError:
        fail(f"package archive escapes its trust boundary: {path}")
    current = boundary
    if current.is_symlink() or not current.is_dir():
        fail(f"package trust boundary is not a real directory: {boundary}")
    for component in relative.parts[:-1]:
        current /= component
        if current.is_symlink() or not current.is_dir():
            fail(f"package archive has an unsafe ancestor: {current}")
    if path.is_symlink() or not path.is_file():
        fail(f"package archive is missing or not a regular file: {path}")


def require_safe_root_path(root: Path, relative: Path, label: str) -> Path:
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        fail(f".NET root is not a real directory: {root}")
    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        if not os.path.lexists(current):
            break
        if current.is_symlink():
            fail(f"{label} has a symlink ancestor: {current}")
        if index < len(relative.parts) - 1 and not current.is_dir():
            fail(f"{label} ancestor is not a directory: {current}")
    return root / relative


def casefold_child(parent: Path, name: str, label: str) -> Path:
    if parent.is_symlink() or not parent.is_dir():
        fail(f"installed {label} parent is unsafe: {parent}")
    matches = [item for item in parent.iterdir() if item.name.casefold() == name.casefold()]
    if len(matches) != 1:
        fail(f"cannot locate one installed {label}: {name} under {parent}")
    result = matches[0]
    if result.is_symlink() or not result.is_dir():
        fail(f"installed {label} is not a real directory: {result}")
    return result


def verify_installed_derivative(root: Path, archive: Path, package_id: str, version: str) -> None:
    """Ensure the selected local manifest archive is the one the CLI extracted."""

    registry_id, separator, feature_band = package_id.partition(".Manifest-")
    if separator != ".Manifest-" or not registry_id or not feature_band:
        fail(f"local derivative has no manifest package identity: {package_id}")
    manifest_parent = require_safe_root_path(
        root, Path("sdk-manifests") / feature_band, "installed manifest"
    )
    manifest_root = casefold_child(manifest_parent, registry_id, "manifest")
    installed = casefold_child(manifest_root, version, "manifest version")
    if installed.is_symlink() or not installed.is_dir():
        fail(f"installed manifest directory is unsafe: {installed}")
    try:
        with zipfile.ZipFile(archive) as package:
            for name in ("WorkloadManifest.json", "WorkloadManifest.targets", "WorkloadDependencies.json"):
                member = f"data/{name}"
                if member not in package.namelist():
                    continue
                target = require_safe_root_path(
                    root,
                    Path("sdk-manifests") / feature_band / manifest_root.name / installed.name / name,
                    "installed local derivative",
                )
                if target.is_symlink() or not target.is_file():
                    fail(f"installed local derivative is missing {target}")
                if target.read_bytes() != package.read(member):
                    fail(f"installed local derivative does not match its selected archive: {target}")
    except (OSError, zipfile.BadZipFile) as error:
        fail(f"cannot inspect local derivative package {archive}: {error}")


def manifest_record(value: dict) -> dict:
    required = ("id", "version", "sha256", "sha512Base64", "feedFile")
    if not all(isinstance(value.get(key), str) and value[key] for key in required):
        fail("companion manifest derivative lock record is incomplete")
    return {
        "id": value["id"],
        "version": value["version"],
        "sha256": value["sha256"],
        "sha512Base64": value["sha512Base64"],
        "feedFile": value["feedFile"],
        "origin": "local-derivative",
    }


def selected_records(lock: dict, workloads: set[str]) -> list[dict]:
    components = lock.get("components")
    if not isinstance(components, dict):
        fail("toolchain lock lacks components")
    records: list[dict] = []
    if "ios" in workloads:
        ios = components.get("linuxIosWorkload", {})
        graph = ios.get("packages") if isinstance(ios, dict) else None
        if not isinstance(graph, list):
            fail("toolchain lock lacks the Linux iOS package inventory")
        records.extend(graph)
    companion_graph = components.get("appleCompanionWorkloads", {})
    companion_items = companion_graph.get("workloads") if isinstance(companion_graph, dict) else None
    if not isinstance(companion_items, list):
        fail("toolchain lock lacks the companion package inventory")
    found = {"ios"} if "ios" in workloads else set()
    for item in companion_items:
        if not isinstance(item, dict) or not isinstance(item.get("workloadId"), str):
            fail("companion package inventory contains an invalid workload")
        if item["workloadId"] not in workloads:
            continue
        packages = item.get("packages")
        manifest = item.get("manifest")
        if not isinstance(packages, list) or not isinstance(manifest, dict):
            fail(f"companion package inventory is incomplete for {item['workloadId']}")
        records.extend(packages)
        records.append(manifest_record(manifest))
        found.add(item["workloadId"])
    missing = sorted(workloads - found)
    if missing:
        fail("toolchain lock lacks selected workload graph(s): " + ", ".join(missing))
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            fail("package inventory contains an invalid record")
        package_id, version = record.get("id"), record.get("version")
        if not isinstance(package_id, str) or not isinstance(version, str):
            fail("package inventory contains an incomplete identity")
        identity = (package_id.casefold(), version.casefold())
        if identity in seen:
            fail(f"package inventory duplicates {package_id} {version}")
        seen.add(identity)
        result.append(record)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="canonical .NET root")
    parser.add_argument("--feed", type=Path, required=True, help="project-owned local workload feed")
    parser.add_argument("--lock", type=Path, required=True, help="toolchain.lock.json")
    parser.add_argument("--dotnet", type=Path, required=True, help="pinned dotnet executable")
    parser.add_argument("--workload", action="append", choices=DIRECT_WORKLOADS, default=[])
    args = parser.parse_args()
    workloads = set(args.workload) or set(DIRECT_WORKLOADS)
    root = args.root.expanduser().absolute()
    feed = args.feed.expanduser().absolute()
    lock_path = args.lock.expanduser().resolve()
    dotnet = args.dotnet.expanduser().resolve()
    if not dotnet.is_file() or not os.access(dotnet, os.X_OK):
        fail(f"dotnet is not executable: {dotnet}")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read package inventory from {lock_path}: {error}")

    signed_official: list[Path] = []
    unsigned_local: list[Path] = []
    for record in selected_records(lock, workloads):
        package_id = record["id"]
        version = record["version"]
        locations: list[tuple[Path, Path]] = []
        if "installedArchive" in record:
            raw = record["installedArchive"]
            if not isinstance(raw, str):
                fail(f"installed package path is invalid for {package_id}")
            locations.append((root / safe_relative(raw), root))
        if "feedFile" in record:
            raw = record["feedFile"]
            if not isinstance(raw, str) or len(PurePosixPath(raw).parts) != 1:
                fail(f"local feed filename is invalid for {package_id}")
            locations.append((feed / safe_relative(raw), feed))
        if not locations:
            fail(f"package has no installed archive or local feed archive: {package_id} {version}")
        expected_sha256 = record.get("sha256")
        expected_sha512 = record.get("sha512Base64")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or not isinstance(expected_sha512, str):
            fail(f"package lock is incomplete for {package_id} {version}")
        for path, boundary in locations:
            require_regular_path(path, boundary)
            if digests(path) != (expected_sha256, expected_sha512):
                fail(f"package archive hash mismatch: {path}")
            if record.get("origin") == "nuget.org":
                signed_official.append(path)
            elif "feedFile" in record:
                unsigned_local.append(path)
                if ".Manifest-" in package_id:
                    verify_installed_derivative(root, path, package_id, version)

    for path in unsigned_local:
        try:
            with zipfile.ZipFile(path) as archive:
                if any(name.lower() == ".signature.p7s" for name in archive.namelist()):
                    fail(f"deterministic local package unexpectedly contains a NuGet signature: {path}")
        except (OSError, zipfile.BadZipFile) as error:
            fail(f"cannot inspect local package signature state {path}: {error}")

    environment = os.environ.copy()
    environment["NUGET_CERT_REVOCATION_MODE"] = "offline"
    result = subprocess.run(
        [str(dotnet), "nuget", "verify", "--all", *(str(path) for path in signed_official)],
        check=False, capture_output=True, text=True, env=environment,
    )
    if result.returncode:
        print(result.stdout, file=sys.stderr, end="")
        print(result.stderr, file=sys.stderr, end="")
        fail("one or more exact official archives failed NuGet author/repository signature verification")
    print(f"workload_packages_verified={len(selected_records(lock, workloads))}")
    print(f"official_nuget_signatures_verified={len(signed_official)}")
    print(f"expected_unsigned_local_archives={len(unsigned_local)}")


if __name__ == "__main__":
    main()
