#!/usr/bin/env bash
# Reference capture only. It does not copy an Apple SDK, pair a device, or sign.
set -u -o pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: capture-mac-reference.sh --output <directory> --dotnet <executable> [--project <HelloIos.csproj>] [--clean]

Invokes the supplied dotnet executable explicitly, derives its SDK root after
resolving symlinks, and records a local Mac reference inventory. When --project
is supplied, performs a Release ios-arm64 build and writes a local binlog.
--clean runs dotnet clean first. Review and sanitize all output before sharing
or turning it into a committed receipt.
EOF
}

resolve_symlink_path() {
    local candidate="$1"
    local target
    local base

    if [[ "$candidate" != */* ]]; then
        candidate="$(command -v "$candidate")"
    fi
    while [[ -L "$candidate" ]]; do
        target="$(readlink "$candidate")"
        if [[ "$target" = /* ]]; then
            candidate="$target"
        else
            base="$(cd -P "$(dirname "$candidate")" && pwd)"
            candidate="$base/$target"
        fi
    done
    base="$(cd -P "$(dirname "$candidate")" && pwd)"
    printf '%s/%s\n' "$base" "$(basename "$candidate")"
}

output=""
dotnet_executable=""
project=""
clean=0
while (($#)); do
    case "$1" in
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet) dotnet_executable="${2:?missing value for --dotnet}"; shift 2 ;;
        --project) project="${2:?missing value for --project}"; shift 2 ;;
        --clean) clean=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ -n "$output" && -n "$dotnet_executable" ]] || { usage >&2; exit 2; }
dotnet_executable="$(resolve_symlink_path "$dotnet_executable")"
[[ -x "$dotnet_executable" ]] || { printf 'dotnet executable is not executable: %s\n' "$dotnet_executable" >&2; exit 2; }
dotnet_root="$(dirname "$dotnet_executable")"
[[ -d "$dotnet_root/sdk" ]] || { printf 'derived SDK root is invalid: %s\n' "$dotnet_root" >&2; exit 2; }

project_dir="$PWD"
if [[ -n "$project" ]]; then
    [[ -f "$project" ]] || { printf 'project does not exist: %s\n' "$project" >&2; exit 2; }
    project_dir="$(cd -P "$(dirname "$project")" && pwd)"
    project="$project_dir/$(basename "$project")"
fi

mkdir -p "$output"
report="$output/mac-reference.txt"
: > "$report"

record() {
    printf '\n$ %s\n' "$*" >> "$report"
    "$@" >> "$report" 2>&1
    local status=$?
    printf '[exit %s]\n' "$status" >> "$report"
    return 0
}

record_in_project_dir() {
    printf '\n$ (cd <project> && %s)\n' "$*" >> "$report"
    (
        cd "$project_dir"
        "$@"
    ) >> "$report" 2>&1
    local status=$?
    printf '[exit %s]\n' "$status" >> "$report"
    return 0
}

{
    printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'dotnet_executable=%s\n' "$dotnet_executable"
    printf 'dotnet_root=%s\n' "$dotnet_root"
    printf 'project_supplied=%s\n' "$([[ -n "$project" ]] && printf yes || printf no)"
    for variable in DOTNET_ROOT DOTNET_ROOT_X64 DEVELOPER_DIR MD_APPLE_SDK_ROOT MSBuildSDKsPath; do
        value="${!variable-}"
        printf '%s=%s\n' "$variable" "${value:-<unset>}"
    done
} >> "$report"

record uname -a
record sw_vers
record xcode-select -p
record xcodebuild -version
record xcrun --sdk iphoneos --show-sdk-path
record xcrun --sdk iphoneos --show-sdk-version
record_in_project_dir "$dotnet_executable" --info
record_in_project_dir "$dotnet_executable" --list-sdks
record_in_project_dir "$dotnet_executable" --list-runtimes
record_in_project_dir "$dotnet_executable" workload --info
record_in_project_dir "$dotnet_executable" workload list

if [[ -d "$dotnet_root/sdk-manifests" ]]; then
    find "$dotnet_root/sdk-manifests" -type f | LC_ALL=C sort > "$output/sdk-manifest-files.txt"
fi
if [[ -d "$dotnet_root/packs" ]]; then
    find "$dotnet_root/packs" -mindepth 2 -maxdepth 2 -type d -print | LC_ALL=C sort > "$output/pack-directories.txt"
fi
if [[ -n "$project" ]]; then
    binlog="$output/helloios-reference.binlog"
    if ((clean)); then
        record_in_project_dir "$dotnet_executable" clean "$project" -f net10.0-ios -r ios-arm64 -c Release
    fi
    record_in_project_dir "$dotnet_executable" build "$project" -f net10.0-ios -r ios-arm64 -c Release "/bl:$binlog"
    [[ -f "$binlog" ]] && shasum -a 256 "$binlog" > "$output/helloios-reference.binlog.sha256"
fi
shasum -a 256 "$report" > "$output/mac-reference.txt.sha256"
printf 'report=%s\n' "$report"
