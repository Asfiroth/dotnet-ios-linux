#!/usr/bin/env bash
set -euo pipefail

readonly expected_revision="4271d88e0aebf3d04f188f1334c2220d80555ef6"
readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly patch_file="$script_dir/patches/0001-linux-host-apple-mobile-aot-cross.patch"

usage() {
    printf 'Usage: apply-patches.sh --source <dotnet-runtime-checkout>\n'
}

source_root=""
while (($#)); do
    case "$1" in
        --source) source_root="${2:?missing value for --source}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$source_root" ]] || { usage >&2; exit 2; }

actual_revision="$(git -C "$source_root" rev-parse HEAD)"
[[ "$actual_revision" == "$expected_revision" ]] || {
    printf 'runtime revision mismatch: expected %s, got %s\n' "$expected_revision" "$actual_revision" >&2
    exit 1
}

source_state="$("$script_dir/verify-source.sh" --source "$source_root" --state either)"
if [[ "$source_state" == runtime_source_state=patched ]]; then
    printf 'runtime patch already applied\n'
elif git -C "$source_root" apply --check "$patch_file"; then
    git -C "$source_root" apply "$patch_file"
    "$script_dir/verify-source.sh" --source "$source_root" --state patched >/dev/null
    printf 'runtime patch applied\n'
else
    printf 'runtime patch does not apply cleanly; source changes were preserved\n' >&2
    exit 1
fi
