#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
. "$script_dir/../install/download-verified.sh"

readonly swift_version="6.4.0"
readonly swift_url="https://download.swift.org/swift-6.4.0-release/ubi10/swift-6.4.0-RELEASE/swift-6.4.0-RELEASE-ubi10.tar.gz"
readonly swift_sha256="50863678e3bafd91fcbc94c0bb76610bab37c1ee01e335fc7d8616904e03e19a"
readonly ncurses_version="6.6"
readonly ncurses_url="https://ftp.gnu.org/gnu/ncurses/ncurses-6.6.tar.gz"
readonly ncurses_sha256="355b4cbbed880b0381a04c46617b7656e362585d52e9cf84a67e2009b749ff11"
readonly libxml_version="2.13.9"
readonly libxml_url="https://download.gnome.org/sources/libxml2/2.13/libxml2-2.13.9.tar.xz"
readonly libxml_sha256="a2c9ae7b770da34860050c309f903221c67830c86e4a7e760692b803df95143a"
readonly swift_binary_sha256="eb654eb03c86e363f3ff7a251f34aa0740efd49fcf260cd4c43041d20a60b52b"
readonly swift_frontend_sha256="eb09157a34c8c030abff718d06b0a4b721b70dc4da910bbaaa4c2c0b5299d446"
readonly clang_binary_sha256="3753e74f2ae853e3455fcf81877a0e1fd8906cd557e74aecf2b63108d3c097c3"
readonly ncurses_output_sha256="c4cd27191189c942084a8973467baa5b83ab8067d951c4b7281ae56f61b82fe0"
readonly panel_output_sha256="7d35183a1ee354e5c0307208129882ba8b18fce3f8348ee50c9fe62067e83387"
readonly form_output_sha256="82fb1a593b08d9801b850a6341cd6dc3c6205a0919f6099012ff4970d5a15c09"
readonly libxml_output_sha256="3a23f2967325395145b43da7b1901eeadb15060f36179ae441ecd419756961b0"
readonly marker_name=".dotnet-ios-linux-swift"

