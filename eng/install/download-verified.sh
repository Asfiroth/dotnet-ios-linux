#!/usr/bin/env bash
# Shared hash-verified cache download with atomic, no-clobber publication.

download_verified() {
    local url="$1" algorithm="$2" expected="$3" output="$4" offline="$5"
    local actual parent part

    parent="$(dirname "$output")"
    [[ ! -L "$parent" ]] || {
        printf 'cache publication directory must not be a symlink: %s\n' "$parent" >&2
        return 1
    }
    mkdir -p "$parent"
    [[ -d "$parent" && ! -L "$parent" ]] || {
        printf 'cache publication parent is not a real directory: %s\n' "$parent" >&2
        return 1
    }

    if [[ -e "$output" || -L "$output" ]]; then
        [[ -f "$output" && ! -L "$output" ]] || {
            printf 'cached input is not a regular non-symlink file: %s\n' "$output" >&2
            return 1
        }
        actual="$("$algorithm" -b "$output" | cut -d' ' -f1)"
        [[ "$actual" == "$expected" ]] || {
            printf 'cached input has the wrong hash; refusing to replace it: %s\n' "$output" >&2
            return 1
        }
        return 0
    fi
    [[ "$offline" == false ]] || {
        printf 'required offline input is missing: %s\n' "$output" >&2
        return 1
    }

    part="$(mktemp -- "$output.part.XXXXXXXXXX")"
    if ! curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        --output "$part" "$url"; then
        rm -f -- "$part"
        return 1
    fi
    actual="$("$algorithm" -b "$part" | cut -d' ' -f1)"
    if [[ "$actual" != "$expected" ]]; then
        rm -f -- "$part"
        printf 'download hash mismatch: %s\n' "$url" >&2
        return 1
    fi

    # The hard-link operation is atomic and fails rather than replacing a path
    # published concurrently. The temporary file is reserved by mktemp in the
    # same directory, so publication cannot cross filesystems.
    if ln -- "$part" "$output" 2>/dev/null; then
        rm -f -- "$part"
        return 0
    fi
    rm -f -- "$part"

    # An identical concurrent publication is reusable; every other collision
    # remains untouched and fails closed.
    if [[ -f "$output" && ! -L "$output" ]]; then
        actual="$("$algorithm" -b "$output" | cut -d' ' -f1)"
        [[ "$actual" == "$expected" ]] && return 0
    fi
    printf 'cache publication collided with a different path: %s\n' "$output" >&2
    return 1
}
