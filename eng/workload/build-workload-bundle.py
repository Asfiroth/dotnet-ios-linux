#!/usr/bin/env python3
"""Plan and build a complete local-only workload bundle for elevated installation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import NoReturn


OWNER = "dotnet-ios-linux"
SCHEMA_VERSION = 1
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024


def fail(message: str) -> NoReturn:
    raise SystemExit(f"workload bundle failed: {message}")


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sha512_base64(path: Path) -> str:
    return base64.b64encode(bytes.fromhex(digest(path, "sha512"))).decode("ascii")


def load_lock(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read toolchain lock: {error}")
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        fail("unsupported toolchain lock")
    return value


def package_filename(package_id: str, version: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", package_id) is None:
        fail(f"invalid package ID: {package_id!r}")
    if re.fullmatch(r"[A-Za-z0-9_.+-]+", version) is None:
        fail(f"invalid package version: {version!r}")
    return f"{package_id.lower()}.{version.lower()}.nupkg"


def official_records(lock: dict) -> list[dict]:
    try:
        records = lock["components"]["linuxIosWorkload"]["packages"]
    except (KeyError, TypeError):
        fail("toolchain lock lacks the Linux iOS package inventory")
    if not isinstance(records, list):
        fail("Linux iOS package inventory is not a list")
    result: list[dict] = []
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, dict) or item.get("origin") != "nuget.org":
            continue
        package_id = item.get("id")
        version = item.get("version")
        sha256 = item.get("sha256")
        sha512 = item.get("sha512Base64")
        if not all(isinstance(value, str) and value for value in (package_id, version, sha256, sha512)):
            fail("official package record is incomplete")
        filename = package_filename(package_id, version)
        if filename in seen:
            fail(f"duplicate official package filename: {filename}")
        seen.add(filename)
        lower_id = package_id.lower()
        lower_version = version.lower()
        result.append(
            {
                "id": package_id,
                "version": version,
                "file": filename,
                "sha256": sha256,
                "sha512Base64": sha512,
                "origin": "nuget.org",
                "url": (
                    "https://api.nuget.org/v3-flatcontainer/"
                    f"{lower_id}/{lower_version}/{filename}"
                ),
            }
        )
    if not result:
        fail("toolchain lock contains no official workload packages")
    return sorted(result, key=lambda item: item["file"])


def workload_set_record(lock: dict) -> dict:
    try:
        value = lock["components"]["workloadSet"]
    except (KeyError, TypeError):
        fail("toolchain lock lacks the workload set")
    package_id = value.get("packageId")
    version = value.get("packageVersion")
    sha256 = value.get("sha256")
    sha512 = value.get("sha512Base64")
    if not all(isinstance(item, str) and item for item in (package_id, version, sha256, sha512)):
        fail("workload-set record is incomplete")
    filename = package_filename(package_id, version)
    return {
        "id": package_id,
        "version": version,
        "file": filename,
        "sha256": sha256,
        "sha512Base64": sha512,
        "origin": "nuget.org",
    }


def plan(lock_path: Path, output_format: str) -> None:
    records = official_records(load_lock(lock_path))
    if output_format == "json":
        print(json.dumps(records, indent=2))
        return
    for record in records:
        for key in ("file", "url", "sha256"):
            sys.stdout.buffer.write(record[key].encode("utf-8") + b"\0")


def safe_feed_filename(raw: object) -> str:
    if not isinstance(raw, str):
        fail("local feed record lacks a filename")
    path = PurePosixPath(raw)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        fail(f"unsafe local feed filename: {raw!r}")
    return path.name


def local_records(feed: Path) -> list[dict]:
    try:
        value = json.loads((feed / "feed-lock.json").read_text(encoding="utf-8"))
        records = value["packages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"cannot read local feed inventory: {error}")
    if not isinstance(records, list) or not records:
        fail("local feed inventory is empty")
    result: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            fail("local feed contains an invalid record")
        filename = safe_feed_filename(item.get("file"))
        path = feed / filename
        if path.is_symlink() or not path.is_file():
            fail(f"local feed package is missing: {filename}")
        expected_sha256 = item.get("sha256")
        expected_sha512 = item.get("sha512Base64")
        if digest(path) != expected_sha256 or sha512_base64(path) != expected_sha512:
            fail(f"local feed package failed integrity validation: {filename}")
        result.append(
            {
                "id": item.get("id"),
                "version": item.get("version"),
                "file": filename,
                "sha256": expected_sha256,
                "sha512Base64": expected_sha512,
                "origin": item.get("origin"),
                "source": path,
            }
        )
    return sorted(result, key=lambda item: item["file"])


def validate_package(path: Path, record: dict) -> None:
    if path.is_symlink() or not path.is_file():
        fail(f"package is missing or unsafe: {path}")
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        fail(f"package exceeds the size limit: {path}")
    if digest(path) != record["sha256"] or sha512_base64(path) != record["sha512Base64"]:
        fail(f"package hash mismatch: {path}")


def tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def verify_existing_bundle(path: Path, manifest: dict) -> None:
    expected = {record["file"]: record for record in manifest["packages"]}
    try:
        with tarfile.open(path, "r:") as archive:
            members = archive.getmembers()
            names = [PurePosixPath(member.name) for member in members]
            if any(
                name.is_absolute()
                or not name.parts
                or any(part in {"", ".", ".."} for part in name.parts)
                for name in names
            ):
                fail("existing bundle contains an unsafe member path")
            if len(names) != len(set(names)):
                fail("existing bundle contains duplicate members")
            by_name = {name.as_posix(): member for name, member in zip(names, members, strict=True)}
            expected_names = {"bundle.json"} | {f"packages/{name}" for name in expected}
            if set(by_name) != expected_names:
                fail("existing bundle has missing or unexpected members")
            manifest_reader = archive.extractfile(by_name["bundle.json"])
            if manifest_reader is None or json.loads(manifest_reader.read().decode("utf-8")) != manifest:
                fail("existing bundle manifest differs from the requested bundle")
            for filename, record in expected.items():
                member = by_name[f"packages/{filename}"]
                if not member.isfile() or member.size > MAX_PACKAGE_BYTES:
                    fail(f"existing bundle package member is invalid: {filename}")
                reader = archive.extractfile(member)
                if reader is None:
                    fail(f"cannot read existing bundle package: {filename}")
                sha256 = hashlib.sha256()
                sha512 = hashlib.sha512()
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    sha256.update(block)
                    sha512.update(block)
                if (
                    sha256.hexdigest() != record["sha256"]
                    or base64.b64encode(sha512.digest()).decode("ascii") != record["sha512Base64"]
                ):
                    fail(f"existing bundle package hash mismatch: {filename}")
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"cannot verify existing workload bundle: {error}")


def build(
    lock_path: Path,
    feed: Path,
    workload_set: Path,
    package_cache: Path,
    output: Path,
) -> None:
    lock = load_lock(lock_path)
    lock_sha256 = digest(lock_path)
    records = local_records(feed)
    try:
        locked_workload_records = lock["components"]["linuxIosWorkload"]["packages"]
    except (KeyError, TypeError):
        fail("toolchain lock lacks the Linux iOS package inventory")
    locked_by_identity = {
        (item.get("id"), item.get("version")): item
        for item in locked_workload_records
        if isinstance(item, dict)
    }
    for record in records:
        expected = locked_by_identity.get((record["id"], record["version"]))
        if (
            expected is None
            or expected.get("feedFile") != record["file"]
            or expected.get("sha256") != record["sha256"]
            or expected.get("sha512Base64") != record["sha512Base64"]
        ):
            fail(f"local feed package does not match the toolchain lock: {record['file']}")
        record["origin"] = expected.get("origin")
    names = {record["file"] for record in records}

    workload_record = workload_set_record(lock)
    validate_package(workload_set, workload_record)
    workload_record["source"] = workload_set
    records.append(workload_record)
    names.add(workload_record["file"])

    for record in official_records(lock):
        if record["file"] in names:
            fail(f"duplicate bundle package filename: {record['file']}")
        source = package_cache / record["file"]
        validate_package(source, record)
        record["source"] = source
        records.append(record)
        names.add(record["file"])

    public_records = [
        {key: value for key, value in record.items() if key not in {"source", "url"}}
        for record in sorted(records, key=lambda item: item["file"])
    ]
    if len({(item["id"], item["version"]) for item in public_records}) != len(public_records):
        fail("bundle contains duplicate package identities")
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "owner": OWNER,
        "toolchainLockSha256": lock_sha256,
        "sdkFeatureBand": lock["components"]["dotnetSdk"]["featureBand"],
        "workloadVersion": lock["components"]["workloadSet"]["cliVersion"],
        "workloadId": lock["components"]["linuxIosWorkload"]["workloadId"],
        "packages": public_records,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        if output.is_symlink() or not output.is_file():
            fail(f"existing bundle output is unsafe: {output}")
        verify_existing_bundle(output, manifest)
        print(f"bundle={output}")
        print(f"bundle_sha256={digest(output)}")
        print("reused=true")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w") as archive:
            archive.addfile(tar_info("bundle.json", len(manifest_bytes)), fileobj=_BytesReader(manifest_bytes))
            by_file = {record["file"]: record for record in records}
            for filename in sorted(by_file):
                source = by_file[filename]["source"]
                info = tar_info(f"packages/{filename}", source.stat().st_size)
                with source.open("rb") as stream:
                    archive.addfile(info, fileobj=stream)
        temporary.chmod(0o600)
        try:
            os.link(temporary, output)
        except FileExistsError:
            if output.is_symlink() or not output.is_file():
                fail(f"bundle output appeared as an unsafe path: {output}")
            verify_existing_bundle(output, manifest)
        temporary.unlink(missing_ok=True)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f"bundle={output}")
    print(f"bundle_sha256={digest(output)}")
    print("reused=false")


class _BytesReader:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.offset = 0

    def read(self, count: int = -1) -> bytes:
        if count < 0:
            count = len(self.value) - self.offset
        result = self.value[self.offset : self.offset + count]
        self.offset += len(result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    planned = subparsers.add_parser("plan", help="emit official package downloads")
    planned.add_argument("--lock", type=Path, required=True)
    planned.add_argument("--format", choices=("json", "null"), default="json")
    bundled = subparsers.add_parser("build", help="build the verified workload bundle")
    bundled.add_argument("--lock", type=Path, required=True)
    bundled.add_argument("--feed", type=Path, required=True)
    bundled.add_argument("--workload-set", type=Path, required=True)
    bundled.add_argument("--package-cache", type=Path, required=True)
    bundled.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(args.lock.expanduser().resolve(), args.format)
    else:
        build(
            args.lock.expanduser().resolve(),
            args.feed.expanduser().resolve(),
            args.workload_set.expanduser().resolve(),
            args.package_cache.expanduser().resolve(),
            args.output.expanduser().absolute(),
        )


if __name__ == "__main__":
    main()
