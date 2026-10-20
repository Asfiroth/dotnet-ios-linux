#!/usr/bin/env bash
set -euo pipefail

readonly repository="https://github.com/dotnet/runtime.git"
readonly revision="4271d88e0aebf3d04f188f1334c2220d80555ef6"
readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

usage() {
    cat <<'EOF'
Usage: acquire-runtime.sh --destination <directory> [--offline]

Clones dotnet/runtime and checks out the exact source revision matched to the
10.0.12 device runtime. An existing checkout is accepted only at that revision
and only when pristine or carrying exactly the locked project patch. It is never
reset or cleaned.
EOF
}

destination=""
offline=false
while (($#)); do
    case "$1" in
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --offline) offline=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$destination" ]] || { usage >&2; exit 2; }
for tool in git mktemp mv realpath; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done

verify_checkout() {
    local root="$1" actual
    [[ -d "$root/.git" && ! -L "$root" ]] || { printf 'not a real git checkout: %s\n' "$root" >&2; return 1; }
    actual="$(git -C "$root" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] || {
        printf 'existing checkout is at %s, expected %s; refusing to reset it\n' "$actual" "$revision" >&2
        return 1
    }
    "$script_dir/verify-source.sh" --source "$root" --state either >/dev/null
}

if [[ -e "$destination" || -L "$destination" ]]; then
    verify_checkout "$destination"
else
    [[ "$offline" == false ]] || {
        printf 'runtime source is unavailable in offline mode: %s\n' "$destination" >&2
        exit 1
    }
    parent="$(dirname "$destination")"
    mkdir -p "$parent"
    stage="$(mktemp -d "$parent/.runtime-source.XXXXXX")"
    rmdir "$stage"
    cleanup() { [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"; }
    trap cleanup EXIT
    git clone --filter=blob:none --no-checkout "$repository" "$stage"
    git -C "$stage" checkout --detach "$revision"
    verify_checkout "$stage"
    mv --no-clobber --no-target-directory "$stage" "$destination"
    if [[ -e "$stage" ]]; then
        rm -rf -- "$stage"
        stage=""
        verify_checkout "$destination"
    else
        stage=""
    fi
    trap - EXIT
fi

verify_checkout "$destination"

printf 'runtime_source=%s\n' "$(realpath "$destination")"
printf 'runtime_revision=%s\n' "$revision"
