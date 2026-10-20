#!/usr/bin/env python3
"""Restore and assemble the exact Mono LLVM SDK/Tools used by runtime 10.0.12."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import exchange_directories, publish_directory_noreplace


VERSION = "19.1.0-alpha.1.25461.1"
FEED = "https://pkgs.dev.azure.com/dnceng/public/_packaging/dotnet10/nuget/v3/index.json"
SOURCE_COMMIT = "2cfe42ea4a7fe364dcb197beca469af8edec0ad6"
PACKAGES = {
    "runtime.linux-x64.Microsoft.NETCore.Runtime.Mono.LLVM.Sdk":
        "0ab321b6a5b4a7cb159b12c5c1e14daa034a50bfae3afecd0b8737649b4811cc",
    "runtime.linux-x64.Microsoft.NETCore.Runtime.Mono.LLVM.Tools":
        "1f2783be29b44c9d53b1f11048de8f123089fafc475a30f6b6494204b842d043",
}
MARKER = "dotnet-ios-linux-llvm.json"


def fail(message: str) -> "None":
    print(f"matched LLVM acquisition failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command), file=sys.stderr)
    result = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="", file=sys.stderr)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        fail(f"command exited with {result.returncode}: {command[0]}")
    return result


def package_root(cache: Path, package_id: str) -> Path:
    return cache / package_id.lower() / VERSION


def verify_package(cache: Path, package_id: str, expected_sha256: str) -> tuple[Path, Path]:
    root = package_root(cache, package_id)
    nupkg = root / f"{package_id.lower()}.{VERSION}.nupkg"
    if not nupkg.is_file():
        fail(f"restored package archive is missing: {nupkg}")
    actual_sha256 = sha256(nupkg)
    if actual_sha256 != expected_sha256:
        fail(f"SHA-256 mismatch for {package_id}: expected {expected_sha256}, got {actual_sha256}")

    nuspec = root / f"{package_id.lower()}.nuspec"
    if not nuspec.is_file():
        fail(f"restored package metadata is missing: {nuspec}")
    text = nuspec.read_text(encoding="utf-8")
    if f'<version>{VERSION}</version>' not in text or f'commit="{SOURCE_COMMIT}"' not in text:
        fail(f"package provenance does not match the locked version/commit: {nuspec}")

    payload = root / "tools" / "linux-x64"
    if not payload.is_dir():
        fail(f"package lacks tools/linux-x64: {package_id}")
    return nupkg, payload


def copy_payload(source: Path, destination: Path) -> None:
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True, symlinks=True)
        else:
            if target.exists() and sha256(target) != sha256(child):
                fail(f"package payload collision has different content: {child.relative_to(source)}")
            shutil.copy2(child, target)


def inventory(root: Path) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == MARKER:
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            entries.append({"path": relative, "kind": "symlink", "target": os.readlink(path), "mode": mode})
        elif path.is_dir():
            entries.append({"path": relative, "kind": "directory", "mode": mode})
        elif path.is_file():
            entries.append({"path": relative, "kind": "file", "sha256": sha256(path), "mode": mode})
        else:
            fail(f"unsupported LLVM payload entry: {path}")
    return entries


def verify_root(root: Path, *, make_executable: bool) -> dict[str, str]:
    required = [
        root / "bin" / "llvm-config",
        root / "bin" / "llc",
        root / "bin" / "opt",
        root / "include" / "llvm" / "Config" / "llvm-config.h",
        root / "lib" / "libLLVMCore.a",
        root / "lib" / "libc++.so.1",
        root / "lib" / "libc++abi.so.1",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        fail("assembled root is incomplete; missing: " + ", ".join(missing))
    if make_executable:
        for path in required[:3]:
            path.chmod(path.stat().st_mode | 0o111)

    versions: dict[str, str] = {}
    for tool in ("llvm-config", "llc", "opt"):
        executable = root / "bin" / tool
        result = run([str(executable), "--version"])
        first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        if tool == "llvm-config":
            if not result.stdout.startswith("19.1.0"):
                fail(f"{tool} does not report LLVM 19.1.0")
        elif "LLVM version 19.1.0" not in result.stdout:
            fail(f"{tool} does not report LLVM 19.1.0")
        versions[tool] = first_line
    return versions


def verify_existing(root: Path) -> None:
    marker = root / MARKER
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read LLVM ownership marker {marker}: {error}")
    if value.get("version") != VERSION or value.get("feed") != FEED or value.get("sourceCommit") != SOURCE_COMMIT:
        fail(f"existing LLVM root has different provenance: {root}")
    observed = {item.get("id"): item.get("sha256") for item in value.get("packages", [])}
    if observed != PACKAGES:
        fail(f"existing LLVM root has different package hashes: {root}")
    expected_inventory = value.get("files")
    if not isinstance(expected_inventory, list) or inventory(root) != expected_inventory:
        fail(f"existing LLVM root failed its payload inventory: {root}")
    verify_root(root, make_executable=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="assembled LLVM root")
    parser.add_argument("--packages", type=Path, required=True, help="isolated NuGet package cache")
    parser.add_argument("--dotnet", default=shutil.which("dotnet"), help="dotnet executable")
    parser.add_argument("--replace", action="store_true", help="replace only a root created by this script")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="assemble only from already restored, hash-verified packages",
    )
    args = parser.parse_args()

    if not args.dotnet:
        fail("dotnet was not found; pass --dotnet")
    dotnet = Path(args.dotnet).expanduser().resolve()
    if not dotnet.is_file() or not os.access(dotnet, os.X_OK):
        fail(f"dotnet is not executable: {dotnet}")

    output = args.output.expanduser().absolute()
    cache = args.packages.expanduser().resolve()
    output_exists = output.exists() or output.is_symlink()
    if output_exists:
        if output.is_symlink() or not output.is_dir() or not (output / MARKER).is_file() or (output / MARKER).is_symlink():
            fail(f"refusing to replace an unowned directory: {output}")
        if not args.replace:
            verify_existing(output)
            print(f"llvm_root={output}")
            print(f"llvm_version={VERSION}")
            print("reused=true")
            return
    output.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="llvm-restore-", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        project = temporary_root / "restore.csproj"
        references = "\n".join(
            f'    <PackageReference Include="{package_id}" Version="{VERSION}" />'
            for package_id in PACKAGES
        )
        project.write_text(
            "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
            "  <PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>\n"
            "  <ItemGroup>\n"
            f"{references}\n"
            "  </ItemGroup>\n"
            "</Project>\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["NUGET_PACKAGES"] = str(cache)
        if not args.offline:
            run(
                [
                    str(dotnet),
                    "restore",
                    str(project),
                    "--packages",
                    str(cache),
                    "--source",
                    FEED,
                    "--no-cache",
                ],
                environment,
            )

        verified: dict[str, tuple[Path, Path]] = {}
        for package_id, expected_hash in PACKAGES.items():
            verified[package_id] = verify_package(cache, package_id, expected_hash)

        stage = temporary_root / "llvm-root"
        stage.mkdir()
        # SDK supplies headers/static libraries; Tools supplies AOT executables
        # and the matching SONAME runtime libraries needed by every executable.
        for package_id in PACKAGES:
            copy_payload(verified[package_id][1], stage)
        versions = verify_root(stage, make_executable=True)

        provenance = {
            "schemaVersion": 1,
            "version": VERSION,
            "feed": FEED,
            "sourceCommit": SOURCE_COMMIT,
            "packages": [
                {
                    "id": package_id,
                    "version": VERSION,
                    "sha256": PACKAGES[package_id],
                }
                for package_id in PACKAGES
            ],
            "toolVersions": versions,
            "files": inventory(stage),
        }
        (stage / MARKER).write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        if output_exists:
            # Replacement was explicitly requested and ownership was checked.
            # Exchange is atomic, so the path never disappears and no unrelated
            # path can be overwritten in a publication gap.
            output_stat = output.stat(follow_symlinks=False)
            expected_identity = (output_stat.st_dev, output_stat.st_ino)
            exchange_directories(stage, output)
            replaced_stat = stage.stat(follow_symlinks=False)
            replaced_identity = (replaced_stat.st_dev, replaced_stat.st_ino)
            if replaced_identity != expected_identity:
                exchange_directories(stage, output)
                fail("LLVM output changed during owned replacement")
            shutil.rmtree(stage)
        else:
            try:
                publish_directory_noreplace(stage, output)
            except FileExistsError:
                fail(f"LLVM output appeared during publication; refusing to replace it: {output}")

    verify_existing(output)
    print(f"llvm_root={output}")
    print(f"llvm_version={VERSION}")
    print("reused=false")


if __name__ == "__main__":
    main()
