#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
. "$script_dir/../install/download-verified.sh"

readonly python_version="3.14.8"
readonly python_build="20261013"
readonly python_archive="cpython-3.14.8+20261013-x86_64-unknown-linux-gnu-install_only.tar.gz"
readonly python_url="https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.14.8%2B20261013-x86_64-unknown-linux-gnu-install_only.tar.gz"
readonly python_sha256="322271a761140ffb3f522431048f701831e412c8f64770103d2b881ee2bbaed0"
readonly python_license_url="https://raw.githubusercontent.com/astral-sh/python-build-standalone/20261013/LICENSE"
readonly python_license_sha256="1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"
readonly python_licenses_url="https://raw.githubusercontent.com/astral-sh/python-build-standalone/20261013/python-licenses.rst"
readonly python_licenses_sha256="e43fb936c6655d7996dba480d7ebdea492d6040ec388eb8ed9d1000f72de8cab"
readonly cpython_license_sha256="b0e25a78cffb43f4d92de8b61ccfa1f1f98ecbc22330b54b5251e7b6ba010231"
readonly uv_version="0.9.10"
readonly uv_archive="uv-x86_64-unknown-linux-gnu-0.9.10.tar.gz"
readonly uv_url="https://github.com/astral-sh/uv/releases/download/0.9.10/uv-x86_64-unknown-linux-gnu.tar.gz"
readonly uv_sha256="440c4215b171e64061d65d16a23753dd25c29a7f7b1b0446c9e9aed0fa372f27"
readonly uv_license_apache_url="https://raw.githubusercontent.com/astral-sh/uv/0.9.10/LICENSE-APACHE"
readonly uv_license_apache_sha256="c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
readonly uv_license_mit_url="https://raw.githubusercontent.com/astral-sh/uv/0.9.10/LICENSE-MIT"
readonly uv_license_mit_sha256="860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c"
readonly python_executable_sha256="1fd534136499c05c3031d9453d279b257e225072341c68a1d0c4b30b96dc5d17"
readonly uv_executable_sha256="040491bf5feb28ce7409dfc2f9aa1532aeb569aba70f61bd4d3ddb2386d13350"
readonly marker_name=".dotnet-ios-linux-python"

