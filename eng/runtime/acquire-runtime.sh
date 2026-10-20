#!/usr/bin/env bash
set -euo pipefail

readonly repository="https://github.com/dotnet/runtime.git"
readonly revision="4271d88e0aebf3d04f188f1334c2220d80555ef6"

usage() {
    cat <<'EOF'
Usage: acquire-runtime.sh --destination <directory>

Clones dotnet/runtime and checks out the exact source revision matched to the
10.0.12 device runtime. An existing checkout is accepted only at that revision;
it is never reset or cleaned.
EOF
}

destination=""
while (($#)); do
    case "$1" in
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$destination" ]] || { usage >&2; exit 2; }

if [[ -e "$destination" ]]; then
    [[ -d "$destination/.git" ]] || { printf 'not a git checkout: %s\n' "$destination" >&2; exit 1; }
    actual="$(git -C "$destination" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] || {
        printf 'existing checkout is at %s, expected %s; refusing to reset it\n' "$actual" "$revision" >&2
        exit 1
    }
else
    mkdir -p "$(dirname "$destination")"
    git clone --filter=blob:none --no-checkout "$repository" "$destination"
    git -C "$destination" checkout --detach "$revision"
fi

printf 'runtime_source=%s\n' "$(realpath "$destination")"
printf 'runtime_revision=%s\n' "$revision"
