#!/usr/bin/env bash
# Private-SDK gate: exercise AotProbe through the unchanged HelloIos executable.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: build-hello-ios.sh --aot-pack <directory> --output <directory>
       [--dotnet <executable>] [--project <HelloIos.csproj>]

Requires DEVELOPER_DIR to select a populated synthetic Xcode. The AOT pack must
be LLVM-enabled. This script imports the host pack through MSBuild's standard
CustomBeforeDirectoryBuildProps hook; it never edits the application project.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repository="$(cd "$script_dir/../.." && pwd -P)"
aot_pack=""
output=""
if [[ -n "${DOTNET_ROOT:-}" ]]; then
    dotnet="$DOTNET_ROOT/dotnet"
else
    dotnet="$(command -v dotnet || true)"
fi
project="$repository/tests/HelloIos/HelloIos.csproj"
while (($#)); do
    case "$1" in
        --aot-pack) aot_pack="${2:?missing value for --aot-pack}"; shift 2 ;;
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet) dotnet="${2:?missing value for --dotnet}"; shift 2 ;;
        --project) project="${2:?missing value for --project}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$aot_pack" && -n "$output" ]] || { usage >&2; exit 2; }
[[ -n "${DEVELOPER_DIR:-}" ]] || { printf 'DEVELOPER_DIR is required\n' >&2; exit 1; }
[[ -x "$dotnet" ]] || { printf 'dotnet is not executable: %s\n' "$dotnet" >&2; exit 1; }
command -v llvm-readobj >/dev/null || { printf 'llvm-readobj is required\n' >&2; exit 1; }
command -v llvm-objdump >/dev/null || { printf 'llvm-objdump is required\n' >&2; exit 1; }
command -v llvm-nm >/dev/null || { printf 'llvm-nm is required\n' >&2; exit 1; }

aot_pack="$(realpath "$aot_pack")"
if [[ -e "$output" ]]; then
    [[ -d "$output" && -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
        printf 'output directory must be empty: %s\n' "$output" >&2
        exit 1
    }
else
    mkdir -p "$output"
fi
output="$(realpath "$output")"
project="$(realpath "$project")"
props="$aot_pack/Sdk/Sdk.props"
compiler="$aot_pack/tools/mono-aot-cross"
compiler_item="$aot_pack/Sdk/../tools/mono-aot-cross"
provenance="$aot_pack/provenance.json"
integration_props="$repository/src/msbuild/DotnetIosLinux.Integration.props"
[[ -f "$props" && -x "$compiler" && -f "$provenance" && -f "$integration_props" ]] || {
    printf 'incomplete AOT pack: %s\n' "$aot_pack" >&2
    exit 1
}
python3 - "$provenance" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
if value.get("llvmMode") != "llvm":
    raise SystemExit("HelloIos integration requires an LLVM-enabled AOT pack")
if value.get("runtimeCommit") != "4271d88e0aebf3d04f188f1334c2220d80555ef6":
    raise SystemExit("AOT pack runtime revision does not match the locked device runtime")
PY

log="$output/hello-ios-build.log"
binlog="$output/hello-ios-build.binlog"
packages="$output/packages"
msbuild_artifacts="$output/msbuild"
"$dotnet" restore "$project" \
    --packages "$packages" \
    --property:RuntimeIdentifier=ios-arm64 \
    --property:DotnetIosLinuxArtifactsRoot="$msbuild_artifacts" \
    --property:DotnetIosLinuxAotPack="$aot_pack" \
    --property:CustomBeforeDirectoryBuildProps="$integration_props"
"$dotnet" build "$project" \
    --no-restore \
    --configuration Release \
    --framework net10.0-ios \
    --runtime ios-arm64 \
    --property:EnableCodeSigning=false \
    --property:NoDSymUtil=true \
    --property:NoSymbolStrip=true \
    --property:RestorePackagesPath="$packages" \
    --property:DotnetIosLinuxArtifactsRoot="$msbuild_artifacts" \
    --property:DotnetIosLinuxAotPack="$aot_pack" \
    --property:CustomBeforeDirectoryBuildProps="$integration_props" \
    --binaryLogger:"$binlog" \
    --verbosity:diagnostic 2>&1 | tee "$log"

if ! grep -F "$compiler" "$log" >/dev/null && ! grep -F "$compiler_item" "$log" >/dev/null; then
    printf 'build log does not show execution of the selected AOT compiler\n' >&2
    exit 1
fi
grep -F 'AotProbe.dll' "$log" | grep -F -- '--aot=' >/dev/null || {
    printf 'build log does not show AotProbe in the AOT stage\n' >&2
    exit 1
}

mono_object="$(find "$msbuild_artifacts/obj/HelloIos" -type f -name 'AotProbe.dll.o' -print -quit)"
llvm_object="$(find "$msbuild_artifacts/obj/HelloIos" -type f -name 'AotProbe.dll.llvm.o' -print -quit)"
[[ -n "$mono_object" && -n "$llvm_object" ]] || {
    printf 'AotProbe did not produce both Mono and LLVM native objects\n' >&2
    exit 1
}
grep -F "$mono_object" "$log" >/dev/null || { printf 'native-link log does not consume the AotProbe Mono object\n' >&2; exit 1; }
grep -F "$llvm_object" "$log" >/dev/null || { printf 'native-link log does not consume the AotProbe LLVM object\n' >&2; exit 1; }
llvm-readobj --file-headers "$mono_object" "$llvm_object" > "$output/aot-probe-objects.txt"
[[ "$(grep -c 'Format: Mach-O arm64' "$output/aot-probe-objects.txt")" == 2 ]] || {
    printf 'AotProbe native outputs are not both Mach-O arm64 objects\n' >&2
    exit 1
}
callback_wrapper='wrapper_native_to_managed_AotProbe_DeviceAotProbe_Compare_intptr_intptr'
if ! llvm-nm "$mono_object" | grep -F "$callback_wrapper" > "$output/aot-probe-callback.txt"; then
    printf 'AotProbe native object does not contain the required native-to-managed callback wrapper\n' >&2
    exit 1
fi

app="$msbuild_artifacts/bin/HelloIos/Release/net10.0-ios/ios-arm64/HelloIos.app"
[[ -f "$app/HelloIos" ]] || { printf 'unsigned HelloIos.app was not produced at the canonical output path\n' >&2; exit 1; }

llvm-readobj --file-headers --macho-version-min "$app/HelloIos" > "$output/hello-ios-mach-o.txt"
grep -F 'Format: Mach-O arm64' "$output/hello-ios-mach-o.txt" >/dev/null || {
    printf 'HelloIos executable is not Mach-O arm64\n' >&2
    exit 1
}
llvm-objdump --macho --private-headers "$app/HelloIos" > "$output/hello-ios-load-commands.txt"
if grep -F 'LC_CODE_SIGNATURE' "$output/hello-ios-load-commands.txt" >/dev/null; then
    printf 'HelloIos executable unexpectedly contains a code-signature load command\n' >&2
    exit 1
fi
if ! llvm-nm "$app/HelloIos" | grep -F "$callback_wrapper" >> "$output/aot-probe-callback.txt"; then
    printf 'HelloIos executable does not contain the required AotProbe callback wrapper\n' >&2
    exit 1
fi

printf 'hello_ios_app=%s\n' "$app"
printf 'binlog=%s\n' "$binlog"
printf 'inspection=%s\n' "$output/hello-ios-mach-o.txt"