usage() {
    cat <<'EOF'
Usage: setup-python.sh --destination <directory> --cache <directory> [--offline]

Stages pinned portable CPython 3.14.8 and uv 0.9.10 for glibc Linux x86-64.
The destination is project-owned and is never merged into a system Python.
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
for tool in curl install ldd ln mktemp mv realpath sha256sum tar; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || {
    printf 'this Python lock supports Linux x86_64 only\n' >&2
    exit 1
}
getconf GNU_LIBC_VERSION >/dev/null 2>&1 || {
    printf 'this Python lock requires a glibc host\n' >&2
    exit 1
}

[[ ! -L "$cache" ]] || { printf 'cache directory must not be a symlink: %s\n' "$cache" >&2; exit 1; }
mkdir -p "$cache"

download_verified "$python_url" sha256sum "$python_sha256" "$cache/$python_archive" "$offline"
download_verified "$uv_url" sha256sum "$uv_sha256" "$cache/$uv_archive" "$offline"
download_verified "$python_license_url" sha256sum "$python_license_sha256" \
    "$cache/python-build-standalone-20261013-LICENSE" "$offline"
download_verified "$python_licenses_url" sha256sum "$python_licenses_sha256" \
    "$cache/python-build-standalone-20261013-python-licenses.rst" "$offline"
download_verified "$uv_license_apache_url" sha256sum "$uv_license_apache_sha256" \
    "$cache/uv-0.9.10-LICENSE-APACHE" "$offline"
download_verified "$uv_license_mit_url" sha256sum "$uv_license_mit_sha256" \
    "$cache/uv-0.9.10-LICENSE-MIT" "$offline"

marker="python_version=$python_version
python_build=$python_build
python_sha256=$python_sha256
python_license_sha256=$python_license_sha256
python_licenses_sha256=$python_licenses_sha256
uv_version=$uv_version
uv_sha256=$uv_sha256
uv_license_apache_sha256=$uv_license_apache_sha256
uv_license_mit_sha256=$uv_license_mit_sha256
host=Linux x86_64 glibc"

verify_stage() {
    local root="$1" expected actual
    [[ -f "$root/$marker_name" ]] || { printf 'Python ownership marker is missing\n' >&2; return 1; }
    [[ "$(cat "$root/$marker_name")" == "$marker" ]] || {
        printf 'Python stage has different provenance: %s\n' "$root" >&2
        return 1
    }
    actual="$("$root/bin/python3" -c 'import platform,sys; print(f"{platform.python_version()} {platform.system()} {platform.machine()}")')"
    [[ "$actual" == "$python_version Linux x86_64" ]] || {
        printf 'unexpected portable Python host: %s\n' "$actual" >&2
        return 1
    }
    [[ "$("$root/bin/uv" --version)" == "uv $uv_version" ]] || {
        printf 'unexpected uv version\n' >&2
        return 1
    }
    printf '%s  %s\n' "$python_executable_sha256" "$root/bin/python3" | sha256sum --check --status || {
        printf 'portable Python executable hash mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$uv_executable_sha256" "$root/bin/uv" | sha256sum --check --status || {
        printf 'portable uv executable hash mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$cpython_license_sha256" "$root/lib/python3.14/LICENSE.txt" |
        sha256sum --check --status || {
        printf 'CPython license hash mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$python_license_sha256" \
        "$root/share/licenses/python-build-standalone/LICENSE" | sha256sum --check --status || {
        printf 'python-build-standalone license hash mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$python_licenses_sha256" \
        "$root/share/licenses/python-build-standalone/python-licenses.rst" | sha256sum --check --status || {
        printf 'python-build-standalone dependency license inventory hash mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$uv_license_apache_sha256" "$root/share/licenses/uv/LICENSE-APACHE" |
        sha256sum --check --status || {
        printf 'uv Apache license hash mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$uv_license_mit_sha256" "$root/share/licenses/uv/LICENSE-MIT" |
        sha256sum --check --status || {
        printf 'uv MIT license hash mismatch\n' >&2
        return 1
    }
    if env -u LD_LIBRARY_PATH ldd "$root/bin/python3" | grep -q 'not found'; then
        env -u LD_LIBRARY_PATH ldd "$root/bin/python3" | grep 'not found' >&2
        return 1
    fi
}

if [[ -e "$destination" || -L "$destination" ]]; then
    [[ -d "$destination" && ! -L "$destination" && -f "$destination/$marker_name" &&
       ! -L "$destination/$marker_name" ]] || {
        printf 'existing destination is not owned by this setup: %s\n' "$destination" >&2
        exit 1
    }
    verify_stage "$destination"
    printf 'python_root=%s\npython_version=%s\nuv_version=%s\nreused=true\n' \
        "$(realpath "$destination")" "$python_version" "$uv_version"
    exit 0
fi

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.python.XXXXXX")"
cleanup() { [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"; }
trap cleanup EXIT
tar -xzf "$cache/$python_archive" --strip-components=1 --no-same-owner --no-same-permissions -C "$stage"
uv_extract="$(mktemp -d "$parent/.uv.XXXXXX")"
tar -xzf "$cache/$uv_archive" --strip-components=1 --no-same-owner --no-same-permissions -C "$uv_extract"
install -m755 "$uv_extract/uv" "$uv_extract/uvx" "$stage/bin/"
rm -rf -- "$uv_extract"
mkdir -p "$stage/share/licenses/python-build-standalone" "$stage/share/licenses/uv"
install -m644 "$cache/python-build-standalone-20261013-LICENSE" \
    "$stage/share/licenses/python-build-standalone/LICENSE"
install -m644 "$cache/python-build-standalone-20261013-python-licenses.rst" \
    "$stage/share/licenses/python-build-standalone/python-licenses.rst"
install -m644 "$cache/uv-0.9.10-LICENSE-APACHE" "$stage/share/licenses/uv/LICENSE-APACHE"
install -m644 "$cache/uv-0.9.10-LICENSE-MIT" "$stage/share/licenses/uv/LICENSE-MIT"
printf '%s\n' "$marker" > "$stage/$marker_name"
verify_stage "$stage"
mv --no-clobber --no-target-directory "$stage" "$destination"
[[ ! -e "$stage" ]] || {
    printf 'Python destination appeared during publication; refusing to replace it: %s\n' "$destination" >&2
    exit 1
}
stage=""
verify_stage "$destination"

printf 'python_root=%s\npython_version=%s\nuv_version=%s\nreused=false\n' \
    "$(realpath "$destination")" "$python_version" "$uv_version"
