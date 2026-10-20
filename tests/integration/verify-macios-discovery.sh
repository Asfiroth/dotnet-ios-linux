#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: verify-macios-discovery.sh --output <log-file> [--dotnet <executable>]

Requires DEVELOPER_DIR. Executes the pinned macios _DetectSdkLocations target
and verifies that it resolves the synthetic Xcode and versioned iPhoneOS SDK.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repository="$(cd "$script_dir/../.." && pwd -P)"
if [[ -n "${DOTNET_ROOT:-}" ]]; then
    dotnet="$DOTNET_ROOT/dotnet"
else
    dotnet="$(command -v dotnet || true)"
fi
output=""
while (($#)); do
    case "$1" in
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet) dotnet="${2:?missing value for --dotnet}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$output" ]] || { usage >&2; exit 2; }
[[ -n "${DEVELOPER_DIR:-}" ]] || { printf 'DEVELOPER_DIR is required\n' >&2; exit 1; }
[[ -x "$dotnet" ]] || { printf 'dotnet is not executable: %s\n' "$dotnet" >&2; exit 1; }
[[ ! -e "$output" ]] || { printf 'output log already exists: %s\n' "$output" >&2; exit 1; }
mkdir -p "$(dirname "$output")"
artifacts="$(realpath -m "$output").msbuild"
[[ ! -e "$artifacts" ]] || { printf 'MSBuild output already exists: %s\n' "$artifacts" >&2; exit 1; }

"$dotnet" msbuild "$repository/tests/HelloIos/HelloIos.csproj" \
    -target:_DetectSdkLocations \
    -property:Configuration=Release \
    -property:RuntimeIdentifier=ios-arm64 \
    -property:EnableCodeSigning=false \
    -property:DotnetIosLinuxArtifactsRoot="$artifacts" \
    -property:CustomBeforeDirectoryBuildProps="$repository/src/msbuild/DotnetIosLinux.Integration.props" \
    -verbosity:diagnostic > "$output" 2>&1

expected_xcode="$(realpath "$DEVELOPER_DIR/../..")"
grep -F "XcodeLocation = $expected_xcode" "$output" >/dev/null
grep -F "DeveloperRoot: $(realpath "$DEVELOPER_DIR")" "$output" >/dev/null
grep -E 'Output Property: _SdkRoot=.*/Platforms/iPhoneOS\.platform/Developer/SDKs/iPhoneOS[0-9.]+\.sdk' "$output" >/dev/null
grep -E 'Output Property: _SdkVersion=[0-9]+\.[0-9]+' "$output" >/dev/null

printf 'macios_discovery_log=%s\n' "$(realpath "$output")"