usage() {
    cat <<'EOF'
Usage: setup-swift.sh --destination <directory> --cache <directory> [--offline]

Stages the Xcode 27-matched Swift 6.4.0 UBI toolchain for a glibc x86-64
host. Pinned ncurses 6.6 and libxml2 2.13.9 compatibility libraries are built
from hash-verified upstream source and installed only in Swift's private runtime
directory. No distro package manager, /usr mutation, or LD_LIBRARY_PATH is used.
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

for tool in curl file install ldd ln make mktemp mv realpath sha256sum tar; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || {
    printf 'this Swift lock supports Linux x86_64 only\n' >&2
    exit 1
}
getconf GNU_LIBC_VERSION >/dev/null 2>&1 || {
    printf 'this Swift lock requires a glibc host\n' >&2
    exit 1
}

[[ ! -L "$cache" ]] || { printf 'cache directory must not be a symlink: %s\n' "$cache" >&2; exit 1; }
mkdir -p "$cache"

swift_archive="$cache/swift-6.4.0-RELEASE-ubi10.tar.gz"
ncurses_archive="$cache/ncurses-6.6.tar.gz"
libxml_archive="$cache/libxml2-2.13.9.tar.xz"
download_verified "$swift_url" sha256sum "$swift_sha256" "$swift_archive" "$offline"
download_verified "$ncurses_url" sha256sum "$ncurses_sha256" "$ncurses_archive" "$offline"
download_verified "$libxml_url" sha256sum "$libxml_sha256" "$libxml_archive" "$offline"

provenance_marker="swift_version=$swift_version
swift_sha256=$swift_sha256
ncurses_version=$ncurses_version
ncurses_sha256=$ncurses_sha256
libxml2_version=$libxml_version
libxml2_sha256=$libxml_sha256
host=Linux x86_64 glibc"

verify_toolchain() {
    local root="$1" actual smoke smoke_exe name expected
    for expected in \
        "swift_version=$swift_version" \
        "swift_sha256=$swift_sha256" \
        "ncurses_version=$ncurses_version" \
        "ncurses_sha256=$ncurses_sha256" \
        "libxml2_version=$libxml_version" \
        "libxml2_sha256=$libxml_sha256" \
        "host=Linux x86_64 glibc"; do
        grep -Fqx "$expected" "$root/$marker_name" || {
            printf 'Swift stage provenance mismatch: %s\n' "$expected" >&2
            return 1
        }
    done
    printf '%s  %s\n' "$swift_binary_sha256" "$root/usr/bin/swift" | sha256sum --check --status || return 1
    printf '%s  %s\n' "$swift_frontend_sha256" "$root/usr/bin/swift-frontend" | sha256sum --check --status || return 1
    printf '%s  %s\n' "$clang_binary_sha256" "$root/usr/bin/clang" | sha256sum --check --status || return 1
    while read -r name expected_hash; do
        [[ -e "$root/usr/lib/swift/linux/$name" ]] || {
            printf 'Swift private compatibility library is missing: %s\n' "$name" >&2
            return 1
        }
        actual="$(sha256sum -b "$root/usr/lib/swift/linux/$name" | cut -d' ' -f1)"
        [[ "$actual" == "$expected_hash" ]] && \
            grep -Fqx "${name}_sha256=$expected_hash" "$root/$marker_name" || {
            printf 'Swift private compatibility library failed integrity validation: %s\n' "$name" >&2
            return 1
        }
    done <<EOF
libncurses.so.6 $ncurses_output_sha256
libpanel.so.6 $panel_output_sha256
libform.so.6 $form_output_sha256
libxml2.so.2 $libxml_output_sha256
EOF
    actual="$("$root/usr/bin/swift" --version | head -n1)"
    [[ "$actual" == "Swift version 6.4 (swift-6.4-RELEASE)" ]] || {
        printf 'unexpected Swift version: %s\n' "$actual" >&2
        return 1
    }
    [[ "$("$root/usr/bin/clang" --version | head -n1)" == clang\ version\ 21.0.0* ]] || {
        printf 'unexpected bundled Clang version\n' >&2
        return 1
    }
    for name in swift-package swift-frontend lld; do
        if env -u LD_LIBRARY_PATH ldd "$root/usr/bin/$name" | grep -q 'not found'; then
            env -u LD_LIBRARY_PATH ldd "$root/usr/bin/$name" | grep 'not found' >&2
            return 1
        fi
    done

    smoke="$(mktemp -d "$(dirname "$root")/.swift-smoke.XXXXXX")"
    mkdir -p "$smoke/Sources/SwiftHostSmoke"
    cat > "$smoke/Package.swift" <<'EOF'
// swift-tools-version:6.4
import PackageDescription
let package = Package(name: "SwiftHostSmoke", targets: [.executableTarget(name: "SwiftHostSmoke")])
EOF
    printf 'print("swift-host-ok")\n' > "$smoke/Sources/SwiftHostSmoke/main.swift"
    env -u LD_LIBRARY_PATH PATH="$root/usr/bin:$PATH" \
        "$root/usr/bin/swift" build --package-path "$smoke" -c release >/dev/null
    smoke_exe="$(find "$smoke/.build" -type f -name SwiftHostSmoke -perm -u+x | head -n1)"
    [[ -n "$smoke_exe" && "$($smoke_exe)" == "swift-host-ok" ]] || {
        rm -rf -- "$smoke"
        printf 'Swift package smoke failed\n' >&2
        return 1
    }
    rm -rf -- "$smoke"
}

if [[ -e "$destination" || -L "$destination" ]]; then
    [[ -d "$destination" && ! -L "$destination" && -f "$destination/$marker_name" &&
       ! -L "$destination/$marker_name" ]] || {
        printf 'existing destination is not owned by this setup: %s\n' "$destination" >&2
        exit 1
    }
    verify_toolchain "$destination"
    printf 'swift_root=%s\n' "$(realpath "$destination")"
    printf 'swift_version=%s\nreused=true\n' "$swift_version"
    exit 0
fi

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.swift.XXXXXX")"
support="$(mktemp -d "$cache/.swift-support.XXXXXX")"
cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
    [[ -z "${support:-}" || ! -e "$support" ]] || rm -rf -- "$support"
}
trap cleanup EXIT

tar -xzf "$swift_archive" --strip-components=1 --no-same-owner --no-same-permissions -C "$stage"
mkdir -p "$support/source" "$support/ncurses-build" "$support/ncurses-prefix" \
    "$support/libxml-build" "$support/libxml-prefix"
tar -xzf "$ncurses_archive" --no-same-owner --no-same-permissions -C "$support/source"
tar -xJf "$libxml_archive" --no-same-owner --no-same-permissions -C "$support/source"

