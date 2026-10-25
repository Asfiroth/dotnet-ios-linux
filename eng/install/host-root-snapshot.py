#!/usr/bin/env python3
"""Inventory and make a verified, external rollback snapshot of a .NET root."""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NoReturn


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "eng" / "common"))
from atomic import (  # noqa: E402
    durable_remove,
    exchange_directories,
    fsync_directory,
    fsync_tree,
    publish_directory_noreplace,
)


OWNER = "dotnet-ios-linux"
SCHEMA_VERSION = 1
ARCHIVE_NAME = "payload.tar"
RECEIPT_NAME = "snapshot.json"
ARCHIVE_ROOT = "tree"
WORKLOAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
IGNORABLE_XATTR_ERRORS = {errno.ENOTSUP, errno.EOPNOTSUPP}


def fail(message: str) -> NoReturn:
    raise SystemExit(f"host-root snapshot failed: {message}")


def path_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def root_path(raw: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        fail(f"host .NET root must be absolute: {raw}")
    if candidate.is_symlink() or not candidate.is_dir():
        fail(f"host .NET root must be a real directory: {candidate}")
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve host .NET root: {error}")


def output_path(raw: Path, root: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        fail(f"snapshot output must be absolute: {raw}")
    if candidate.name in {"", ".", ".."}:
        fail(f"snapshot output has an invalid final path component: {raw}")
    parent = candidate.parent
    if parent.is_symlink() or not parent.is_dir():
        fail(f"snapshot output parent must be an existing real directory: {parent}")
    try:
        parent = parent.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve snapshot output parent: {error}")
    candidate = parent / candidate.name
    if os.path.lexists(candidate):
        fail(f"snapshot output already exists: {candidate}")
    try:
        candidate.relative_to(root)
    except ValueError:
        pass
    else:
        fail("snapshot output must be outside the host .NET root")
    try:
        root.relative_to(candidate)
    except ValueError:
        pass
    else:
        fail("snapshot output must not be an ancestor of the host .NET root")
    return candidate


def existing_snapshot_path(raw: Path, root: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute():
        fail(f"snapshot path must be absolute: {raw}")
    if candidate.is_symlink() or not candidate.is_dir():
        fail(f"snapshot must be a real directory: {candidate}")
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve rollback snapshot: {error}")
    try:
        candidate.relative_to(root)
    except ValueError:
        pass
    else:
        fail("rollback snapshot must be outside the host .NET root")
    try:
        root.relative_to(candidate)
    except ValueError:
        pass
    else:
        fail("rollback snapshot must not be an ancestor of the host .NET root")
    return candidate


def safe_name(name: str, parent: str) -> str:
    if name in {"", ".", ".."} or "/" in name or "\x00" in name:
        fail(f"host .NET root contains an unsafe entry name below {parent!r}: {name!r}")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        fail(f"host .NET root contains a non-UTF-8 entry name below {parent!r}")
    return name


def xattrs(path: Path) -> dict[str, str]:
    if not hasattr(os, "listxattr"):
        return {}
    try:
        names = os.listxattr(path, follow_symlinks=False)
    except OSError as error:
        if error.errno in IGNORABLE_XATTR_ERRORS:
            return {}
        fail(f"cannot enumerate extended attributes for {path}: {error}")
    result: dict[str, str] = {}
    for name in sorted(names):
        try:
            raw = os.getxattr(path, name, follow_symlinks=False)
        except OSError as error:
            fail(f"cannot read extended attribute {name!r} for {path}: {error}")
        result[name] = base64.b64encode(raw).decode("ascii")
    return result


def entry_for(path: Path, relative: str) -> dict[str, Any]:
    try:
        details = path.lstat()
    except OSError as error:
        fail(f"cannot stat host .NET path {path}: {error}")
    mode = stat.S_IMODE(details.st_mode)
    common: dict[str, Any] = {
        "path": relative,
        "mode": mode,
        "uid": details.st_uid,
        "gid": details.st_gid,
        "mtimeNs": details.st_mtime_ns,
        "xattrs": xattrs(path),
    }
    if stat.S_ISREG(details.st_mode):
        common.update({"kind": "file", "size": details.st_size, "sha256": path_hash(path)})
    elif stat.S_ISDIR(details.st_mode):
        common["kind"] = "directory"
    elif stat.S_ISLNK(details.st_mode):
        try:
            target = os.readlink(path)
        except OSError as error:
            fail(f"cannot read symlink {path}: {error}")
        common.update({"kind": "symlink", "target": target})
    else:
        fail(f"host .NET root contains an unsupported filesystem entry: {path}")
    return common


def inventory_tree(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root_metadata = entry_for(root, ".")
    entries: list[dict[str, Any]] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            fail(f"cannot enumerate host .NET directory {directory}: {error}")
        for child in children:
            name = safe_name(child.name, prefix or ".")
            relative = f"{prefix}/{name}" if prefix else name
            path = directory / name
            record = entry_for(path, relative)
            entries.append(record)
            if record["kind"] == "directory":
                visit(path, relative)

    visit(root, "")
    return root_metadata, entries


def workload_inventory(
    root: Path,
    entries: list[dict[str, Any]],
    required: list[str],
    *,
    legacy_requested_workloads: bool = False,
) -> dict[str, Any]:
    # Required-workload fields existed in snapshot schema v1. They are replayed
    # only to verify an existing v1 snapshot; no new inventory may manufacture
    # absent workload markers from requested IDs.
    requested: set[str] = set()
    for workload in required:
        if WORKLOAD_ID.fullmatch(workload) is None:
            fail(f"required workload ID is invalid: {workload!r}")
        requested.add(workload)
    if requested and not legacy_requested_workloads:
        fail("snapshot inventory no longer accepts requested workload IDs")
    registrations: list[dict[str, str]] = []
    for entry in entries:
        parts = PurePosixPath(entry["path"]).parts
        if (
            len(parts) < 5
            or parts[0:2] != ("metadata", "workloads")
            or parts[-2] != "InstalledWorkloads"
        ):
            continue
        if entry["kind"] != "file":
            fail(f"workload registration is not a regular file: {entry['path']}")
        workload = parts[-1]
        if WORKLOAD_ID.fullmatch(workload) is None:
            fail(f"host workload registration has an invalid workload ID: {entry['path']}")
        registrations.append(
            {
                "workloadId": workload,
                "markerPath": entry["path"],
                "metadataScope": "/".join(parts[2:-2]),
            }
        )
    registrations.sort(key=lambda item: (item["workloadId"].casefold(), item["markerPath"]))
    baseline = sorted({item["workloadId"] for item in registrations}, key=str.casefold)
    return {
        "dotnetRoot": str(root),
        "baselineWorkloads": baseline,
        "requiredWorkloads": sorted(requested, key=str.casefold),
        "desiredWorkloads": sorted(set(baseline) | requested, key=str.casefold),
        "workloadRegistrations": registrations,
    }


def tree_hash(root_metadata: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in [root_metadata, *entries]:
        digest.update(json_bytes(entry))
        digest.update(b"\n")
    return digest.hexdigest()


def snapshot_inventory(
    root: Path, required: list[str], *, legacy_requested_workloads: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], str]:
    root_metadata, entries = inventory_tree(root)
    workloads = workload_inventory(
        root, entries, required, legacy_requested_workloads=legacy_requested_workloads
    )
    return root_metadata, entries, workloads, tree_hash(root_metadata, entries)


def tar_info(name: str, entry: dict[str, Any]) -> tarfile.TarInfo:
    result = tarfile.TarInfo(name)
    result.mode = entry["mode"]
    result.uid = entry["uid"]
    result.gid = entry["gid"]
    result.mtime = entry["mtimeNs"] // 1_000_000_000
    kind = entry["kind"]
    if kind == "directory":
        result.type = tarfile.DIRTYPE
    elif kind == "symlink":
        result.type = tarfile.SYMTYPE
        result.linkname = entry["target"]
    elif kind == "file":
        result.type = tarfile.REGTYPE
        result.size = entry["size"]
    else:
        fail(f"cannot archive unknown entry kind: {kind!r}")
    return result


def same_entry(current: dict[str, Any], expected: dict[str, Any]) -> bool:
    return current == expected


def add_file(archive: tarfile.TarFile, root: Path, entry: dict[str, Any]) -> None:
    path = root / entry["path"]
    before = entry_for(path, entry["path"])
    if not same_entry(before, entry):
        fail(f"host .NET root changed while creating snapshot: {entry['path']}")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(f"cannot safely read snapshot file {path}: {error}")
    try:
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                fail(f"snapshot file changed type while opening: {path}")
            archive.addfile(tar_info(f"{ARCHIVE_ROOT}/{entry['path']}", entry), stream)
    except tarfile.TarError as error:
        fail(f"cannot archive host .NET file {path}: {error}")
    after = entry_for(path, entry["path"])
    if not same_entry(after, entry):
        fail(f"host .NET root changed while creating snapshot: {entry['path']}")


def create_archive(path: Path, root: Path, root_metadata: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    try:
        with tarfile.open(path, "w", format=tarfile.PAX_FORMAT, dereference=False) as archive:
            archive.addfile(tar_info(ARCHIVE_ROOT, root_metadata))
            for entry in entries:
                if entry["kind"] == "file":
                    add_file(archive, root, entry)
                else:
                    archive.addfile(tar_info(f"{ARCHIVE_ROOT}/{entry['path']}", entry))
    except (OSError, tarfile.TarError) as error:
        fail(f"cannot create rollback archive: {error}")


def expected_member_names(entries: list[dict[str, Any]]) -> set[str]:
    return {ARCHIVE_ROOT} | {f"{ARCHIVE_ROOT}/{entry['path']}" for entry in entries}


def verify_archive(path: Path, root_metadata: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    expected = {entry["path"]: entry for entry in entries}
    expected_names = expected_member_names(entries)
    try:
        with tarfile.open(path, "r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or set(names) != expected_names:
                fail("rollback archive has missing, duplicate, or unexpected members")
            for member in members:
                if member.name == ARCHIVE_ROOT:
                    entry = root_metadata
                else:
                    prefix = f"{ARCHIVE_ROOT}/"
                    if not member.name.startswith(prefix):
                        fail("rollback archive contains an unsafe member path")
                    relative = member.name[len(prefix) :]
                    pure = PurePosixPath(relative)
                    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
                        fail("rollback archive contains an unsafe member path")
                    entry = expected.get(relative)
                    if entry is None:
                        fail("rollback archive contains an unexpected member")
                if member.mode != entry["mode"] or member.uid != entry["uid"] or member.gid != entry["gid"]:
                    fail(f"rollback archive metadata differs for {entry['path']}")
                if int(member.mtime) != entry["mtimeNs"] // 1_000_000_000:
                    fail(f"rollback archive timestamp differs for {entry['path']}")
                kind = entry["kind"]
                if kind == "directory":
                    if not member.isdir():
                        fail(f"rollback archive type differs for {entry['path']}")
                elif kind == "symlink":
                    if not member.issym() or member.linkname != entry["target"]:
                        fail(f"rollback archive link differs for {entry['path']}")
                elif kind == "file":
                    if not member.isfile() or member.size != entry["size"]:
                        fail(f"rollback archive type differs for {entry['path']}")
                    stream = archive.extractfile(member)
                    if stream is None:
                        fail(f"cannot read rollback archive member {entry['path']}")
                    digest = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                    if digest.hexdigest() != entry["sha256"]:
                        fail(f"rollback archive content differs for {entry['path']}")
    except (OSError, tarfile.TarError) as error:
        fail(f"cannot verify rollback archive: {error}")


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def snapshot(root: Path, output: Path, required: list[str]) -> None:
    target = output_path(output, root)
    root_metadata, entries, workloads, before_hash = snapshot_inventory(root, required)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.snapshot-", dir=target.parent))
    try:
        archive = stage / ARCHIVE_NAME
        create_archive(archive, root, root_metadata, entries)
        verify_archive(archive, root_metadata, entries)
        after_metadata, after_entries, after_workloads, after_hash = snapshot_inventory(root, required)
        if (
            before_hash != after_hash
            or root_metadata != after_metadata
            or entries != after_entries
            or workloads != after_workloads
        ):
            fail("host .NET root changed while creating the rollback snapshot")
        receipt = {
            "schemaVersion": SCHEMA_VERSION,
            "owner": OWNER,
            "dotnetRoot": str(root),
            "archive": ARCHIVE_NAME,
            "archiveSha256": path_hash(archive),
            "treeSha256": before_hash,
            "rootMetadata": root_metadata,
            "entries": entries,
            "workloadInventory": workloads,
        }
        write_receipt(stage / RECEIPT_NAME, receipt)
        fsync_tree(stage)
        try:
            publish_directory_noreplace(stage, target)
        except FileExistsError:
            fail(f"snapshot output appeared during publication: {target}")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"rollback_snapshot={target}")
    print(f"rollback_snapshot_sha256={receipt['archiveSha256']}")
    print(f"baseline_workloads={','.join(workloads['baselineWorkloads'])}")
    print(f"desired_workloads={','.join(workloads['desiredWorkloads'])}")


def validated_entry(value: object, *, root: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("snapshot contains an invalid tree entry")
    path = value.get("path")
    if root and path != ".":
        fail("snapshot contains an invalid tree entry path")
    if not root and not isinstance(path, str):
        fail("snapshot contains an invalid tree entry path")
    if not root:
        pure = PurePosixPath(path)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            fail("snapshot contains an unsafe tree entry path")
    kind = value.get("kind")
    if kind not in {"file", "directory", "symlink"}:
        fail("snapshot contains an invalid tree entry kind")
    for key in ("mode", "uid", "gid", "mtimeNs"):
        if not isinstance(value.get(key), int) or value[key] < 0:
            fail("snapshot contains invalid tree entry metadata")
    attributes = value.get("xattrs")
    if not isinstance(attributes, dict) or not all(
        isinstance(name, str) and isinstance(encoded, str) for name, encoded in attributes.items()
    ):
        fail("snapshot contains invalid extended attributes")
    if kind == "file" and (
        not isinstance(value.get("size"), int)
        or value["size"] < 0
        or not isinstance(value.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
    ):
        fail("snapshot contains invalid file metadata")
    if kind == "symlink" and not isinstance(value.get("target"), str):
        fail("snapshot contains invalid symlink metadata")
    return value


def read_receipt(snapshot: Path, root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    try:
        value = json.loads((snapshot / RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read rollback snapshot receipt: {error}")
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("owner") != OWNER
        or value.get("dotnetRoot") != str(root)
        or value.get("archive") != ARCHIVE_NAME
    ):
        fail("rollback snapshot has different ownership or root identity")
    expected_digest = value.get("archiveSha256")
    archive = snapshot / ARCHIVE_NAME
    if (
        archive.is_symlink()
        or not archive.is_file()
        or not isinstance(expected_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
        or path_hash(archive) != expected_digest
    ):
        fail("rollback snapshot archive integrity validation failed")
    root_metadata = validated_entry(value.get("rootMetadata"), root=True)
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        fail("rollback snapshot has no tree inventory")
    entries = [validated_entry(item, root=False) for item in raw_entries]
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        fail("rollback snapshot has duplicate tree entries")
    expected_tree_hash = value.get("treeSha256")
    if not isinstance(expected_tree_hash, str) or tree_hash(root_metadata, entries) != expected_tree_hash:
        fail("rollback snapshot tree inventory integrity validation failed")
    return value, root_metadata, entries


def required_workloads_from_receipt(value: dict[str, Any], root: Path) -> list[str]:
    workload_state = value.get("workloadInventory")
    if not isinstance(workload_state, dict) or workload_state.get("dotnetRoot") != str(root):
        fail("rollback snapshot workload inventory has different root identity")
    required = workload_state.get("requiredWorkloads")
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) or WORKLOAD_ID.fullmatch(item) is None for item in required)
        or required != sorted(set(required), key=str.casefold)
    ):
        fail("rollback snapshot workload inventory has invalid required workloads")
    return required


def verify(root: Path, snapshot: Path) -> None:
    snapshot = existing_snapshot_path(snapshot, root)
    receipt, root_metadata, entries = read_receipt(snapshot, root)
    verify_archive(snapshot / ARCHIVE_NAME, root_metadata, entries)
    required = required_workloads_from_receipt(receipt, root)
    current_metadata, current_entries, current_workloads, current_hash = snapshot_inventory(
        root, required, legacy_requested_workloads=True
    )
    if (
        current_hash != receipt["treeSha256"]
        or current_metadata != root_metadata
        or current_entries != entries
        or current_workloads != receipt["workloadInventory"]
    ):
        fail("host .NET root no longer matches the rollback snapshot")
    print("rollback_snapshot=ok")


def prepare_stage_parent(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        fail(f"host .NET root parent must be a real directory: {parent}")


def member_map(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members = archive.getmembers()
    result = {member.name: member for member in members}
    if len(result) != len(members):
        fail("rollback archive has duplicate members")
    return result


def apply_xattrs(path: Path, expected: dict[str, str]) -> None:
    if not hasattr(os, "listxattr"):
        if expected:
            fail(f"extended attributes cannot be restored for {path}")
        return
    try:
        current = set(os.listxattr(path, follow_symlinks=False))
    except OSError as error:
        if error.errno in IGNORABLE_XATTR_ERRORS and not expected:
            return
        fail(f"cannot enumerate extended attributes while restoring {path}: {error}")
    for name in current - set(expected):
        try:
            os.removexattr(path, name, follow_symlinks=False)
        except OSError as error:
            fail(f"cannot remove extended attribute {name!r} from {path}: {error}")
    for name, encoded in expected.items():
        try:
            value = base64.b64decode(encoded, validate=True)
            os.setxattr(path, name, value, follow_symlinks=False)
        except (OSError, ValueError) as error:
            fail(f"cannot restore extended attribute {name!r} for {path}: {error}")


def apply_metadata(path: Path, entry: dict[str, Any]) -> None:
    details = path.lstat()
    if details.st_uid != entry["uid"] or details.st_gid != entry["gid"]:
        try:
            os.chown(path, entry["uid"], entry["gid"], follow_symlinks=False)
        except OSError as error:
            fail(f"cannot restore ownership for {path}: {error}")
    if entry["kind"] != "symlink":
        try:
            os.chmod(path, entry["mode"], follow_symlinks=False)
        except OSError as error:
            fail(f"cannot restore mode for {path}: {error}")
    try:
        os.utime(path, ns=(entry["mtimeNs"], entry["mtimeNs"]), follow_symlinks=False)
    except OSError as error:
        fail(f"cannot restore timestamp for {path}: {error}")
    apply_xattrs(path, entry["xattrs"])


def extraction_target(stage: Path, relative: str) -> Path:
    result = stage.joinpath(*PurePosixPath(relative).parts)
    if result.parent != stage and (result.parent.is_symlink() or not result.parent.is_dir()):
        fail(f"rollback extraction parent is unsafe: {relative}")
    return result


def write_archive_file(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path, entry: dict[str, Any]) -> None:
    stream = archive.extractfile(member)
    if stream is None:
        fail(f"cannot read rollback archive member {entry['path']}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
        digest = hashlib.sha256()
        written = 0
        with os.fdopen(descriptor, "wb") as output:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                output.write(block)
                digest.update(block)
                written += len(block)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        target.unlink(missing_ok=True)
        fail(f"cannot restore file {entry['path']}: {error}")
    if written != entry["size"] or digest.hexdigest() != entry["sha256"]:
        target.unlink(missing_ok=True)
        fail(f"rollback archive content differs while restoring {entry['path']}")


def extract_archive(stage: Path, archive_path: Path, root_metadata: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    try:
        with tarfile.open(archive_path, "r:") as archive:
            members = member_map(archive)
            directories = sorted(
                (entry for entry in entries if entry["kind"] == "directory"),
                key=lambda entry: (entry["path"].count("/"), entry["path"]),
            )
            for entry in directories:
                target = extraction_target(stage, entry["path"])
                target.mkdir(mode=0o700)
            for entry in entries:
                if entry["kind"] == "directory":
                    continue
                target = extraction_target(stage, entry["path"])
                member = members[f"{ARCHIVE_ROOT}/{entry['path']}"]
                if entry["kind"] == "file":
                    write_archive_file(archive, member, target, entry)
                else:
                    os.symlink(entry["target"], target)
            for entry in sorted(
                entries, key=lambda item: (item["path"].count("/"), item["path"]), reverse=True
            ):
                apply_metadata(stage / entry["path"], entry)
            apply_metadata(stage, root_metadata)
    except (OSError, tarfile.TarError, KeyError) as error:
        fail(f"cannot extract rollback archive: {error}")

    current_metadata, current_entries = inventory_tree(stage)
    if current_metadata != root_metadata or current_entries != entries:
        fail("restored staging tree differs from the verified rollback snapshot")


def restore(root: Path, snapshot: Path) -> None:
    snapshot = existing_snapshot_path(snapshot, root)
    receipt, root_metadata, entries = read_receipt(snapshot, root)
    archive = snapshot / ARCHIVE_NAME
    verify_archive(archive, root_metadata, entries)
    prepare_stage_parent(root)
    stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.rollback-", dir=root.parent))
    exchanged = False
    try:
        extract_archive(stage, archive, root_metadata, entries)
        original = root.stat(follow_symlinks=False)
        exchange_directories(stage, root)
        exchanged = True
        displaced = stage.stat(follow_symlinks=False)
        if (displaced.st_dev, displaced.st_ino) != (original.st_dev, original.st_ino):
            exchange_directories(stage, root)
            exchanged = False
            fail("host .NET root changed during rollback exchange")
        tombstone = root.with_name(f".{root.name}.rollback-preimage-{os.getpid()}")
        durable_remove(stage, tombstone)
        exchanged = False
        fsync_directory(root.parent)
    except BaseException:
        if not exchanged and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    if receipt["treeSha256"] != tree_hash(root_metadata, entries):
        fail("rollback snapshot tree inventory changed during restore")
    print("rollback_restore=passed")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory", help="emit a static workload inventory")
    inventory.add_argument("--root", type=Path, required=True)
    inventory.add_argument("--required-workload", action="append", default=[])
    captured = subparsers.add_parser("snapshot", help="create and verify an external rollback snapshot")
    captured.add_argument("--root", type=Path, required=True)
    captured.add_argument("--output", type=Path, required=True)
    captured.add_argument("--required-workload", action="append", default=[])
    checked = subparsers.add_parser("verify", help="verify an external rollback snapshot")
    checked.add_argument("--root", type=Path, required=True)
    checked.add_argument("--snapshot", type=Path, required=True)
    restored = subparsers.add_parser("restore", help="replace the host root from a verified rollback snapshot")
    restored.add_argument("--root", type=Path, required=True)
    restored.add_argument("--snapshot", type=Path, required=True)
    restored.add_argument("--yes", action="store_true", help="confirm destructive root replacement")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    root = root_path(args.root)
    if args.command == "inventory":
        _metadata, entries = inventory_tree(root)
        print(json.dumps(workload_inventory(root, entries, args.required_workload), indent=2, sort_keys=True))
    elif args.command == "snapshot":
        snapshot(root, args.output, args.required_workload)
    elif args.command == "verify":
        verify(root, args.snapshot)
    else:
        if not args.yes:
            fail("restore is destructive; pass --yes after stopping all .NET processes")
        restore(root, args.snapshot)


if __name__ == "__main__":
    main()
