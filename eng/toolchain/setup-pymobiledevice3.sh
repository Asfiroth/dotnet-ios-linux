#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly requirements="$script_dir/pymobiledevice3-requirements.lock"
readonly requirements_sha256="b76b9db5d691b7ae6d6e377884e7ff070f2545b5749d724fb24dc7d60f3d90d5"
readonly expected_version="11.26.0"
readonly marker_name=".dotnet-ios-linux-pymobiledevice3"

usage() {
    cat <<'EOF'
Usage: setup-pymobiledevice3.sh --destination <directory> [--python <executable>]
       [--uv <executable>] [--cache <directory>] [--offline]

Creates an isolated pymobiledevice3 11.26.0 environment from the complete,
hash-enforced lock. This lock is intentionally specific to Linux x86-64 and
Python 3.14. Existing unowned or mismatched destinations are never replaced.
EOF
}

destination=""
python="/usr/bin/python3"
uv="uv"
cache=""
offline=false
while (($#)); do
    case "$1" in
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --python) python="${2:?missing value for --python}"; shift 2 ;;
        --uv) uv="${2:?missing value for --uv}"; shift 2 ;;
        --cache) cache="${2:?missing value for --cache}"; shift 2 ;;
        --offline) offline=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$destination" ]] || { usage >&2; exit 2; }
for tool in mktemp mv realpath sha256sum; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ -x "$python" ]] || { printf 'Python is not executable: %s\n' "$python" >&2; exit 1; }
if [[ "$uv" == */* ]]; then
    [[ -x "$uv" ]] || { printf 'uv is not executable: %s\n' "$uv" >&2; exit 1; }
else
    uv="$(command -v "$uv")" || { printf 'uv is required\n' >&2; exit 1; }
fi

printf '%s  %s\n' "$requirements_sha256" "$requirements" |
    sha256sum --check --status || {
        printf 'requirements lock hash mismatch: %s\n' "$requirements" >&2
        exit 1
    }

host="$($python - <<'PY'
import platform, sys
print(f"{sys.version_info.major}.{sys.version_info.minor} {platform.system()} {platform.machine()}")
PY
)"
[[ "$host" == "3.14 Linux x86_64" ]] || {
    printf 'unsupported Python host %s; expected 3.14 Linux x86_64\n' "$host" >&2
    exit 1
}

verify_environment() {
    local root="$1"
    "$root/bin/python" - "$expected_version" <<'PY'
from importlib.metadata import version
import cryptography
import pymobiledevice3
import sys

actual = version("pymobiledevice3")
if actual != sys.argv[1]:
    raise SystemExit(f"pymobiledevice3 version mismatch: {actual} != {sys.argv[1]}")
PY
    [[ "$("$root/bin/pymobiledevice3" version)" == "$expected_version" ]] || {
        printf 'pymobiledevice3 command validation failed in %s\n' "$root" >&2
        return 1
    }
}

python_sha256="$(sha256sum -b "$(realpath "$python")" | cut -d' ' -f1)"
marker="requirements_sha256=$requirements_sha256
pymobiledevice3=$expected_version
host=$host
python_sha256=$python_sha256"

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
    verify_environment "$destination"
    printf 'pymobiledevice3_root=%s\n' "$(realpath "$destination")"
    printf 'pymobiledevice3_version=%s\n' "$expected_version"
    printf 'reused=true\n'
    exit 0
fi

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.pymobiledevice3.XXXXXX")"
cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
}
trap cleanup EXIT

uv_environment=()
if [[ -n "$cache" ]]; then
    [[ ! -L "$cache" ]] || { printf 'cache directory must not be a symlink: %s\n' "$cache" >&2; exit 1; }
    mkdir -p "$cache"
    uv_environment+=("UV_CACHE_DIR=$cache")
fi
uv_arguments=()
[[ "$offline" == false ]] || uv_arguments+=(--offline)
env "${uv_environment[@]}" "$uv" venv --python "$python" "$stage"
env "${uv_environment[@]}" "$uv" pip sync --python "$stage/bin/python" \
    --require-hashes "${uv_arguments[@]}" "$requirements"
verify_environment "$stage"
"$stage/bin/python" - "$stage" "$destination" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
old = sys.argv[1].encode()
new = sys.argv[2].encode()
for path in [root / "pyvenv.cfg", *(root / "bin").iterdir()]:
    if path.is_symlink() or not path.is_file():
        continue
    data = path.read_bytes()
    if old in data:
        path.write_bytes(data.replace(old, new))
PY
printf '%s\n' "$marker" > "$stage/$marker_name"
mv --no-clobber --no-target-directory "$stage" "$destination"
[[ ! -e "$stage" ]] || {
    printf 'pymobiledevice3 destination appeared during publication; refusing to replace it: %s\n' "$destination" >&2
    exit 1
}
stage=""
verify_environment "$destination"

printf 'pymobiledevice3_root=%s\n' "$(realpath "$destination")"
printf 'pymobiledevice3_version=%s\n' "$expected_version"
printf 'reused=false\n'
