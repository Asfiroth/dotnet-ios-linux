#!/usr/bin/env python3
"""Smoke-test a Linux-hosted iOS Mono AOT compiler and inspect its object."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> "None":
    print(f"AOT smoke test failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command), file=sys.stderr)
    result = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        fail(f"command exited with {result.returncode}: {command[0]}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_elf_x64(path: Path) -> None:
    try:
        header = path.read_bytes()[:20]
    except OSError as error:
        fail(str(error))
    if len(header) < 20 or header[:4] != b"\x7fELF":
        fail(f"compiler is not ELF: {path}")
    if header[4] != 2 or header[5] != 1:
        fail("compiler must be a little-endian ELF64 executable")
    if struct.unpack_from("<H", header, 18)[0] != 62:
        fail("compiler ELF machine is not x86-64")


def verify_macho_arm64(path: Path) -> None:
    try:
        header = path.read_bytes()[:32]
    except OSError as error:
        fail(str(error))
    if len(header) < 32 or header[:4] != b"\xcf\xfa\xed\xfe":
        fail(f"output is not a little-endian Mach-O 64 object: {path}")
    cpu_type, file_type = struct.unpack_from("<II", header, 4)[0], struct.unpack_from("<I", header, 12)[0]
    if cpu_type != 0x0100000C:
        fail(f"Mach-O CPU type is not arm64: 0x{cpu_type:08x}")
    if file_type != 1:
        fail(f"Mach-O file type is not relocatable: {file_type}")


def runtime_paths(pack: Path) -> list[Path]:
    root = pack / "runtimes" / "ios-arm64"
    paths = [root / "native", root / "lib" / "net10.0"]
    if not (paths[0] / "System.Private.CoreLib.dll").is_file():
        fail(f"device runtime pack lacks System.Private.CoreLib.dll: {pack}")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--assembly", type=Path, required=True)
    parser.add_argument("--runtime-pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clang", default="clang")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--llvm-root", type=Path)
    mode.add_argument("--without-llvm", action="store_true")
    args = parser.parse_args()

    compiler = args.compiler.expanduser().resolve()
    assembly = args.assembly.expanduser().resolve()
    runtime_pack = args.runtime_pack.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        fail(f"compiler is not executable: {compiler}")
    if not assembly.is_file():
        fail(f"assembly does not exist: {assembly}")
    verify_elf_x64(compiler)

    version = run([str(compiler), "--version"]).stdout
    if "Architecture:  arm64" not in version:
        fail("compiler does not report the arm64 target architecture")

    llvm_root: Path | None = None
    if args.llvm_root:
        llvm_root = args.llvm_root.expanduser().resolve()
        if not re.search(r"LLVM:\s+yes\(1900\)", version):
            fail("--llvm-root was requested but the compiler lacks LLVM 19 support")
        for tool in ("llc", "opt"):
            candidate = llvm_root / "bin" / tool
            if not os.access(candidate, os.X_OK):
                fail(f"matched LLVM tool is missing: {candidate}")
            tool_version = run([str(candidate), "--version"]).stdout
            if "LLVM version 19.1.0" not in tool_version:
                fail(f"{tool} is not matched LLVM 19.1.0")

    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        fail(f"output directory must be empty: {output}")
    stem = assembly.name
    assembly_output = output / f"{stem}.s"
    data_output = output / f"{stem}.aotdata.arm64"
    object_output = output / f"{stem}.o"
    llvm_output = output / f"{stem}.llvm.o"

    aot = [
        "mtriple=arm64-ios",
        f"data-outfile={data_output}",
        "static",
        "asmonly",
        "direct-icalls",
        "full",
        "nodebug",
        "dwarfdebug",
        "direct-pinvoke",
        f"outfile={assembly_output}",
    ]
    command = [str(compiler), "--debug"]
    if llvm_root:
        command.append("--llvm")
        aot.extend([f"llvm-path={llvm_root / 'bin'}/", f"llvm-outfile={llvm_output}"])
    command.extend(["-O=gsharedvt", "--aot=" + ",".join(aot), str(assembly)])

    environment = os.environ.copy()
    environment["MONO_PATH"] = os.pathsep.join(str(path) for path in runtime_paths(runtime_pack))
    compiler_result = run(command, environment=environment)
    compiled = re.search(r"Compiled:\s+(\d+)/(\d+)", compiler_result.stdout)
    if not compiled or compiled.group(1) != compiled.group(2):
        fail("compiler did not report that every discovered method was compiled")
    aot_id = re.search(r"AOTID\s+([0-9A-F-]+)", compiler_result.stdout)
    if not aot_id:
        fail("compiler did not report an AOTID")
    if not assembly_output.is_file() or not data_output.is_file():
        fail("compiler did not produce assembly and AOT data outputs")
    if llvm_root:
        if not llvm_output.is_file():
            fail("LLVM mode did not produce its native object")
        verify_macho_arm64(llvm_output)

    clang = shutil.which(args.clang)
    if not clang:
        fail(f"clang executable not found: {args.clang}")
    run([clang, "--target=arm64-apple-ios15.0", "-c", str(assembly_output), "-o", str(object_output)])
    verify_macho_arm64(object_output)

    evidence = {
        "schemaVersion": 1,
        "scope": "compiler-smoke-not-M4-acceptance",
        "llvmMode": "llvm" if llvm_root else "without-llvm",
        "compiler": str(compiler),
        "compilerSha256": sha256(compiler),
        "inputAssembly": str(assembly),
        "inputAssemblySha256": sha256(assembly),
        "assemblyOutputSha256": sha256(assembly_output),
        "aotDataSha256": sha256(data_output),
        "object": str(object_output),
        "objectSha256": sha256(object_output),
        "objectFormat": "Mach-O 64-bit arm64 relocatable",
        "compiledMethods": int(compiled.group(1)),
        "aotId": aot_id.group(1),
    }
    if llvm_root:
        evidence.update(
            {
                "llvmObject": str(llvm_output),
                "llvmObjectSha256": sha256(llvm_output),
                "llvmObjectFormat": "Mach-O 64-bit arm64 relocatable",
            }
        )
    (output / "smoke-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"verified_object={object_output}")


if __name__ == "__main__":
    main()