support_prefix_map="$support=/usr/src/dotnet-ios-linux/swift-support"
support_cc="$stage/usr/bin/clang"
support_cflags="-O2 -g0 -ffile-prefix-map=$support_prefix_map -fdebug-prefix-map=$support_prefix_map"
support_ldflags="-Wl,--build-id=none"

(
    cd "$support/ncurses-build"
    PATH="$stage/usr/bin:$PATH" CC="$support_cc" AR="$stage/usr/bin/llvm-ar" \
        RANLIB="$stage/usr/bin/llvm-ranlib" CFLAGS="$support_cflags" \
        LDFLAGS="$support_ldflags" SOURCE_DATE_EPOCH=1767225600 \
        "$support/source/ncurses-$ncurses_version/configure" \
        --prefix="$support/ncurses-prefix" \
        --disable-widec --with-shared --without-normal --without-debug \
        --without-ada --without-cxx --without-cxx-binding --without-manpages \
        --without-tests --disable-db-install \
        --with-default-terminfo-dir=/usr/share/terminfo \
        --with-terminfo-dirs=/etc/terminfo:/lib/terminfo:/usr/share/terminfo
    make --jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 1)" libs
    make install.libs
)
(
    cd "$support/libxml-build"
    PATH="$stage/usr/bin:$PATH" CC="$support_cc" AR="$stage/usr/bin/llvm-ar" \
        RANLIB="$stage/usr/bin/llvm-ranlib" CFLAGS="$support_cflags" \
        LDFLAGS="$support_ldflags" SOURCE_DATE_EPOCH=1767225600 \
        "$support/source/libxml2-$libxml_version/configure" \
        --prefix="$support/libxml-prefix" --without-python --without-lzma \
        --without-zlib --without-iconv --without-readline --without-http \
        --without-ftp --without-catalog --without-modules
    make --jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 1)"
    make install
)

private_lib="$stage/usr/lib/swift/linux"
for library in \
    "$support/ncurses-prefix/lib/libncurses.so.6.6" \
    "$support/ncurses-prefix/lib/libpanel.so.6.6" \
    "$support/ncurses-prefix/lib/libform.so.6.6" \
    "$support/libxml-prefix/lib/libxml2.so.2.13.9"; do
    "$stage/usr/bin/llvm-objcopy" --remove-section=.comment "$library"
done
for name in ncurses panel form; do
    install -m644 "$support/ncurses-prefix/lib/lib$name.so.6.6" "$private_lib/lib$name.so.6.6"
    ln -s "lib$name.so.6.6" "$private_lib/lib$name.so.6"
done
install -m644 "$support/libxml-prefix/lib/libxml2.so.2.13.9" "$private_lib/libxml2.so.2.13.9"
ln -s libxml2.so.2.13.9 "$private_lib/libxml2.so.2"
install -m644 "$support/libxml-prefix/lib/libxml2.so.2.13.9" "$stage/usr/lib/libxml2.so.2.13.9"
ln -s libxml2.so.2.13.9 "$stage/usr/lib/libxml2.so.2"

mkdir -p "$stage/share/licenses/ncurses" "$stage/share/licenses/libxml2"
install -m644 "$support/source/ncurses-$ncurses_version/COPYING" "$stage/share/licenses/ncurses/COPYING"
install -m644 "$support/source/libxml2-$libxml_version/Copyright" "$stage/share/licenses/libxml2/Copyright"

{
    printf '%s\n' "$provenance_marker"
    printf 'libncurses.so.6_sha256=%s\n' "$ncurses_output_sha256"
    printf 'libpanel.so.6_sha256=%s\n' "$panel_output_sha256"
    printf 'libform.so.6_sha256=%s\n' "$form_output_sha256"
    printf 'libxml2.so.2_sha256=%s\n' "$libxml_output_sha256"
} > "$stage/$marker_name"
verify_toolchain "$stage"
mv --no-clobber --no-target-directory "$stage" "$destination"
[[ ! -e "$stage" ]] || {
    printf 'Swift destination appeared during publication; refusing to replace it: %s\n' "$destination" >&2
    exit 1
}
stage=""
verify_toolchain "$destination"

printf 'swift_root=%s\n' "$(realpath "$destination")"
printf 'swift_version=%s\nreused=false\n' "$swift_version"
