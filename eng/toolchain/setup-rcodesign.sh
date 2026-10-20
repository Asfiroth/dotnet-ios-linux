#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
. "$script_dir/../install/download-verified.sh"

readonly version="0.29.0"
readonly release_commit="2312b1e5bd77322c019613e0ca624e222c6c6e08"
readonly archive_name="apple-codesign-$version-x86_64-unknown-linux-musl.tar.gz"
readonly archive_root="apple-codesign-$version-x86_64-unknown-linux-musl"
readonly archive_url="https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F$version/$archive_name"
readonly archive_sha256="dbe85cedd8ee4217b64e9a0e4c2aef92ab8bcaaa41f20bde99781ff02e600002"
readonly executable_sha256="dab9a7465f96aba3c81e793775510f745b91a46b6418e89f7317b5d8fc7bcea2"
readonly copying_sha256="856867e127c2349ea11dff0930a488e7a1e386d975bcb9088a3c6e2bf1b3d990"
readonly marker_name=".dotnet-ios-linux-rcodesign"

usage() {
    cat <<'EOF'
Usage: setup-rcodesign.sh --destination <directory> --cache <directory> [--offline]

Downloads, verifies, and transactionally stages the locked rcodesign 0.29.0
Linux x86-64 release. The destination remains project-owned; this script does
not write ~/.local, load credentials, create certificates, or sign anything.
Existing unowned or mismatched destinations are never replaced.
EOF
}

destination=""
cache=""
offline=false
while (($#)); do
    case "$1" in
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --cache) cache="${2:?missing value for --cache}"; shift 2 ;;
        --offline) offline=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$destination" && -n "$cache" ]] || { usage >&2; exit 2; }

for tool in curl file install ln mktemp mv readelf realpath sha256sum tar; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || {
    printf 'this rcodesign lock supports Linux x86_64 only\n' >&2
    exit 1
}

verify_hash() {
    local expected="$1" path="$2"
    printf '%s  %s\n' "$expected" "$path" | sha256sum --check --status
}

verify_archive_members() {
    local archive="$1" expected actual
    expected="$archive_root/
$archive_root/COPYING
$archive_root/rcodesign"
    actual="$(tar -tzf "$archive")"
    [[ "$actual" == "$expected" ]] || {
        printf 'unexpected rcodesign archive members\n' >&2
        return 1
    }
}

verify_stage() {
    local root="$1" actual file_description
    verify_hash "$executable_sha256" "$root/rcodesign" || {
        printf 'rcodesign executable hash mismatch: %s\n' "$root/rcodesign" >&2
        return 1
    }
    verify_hash "$copying_sha256" "$root/COPYING" || {
        printf 'rcodesign license inventory hash mismatch: %s\n' "$root/COPYING" >&2
        return 1
    }
    actual="$("$root/rcodesign" --version)"
    [[ "$actual" == "apple-codesign $version" ]] || {
        printf 'unexpected rcodesign version: %s\n' "$actual" >&2
        return 1
    }
    "$root/rcodesign" --help >/dev/null
    file_description="$(file -b "$root/rcodesign")"
    [[ "$file_description" == *"ELF 64-bit LSB pie executable, x86-64"* &&
       "$file_description" == *"static-pie linked"* ]] || {
        printf 'unexpected rcodesign executable format: %s\n' "$file_description" >&2
        return 1
    }
    readelf -h "$root/rcodesign" | grep -Eq '^  Machine:[[:space:]]+Advanced Micro Devices X86-64$' || {
        printf 'rcodesign ELF machine validation failed\n' >&2
        return 1
    }
}

marker="version=$version
release_commit=$release_commit
archive_sha256=$archive_sha256
executable_sha256=$executable_sha256
copying_sha256=$copying_sha256
host=Linux x86_64"

if [[ -e "$destination" || -L "$destination" ]]; then
    [[ -d "$destination" && ! -L "$destination" && -f "$destination/$marker_name" &&
       ! -L "$destination/$marker_name" ]] || {
        printf 'existing destination is not owned by this setup: %s\n' "$destination" >&2
        exit 1
    }
    [[ "$(cat "$destination/$marker_name")" == "$marker" ]] || {
        printf 'existing destination has different provenance; refusing to replace it: %s\n' "$destination" >&2
        exit 1
    }
    verify_stage "$destination"
    printf 'rcodesign_root=%s\n' "$(realpath "$destination")"
    printf 'rcodesign_version=%s\nreused=true\n' "$version"
    exit 0
fi

[[ ! -L "$cache" ]] || { printf 'cache directory must not be a symlink: %s\n' "$cache" >&2; exit 1; }
mkdir -p "$cache"
archive="$cache/$archive_name"
download_verified "$archive_url" sha256sum "$archive_sha256" "$archive" "$offline"
verify_archive_members "$archive"

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.rcodesign.XXXXXX")"
extracted="$(mktemp -d "$parent/.rcodesign-extract.XXXXXX")"
cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
    [[ -z "${extracted:-}" || ! -e "$extracted" ]] || rm -rf -- "$extracted"
}
trap cleanup EXIT

tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$extracted"
install -m755 "$extracted/$archive_root/rcodesign" "$stage/rcodesign"
install -m644 "$extracted/$archive_root/COPYING" "$stage/COPYING"
printf '%s\n' "$marker" > "$stage/$marker_name"
verify_stage "$stage"
mv --no-clobber --no-target-directory "$stage" "$destination"
[[ ! -e "$stage" ]] || {
    printf 'rcodesign destination appeared during publication; refusing to replace it: %s\n' "$destination" >&2
    exit 1
}
stage=""
rm -rf -- "$extracted"
extracted=""
verify_stage "$destination"

printf 'rcodesign_root=%s\n' "$(realpath "$destination")"
printf 'rcodesign_version=%s\nreused=false\n' "$version"
