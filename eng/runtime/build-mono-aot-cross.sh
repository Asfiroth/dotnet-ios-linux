#!/usr/bin/env bash
set -euo pipefail

readonly expected_revision="4271d88e0aebf3d04f188f1334c2220d80555ef6"
readonly llvm_config_version="19.1.0-mono"
readonly llvm_package_version="19.1.0-alpha.1.25461.1"
readonly llvm_source_commit="2cfe42ea4a7fe364dcb197beca469af8edec0ad6"
readonly target_triple="aarch64-apple-darwin10"
readonly canonical_source_prefix="/usr/src/dotnet-runtime"
readonly canonical_build_prefix="/usr/build/dotnet-ios-linux/mono-aot"
readonly canonical_install_prefix="/usr/lib/dotnet-ios-linux/mono-aot"
readonly canonical_llvm_prefix="/usr/lib/dotnet-ios-linux/llvm"
readonly expected_compiler_sha256="55dcaa7bf9dd71206ff8f69a26e0eacd79af7146b2a0f7a1143c3d9e0d3267e7"

usage() {
    cat <<'EOF'
Usage: build-mono-aot-cross.sh --source <runtime> --build <directory>
       --install <directory> (--llvm-root <directory> | --without-llvm)
       [--jobs <count>]

The non-LLVM mode is an explicit feasibility mode and is not LLVM parity. The
default is therefore to fail until either --llvm-root or --without-llvm is given.
The runtime patch must already be applied with apply-patches.sh.
EOF
}

source_root=""
build_root=""
install_root=""
llvm_root=""
without_llvm=false
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1')"
while (($#)); do
    case "$1" in
        --source) source_root="${2:?missing value for --source}"; shift 2 ;;
        --build) build_root="${2:?missing value for --build}"; shift 2 ;;
        --install) install_root="${2:?missing value for --install}"; shift 2 ;;
        --llvm-root) llvm_root="${2:?missing value for --llvm-root}"; shift 2 ;;
        --without-llvm) without_llvm=true; shift ;;
        --jobs) jobs="${2:?missing value for --jobs}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ -n "$source_root" && -n "$build_root" && -n "$install_root" ]] || { usage >&2; exit 2; }
if [[ -n "$llvm_root" && "$without_llvm" == true ]]; then
    printf -- '--llvm-root and --without-llvm are mutually exclusive\n' >&2
    exit 2
fi
if [[ -z "$llvm_root" && "$without_llvm" == false ]]; then
    printf 'select --llvm-root for parity or explicitly acknowledge --without-llvm feasibility mode\n' >&2
    exit 2
fi

for command in cmake ninja git python3 clang clang++; do
    command -v "$command" >/dev/null || { printf 'missing build dependency: %s\n' "$command" >&2; exit 1; }
done

source_root="$(realpath "$source_root")"
mkdir -p "$build_root" "$install_root"
build_root="$(realpath "$build_root")"
install_root="$(realpath "$install_root")"
[[ "$source_root" != "$build_root" && "$source_root" != "$install_root" && "$build_root" != "$install_root" ]] || {
    printf 'source, build, and install roots must be distinct\n' >&2
    exit 2
}
actual_revision="$(git -C "$source_root" rev-parse HEAD)"
[[ "$actual_revision" == "$expected_revision" ]] || {
    printf 'runtime revision mismatch: expected %s, got %s\n' "$expected_revision" "$actual_revision" >&2
    exit 1
}

runtime_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
"$runtime_script_dir/verify-source.sh" --source "$source_root" --state patched >/dev/null

version_directory="$source_root/artifacts/obj"
mkdir -p "$version_directory"
cat > "$version_directory/_version.c" <<EOF
static char sccsid[] __attribute__((used)) = "@(#)Version 10.0.12.0 @Commit: $actual_revision";
EOF
cat > "$version_directory/runtime_version.h" <<'EOF'
#define RuntimeAssemblyMajorVersion 10
#define RuntimeAssemblyMinorVersion 0
#define RuntimeFileMajorVersion 10
#define RuntimeFileMinorVersion 0
#define RuntimeFileBuildVersion 12
#define RuntimeFileRevisionVersion 0
#define RuntimeProductMajorVersion 10
#define RuntimeProductMinorVersion 0
#define RuntimeProductPatchVersion 12
#define RuntimeProductVersion 10.0.12
EOF

prefix_map_flags="-ffile-prefix-map=$source_root=$canonical_source_prefix -ffile-prefix-map=$build_root=$canonical_build_prefix -ffile-prefix-map=$install_root=$canonical_install_prefix"

cmake_arguments=(
    -S "$source_root/src/mono"
    -B "$build_root"
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_C_COMPILER=clang
    -DCMAKE_CXX_COMPILER=clang++
    "-DCMAKE_C_FLAGS=$prefix_map_flags"
    "-DCMAKE_CXX_FLAGS=$prefix_map_flags"
    "-DCMAKE_INSTALL_PREFIX=$install_root"
    "-DAOT_TARGET_TRIPLE=$target_triple"
    "-DAOT_OFFSETS_FILE=$source_root/src/mono/mono/offsets/aarch64-apple-darwin10.h"
    -DMONO_CROSS_COMPILE_EXECUTABLE_NAME=1
    -DENABLE_MINIMAL=
    -DENABLE_ICALL_SYMBOL_MAP=1
    -DDISABLE_SHARED_LIBS=1
    -DDISABLE_LIBS=1
    -DAOT_COMPONENTS=1
    -DSTATIC_COMPONENTS=1
    -DGC_SUSPEND=hybrid
    -DMONO_SET_RPATH_ORIGIN=true
)

