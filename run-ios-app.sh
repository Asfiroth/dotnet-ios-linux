#!/usr/bin/env bash
# Build, sign, install, and launch one net10.0-ios application on a paired USB device.
set -Eeuo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
    cat <<'EOF'
Usage:
  ./run-ios-app.sh --project /path/to/App.csproj \
    --signing-config /private/path/signing.json \
    --confirm-install --confirm-launch

Builds the selected project for Release/net10.0-ios/ios-arm64 without code
signing, signs a private copy with the supplied development assets, installs it
on the sole paired USB iPhone, and launches it. This command does not attach a
debugger. A personalized Developer Disk Image must already be mounted.

Required options:
  --project PATH          Application project containing the net10.0-ios target.
  --signing-config PATH   Mode-600 private signing configuration.
  --confirm-install       Acknowledge that the command will modify the device.
  --confirm-launch        Acknowledge that the command will start the application.
EOF
}

project=""
signing_config=""
confirm_install=false
confirm_launch=false
while (($#)); do
    case "$1" in
        --project) project="${2:?missing value for --project}"; shift 2 ;;
        --signing-config) signing_config="${2:?missing value for --signing-config}"; shift 2 ;;
        --confirm-install) confirm_install=true; shift ;;
        --confirm-launch) confirm_launch=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -n "$project" ]] || { printf -- '--project is required\n' >&2; usage >&2; exit 2; }
[[ -n "$signing_config" ]] || { printf -- '--signing-config is required\n' >&2; usage >&2; exit 2; }
[[ "$confirm_install" == true ]] || {
    printf -- '--confirm-install is required because this command modifies the connected device\n' >&2
    exit 2
}
[[ "$confirm_launch" == true ]] || {
    printf -- '--confirm-launch is required because this command starts the application\n' >&2
    exit 2
}

