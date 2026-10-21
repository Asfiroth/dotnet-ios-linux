#!/usr/bin/env python3
"""Conflict-safe, receipt-backed installation of a verified .NET SDK archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import secrets
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import fsync_directory, fsync_file, fsync_path_state, mkdir_durable


OWNER = "dotnet-ios-linux"
RETAINABLE_ROOT_FILES = {"dotnet", "dnx", "LICENSE.txt", "ThirdPartyNotices.txt"}


def fail(message: str) -> "None":
    raise SystemExit(f".NET SDK installation failed: {message}")


def validate_sdk_version(value: str) -> None:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", value) is None:
        fail(f"invalid SDK version: {value!r}")


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def safe_name(raw: str) -> PurePosixPath | None:
    name = raw.removeprefix("./")
    if name in {"", "."}:
        return None
    value = PurePosixPath(name)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"unsafe SDK archive member: {raw!r}")
    return value


def safe_receipt_path(raw: object, label: str) -> Path:
    if not isinstance(raw, str):
        fail(f"ownership receipt has a non-string {label} path")
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"ownership receipt has an unsafe {label} path: {raw!r}")
    return Path(*value.parts)


def receipt_items(value: object, label: str) -> list[dict]:
    if not isinstance(value, list):
        fail(f"ownership receipt has an invalid {label} list")
    result: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            fail(f"ownership receipt has an invalid {label} record")
        relative = safe_receipt_path(item.get("path"), label).as_posix()
        if relative in seen:
            fail(f"ownership receipt has a duplicate {label} path: {relative}")
        seen.add(relative)
        result.append(item | {"path": relative})
    return result


def ensure_no_symlink_ancestors(root: Path, relative: Path, *, include_leaf: bool = False) -> None:
    current = root
    parts = relative.parts if include_leaf else relative.parts[:-1]
    for part in parts:
        current /= part
        if current.is_symlink():
            fail(f"SDK path has a symlink ancestor: {current}")
        if os.path.lexists(current) and not current.is_dir():
            fail(f"SDK path has a non-directory ancestor: {current}")


def extract(archive: Path, output: Path) -> None:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        names = [safe_name(member.name) for member in members]
        populated_names = [name for name in names if name is not None]
        if len(populated_names) != len(set(populated_names)):
            fail("SDK archive contains duplicate members")
        for member, relative in zip(members, names, strict=True):
            if relative is None:
                if not member.isdir():
                    fail("SDK archive root is not a directory")
                continue
            target = output.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=False)
                target.chmod(stat.S_IMODE(member.mode))
                continue
            if not member.isfile():
                fail(f"unsupported SDK archive member type: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = source.extractfile(member)
            if reader is None:
                fail(f"cannot read SDK archive member: {member.name}")
            with target.open("xb") as destination:
                shutil.copyfileobj(reader, destination, 1024 * 1024)
            target.chmod(stat.S_IMODE(member.mode))


def source_inventory(stage: Path) -> tuple[list[dict], list[str]]:
    files: list[dict] = []
    directories: list[str] = []
    for path in sorted(stage.rglob("*")):
        relative = path.relative_to(stage).as_posix()
        if path.is_symlink():
            fail(f"unexpected symlink in extracted SDK: {relative}")
        if path.is_dir():
            directories.append(relative)
        elif path.is_file():
            files.append(
                {
                    "path": relative,
                    "sha256": digest(path),
                    "mode": stat.S_IMODE(path.stat().st_mode),
                }
            )
        else:
            fail(f"unexpected extracted SDK entry: {relative}")
    return files, directories


def load_receipt(path: Path, root: Path, sdk_version: str) -> dict:
    validate_sdk_version(sdk_version)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read ownership receipt {path}: {error}")
    if value.get("owner") != OWNER or value.get("sdkVersion") != sdk_version:
        fail(f"ownership receipt has different provenance: {path}")
    if value.get("dotnetRoot") != str(root):
        fail(f"ownership receipt belongs to a different .NET root: {path}")
    receipt_items(value.get("createdFiles", []), "created file")
    receipt_items(value.get("temporaryFiles", []), "temporary file")
    receipt_items(value.get("retainedFiles", []), "retained file")
    directories = value.get("createdDirectories", [])
    if not isinstance(directories, list):
        fail("ownership receipt has an invalid created directory list")
    seen_directories: set[str] = set()
    for raw in directories:
        relative = safe_receipt_path(raw, "created directory").as_posix()
        if relative in seen_directories:
            fail(f"ownership receipt has a duplicate created directory path: {relative}")
        seen_directories.add(relative)
    return value


def rollback(root: Path, receipt_path: Path, sdk_version: str, *, remove_receipt: bool) -> None:
    receipt = load_receipt(receipt_path, root, sdk_version)
    conflicts: list[str] = []
    created_files = receipt_items(receipt.get("createdFiles", []), "created file")
    for item in created_files:
        relative = safe_receipt_path(item["path"], "created file")
        ensure_no_symlink_ancestors(root, relative)
        path = root / relative
        if not os.path.lexists(path):
            continue
        if (
            path.is_symlink()
            or not path.is_file()
            or digest(path) != item["sha256"]
            or stat.S_IMODE(path.stat().st_mode) != item["mode"]
        ):
            conflicts.append(item["path"])
    temporary_files = receipt_items(receipt.get("temporaryFiles", []), "temporary file")
    for item in temporary_files:
        relative = safe_receipt_path(item["path"], "temporary file")
        ensure_no_symlink_ancestors(root, relative)
        path = root / relative
        if os.path.lexists(path) and (path.is_symlink() or not path.is_file()):
            conflicts.append(item["path"])
    if conflicts:
        fail("owned SDK files changed; refusing to remove: " + ", ".join(conflicts))

    for item in reversed(created_files):
        path = root / safe_receipt_path(item["path"], "created file")
        if os.path.lexists(path):
            path.unlink()
            fsync_directory(path.parent)
        else:
            fsync_path_state(path)
    # These randomized paths are reserved in the installing receipt before any
    # bytes are written. A partial file is therefore safe to remove when an
    # interrupted installation resumes.
    for item in temporary_files:
        path = root / safe_receipt_path(item["path"], "temporary file")
        if os.path.lexists(path):
            path.unlink()
            fsync_directory(path.parent)
        else:
            fsync_path_state(path)
    created_directories = [
        safe_receipt_path(raw, "created directory")
        for raw in receipt.get("createdDirectories", [])
    ]
    for relative in sorted(created_directories, key=lambda x: (len(x.parts), x.as_posix()), reverse=True):
        ensure_no_symlink_ancestors(root, relative)
        path = root / relative
        try:
            path.rmdir()
            fsync_directory(path.parent)
        except FileNotFoundError:
            fsync_path_state(path)
        except OSError:
            # Workload files or another SDK may legitimately keep a merged
            # directory non-empty; the installer never owns those contents.
            pass
    if remove_receipt:
        if os.path.lexists(receipt_path):
            receipt_path.unlink()
            fsync_directory(receipt_path.parent)
        else:
            fsync_path_state(receipt_path)


def verify(root: Path, receipt_path: Path, sdk_version: str) -> None:
    receipt = load_receipt(receipt_path, root, sdk_version)
    if receipt.get("status") != "committed":
        fail(f"SDK ownership receipt is incomplete: {receipt_path}")
    for item in receipt_items(receipt.get("createdFiles", []), "created file"):
        relative = safe_receipt_path(item["path"], "created file")
        ensure_no_symlink_ancestors(root, relative)
        path = root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or digest(path) != item["sha256"]
            or stat.S_IMODE(path.stat().st_mode) != item["mode"]
        ):
            fail(f"owned SDK file failed integrity validation: {path}")
    if any(
        os.path.lexists(root / safe_receipt_path(item["path"], "temporary file"))
        for item in receipt_items(receipt.get("temporaryFiles", []), "temporary file")
    ):
        fail("SDK installation left temporary files in the canonical root")
    sdk = root / "sdk" / sdk_version
    if not sdk.is_dir() or not (root / "dotnet").is_file():
        fail(f"SDK {sdk_version} is incomplete under {root}")


def release(root: Path, receipt_path: Path, sdk_version: str) -> None:
    """Relinquish intact SDK files that retained shared workload state may need."""
    verify(root, receipt_path, sdk_version)
    receipt_path.unlink()
    fsync_directory(receipt_path.parent)
    print("dotnet_sdk=released")


def install(
    archive: Path,
    root: Path,
    receipt_path: Path,
    sdk_version: str,
    sdk_sha512: str,
) -> None:
    if root.is_symlink():
        fail(f"canonical SDK root must not be a symlink: {root}")
    if receipt_path.exists():
        existing = load_receipt(receipt_path, root, sdk_version)
        if existing.get("status") == "committed":
            verify(root, receipt_path, sdk_version)
            print(f"dotnet_root={root}")
            print("reused=true")
            return
        rollback(root, receipt_path, sdk_version, remove_receipt=True)

    if not archive.is_file() or digest(archive, "sha512") != sdk_sha512:
        fail(f"SDK archive SHA-512 mismatch: {archive}")
    mkdir_durable(root.parent)
    mkdir_durable(receipt_path.parent, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".dotnet-sdk-stage-", dir=receipt_path.parent) as temporary:
        stage = Path(temporary)
        extract(archive, stage)
        files, directories = source_inventory(stage)
        created_directories: list[str] = []
        created_files: list[dict] = []
        temporary_files: list[dict] = []
        retained: list[dict] = []

        if root.exists() and not root.is_dir():
            fail(f"canonical SDK root is not a directory: {root}")
        for relative in directories:
            relative_path = safe_receipt_path(relative, "SDK directory")
            ensure_no_symlink_ancestors(root, relative_path, include_leaf=True)
            target = root / relative_path
            if os.path.lexists(target) and (target.is_symlink() or not target.is_dir()):
                fail(f"SDK directory conflicts with an existing path: {target}")
            if not target.exists():
                created_directories.append(relative)
        for item in files:
            relative_path = safe_receipt_path(item["path"], "SDK file")
            ensure_no_symlink_ancestors(root, relative_path)
            target = root / relative_path
            if not os.path.lexists(target):
                created_files.append(item)
                continue
            if target.is_symlink() or not target.is_file():
                fail(f"SDK file conflicts with an existing path: {target}")
            actual = digest(target)
            if actual == item["sha256"]:
                retained.append({"path": item["path"], "sha256": actual, "reason": "identical"})
                continue
            if item["path"] in RETAINABLE_ROOT_FILES:
                retained.append({"path": item["path"], "sha256": actual, "reason": "existing-root"})
                continue
            fail(f"SDK archive would overwrite an unowned file with different content: {target}")

        token = secrets.token_hex(12)
        for index, item in enumerate(created_files):
            relative = Path(item["path"])
            temporary_files.append(
                {
                    "path": (relative.parent / f".{relative.name}.dotnet-ios-linux-{token}-{index}").as_posix(),
                    "sha256": item["sha256"],
                }
            )
        for item in temporary_files:
            if os.path.lexists(root / item["path"]):
                fail(f"SDK temporary path already exists: {root / item['path']}")

        receipt = {
            "schemaVersion": 1,
            "owner": OWNER,
            "status": "installing",
            "sdkVersion": sdk_version,
            "archiveSha512": sdk_sha512,
            "dotnetRoot": str(root),
            "createdFiles": created_files,
            "createdDirectories": created_directories,
            "retainedFiles": retained,
            "temporaryFiles": temporary_files,
        }
        write_json(receipt_path, receipt)
        try:
            mkdir_durable(root)
            for relative in sorted(created_directories, key=lambda x: (x.count("/"), x)):
                mkdir_durable(root / relative)
            for item, temporary_item in zip(created_files, temporary_files, strict=True):
                source = stage / item["path"]
                target = root / item["path"]
                temporary_file = root / temporary_item["path"]
                with source.open("rb") as input_stream, temporary_file.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                temporary_file.chmod(item["mode"])
                fsync_file(temporary_file)
                if digest(temporary_file) != item["sha256"]:
                    fail(f"copied SDK file failed integrity validation: {temporary_file}")
                # Publish without replacing a path that appeared after preflight.
                os.link(temporary_file, target)
                fsync_directory(target.parent)
                temporary_file.unlink()
                fsync_directory(temporary_file.parent)
        except BaseException:
            rollback(root, receipt_path, sdk_version, remove_receipt=True)
            raise
        for item in created_files:
            fsync_file(root / safe_receipt_path(item["path"], "created file"))
        for relative in sorted(
            created_directories,
            key=lambda value: (value.count("/"), value),
            reverse=True,
        ):
            path = root / safe_receipt_path(relative, "created directory")
            if path.is_dir() and not path.is_symlink():
                fsync_directory(path)
        fsync_directory(root)
        fsync_directory(root.parent)
        receipt["status"] = "committed"
        write_json(receipt_path, receipt)

    verify(root, receipt_path, sdk_version)
    print(f"dotnet_root={root}")
    print("reused=false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "verify", "uninstall", "release"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sdk-version", required=True)
    parser.add_argument("--archive-sha512")
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    receipt = args.receipt.expanduser().absolute()
    if args.command == "install":
        if args.archive is None:
            fail("--archive is required for install")
        if args.archive_sha512 is None:
            fail("--archive-sha512 is required for install")
        if not re.fullmatch(r"[0-9a-f]{128}", args.archive_sha512):
            fail("--archive-sha512 must contain 128 lowercase hex characters")
        install(
            args.archive.expanduser().resolve(),
            root,
            receipt,
            args.sdk_version,
            args.archive_sha512,
        )
    elif args.command == "verify":
        verify(root, receipt, args.sdk_version)
        print("dotnet_sdk=ok")
    elif args.command == "release":
        release(root, receipt, args.sdk_version)
    else:
        rollback(root, receipt, args.sdk_version, remove_receipt=True)
        print("dotnet_sdk=removed-owned-files")


if __name__ == "__main__":
    main()