mode="without-llvm"
if [[ -n "$llvm_root" ]]; then
    llvm_root="$(realpath "$llvm_root")"
    llvm_config="$llvm_root/bin/llvm-config"
    [[ -x "$llvm_config" ]] || { printf 'missing LLVM config: %s\n' "$llvm_config" >&2; exit 1; }
    llvm_marker="$llvm_root/dotnet-ios-linux-llvm.json"
    [[ -f "$llvm_marker" ]] || { printf 'matched LLVM provenance marker is missing: %s\n' "$llvm_marker" >&2; exit 1; }
    python3 - "$llvm_marker" "$llvm_package_version" "$llvm_source_commit" <<'PY'
import json
import sys
from pathlib import Path

marker, expected_version, expected_commit = sys.argv[1:]
try:
    value = json.loads(Path(marker).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    raise SystemExit(f"invalid matched LLVM provenance marker: {error}")
if value.get("version") != expected_version:
    raise SystemExit("matched LLVM package version does not match the lock")
if value.get("sourceCommit") != expected_commit:
    raise SystemExit("matched LLVM source commit does not match the lock")
expected = {
    "runtime.linux-x64.Microsoft.NETCore.Runtime.Mono.LLVM.Sdk": "0ab321b6a5b4a7cb159b12c5c1e14daa034a50bfae3afecd0b8737649b4811cc",
    "runtime.linux-x64.Microsoft.NETCore.Runtime.Mono.LLVM.Tools": "1f2783be29b44c9d53b1f11048de8f123089fafc475a30f6b6494204b842d043",
}
observed = {package.get("id"): package.get("sha256") for package in value.get("packages", [])}
if observed != expected:
    raise SystemExit("matched LLVM package hashes do not match the lock")
PY
    actual_llvm_version="$($llvm_config --version)"
    [[ "$actual_llvm_version" == "$llvm_config_version" ]] || {
        printf 'matched LLVM %s is required, got %s\n' "$llvm_config_version" "$actual_llvm_version" >&2
        exit 1
    }
    cmake_arguments+=(
        "-DENABLE_LLVM=1"
        "-DLLVM_PREFIX=$llvm_root"
        "-DCMAKE_C_FLAGS=$prefix_map_flags -ffile-prefix-map=$llvm_root=$canonical_llvm_prefix"
        "-DCMAKE_CXX_FLAGS=-I$llvm_root/include/c++/v1 -L$llvm_root/lib -stdlib=libc++ $prefix_map_flags -ffile-prefix-map=$llvm_root=$canonical_llvm_prefix"
    )
    mode="llvm"
else
    cmake_arguments+=("-DENABLE_LLVM=0")
fi

export LC_ALL=C
export TZ=UTC
export SOURCE_DATE_EPOCH=0
cmake "${cmake_arguments[@]}"
cmake --build "$build_root" --target install --parallel "$jobs"

compiler="$install_root/bin/mono-aot-cross"
[[ -x "$compiler" ]] || { printf 'compiler was not installed: %s\n' "$compiler" >&2; exit 1; }
if [[ "$mode" == llvm ]]; then
    for library in libc++.so.1 libc++abi.so.1; do
        source_library="$llvm_root/lib/$library"
        [[ -f "$source_library" ]] || {
            printf 'matched LLVM runtime library is missing: %s\n' "$source_library" >&2
            exit 1
        }
        cp -f "$source_library" "$install_root/bin/$library"
    done
fi
read -r magic < <(od -An -tx1 -N4 "$compiler" | tr -d ' ')
[[ "$magic" == "7f454c46" ]] || { printf 'compiler is not an ELF executable\n' >&2; exit 1; }
compiler_sha256="$(sha256sum -b "$compiler" | cut -d' ' -f1)"
if [[ "$mode" == llvm ]]; then
    [[ "$compiler_sha256" == "$expected_compiler_sha256" ]] || {
        printf 'compiler output hash mismatch: expected %s, got %s\n' \
            "$expected_compiler_sha256" "$compiler_sha256" >&2
        exit 1
    }
fi

version_output="$($compiler --version)"
if [[ "$mode" == llvm ]]; then
    grep -Eq 'LLVM:[[:space:]]+yes\(1900\)' <<<"$version_output" || {
        printf 'compiler does not report matched LLVM 19 support\n' >&2
        exit 1
    }
fi

python3 - "$install_root/aot-build-provenance.json" "$actual_revision" "$mode" "$target_triple" "$llvm_package_version" "$llvm_source_commit" "$compiler_sha256" "$canonical_source_prefix" "$canonical_build_prefix" "$canonical_install_prefix" "$canonical_llvm_prefix" <<'PY'
import json
import sys
from pathlib import Path

(
    path,
    revision,
    mode,
    triple,
    llvm,
    llvm_commit,
    compiler_sha256,
    source_prefix,
    build_prefix,
    install_prefix,
    llvm_prefix,
) = sys.argv[1:]
Path(path).write_text(json.dumps({
    "schemaVersion": 2,
    "state": "verified" if mode == "llvm" else "feasibility",
    "runtimeCommit": revision,
    "targetTriple": triple,
    "llvmMode": mode,
    "matchedLlvmPackageVersion": llvm if mode == "llvm" else None,
    "matchedLlvmSourceCommit": llvm_commit if mode == "llvm" else None,
    "compilerSha256": compiler_sha256,
    "pathNormalization": {
        "source": source_prefix,
        "build": build_prefix,
        "install": install_prefix,
        "llvm": llvm_prefix if mode == "llvm" else None,
    },
}, indent=2) + "\n", encoding="utf-8")
PY

printf '%s\n' "$version_output"
printf 'compiler=%s\n' "$compiler"
printf 'llvm_mode=%s\n' "$mode"
