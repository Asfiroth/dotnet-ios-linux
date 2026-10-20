#!/usr/bin/env bash
set -euo pipefail

readonly repository="https://github.com/joshuaswarren/omarchy-apple-dev.git"
readonly revision="172e28cff7b6d5adebfa49f93957d6a864378dfb"
readonly tree="c658002d37a28f922e1568cf9959be579d9f6cfc"
readonly license_sha256="f13d4bd266e12504f3af553ee1f5617abf23106de0f3e9657596fd80a21c8fee"
readonly installer_sha256="f32daeb4017d2880b9394600e2d99af57dec3834949b8eaa919caaec6c85eb99"
readonly device_run_sha256="f127d22a21648d5f09e776c7cd66a696e6d991dd80a3cc6808603a54dd4f045a"

usage() {
    cat <<'EOF'
Usage: acquire-omarchy-backend.sh --destination <directory>

Clones the selected omarchy-apple-dev source revision and verifies the exact
tree and reviewed entrypoint hashes. It does not execute the upstream installer
or mutate any user-global toolchain, SDK, authentication, or pairing state.
An existing checkout is accepted only when clean and at the selected revision.
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
    [[ -d "$destination/.git" ]] || {
        printf 'not a git checkout: %s\n' "$destination" >&2
        exit 1
    }
    actual="$(git -C "$destination" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] || {
        printf 'existing checkout is at %s, expected %s; refusing to reset it\n' "$actual" "$revision" >&2
        exit 1
    }
    [[ -z "$(git -C "$destination" status --porcelain)" ]] || {
        printf 'existing checkout has local changes; refusing to use or clean it: %s\n' "$destination" >&2
        exit 1
    }
else
    mkdir -p "$(dirname "$destination")"
    git clone --filter=blob:none --no-checkout "$repository" "$destination"
    git -C "$destination" checkout --detach "$revision"
fi

actual_tree="$(git -C "$destination" rev-parse 'HEAD^{tree}')"
[[ "$actual_tree" == "$tree" ]] || {
    printf 'source tree mismatch: got %s, expected %s\n' "$actual_tree" "$tree" >&2
    exit 1
}

printf '%s  %s\n' "$license_sha256" "$destination/LICENSE" |
    sha256sum --check --status
printf '%s  %s\n' "$installer_sha256" "$destination/install-toolchain.sh" |
    sha256sum --check --status
printf '%s  %s\n' "$device_run_sha256" "$destination/device-run.sh" |
    sha256sum --check --status

printf 'omarchy_source=%s\n' "$(realpath "$destination")"
printf 'omarchy_revision=%s\n' "$revision"
printf 'omarchy_tree=%s\n' "$tree"
