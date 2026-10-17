#!/usr/bin/env bash
# Read-only host inventory. It never invokes workload repair, install, or uninstall.
set -u -o pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: capture-linux-inventory.sh --output <directory> [--dotnet-root <directory>]

Writes local command output and a file inventory. Review and sanitize output
before turning it into a committed receipt. No mutation is made to a .NET installation.
EOF
}

output=""
dotnet_root="${DOTNET_ROOT:-$HOME/.dotnet}"
while (($#)); do
    case "$1" in
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet-root) dotnet_root="${2:?missing value for --dotnet-root}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ -n "$output" ]] || { usage >&2; exit 2; }
mkdir -p "$output"
report="$output/linux-host-inventory.txt"
: > "$report"

record() {
    printf '\n$ %s\n' "$*" >> "$report"
    "$@" >> "$report" 2>&1
    status=$?
    printf '[exit %s]\n' "$status" >> "$report"
    return 0
}

{
    printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'dotnet_root=%s\n' "$dotnet_root"
    printf 'dotnet_path=%s\n' "$(command -v dotnet || true)"
    for variable in DOTNET_ROOT DOTNET_ROOT_X64 DOTNET_CLI_HOME MSBuildSDKsPath DEVELOPER_DIR; do
        value="${!variable-}"
        if [[ -n "$value" ]]; then
            printf '%s=%s\n' "$variable" "$value"
        else
            printf '%s=<unset>\n' "$variable"
        fi
    done
} >> "$report"

record uname -a
[[ -r /etc/os-release ]] && record cat /etc/os-release
if command -v dotnet >/dev/null 2>&1; then
    record dotnet --info
    record dotnet --list-sdks
    record dotnet --list-runtimes
    record dotnet workload --info
    record dotnet workload list
fi

inventory="$output/dotnet-root-files.txt"
if [[ -d "$dotnet_root" ]]; then
    find "$dotnet_root" -xdev -type f -printf '%m %s %p\n' | LC_ALL=C sort > "$inventory"
else
    printf 'dotnet root not present: %s\n' "$dotnet_root" > "$inventory"
fi

printf 'report=%s\n' "$report"
printf 'report_sha256=%s\n' "$(sha256sum "$report" | awk '{print $1}')"
printf 'inventory=%s\n' "$inventory"
printf 'inventory_sha256=%s\n' "$(sha256sum "$inventory" | awk '{print $1}')"
