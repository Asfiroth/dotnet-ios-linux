#!/usr/bin/env python3
"""Create the project-owned pack layout consumed by MonoAotCrossCompiler."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
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
EXPECTED_AOT_FILES = {
    "mono-aot-cross": "c85a0aad43764e9d17077f23faf6e638e79b47b315263116b2350038d977866d",
    "libc++.so.1": "9233ff02fd30aba191b9a936eeb351d78682616fce2db29c08bbd1c3a581ca9c",
    "libc++abi.so.1": "80bef63649f325c706768ecf9f29598429c712721b58c990117e86c826dbe534",
    "llc": "8b53230508c94cda4ee541be27e7f9957becd32d5e2522f3be1bbe4c61079662",
    "opt": "99464211ff7ba2175408c935d17ca375a99c564e88b476581e9b3d59af81d011",
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


def verify_compiler_provenance(compiler: Path, expected_commit: str, expected_mode: str) -> dict:
    path = compiler.parent.parent / "aot-build-provenance.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read compiler build provenance {path}: {error}")
    if value.get("runtimeCommit") != expected_commit:
        fail("compiler build provenance has the wrong runtime commit")
    if value.get("targetTriple") != "aarch64-apple-darwin10":
        fail("compiler build provenance has the wrong target triple")
    if value.get("llvmMode") != expected_mode:
        fail("compiler build provenance does not match the requested LLVM mode")
    if value.get("compilerSha256") not in {None, EXPECTED_AOT_FILES["mono-aot-cross"]}:
        fail("compiler build provenance has the wrong compiler hash")
    if expected_mode == "llvm":
        if value.get("matchedLlvmPackageVersion") != "19.1.0-alpha.1.25461.1":
            fail("compiler build provenance has the wrong LLVM package version")
        if value.get("matchedLlvmSourceCommit") != "2cfe42ea4a7fe364dcb197beca469af8edec0ad6":
            fail("compiler build provenance has the wrong LLVM source commit")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-commit", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--llvm-root", type=Path)
    modes.add_argument("--without-llvm", action="store_true")
    args = parser.parse_args()

    expected_commit = "4271d88e0aebf3d04f188f1334c2220d80555ef6"
    if args.runtime_commit != expected_commit:
        fail(f"runtime commit must be {expected_commit}")
    compiler = args.compiler.expanduser().resolve()
    output = args.output.expanduser().absolute()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        fail(f"compiler is not executable: {compiler}")
    if sha256(compiler) != EXPECTED_AOT_FILES["mono-aot-cross"]:
        fail("compiler hash does not match the proven LLVM-enabled output")
    if output.exists() or output.is_symlink():
        fail(f"output must not already exist: {output}")

    version = execute([str(compiler), "--version"])
    if "Architecture:  arm64" not in version:
        fail("compiler does not target arm64")
    llvm_mode = "without-llvm"
    llvm_root: Path | None = None
    llvm_provenance: dict | None = None
    if args.llvm_root:
        if not re.search(r"LLVM:\s+yes\(1900\)", version):
            fail("compiler does not report LLVM 19 support")
        llvm_mode = "llvm"
        llvm_root = args.llvm_root.expanduser().resolve()
        llvm_provenance = verify_llvm_root(llvm_root)
    compiler_provenance = verify_compiler_provenance(compiler, expected_commit, llvm_mode)

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
                fail("AOT payload hashes do not match the locked compiler and LLVM outputs")

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
        provenance = {
            "schemaVersion": 1,
            "packageId": "DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64",
            "runtimeCommit": expected_commit,
            "runtimeVersion": "10.0.12",
            "targetTriple": "aarch64-apple-darwin10",
            "llvmMode": llvm_mode,
            "matchedLlvmVersion": "19.1.0-alpha.1.25461.1",
            "llvmPackages": llvm_provenance.get("packages") if llvm_provenance else [],
            "compilerBuild": compiler_provenance,
            "files": hashes,
            "compilerVersionOutput": version.splitlines(),
        }
        (stage / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        try:
            publish_directory_noreplace(stage, output)
        except FileExistsError:
            fail(f"output appeared during publication; refusing to replace it: {output}")
    print(f"aot_pack={output}")
    print(f"llvm_mode={llvm_mode}")


if __name__ == "__main__":
    main()
