#!/usr/bin/env bash
# Destructive workload lifecycle gate confined to a newly created output root.
set -euo pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: verify-workload-lifecycle.sh --sdk-archive <tar.gz>
       --official-manifest <nupkg> --aot-pack <directory> --output <directory>
       [--developer-dir <Xcode.app/Contents/Developer>]

Creates an isolated .NET home, installs the pinned workload set and Linux ios
workload, exercises restore/repair/uninstall/reinstall, and optionally builds an
unchanged copy of HelloIos. The output path must not already exist.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repository="$(cd "$script_dir/../.." && pwd -P)"
sdk_archive=""
official_manifest=""
aot_pack=""
output=""
developer_dir=""
while (($#)); do
    case "$1" in
        --sdk-archive) sdk_archive="${2:?missing value for --sdk-archive}"; shift 2 ;;
        --official-manifest) official_manifest="${2:?missing value for --official-manifest}"; shift 2 ;;
        --aot-pack) aot_pack="${2:?missing value for --aot-pack}"; shift 2 ;;
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --developer-dir) developer_dir="${2:?missing value for --developer-dir}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$sdk_archive" && -n "$official_manifest" && -n "$aot_pack" && -n "$output" ]] || {
    usage >&2
    exit 2
}
[[ ! -e "$output" ]] || { printf 'output must not already exist: %s\n' "$output" >&2; exit 1; }
for tool in python3 sha512sum tar; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done

sdk_archive="$(realpath "$sdk_archive")"
official_manifest="$(realpath "$official_manifest")"
aot_pack="$(realpath "$aot_pack")"
if [[ -n "$developer_dir" ]]; then
    developer_dir="$(realpath "$developer_dir")"
fi
readonly sdk_sha512="1904a98dda2db34ba78ba3c4fb1b224455329af91c341d83547843c1839e112b04ae699ee92fa2449a8f62249196a938723078c0c60b9f65c88539d5687729c2"
printf '%s  %s\n' "$sdk_sha512" "$sdk_archive" | sha512sum --check --status || {
    printf 'SDK archive SHA-512 mismatch: %s\n' "$sdk_archive" >&2
    exit 1
}

mkdir -m700 -p "$output/dotnet" "$output/home" "$output/cli-home" \
    "$output/nuget" "$output/temp"
tar -xzf "$sdk_archive" -C "$output/dotnet"
"$repository/eng/workload/build-local-feed.py" \
    --official-manifest "$official_manifest" \
    --aot-pack "$aot_pack" \
    --output "$output/feed"

export HOME="$output/home"
export DOTNET_ROOT="$output/dotnet"
export DOTNET_CLI_HOME="$output/cli-home"
export NUGET_PACKAGES="$output/nuget"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true
export PATH="$DOTNET_ROOT:$PATH"
dotnet="$DOTNET_ROOT/dotnet"
config="$output/feed/NuGet.Config"

workload_update() {
    "$dotnet" workload update --configfile "$config" \
        --disable-parallel --temp-dir "$output/temp" --verbosity minimal
}
workload_install() {
    "$dotnet" workload install ios --configfile "$config" \
        --disable-parallel --temp-dir "$output/temp" --verbosity minimal
}
assert_installed() {
    "$dotnet" workload list | grep -Eq '^ios[[:space:]]+27\.0\.10722/10\.0\.100'
    [[ -f "$DOTNET_ROOT/packs/DotnetIosLinux.Sdk/0.1.0-preview.1/Sdk/Sdk.props" ]]
    [[ -x "$DOTNET_ROOT/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/0.1.0-preview.1/tools/mono-aot-cross" ]]
    [[ -d "$DOTNET_ROOT/packs/Microsoft.iOS.Sdk.net10.0_26.0/26.0.11017" ]]
    [[ -d "$DOTNET_ROOT/packs/Microsoft.iOS.Sdk.net10.0_27.0/27.0.10722" ]]
    "$repository/eng/install/verify-workload-packages.py" --root "$DOTNET_ROOT" \
        --feed "$output/feed" --lock "$repository/toolchain.lock.json" \
        --dotnet "$dotnet" >/dev/null
}

mkdir -p "$output/control"
cat > "$output/control/global.json" <<'EOF'
{
  "sdk": {
    "version": "10.0.402",
    "rollForward": "disable",
    "workloadVersion": "10.0.401.1"
  }
}
EOF
cd "$output/control"
workload_update
workload_install
assert_installed

mkdir -p "$output/project/tests"
cp -a "$repository/tests/HelloIos" "$repository/tests/AotProbe" "$output/project/tests/"
project="$output/project/tests/HelloIos/HelloIos.csproj"
"$dotnet" workload restore "$project" --configfile "$config" \
    --disable-parallel --temp-dir "$output/temp" --verbosity minimal

"$dotnet" workload repair --configfile "$config" --disable-parallel --verbosity minimal
assert_installed

"$dotnet" workload uninstall ios
if "$dotnet" workload list | grep -Eq '^ios[[:space:]]'; then
    printf 'ios remained installed after workload uninstall\n' >&2
    exit 1
fi
"$dotnet" workload clean
workload_install
assert_installed

if [[ -n "$developer_dir" ]]; then
    export DEVELOPER_DIR="$developer_dir"
    export PATH="$DEVELOPER_DIR/usr/bin:$PATH"
    "$dotnet" restore "$project" --configfile "$config" --runtime ios-arm64
    "$dotnet" build "$project" --no-restore --configuration Release \
        --framework net10.0-ios --runtime ios-arm64 \
        --property:EnableCodeSigning=false --property:NoDSymUtil=true \
        --property:NoSymbolStrip=true --verbosity:normal 2>&1 | tee "$output/hello-ios-build.log"
    app="$output/project/tests/HelloIos/bin/Release/net10.0-ios/ios-arm64/HelloIos.app/HelloIos"
    [[ -f "$app" ]] || { printf 'HelloIos executable was not produced\n' >&2; exit 1; }
    grep -F "$DOTNET_ROOT/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/0.1.0-preview.1" \
        "$output/hello-ios-build.log" >/dev/null
fi

printf 'workload_lifecycle=passed\n'
printf 'isolated_dotnet_root=%s\n' "$DOTNET_ROOT"
printf 'local_feed=%s\n' "$output/feed"
