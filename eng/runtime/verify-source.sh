#!/usr/bin/env bash
# Validate that a runtime checkout is pristine or contains only the locked patch.
set -euo pipefail

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly expected_revision="4271d88e0aebf3d04f188f1334c2220d80555ef6"
readonly patch_file="$script_dir/patches/0001-linux-host-apple-mobile-aot-cross.patch"
readonly patch_sha256="b1692993777490b4513d60f7fc0140db5eca8c7d8cdd06ec33fe46bc9724e67c"
readonly expected_paths=$'src/mono/mono/mini/aot-compiler.c\nsrc/mono/mono/mini/aot-runtime.c\nsrc/mono/mono/utils/mono-dl-darwin.c'
readonly expected_post_patch_blobs=$'src/mono/mono/mini/aot-compiler.c\t7e756d354bba46cd5f7dfd1f1827e477172c3853\nsrc/mono/mono/mini/aot-runtime.c\tbced8e047d61a458cbccea86d1ff491aa24460be\nsrc/mono/mono/utils/mono-dl-darwin.c\t7235aa1e7c055b320c0731bba3f7ceae257cf845'

usage() {
    printf 'Usage: verify-source.sh --source <dotnet-runtime-checkout> --state <pristine|patched|either>\n'
}

source_root=""
expected_state=""
while (($#)); do
    case "$1" in
        --source) source_root="${2:?missing value for --source}"; shift 2 ;;
        --state) expected_state="${2:?missing value for --state}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$source_root" && "$expected_state" =~ ^(pristine|patched|either)$ ]] || {
    usage >&2
    exit 2
}
for tool in git sha256sum; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ -d "$source_root/.git" ]] || { printf 'not a git checkout: %s\n' "$source_root" >&2; exit 1; }
source_root="$(realpath "$source_root")"
printf '%s  %s\n' "$patch_sha256" "$patch_file" | sha256sum --check --status || {
    printf 'runtime patch hash mismatch: %s\n' "$patch_file" >&2
    exit 1
}

actual_revision="$(git -C "$source_root" rev-parse HEAD)"
[[ "$actual_revision" == "$expected_revision" ]] || {
    printf 'runtime revision mismatch: expected %s, got %s\n' "$expected_revision" "$actual_revision" >&2
    exit 1
}
git -C "$source_root" diff --cached --quiet -- || {
    printf 'runtime checkout contains staged changes: %s\n' "$source_root" >&2
    exit 1
}
untracked="$(git -C "$source_root" ls-files --others --exclude-standard)"
[[ -z "$untracked" ]] || {
    printf 'runtime checkout contains untracked source files:\n%s\n' "$untracked" >&2
    exit 1
}

if git -C "$source_root" diff --quiet HEAD --; then
    [[ "$expected_state" != patched ]] || {
        printf 'locked runtime patch is not applied: %s\n' "$source_root" >&2
        exit 1
    }
    printf 'runtime_source_state=pristine\n'
    exit 0
fi

actual_paths="$(git -C "$source_root" diff --name-only HEAD -- | LC_ALL=C sort)"
[[ "$actual_paths" == "$expected_paths" ]] || {
    printf 'runtime checkout contains changes outside the locked patch:\n%s\n' "$actual_paths" >&2
    exit 1
}
while IFS=$'\t' read -r path expected_blob; do
    actual_blob="$(git -C "$source_root" hash-object "$path")" || {
        printf 'cannot hash expected patched runtime file: %s\n' "$path" >&2
        exit 1
    }
    [[ "$actual_blob" == "$expected_blob" ]] || {
        printf 'runtime patched file has an unexpected content hash: %s\n' "$path" >&2
        exit 1
    }
done <<< "$expected_post_patch_blobs"
[[ "$expected_state" != pristine ]] || {
    printf 'runtime checkout is patched but pristine source was required\n' >&2
    exit 1
}
printf 'runtime_source_state=patched\n'
