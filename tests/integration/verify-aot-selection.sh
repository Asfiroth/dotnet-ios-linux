#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: verify-aot-selection.sh --aot-pack <directory> --output <directory>
       [--dotnet <executable>]

Runs the pinned macios _FindAotCompiler target and verifies that its normal
MonoAotCrossCompiler input selects the project-owned ios-arm64 host compiler.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repository="$(cd "$script_dir/../.." && pwd -P)"
if [[ -n "${DOTNET_ROOT:-}" ]]; then
    dotnet="$DOTNET_ROOT/dotnet"
else
    dotnet="$(command -v dotnet || true)"
fi
aot_pack=""
output=""
while (($#)); do
    case "$1" in
        --aot-pack) aot_pack="${2:?missing value for --aot-pack}"; shift 2 ;;
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet) dotnet="${2:?missing value for --dotnet}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$aot_pack" && -n "$output" ]] || { usage >&2; exit 2; }
[[ -x "$dotnet" ]] || { printf 'dotnet is not executable: %s\n' "$dotnet" >&2; exit 1; }
aot_pack="$(realpath "$aot_pack")"
compiler="$aot_pack/tools/mono-aot-cross"
[[ -x "$compiler" && -f "$aot_pack/Sdk/Sdk.props" ]] || {
    printf 'incomplete AOT pack: %s\n' "$aot_pack" >&2
    exit 1
}
if [[ -e "$output" ]]; then
    [[ -d "$output" && -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
        printf 'output directory must be empty: %s\n' "$output" >&2
        exit 1
    }
else
    mkdir -p "$output"
fi
output="$(realpath "$output")"
packages="$output/packages"
log="$output/find-aot-compiler.log"
msbuild_artifacts="$output/msbuild"
integration_props="$repository/src/msbuild/DotnetIosLinux.Integration.props"
project="$repository/tests/HelloIos/HelloIos.csproj"

"$dotnet" restore "$project" \
    --packages "$packages" \
    --property:Configuration=Release \
    --property:RuntimeIdentifier=ios-arm64 \
    --property:DotnetIosLinuxArtifactsRoot="$msbuild_artifacts" \
    --property:DotnetIosLinuxAotPack="$aot_pack" \
    --property:CustomBeforeDirectoryBuildProps="$integration_props"

"$dotnet" msbuild "$project" \
    -target:_FindAotCompiler \
    -property:Configuration=Release \
    -property:RuntimeIdentifier=ios-arm64 \
    -property:RestorePackagesPath="$packages" \
    -property:DotnetIosLinuxArtifactsRoot="$msbuild_artifacts" \
    -property:DotnetIosLinuxAotPack="$aot_pack" \
    -property:CustomBeforeDirectoryBuildProps="$integration_props" \
    -verbosity:diagnostic > "$log" 2>&1

selected="$aot_pack/Sdk/../tools/mono-aot-cross"
grep -F "Output Property: _XamarinAOTCompiler=$selected" "$log" >/dev/null || {
    printf 'macios did not select the project-owned MonoAotCrossCompiler item\n' >&2
    exit 1
}

printf 'aot_compiler=%s\n' "$compiler"
printf 'selection_log=%s\n' "$log"
