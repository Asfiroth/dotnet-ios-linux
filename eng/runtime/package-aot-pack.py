#!/usr/bin/env python3
"""Create the project-owned pack layout consumed by MonoAotCrossCompiler."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import publish_directory_noreplace


EXPECTED_LLVM_PACKAGES = {
    "runtime.linux-x64.Microsoft.NETCore.Runtime.Mono.LLVM.Sdk":
        "0ab321b6a5b4a7cb159b12c5c1e14daa034a50bfae3afecd0b8737649b4811cc",
    "runtime.linux-x64.Microsoft.NETCore.Runtime.Mono.LLVM.Tools":
        "1f2783be29b44c9d53b1f11048de8f123089fafc475a30f6b6494204b842d043",
}
EXPECTED_COMPILER_SHA256 = "55dcaa7bf9dd71206ff8f69a26e0eacd79af7146b2a0f7a1143c3d9e0d3267e7"
AOT_PACK_ID = "DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64"
RUNTIME_COMMIT = "4271d88e0aebf3d04f188f1334c2220d80555ef6"
RUNTIME_VERSION = "10.0.12"
TARGET_TRIPLE = "aarch64-apple-darwin10"
LLVM_PACKAGE_VERSION = "19.1.0-alpha.1.25461.1"
LLVM_SOURCE_COMMIT = "2cfe42ea4a7fe364dcb197beca469af8edec0ad6"
EXPECTED_AOT_FILES = {
    "mono-aot-cross": EXPECTED_COMPILER_SHA256,
    "libc++.so.1": "9233ff02fd30aba191b9a936eeb351d78682616fce2db29c08bbd1c3a581ca9c",
    "libc++abi.so.1": "80bef63649f325c706768ecf9f29598429c712721b58c990117e86c826dbe534",
    "llc": "8b53230508c94cda4ee541be27e7f9957becd32d5e2522f3be1bbe4c61079662",
    "opt": "99464211ff7ba2175408c935d17ca375a99c564e88b476581e9b3d59af81d011",
}
EXPECTED_LLVM_AOT_FILES = {
    name: value for name, value in EXPECTED_AOT_FILES.items() if name != "mono-aot-cross"
}
EXPECTED_PATH_NORMALIZATION = {
    "source": "/usr/src/dotnet-runtime",
    "build": "/usr/build/dotnet-ios-linux/mono-aot",
    "install": "/usr/lib/dotnet-ios-linux/mono-aot",
    "llvm": "/usr/lib/dotnet-ios-linux/llvm",
}
EXPECTED_AOT_MODES = {
    "mono-aot-cross": 0o755,
    "libc++.so.1": 0o700,
    "libc++abi.so.1": 0o700,
    "llc": 0o711,
    "opt": 0o711,
}


def fail(message: str) -> "None":
    print(f"AOT pack: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def execute(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(f"command exited with {result.returncode}: {command[0]}: {detail}")
    return result.stdout


def verify_llvm_root(root: Path) -> dict:
    marker = root / "dotnet-ios-linux-llvm.json"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read matched LLVM provenance {marker}: {error}")
    if value.get("version") != "19.1.0-alpha.1.25461.1":
        fail("LLVM root does not contain the locked package version")
    if value.get("sourceCommit") != "2cfe42ea4a7fe364dcb197beca469af8edec0ad6":
        fail("LLVM root does not contain the locked source commit")
    observed = {package.get("id"): package.get("sha256") for package in value.get("packages", [])}
    if observed != EXPECTED_LLVM_PACKAGES:
        fail("LLVM root does not contain the locked package hashes")
    return value


def verify_compiler_provenance(
    compiler: Path, expected_commit: str, expected_mode: str, compiler_sha256: str
) -> dict:
    path = compiler.parent.parent / "aot-build-provenance.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read compiler build provenance {path}: {error}")
    if value.get("runtimeCommit") != expected_commit:
        fail("compiler build provenance has the wrong runtime commit")
    if value.get("targetTriple") != TARGET_TRIPLE:
        fail("compiler build provenance has the wrong target triple")
    if value.get("llvmMode") != expected_mode:
        fail("compiler build provenance does not match the requested LLVM mode")
    expected_state = "verified" if expected_mode == "llvm" else "feasibility"
    if value.get("schemaVersion") != 2 or value.get("state") != expected_state:
        fail("compiler build provenance does not have the expected cache state")
    if expected_mode == "llvm" and compiler_sha256 != EXPECTED_COMPILER_SHA256:
        fail("compiler hash does not match the reproducible LLVM-enabled output")
    if value.get("compilerSha256") != compiler_sha256:
        fail("compiler build provenance does not match compiler bytes")
    if expected_mode == "llvm":
        if value.get("matchedLlvmPackageVersion") != LLVM_PACKAGE_VERSION:
            fail("compiler build provenance has the wrong LLVM package version")
        if value.get("matchedLlvmSourceCommit") != LLVM_SOURCE_COMMIT:
            fail("compiler build provenance has the wrong LLVM source commit")
        if value.get("pathNormalization") != EXPECTED_PATH_NORMALIZATION:
            fail("compiler build provenance does not have canonical path normalization")
    elif value.get("pathNormalization") != {
        **EXPECTED_PATH_NORMALIZATION,
        "llvm": None,
    }:
        fail("compiler feasibility provenance does not have canonical path normalization")
    return value


def verify_compiler(compiler: Path, expected_commit: str, expected_mode: str) -> tuple[str, dict]:
    if not compiler.is_file() or compiler.is_symlink() or not os.access(compiler, os.X_OK):
        fail(f"compiler is not an executable regular file: {compiler}")
    if stat.S_IMODE(compiler.stat().st_mode) != EXPECTED_AOT_MODES["mono-aot-cross"]:
        fail("compiler executable mode does not match the locked output")
    compiler_sha256 = sha256(compiler)
    provenance = verify_compiler_provenance(
        compiler, expected_commit, expected_mode, compiler_sha256
    )
    return compiler_sha256, provenance


def verify_published_aot_pack(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        fail(f"AOT pack is not a real directory: {root}")
    sdk = root / "Sdk" / "Sdk.props"
    tools = root / "tools"
    provenance_path = root / "provenance.json"
    if (
        {path.name for path in root.iterdir()} != {"Sdk", "tools", "provenance.json"}
        or not sdk.is_file()
        or sdk.is_symlink()
        or tools.is_symlink()
        or not tools.is_dir()
        or provenance_path.is_symlink()
    ):
        fail(f"AOT pack has an incomplete layout: {root}")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read AOT pack provenance {provenance_path}: {error}")
    expected_sdk = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "packs"
        / AOT_PACK_ID
        / "Sdk"
        / "Sdk.props"
    )
    if not expected_sdk.is_file() or sha256(sdk) != sha256(expected_sdk):
        fail("AOT pack SDK props do not match this repository revision")
    observed = {path.name for path in tools.iterdir()}
    if observed != set(EXPECTED_AOT_FILES):
        fail("AOT pack tools do not match the locked payload inventory")
    for name, expected in EXPECTED_AOT_FILES.items():
        path = tools / name
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            fail(f"AOT pack payload does not match the locked output: {name}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_AOT_MODES[name]:
            fail(f"AOT pack payload mode does not match the locked output: {name}")
    version_output = execute([str(tools / "mono-aot-cross"), "--version"]).splitlines()
    expected_provenance = aot_pack_provenance(version_output)
    if provenance != expected_provenance:
        fail("AOT pack provenance does not match the canonical locked record")
    expected_bytes = (json.dumps(expected_provenance, indent=2) + "\n").encode("utf-8")
    if provenance_path.read_bytes() != expected_bytes:
        fail("AOT pack provenance bytes are not canonical")


def compiler_build_provenance() -> dict:
    return {
        "schemaVersion": 2,
        "state": "verified",
        "runtimeCommit": RUNTIME_COMMIT,
        "targetTriple": TARGET_TRIPLE,
        "llvmMode": "llvm",
        "matchedLlvmPackageVersion": LLVM_PACKAGE_VERSION,
        "matchedLlvmSourceCommit": LLVM_SOURCE_COMMIT,
        "compilerSha256": EXPECTED_COMPILER_SHA256,
        "pathNormalization": EXPECTED_PATH_NORMALIZATION,
    }


def aot_pack_provenance(version_output: list[str]) -> dict:
    return {
        "schemaVersion": 1,
        "packageId": AOT_PACK_ID,
        "runtimeCommit": RUNTIME_COMMIT,
        "runtimeVersion": RUNTIME_VERSION,
        "targetTriple": TARGET_TRIPLE,
        "llvmMode": "llvm",
        "matchedLlvmVersion": LLVM_PACKAGE_VERSION,
        "llvmPackages": [
            {"id": package_id, "version": LLVM_PACKAGE_VERSION, "sha256": digest}
            for package_id, digest in EXPECTED_LLVM_PACKAGES.items()
        ],
        "compilerBuild": compiler_build_provenance(),
        "files": EXPECTED_AOT_FILES,
        "compilerVersionOutput": version_output,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--verify-compiler-cache",
        action="store_true",
        help="verify an unpublished/reusable compiler cache record without packaging it",
    )
    parser.add_argument("--verify-aot-pack", type=Path)
    parser.add_argument("--runtime-commit")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--llvm-root", type=Path)
    modes.add_argument("--without-llvm", action="store_true")
    args = parser.parse_args()

    if args.verify_aot_pack:
        if any(
            value is not None
            for value in (args.compiler, args.output, args.runtime_commit, args.llvm_root)
        ) or args.without_llvm or args.verify_compiler_cache:
            parser.error("--verify-aot-pack does not accept compiler or packaging options")
        verify_published_aot_pack(args.verify_aot_pack.expanduser().absolute())
        print("aot_pack=verified")
        return
    if args.compiler is None or args.runtime_commit is None:
        parser.error("--compiler and --runtime-commit are required for compiler verification or packaging")
    if args.llvm_root is None and not args.without_llvm:
        parser.error("select --llvm-root or --without-llvm")
    if args.verify_compiler_cache and args.output is not None:
        parser.error("--verify-compiler-cache does not accept --output")
    if not args.verify_compiler_cache and args.output is None:
        parser.error("--output is required unless --verify-compiler-cache is used")

    expected_commit = RUNTIME_COMMIT
    if args.runtime_commit != expected_commit:
        fail(f"runtime commit must be {expected_commit}")
    compiler = args.compiler.expanduser().absolute()
    llvm_mode = "without-llvm"
    llvm_root: Path | None = None
    if args.llvm_root:
        llvm_mode = "llvm"
        llvm_root = args.llvm_root.expanduser().resolve()
        verify_llvm_root(llvm_root)
    compiler_sha256, _compiler_provenance = verify_compiler(compiler, expected_commit, llvm_mode)
    version = execute([str(compiler), "--version"])
    if "Architecture:  arm64" not in version:
        fail("compiler does not target arm64")
    if llvm_mode == "llvm" and not re.search(r"LLVM:\s+yes\(1900\)", version):
        fail("compiler does not report LLVM 19 support")
    if args.verify_compiler_cache:
        print(f"compiler_cache=verified\ncompiler_sha256={compiler_sha256}")
        return

    if args.without_llvm:
        fail("--without-llvm is a compiler feasibility mode and cannot create an installable AOT pack")

    assert args.output is not None
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        fail(f"output must not already exist: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aot-pack-", dir=output.parent) as temporary:
        stage = Path(temporary) / "pack"
        tools = stage / "tools"
        sdk = stage / "Sdk"
        tools.mkdir(parents=True)
        sdk.mkdir(parents=True)
        installed_compiler = tools / "mono-aot-cross"
        shutil.copy2(compiler, installed_compiler)

        hashes = {"mono-aot-cross": sha256(installed_compiler)}
        if llvm_root:
            for name in ("libc++.so.1", "libc++abi.so.1"):
                source = llvm_root / "lib" / name
                if not source.is_file():
                    fail(f"matched LLVM runtime library is missing: {source}")
                destination = tools / name
                shutil.copy2(source, destination)
                hashes[name] = sha256(destination)
            for name in ("llc", "opt"):
                source = llvm_root / "bin" / name
                if not source.is_file():
                    fail(f"matched LLVM tool is missing: {source}")
                destination = tools / name
                shutil.copy2(source, destination)
                tool_version = execute([str(destination), "--version"])
                if "LLVM version 19.1.0" not in tool_version:
                    fail(f"{name} is not matched LLVM 19.1.0")
                hashes[name] = sha256(destination)

            if hashes != EXPECTED_AOT_FILES:
                fail("AOT payload hashes do not match the verified compiler and locked LLVM outputs")

            # mono-aot-cross uses an $ORIGIN runtime path; prove the staged pack
            # is self-contained before publishing it.
            staged_version = execute([str(installed_compiler), "--version"])
            if not re.search(r"LLVM:\s+yes\(1900\)", staged_version):
                fail("staged compiler cannot load its matched LLVM dependencies")

        sdk_source = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "packs"
            / "DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64"
            / "Sdk"
            / "Sdk.props"
        )
        if not sdk_source.is_file():
            fail(f"AOT SDK source is missing: {sdk_source}")
        shutil.copy2(sdk_source, sdk / "Sdk.props")
        provenance = aot_pack_provenance(version.splitlines())
        (stage / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        verify_published_aot_pack(stage)
        try:
            publish_directory_noreplace(stage, output)
        except FileExistsError:
            fail(f"output appeared during publication; refusing to replace it: {output}")
    print(f"aot_pack={output}")
    print(f"llvm_mode={llvm_mode}")


if __name__ == "__main__":
    main()
