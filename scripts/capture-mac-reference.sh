#!/usr/bin/env bash
# Reference capture only. It does not copy an Apple SDK, pair a device, or sign.
set -u -o pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: capture-mac-reference.sh --output <directory> --dotnet-root <directory> [--project <HelloIos.csproj>]

When --project is supplied, performs the ordinary Release ios-arm64 build and
writes a local binlog. Do not commit the raw binlog; create a sanitized receipt
that references its hash instead.
EOF
}

output=""
dotnet_root=""
project=""
while (($#)); do
    case "$1" in
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet-root) dotnet_root="${2:?missing value for --dotnet-root}"; shift 2 ;;
        --project) project="${2:?missing value for --project}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ -n "$output" && -n "$dotnet_root" ]] || { usage >&2; exit 2; }
mkdir -p "$output"
report="$output/mac-reference.txt"
: > "$report"

record() {
    printf '\n$ %s\n' "$*" >> "$report"
    "$@" >> "$report" 2>&1
    status=$?
    printf '[exit %s]\n' "$status" >> "$report"
    return 0
}

printf 'captured_at_utc=%s\ndotnet_root=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$dotnet_root" >> "$report"
record uname -a
record sw_vers
record xcode-select -p
record xcodebuild -version
record xcrun --sdk iphoneos --show-sdk-path
record xcrun --sdk iphoneos --show-sdk-version
record dotnet --info
record dotnet --list-sdks
record dotnet --list-runtimes
record dotnet workload --info
record dotnet workload list
if [[ -d "$dotnet_root/sdk-manifests" ]]; then
    find "$dotnet_root/sdk-manifests" -type f | LC_ALL=C sort > "$output/sdk-manifest-files.txt"
fi
if [[ -n "$project" ]]; then
    binlog="$output/helloios-reference.binlog"
    record dotnet build "$project" -f net10.0-ios -r ios-arm64 -c Release "/bl:$binlog"
    [[ -f "$binlog" ]] && shasum -a 256 "$binlog" > "$output/helloios-reference.binlog.sha256"
fi
shasum -a 256 "$report" > "$output/mac-reference.txt.sha256"
printf 'report=%s\n' "$report"
