#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'Usage: run-in-fixed-mount.sh <physical-directory> <logical-directory> <command> [argument ...]\n'
}

readonly canonical_parent=/tmp/dotnet-ios-linux

[[ $# -ge 3 ]] || { usage >&2; exit 2; }
physical="$1"
logical="$2"
shift 2

[[ "$(id -u)" == 0 ]] || {
    printf 'fixed mount runner must execute in a mapped-root user namespace\n' >&2
    exit 1
}
read -r namespace_uid host_uid mapped_uids < /proc/self/uid_map
[[ "$namespace_uid" == 0 && "$host_uid" != 0 && "$mapped_uids" == 1 ]] || {
    printf 'fixed mount runner requires a single-ID mapped-root user namespace\n' >&2
    exit 1
}
[[ -d "$physical" && ! -L "$physical" ]] || {
    printf 'physical build root is not a real directory: %s\n' "$physical" >&2
    exit 1
}
[[ -d /tmp && ! -L /tmp ]] || {
    printf 'temporary directory is not a real directory: /tmp\n' >&2
    exit 1
}
[[ "$logical" == "$canonical_parent/"* &&
   "${logical#"$canonical_parent/"}" != */* ]] || {
    printf 'logical build mount is outside the canonical namespace path: %s\n' "$logical" >&2
    exit 1
}

physical="$(realpath "$physical")"
[[ "$physical" != "$canonical_parent" && "$physical" != "$canonical_parent/"* ]] || {
    printf 'physical build root uses the reserved canonical mountpoint: %s\n' "$physical" >&2
    exit 1
}
# Cover only the project-owned host mountpoint. The fixed logical child is
# created in namespace-private tmpfs, so stale or foreign host entries cannot
# influence the Swift path while the rest of /tmp remains untouched.
[[ -d "$canonical_parent" && ! -L "$canonical_parent" ]] || {
    printf 'canonical namespace mountpoint is unavailable: %s\n' "$canonical_parent" >&2
    exit 1
}
mount -t tmpfs -o mode=700,nosuid,nodev tmpfs "$canonical_parent"
mkdir -m700 "$logical"
mount --bind "$physical" "$logical"
mount --make-private "$logical"
[[ "$(findmnt -n -o TARGET --target "$logical")" == "$logical" ]] || {
    printf 'fixed logical build mount was not established: %s\n' "$logical" >&2
    exit 1
}

exec "$@"
