#!/usr/bin/env python3
"""Conflict-safe, receipt-backed installation of the pinned .NET SDK archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import secrets
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


SDK_VERSION = "10.0.402"
SDK_SHA512 = "1904a98dda2db34ba78ba3c4fb1b224455329af91c341d83547843c1839e112b04ae699ee92fa2449a8f62249196a938723078c0c60b9f65c88539d5687729c2"
OWNER = "dotnet-ios-linux"
RETAINABLE_ROOT_FILES = {"dotnet", "dnx", "LICENSE.txt", "ThirdPartyNotices.txt"}


def fail(message: str) -> "None":
    raise SystemExit(f".NET SDK installation failed: {message}")


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_name(raw: str) -> PurePosixPath | None:
    name = raw.removeprefix("./")
    if name in {"", "."}:
        return None
    value = PurePosixPath(name)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"unsafe SDK archive member: {raw!r}")
    return value


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


def load_receipt(path: Path, root: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read ownership receipt {path}: {error}")
    if value.get("owner") != OWNER or value.get("sdkVersion") != SDK_VERSION:
        fail(f"ownership receipt has different provenance: {path}")
    if value.get("dotnetRoot") != str(root):
        fail(f"ownership receipt belongs to a different .NET root: {path}")
    return value


def rollback(root: Path, receipt_path: Path, *, remove_receipt: bool) -> None:
    receipt = load_receipt(receipt_path, root)
    conflicts: list[str] = []
    created_files = list(receipt.get("createdFiles", []))
    for item in created_files:
        path = root / item["path"]
        if not os.path.lexists(path):
            continue
        if (
            path.is_symlink()
            or not path.is_file()
            or digest(path) != item["sha256"]
            or stat.S_IMODE(path.stat().st_mode) != item["mode"]
        ):
            conflicts.append(item["path"])
    temporary_files = list(receipt.get("temporaryFiles", []))
    for item in temporary_files:
        path = root / item["path"]
        if os.path.lexists(path) and (path.is_symlink() or not path.is_file()):
            conflicts.append(item["path"])
    if conflicts:
        fail("owned SDK files changed; refusing to remove: " + ", ".join(conflicts))

    for item in reversed(created_files):
        (root / item["path"]).unlink(missing_ok=True)
    # These randomized paths are reserved in the installing receipt before any
    # bytes are written. A partial file is therefore safe to remove when an
    # interrupted installation resumes.
    for item in temporary_files:
        (root / item["path"]).unlink(missing_ok=True)
    for relative in sorted(receipt.get("createdDirectories", []), key=lambda x: (x.count("/"), x), reverse=True):
        path = root / relative
        try:
            path.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            # Workload files or another SDK may legitimately keep a merged
            # directory non-empty; the installer never owns those contents.
            pass
    if remove_receipt:
        receipt_path.unlink(missing_ok=True)


def verify(root: Path, receipt_path: Path) -> None:
    receipt = load_receipt(receipt_path, root)
    if receipt.get("status") != "committed":
        fail(f"SDK ownership receipt is incomplete: {receipt_path}")
    for item in receipt.get("createdFiles", []):
        path = root / item["path"]
        if (
            not path.is_file()
            or path.is_symlink()
            or digest(path) != item["sha256"]
            or stat.S_IMODE(path.stat().st_mode) != item["mode"]
        ):
            fail(f"owned SDK file failed integrity validation: {path}")
    if any(os.path.lexists(root / item["path"]) for item in receipt.get("temporaryFiles", [])):
        fail("SDK installation left temporary files in the canonical root")
    sdk = root / "sdk" / SDK_VERSION
    if not sdk.is_dir() or not (root / "dotnet").is_file():
        fail(f"SDK {SDK_VERSION} is incomplete under {root}")


def install(archive: Path, root: Path, receipt_path: Path) -> None:
    if root.is_symlink():
        fail(f"canonical SDK root must not be a symlink: {root}")
    if receipt_path.exists():
        existing = load_receipt(receipt_path, root)
        if existing.get("status") == "committed":
            verify(root, receipt_path)
            print(f"dotnet_root={root}")
            print("reused=true")
            return
        rollback(root, receipt_path, remove_receipt=True)

    if not archive.is_file() or digest(archive, "sha512") != SDK_SHA512:
        fail(f"SDK archive SHA-512 mismatch: {archive}")
    root.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
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
            target = root / relative
            if os.path.lexists(target) and (target.is_symlink() or not target.is_dir()):
                fail(f"SDK directory conflicts with an existing path: {target}")
            if not target.exists():
                created_directories.append(relative)
        for item in files:
            target = root / item["path"]
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
            "sdkVersion": SDK_VERSION,
            "archiveSha512": SDK_SHA512,
            "dotnetRoot": str(root),
            "createdFiles": created_files,
            "createdDirectories": created_directories,
            "retainedFiles": retained,
            "temporaryFiles": temporary_files,
        }
        write_json(receipt_path, receipt)
        try:
            root.mkdir(parents=True, exist_ok=True)
            for relative in sorted(created_directories, key=lambda x: (x.count("/"), x)):
                (root / relative).mkdir(exist_ok=False)
            for item, temporary_item in zip(created_files, temporary_files, strict=True):
                source = stage / item["path"]
                target = root / item["path"]
                temporary_file = root / temporary_item["path"]
                with source.open("rb") as input_stream, temporary_file.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                temporary_file.chmod(item["mode"])
                if digest(temporary_file) != item["sha256"]:
                    fail(f"copied SDK file failed integrity validation: {temporary_file}")
                # Publish without replacing a path that appeared after preflight.
                os.link(temporary_file, target)
                temporary_file.unlink()
        except BaseException:
            rollback(root, receipt_path, remove_receipt=True)
            raise
        receipt["status"] = "committed"
        write_json(receipt_path, receipt)

    verify(root, receipt_path)
    print(f"dotnet_root={root}")
    print("reused=false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "verify", "uninstall"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    receipt = args.receipt.expanduser().absolute()
    if args.command == "install":
        if args.archive is None:
            fail("--archive is required for install")
        install(args.archive.expanduser().resolve(), root, receipt)
    elif args.command == "verify":
        verify(root, receipt)
        print("dotnet_sdk=ok")
    else:
        rollback(root, receipt, remove_receipt=True)
        print("dotnet_sdk=removed-owned-files")


if __name__ == "__main__":
    main()
