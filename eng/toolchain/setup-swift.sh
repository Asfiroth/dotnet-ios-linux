#!/usr/bin/env bash
set -euo pipefail

readonly swift_url="https://download.swift.org/swift-6.4.0-release/ubi10/swift-6.4.0-RELEASE/swift-6.4.0-RELEASE-ubi10.tar.gz"
readonly swift_sha256="50863678e3bafd91fcbc94c0bb76610bab37c1ee01e335fc7d8616904e03e19a"
readonly patchelf_url="https://archive.archlinux.org/packages/p/patchelf/patchelf-0.19.2-1-x86_64.pkg.tar.zst"
readonly patchelf_sha256="3d4904a9d634de14b7dbacdfc521bf07bb5f20033707f5ff965416b9c17edc3b"
readonly patchelf_sig_sha256="8ecf8549588134603d8aac75ddf1ab4d9d961b9abff2db3fd5b81d595d9fbc9b"
readonly libxml_url="https://archive.archlinux.org/packages/l/libxml2-legacy/libxml2-legacy-2.13.9-2-x86_64.pkg.tar.zst"
readonly libxml_sha256="aac28a52e81422935e3bfa44044030640c5d525ad8f65a9672a3ab7139974652"
readonly libxml_sig_sha256="17ee7b04602a2d5b24f76332f8e146a55a54317a25f11bc17e88a36bdc6d64db"
readonly marker_name=".dotnet-ios-linux-swift"

usage() {
    cat <<'EOF'
Usage: setup-swift.sh --destination <directory> --cache <directory>

Stages the Xcode 27-matched Swift 6.4.0 Linux toolchain without installing an
AUR package. Downloads are hash checked. The two Arch compatibility packages
are also signature checked with the host pacman keyring. Existing unowned or
mismatched destinations are never replaced.
EOF
}

destination=""
cache=""
while (($#)); do
    case "$1" in
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --cache) cache="${2:?missing value for --cache}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$destination" && -n "$cache" ]] || { usage >&2; exit 2; }

for tool in bsdtar curl file pacman-key sha256sum; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || {
    printf 'this Swift lock supports Linux x86_64 only\n' >&2
    exit 1
}
for library in libncursesw.so libpanelw.so libformw.so; do
    [[ -e "/usr/lib/$library" ]] || {
        printf 'host ncurses library is missing: /usr/lib/%s\n' "$library" >&2
        exit 1
    }
done

mkdir -p "$cache"
download() {
    local url="$1" sha="$2" output="$3" part
    if [[ -f "$output" ]] && printf '%s  %s\n' "$sha" "$output" | sha256sum --check --status; then
        return
    fi
    [[ ! -e "$output" ]] || {
        printf 'cached file has the wrong hash; refusing to replace it: %s\n' "$output" >&2
        exit 1
    }
    part="$output.part.$$"
    trap 'rm -f -- "$part"' RETURN
    curl -fL "$url" -o "$part"
    printf '%s  %s\n' "$sha" "$part" | sha256sum --check --status || {
        printf 'download hash mismatch: %s\n' "$url" >&2
        exit 1
    }
    mv "$part" "$output"
    trap - RETURN
}

swift_archive="$cache/swift-6.4.0-RELEASE-ubi10.tar.gz"
patchelf_package="$cache/patchelf-0.19.2-1-x86_64.pkg.tar.zst"
libxml_package="$cache/libxml2-legacy-2.13.9-2-x86_64.pkg.tar.zst"
download "$swift_url" "$swift_sha256" "$swift_archive"
download "$patchelf_url" "$patchelf_sha256" "$patchelf_package"
download "$patchelf_url.sig" "$patchelf_sig_sha256" "$patchelf_package.sig"
download "$libxml_url" "$libxml_sha256" "$libxml_package"
download "$libxml_url.sig" "$libxml_sig_sha256" "$libxml_package.sig"
pacman-key --verify "$patchelf_package.sig" "$patchelf_package" >/dev/null
pacman-key --verify "$libxml_package.sig" "$libxml_package" >/dev/null

marker="swift_sha256=$swift_sha256
patchelf_sha256=$patchelf_sha256
libxml2_legacy_sha256=$libxml_sha256
host=Linux x86_64"

verify_toolchain() {
    local root="$1" smoke actual
    actual="$("$root/usr/bin/swift" --version | head -n1)"
    [[ "$actual" == "Swift version 6.4 (swift-6.4-RELEASE)" ]] || {
        printf 'unexpected Swift version: %s\n' "$actual" >&2
        return 1
    }
    [[ "$("$root/usr/bin/clang" --version | head -n1)" == clang\ version\ 21.0.0* ]] || {
        printf 'unexpected bundled Clang version\n' >&2
        return 1
    }
    if ldd "$root/usr/bin/swift-frontend" | grep -q 'not found'; then
        ldd "$root/usr/bin/swift-frontend" | grep 'not found' >&2
        return 1
    fi

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

if [[ -e "$destination" ]]; then
    [[ -d "$destination" && -f "$destination/$marker_name" ]] || {
        printf 'existing destination is not owned by this setup: %s\n' "$destination" >&2
        exit 1
    }
    [[ "$(cat "$destination/$marker_name")" == "$marker" ]] || {
        printf 'existing destination has different provenance; refusing to replace it: %s\n' "$destination" >&2
        exit 1
    }
    verify_toolchain "$destination"
    printf 'swift_root=%s\n' "$(realpath "$destination")"
    printf 'swift_version=6.4.0\nreused=true\n'
    exit 0
fi

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.swift.XXXXXX")"
support="$(mktemp -d "$parent/.swift-support.XXXXXX")"
cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
    [[ -z "${support:-}" || ! -e "$support" ]] || rm -rf -- "$support"
}
trap cleanup EXIT

bsdtar -xzf "$swift_archive" --strip-components=1 -C "$stage"
mkdir -p "$support/patchelf" "$support/libxml"
bsdtar -xf "$patchelf_package" -C "$support/patchelf" usr/bin/patchelf
bsdtar -xf "$libxml_package" -C "$support/libxml" \
    usr/lib/libxml2.so.2 usr/lib/libxml2.so.2.13.9 \
    usr/share/licenses/libxml2-legacy/Copyright
patchelf="$support/patchelf/usr/bin/patchelf"

while IFS= read -r -d '' candidate; do
    file -b "$candidate" | grep -q '^ELF ' || continue
    "$patchelf" \
        --replace-needed libncurses.so.6 libncursesw.so \
        --replace-needed libpanel.so.6 libpanelw.so \
        --replace-needed libform.so.6 libformw.so \
        "$candidate"
done < <(find "$stage/usr" -executable -type f \
    \( -path '*/bin/*' -o -name '*.so*' \) \
    ! \( -name '*.py' -o -name hwasan_symbolize \) -print0)

cp -L "$support/libxml/usr/lib/libxml2.so.2" "$stage/usr/lib/swift/linux/libxml2.so.2"
cp -L "$support/libxml/usr/lib/libxml2.so.2" "$stage/usr/lib/libxml2.so.2"
mkdir -p "$stage/share/licenses/libxml2-legacy"
cp "$support/libxml/usr/share/licenses/libxml2-legacy/Copyright" \
    "$stage/share/licenses/libxml2-legacy/"
printf '%s\n' "$marker" > "$stage/$marker_name"
verify_toolchain "$stage"
mv "$stage" "$destination"
stage=""
verify_toolchain "$destination"

printf 'swift_root=%s\n' "$(realpath "$destination")"
printf 'swift_version=6.4.0\nreused=false\n'