[[ -n "${HOME:-}" && "$HOME" == /* ]] || { printf 'HOME must be an absolute path\n' >&2; exit 1; }
if [[ -n "${XDG_DATA_HOME:-}" && "$XDG_DATA_HOME" != /* ]]; then
    printf 'XDG_DATA_HOME must be an absolute path\n' >&2
    exit 1
fi
if [[ -n "${XDG_CACHE_HOME:-}" && "$XDG_CACHE_HOME" != /* ]]; then
    printf 'XDG_CACHE_HOME must be an absolute path\n' >&2
    exit 1
fi

readonly data_root="${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux"
readonly cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux"
readonly activation="$data_root/activate"
readonly receipt="$data_root/receipts/install.json"
readonly integration_props="$script_dir/src/msbuild/DotnetIosLinux.Integration.props"
readonly signing_tool="$script_dir/eng/toolchain/sign-ios-app.py"
readonly device_tool="$script_dir/eng/toolchain/device-ios-app.py"

[[ -d "$data_root" && ! -L "$data_root" ]] || {
    printf 'installed data root is missing or unsafe: %s\n' "$data_root" >&2
    exit 1
}
[[ -d "$cache_root" && ! -L "$cache_root" ]] || {
    printf 'installed cache root is missing or unsafe: %s\n' "$cache_root" >&2
    exit 1
}
[[ -f "$activation" && ! -L "$activation" ]] || {
    printf 'installed activation file is missing: %s\n' "$activation" >&2
    printf 'complete ./install.sh install before running an application\n' >&2
    exit 1
}
[[ -f "$receipt" && -f "$integration_props" && -f "$signing_tool" && -f "$device_tool" ]] || {
    printf 'dotnet-ios-linux installation or repository tooling is incomplete\n' >&2
    exit 1
}

[[ -f "$project" ]] || { printf 'project is not a file: %s\n' "$project" >&2; exit 1; }
[[ "$project" == *.csproj ]] || { printf 'project must be a .csproj file\n' >&2; exit 1; }
[[ -f "$signing_config" ]] || {
    printf 'signing configuration is not a file: %s\n' "$signing_config" >&2
    exit 1
}
project="$(realpath "$project")"
signing_config="$(realpath "$signing_config")"

# Architecture-specific roots and an explicit SDK resolver override can make
# Rider or a shell select a different SDK than the receipt-backed installation.
unset MSBuildSDKsPath DOTNET_ROOT_X64 DOTNET_ROOT_X86 DOTNET_ROOT_ARM64
"$script_dir/install.sh" doctor
# shellcheck source=/dev/null
. "$activation"

[[ "${DOTNET_IOS_LINUX_ROOT:-}" == "$data_root" ]] || {
    printf 'activation selected an unexpected data root\n' >&2
    exit 1
}
[[ -n "${DOTNET_ROOT:-}" && -x "$DOTNET_ROOT/dotnet" ]] || {
    printf 'activation did not select the receipt-bound host .NET installation\n' >&2
    exit 1
}

expected_sdk="$(python3 - "$receipt" "$DOTNET_ROOT" "$data_root" <<'PY'
import json
import sys
from pathlib import Path

try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise SystemExit(f"cannot read installation receipt: {type(error).__name__}")
if (
    value.get("owner") != "dotnet-ios-linux"
    or value.get("status") != "complete"
    or value.get("dotnetRoot") != sys.argv[2]
    or value.get("dataRoot") != sys.argv[3]
):
    raise SystemExit("installation receipt does not match the active environment")
sdk = value.get("sdkVersion")
if not isinstance(sdk, str) or not sdk:
    raise SystemExit("installation receipt has no SDK version")
print(sdk)
PY
)"
actual_sdk="$(cd "$(dirname "$project")" && "$DOTNET_ROOT/dotnet" --version)"
[[ "$actual_sdk" == "$expected_sdk" ]] || {
    printf 'the project selects .NET SDK %s, but the installed iOS toolchain uses %s\n' \
        "$actual_sdk" "$expected_sdk" >&2
    printf 'update the project global.json or reinstall against a compatible SDK\n' >&2
    exit 1
}

rcodesign_executable="$(realpath -e "$data_root/bin/rcodesign")"
pymobiledevice3_executable="$(realpath -e "$data_root/bin/pymobiledevice3")"
readonly rcodesign_root="$(dirname "$rcodesign_executable")"
readonly pymobiledevice3_root="$(dirname "$(dirname "$pymobiledevice3_executable")")"
readonly device_python="$pymobiledevice3_root/bin/python"
[[ -x "$rcodesign_executable" && -x "$pymobiledevice3_executable" && -x "$device_python" ]] || {
    printf 'installed signing or device tools are incomplete\n' >&2
    exit 1
}

export DOTNET_CLI_HOME="$cache_root/dotnet-cli-home"
export NUGET_PACKAGES="$cache_root/nuget/packages"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true
[[ ! -L "$DOTNET_CLI_HOME" && ! -L "$NUGET_PACKAGES" ]] || {
    printf 'the .NET CLI or NuGet cache path must not be a symlink\n' >&2
    exit 1
}
mkdir -p "$DOTNET_CLI_HOME" "$NUGET_PACKAGES"

exec {run_lock_fd}>"$cache_root/device-run.lock"
flock -n "$run_lock_fd" || {
    printf 'another dotnet-ios-linux device run is already active\n' >&2
    exit 1
}

readonly runs_root="$cache_root/runs"
[[ ! -L "$runs_root" ]] || { printf 'run cache must not be a symlink: %s\n' "$runs_root" >&2; exit 1; }
mkdir -p "$runs_root"
chmod 700 "$runs_root"
readonly run_root="$(mktemp -d "$runs_root/run.XXXXXX")"
readonly msbuild_artifacts="$run_root/msbuild"
readonly signed_root="$run_root/signed"
mkdir -p "$msbuild_artifacts" "$signed_root"

on_error() {
    local status=$?
    trap - ERR
    printf 'run=failed\nrun_artifacts=%s\n' "$run_root" >&2
    exit "$status"
}
trap on_error ERR

dotnet="$DOTNET_ROOT/dotnet"
"$dotnet" restore "$project" \
    --runtime ios-arm64 \
    --property:TargetFramework=net10.0-ios \
    --property:RuntimeIdentifier=ios-arm64 \
    --property:RestorePackagesPath="$NUGET_PACKAGES" \
    --property:DotnetIosLinuxArtifactsRoot="$msbuild_artifacts" \
    --property:CustomBeforeDirectoryBuildProps="$integration_props"

"$dotnet" build "$project" \
    --no-restore \
    --configuration Release \
    --framework net10.0-ios \
    --runtime ios-arm64 \
    --property:EnableCodeSigning=false \
    --property:NoDSymUtil=true \
    --property:NoSymbolStrip=true \
    --property:RestorePackagesPath="$NUGET_PACKAGES" \
    --property:DotnetIosLinuxArtifactsRoot="$msbuild_artifacts" \
    --property:CustomBeforeDirectoryBuildProps="$integration_props"

mapfile -d '' -t applications < <(
    find "$msbuild_artifacts/bin" -type d -name '*.app' -prune -print0
)
if ((${#applications[@]} != 1)); then
    printf 'expected one application bundle, found %d under %s\n' \
        "${#applications[@]}" "$msbuild_artifacts/bin" >&2
    false
fi
readonly unsigned_app="${applications[0]}"
readonly signed_app="$signed_root/$(basename "$unsigned_app")"

bundle_identifier="$(python3 - "$unsigned_app/Info.plist" <<'PY'
import plistlib
import sys
from pathlib import Path

try:
    with Path(sys.argv[1]).open("rb") as stream:
        value = plistlib.load(stream)
except (OSError, plistlib.InvalidFileException) as error:
    raise SystemExit(f"cannot read application Info.plist: {type(error).__name__}")
bundle = value.get("CFBundleIdentifier") if isinstance(value, dict) else None
if not isinstance(bundle, str) or not bundle:
    raise SystemExit("application Info.plist has no bundle identifier")
print(bundle)
PY
)"

python3 "$signing_tool" \
    --config "$signing_config" \
    --app "$unsigned_app" \
    --output "$signed_app" \
    --rcodesign-root "$rcodesign_root" \
    --pymobiledevice3-root "$pymobiledevice3_root"

preflight_output="$(
    "$device_python" "$device_tool" preflight \
        --app "$signed_app" \
        --rcodesign-root "$rcodesign_root"
)"
printf '%s\n' "$preflight_output"
if ! grep -Fxq 'personalized_developer_image_mounted=true' <<<"$preflight_output"; then
    printf 'launch requires an operator-approved personalized Developer Disk Image to be mounted\n' >&2
    false
fi

"$device_python" "$device_tool" install \
    --confirm-install \
    --app "$signed_app" \
    --rcodesign-root "$rcodesign_root"
"$device_python" "$device_tool" launch \
    --confirm-launch \
    --bundle-identifier "$bundle_identifier"

trap - ERR
printf 'run=success\n'
printf 'unsigned_app=%s\n' "$unsigned_app"
printf 'signed_app=%s\n' "$signed_app"
printf 'run_artifacts=%s\n' "$run_root"
