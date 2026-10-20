#!/usr/bin/env python3
"""Transactionally extract one authentic iPhoneOS SDK from a pinned Xcode XIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from xip_archive import CpioEntry, XcodeMetadata, inspect_metadata, open_cpio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import exchange_directories, publish_directory_noreplace


MARKER = ".dotnet-ios-linux-sdk.json"
OWNER = "dotnet-ios-linux"
MAX_FILE_SIZE = 8 * 1024 * 1024 * 1024
MAX_EXTRACTED_SIZE = 40 * 1024 * 1024 * 1024
MAX_SYMLINK_SIZE = 64 * 1024
COPY_BLOCK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SelectedEntry:
    relative_path: PurePosixPath
    archive_entry: CpioEntry


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--xcode-version", required=True)
    parser.add_argument("--xcode-build", required=True)
    parser.add_argument("--platform-version", required=True)
    parser.add_argument("--platform-build", required=True)
    parser.add_argument("--sdk-version", required=True)
    parser.add_argument("--sdk-build", required=True)
    parser.add_argument(
        "--replace", action="store_true", help="replace only an SDK carrying this importer's marker"
    )
    return parser.parse_args()


def fail(message: str) -> "None":
    raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(COPY_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def plist_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        value = plistlib.load(stream)
    if not isinstance(value, dict):
        fail(f"plist root is not a dictionary: {path}")
    return sha256(path)


def verify_expected(metadata: XcodeMetadata, options: argparse.Namespace) -> None:
    expected = {
        "xcode version": options.xcode_version,
        "Xcode build": options.xcode_build,
        "platform version": options.platform_version,
        "platform build": options.platform_build,
        "SDK version": options.sdk_version,
        "SDK build": options.sdk_build,
    }
    observed = {
        "xcode version": metadata.xcode_version,
        "Xcode build": metadata.xcode_build,
        "platform version": metadata.platform_version,
        "platform build": metadata.platform_build,
        "SDK version": metadata.sdk_version,
        "SDK build": metadata.sdk_build,
    }
    for label, value in expected.items():
        if value != observed[label]:
            fail(f"expected {label} {value!r}, archive contains {observed[label]!r}")


def relative_sdk_path(name: str, root: str) -> PurePosixPath | None:
    if name == root:
        return PurePosixPath()
    prefix = root + "/"
    if not name.startswith(prefix):
        return None
    raw = name[len(prefix) :]
    components = raw.split("/")
    if not raw or any(component in {"", ".", ".."} for component in components):
        fail(f"unsafe SDK archive path: {name!r}")
    return PurePosixPath(*components)


def scan_sdk(
    archive: Path, root: str
) -> tuple[
    dict[PurePosixPath, SelectedEntry],
    dict[tuple[int, int], CpioEntry],
    dict[tuple[int, int], int],
    int,
    CpioEntry,
]:
    selected: dict[PurePosixPath, SelectedEntry] = {}
    hardlink_sources: dict[tuple[int, int], CpioEntry] = {}
    regular_counts: dict[tuple[int, int], int] = {}
    logical_size = 0
    root_entry: CpioEntry | None = None
    for entry, _reader in open_cpio(archive):
        if stat.S_ISREG(entry.mode):
            hardlink_sources.setdefault(entry.hardlink_key, entry)
            regular_counts[entry.hardlink_key] = regular_counts.get(entry.hardlink_key, 0) + 1
        relative = relative_sdk_path(entry.name, root)
        if relative is None:
            continue
        if not relative.parts:
            if root_entry is not None:
                fail("duplicate SDK root entry")
            if not stat.S_ISDIR(entry.mode):
                fail("SDK archive root is not a directory")
            root_entry = entry
            continue
        if relative in selected:
            fail(f"duplicate SDK member: {relative}")
        if entry.size > MAX_FILE_SIZE:
            fail(f"SDK member exceeds the per-file size limit: {relative}")
        file_type = stat.S_IFMT(entry.mode)
        if file_type not in {stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK}:
            fail(f"unsupported SDK member type: {relative}")
        logical_size += entry.size
        if logical_size > MAX_EXTRACTED_SIZE:
            fail("SDK exceeds the extraction size limit")
        selected[relative] = SelectedEntry(relative, entry)
    if root_entry is None or not selected:
        fail("XIP does not contain a populated SDK root")
    return selected, hardlink_sources, regular_counts, logical_size, root_entry


def destination_path(stage: Path, relative: PurePosixPath) -> Path:
    return stage.joinpath(*relative.parts)


def ensure_parents(stage: Path, relative_parent: PurePosixPath) -> None:
    current = stage
    for component in relative_parent.parts:
        current /= component
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                fail(f"SDK member parent is not a real directory: {relative_parent}")
        else:
            current.mkdir(mode=0o755)


def create_directory(stage: Path, relative: PurePosixPath) -> None:
    ensure_parents(stage, relative.parent)
    path = destination_path(stage, relative)
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            fail(f"SDK directory conflicts with another member: {relative}")
    else:
        path.mkdir(mode=0o755)


def copy_member(reader, destination: Path, size: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    written = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output:
            while reader.remaining:
                block = reader.read(min(COPY_BLOCK_SIZE, reader.remaining))
                output.write(block)
                digest.update(block)
                written += len(block)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if written != size:
        destination.unlink(missing_ok=True)
        fail(f"cpio member size changed while extracting {destination.name}")
    return written, digest.hexdigest()


def extract_sdk(
    archive: Path,
    stage: Path,
    sdk_root: str,
    selected: dict[PurePosixPath, SelectedEntry],
    hardlink_sources: dict[tuple[int, int], CpioEntry],
    regular_counts: dict[tuple[int, int], int],
) -> tuple[int, int, int]:
    hardlink_paths: dict[tuple[int, int], list[PurePosixPath]] = {}
    for item in selected.values():
        entry = item.archive_entry
        if stat.S_ISREG(entry.mode) and regular_counts[entry.hardlink_key] > 1:
            hardlink_paths.setdefault(entry.hardlink_key, []).append(item.relative_path)
    hardlink_primary: dict[tuple[int, int], PurePosixPath] = {}
    for key, paths in hardlink_paths.items():
        paths.sort(key=str)
        hardlink_primary[key] = paths[0]
        metadata = [selected[path].archive_entry for path in paths]
        if len({stat.S_IMODE(entry.mode) for entry in metadata}) != 1:
            fail("hardlinked SDK members have inconsistent permissions")
        if len({entry.modified_time for entry in metadata}) != 1:
            fail("hardlinked SDK members have inconsistent modification times")

    written_hardlinks: set[tuple[int, int]] = set()
    written_bytes = 0
    regular_files = 0
    symlinks = 0
    for entry, reader in open_cpio(archive):
        relative = relative_sdk_path(entry.name, sdk_root)
        selected_item = selected.get(relative) if relative and relative.parts else None

        key = entry.hardlink_key
        if key in hardlink_paths and entry.name == hardlink_sources[key].name:
            if key in written_hardlinks:
                fail(f"duplicate authoritative hardlink member: {key}")
            if entry.size > MAX_FILE_SIZE:
                fail(f"hardlink data exceeds the per-file size limit: {key}")
            primary = hardlink_primary[key]
            ensure_parents(stage, primary.parent)
            copied, _digest = copy_member(reader, destination_path(stage, primary), entry.size)
            written_bytes += copied
            if written_bytes > MAX_EXTRACTED_SIZE:
                fail("SDK exceeds the extraction size limit")
            written_hardlinks.add(key)
            regular_files += 1
            continue

        if selected_item is None:
            continue
        mode = entry.mode
        assert relative is not None
        destination = destination_path(stage, relative)
        if stat.S_ISDIR(mode):
            create_directory(stage, relative)
        elif stat.S_ISLNK(mode):
            if entry.size > MAX_SYMLINK_SIZE:
                fail(f"SDK symlink target is too large: {relative}")
            ensure_parents(stage, relative.parent)
            target = reader.read().decode("utf-8", "strict")
            if "\0" in target:
                fail(f"SDK symlink target contains a null byte: {relative}")
            os.symlink(target, destination)
            symlinks += 1
        elif stat.S_ISREG(mode) and key not in hardlink_paths:
            ensure_parents(stage, relative.parent)
            copied, _digest = copy_member(reader, destination, entry.size)
            written_bytes += copied
            if written_bytes > MAX_EXTRACTED_SIZE:
                fail("SDK exceeds the extraction size limit")
            regular_files += 1

    for key, paths in hardlink_paths.items():
        primary_relative = hardlink_primary[key]
        primary = destination_path(stage, primary_relative)
        if key not in written_hardlinks:
            fail(f"authoritative hardlink member was not found: {key}")
        for relative in paths:
            if relative == primary_relative:
                continue
            ensure_parents(stage, relative.parent)
            os.link(primary, destination_path(stage, relative), follow_symlinks=False)

    for item in selected.values():
        path = destination_path(stage, item.relative_path)
        if not os.path.lexists(path):
            fail(f"SDK member was not materialized: {item.relative_path}")

    non_directories = [item for item in selected.values() if not stat.S_ISDIR(item.archive_entry.mode)]
    directories = sorted(
        (item for item in selected.values() if stat.S_ISDIR(item.archive_entry.mode)),
        key=lambda item: len(item.relative_path.parts),
        reverse=True,
    )
    for item in [*non_directories, *directories]:
        entry = item.archive_entry
        path = destination_path(stage, item.relative_path)
        if stat.S_ISLNK(entry.mode):
            os.utime(path, (entry.modified_time, entry.modified_time), follow_symlinks=False)
            continue
        os.chmod(path, stat.S_IMODE(entry.mode), follow_symlinks=False)
        os.utime(path, (entry.modified_time, entry.modified_time), follow_symlinks=False)

    return regular_files, symlinks, written_bytes


def require_owned_sdk(path: Path) -> None:
    marker = path / MARKER
    if path.is_symlink() or not path.is_dir() or marker.is_symlink():
        fail(f"refusing to replace unowned SDK directory: {path}")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        fail(f"refusing to replace unowned SDK directory: {path} ({error})")
    if not isinstance(value, dict) or value.get("owner") != OWNER:
        fail(f"refusing to replace unowned SDK directory: {path}")


def install_stage(stage: Path, output: Path, replace: bool) -> None:
    if not os.path.lexists(output):
        try:
            publish_directory_noreplace(stage, output)
        except FileExistsError:
            fail(f"output appeared during publication; refusing to replace it: {output}")
        return
    if not replace:
        fail(f"output already exists: {output}")
    require_owned_sdk(output)
    output_stat = output.stat(follow_symlinks=False)
    expected_identity = (output_stat.st_dev, output_stat.st_ino)
    exchange_directories(stage, output)
    replaced_stat = stage.stat(follow_symlinks=False)
    if (replaced_stat.st_dev, replaced_stat.st_ino) != expected_identity:
        exchange_directories(stage, output)
        fail("SDK output changed during owned replacement")
    shutil.rmtree(stage)


def main() -> int:
    options = parse_arguments()
    archive = options.archive.expanduser().resolve()
    output = options.output.expanduser().absolute()
    expected_hash = options.archive_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        print("Xcode SDK import failed: --archive-sha256 must contain 64 lowercase hex characters")
        return 1
    if not archive.is_file():
        print(f"Xcode SDK import failed: archive is not a file: {archive}")
        return 1
    if output.name != f"iPhoneOS{options.sdk_version}.sdk":
        print(f"Xcode SDK import failed: output must be named iPhoneOS{options.sdk_version}.sdk")
        return 1

    stage: Path | None = None
    try:
        archive_hash = sha256(archive)
        if archive_hash != expected_hash:
            fail(f"archive SHA-256 mismatch: expected {expected_hash}, got {archive_hash}")
        metadata = inspect_metadata(archive)
        verify_expected(metadata, options)
        selected, hardlink_sources, regular_counts, logical_size, root_entry = scan_sdk(
            archive, metadata.sdk_archive_root
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
        regular_files, symlinks, written_bytes = extract_sdk(
            archive,
            stage,
            metadata.sdk_archive_root,
            selected,
            hardlink_sources,
            regular_counts,
        )
        os.chmod(stage, stat.S_IMODE(root_entry.mode), follow_symlinks=False)

        settings = stage / "SDKSettings.plist"
        system_version = stage / "System" / "Library" / "CoreServices" / "SystemVersion.plist"
        required = [
            settings,
            system_version,
            stage / "System" / "Library" / "Frameworks" / "UIKit.framework",
            stage / "usr" / "lib" / "libSystem.tbd",
        ]
        missing = [str(path.relative_to(stage)) for path in required if not path.exists()]
        if missing:
            fail("extracted SDK is incomplete; missing: " + ", ".join(missing))

        final_archive_hash = sha256(archive)
        if final_archive_hash != archive_hash:
            fail("archive changed during extraction")
        marker = {
            "schemaVersion": 1,
            "owner": OWNER,
            "archiveName": archive.name,
            "archiveSha256": archive_hash,
            "xcodeVersion": metadata.xcode_version,
            "xcodeBuild": metadata.xcode_build,
            "platformVersion": metadata.platform_version,
            "platformBuild": metadata.platform_build,
            "sdkVersion": metadata.sdk_version,
            "sdkBuild": metadata.sdk_build,
            "sdkBuildSource": metadata.sdk_build_source,
            "sdkCanonicalName": metadata.sdk_canonical_name,
            "sdkSettingsSha256": plist_sha256(settings),
            "sdkSystemVersionSha256": plist_sha256(system_version),
            "archiveMemberCount": len(selected),
            "archiveLogicalBytes": logical_size,
            "materializedRegularFiles": regular_files,
            "materializedSymlinks": symlinks,
            "materializedBytes": written_bytes,
        }
        (stage / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        install_stage(stage, output, options.replace)
        stage = None
    except (EOFError, OSError, UnicodeDecodeError, ValueError) as error:
        print(f"Xcode SDK import failed: {error}")
        return 1
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)

    print(f"sdk={output}")
    print(f"sdk_version={metadata.sdk_version}")
    print(f"sdk_build={metadata.sdk_build}")
    print(f"platform_build={metadata.platform_build}")
    print(f"archive_sha256={archive_hash}")
    print(f"archive_member_count={len(selected)}")
    print(f"materialized_bytes={written_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
