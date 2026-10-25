#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
. "$script_dir/../install/download-verified.sh"

readonly version="2.47"
readonly archive_name="binutils-$version.tar.bz2"
readonly archive_url="https://ftp.gnu.org/gnu/binutils/$archive_name"
readonly archive_sha256="3068128c75cda9f898ccb4211d360246e8e195ffcc9dfb655b23ae23a54800e8"
readonly source_date_epoch="1785024000"
readonly swift_sha256="50863678e3bafd91fcbc94c0bb76610bab37c1ee01e335fc7d8616904e03e19a"
readonly clang_sha256="3753e74f2ae853e3455fcf81877a0e1fd8906cd557e74aecf2b63108d3c097c3"
readonly lld_sha256="ba3e98cb088a0a16b40f12cf66d7e63be1e5f94fd62687504f6a814f9de1941e"
readonly llvm_ar_sha256="9ed85673eba8650d91c7eac4b251cdfc99c29b8318634a3da8d94361b8617abc"
readonly llvm_nm_sha256="d90b641f9efbb71a7b23a6a52d944289f5da9b4ca7f11de2c0f86780a43ada65"
readonly llvm_objcopy_sha256="02901f6141b43b490e5b67ae9cc8f0414025897e1051333e1c32ca0a5fb4b0b3"
readonly llvm_objdump_sha256="f8ce2e273275327efde83ec2795b5c9aa1a73e11c3f24cec60d898b39148c71f"
readonly objcopy_sha256="33959aff8231f8e050b3603eeddf75bf66e54a41f8cdb382371c202d5be370e0"
readonly strip_sha256="705d6ecb90640da4844a9f2b847f077c8d721f1b613adc2f52b3c8daa243d49e"
readonly readelf_sha256="9e61a3bb5cbf44dd92504562e751f737e636464d5425c28967323ef7b6545234"
readonly copying_sha256="8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"
readonly marker_name=".dotnet-ios-linux-binutils"

usage() {
    cat <<'EOF'
Usage: setup-binutils.sh --destination <directory> --cache <directory>
       --swift-root <directory> [--offline]

Builds and transactionally stages the exact GNU Binutils 2.47 objcopy, strip,
and readelf tools used by xtool normalization. The hash-verified source is built
with the locked Swift Clang/LLD stage; ambient binutils are never staged.
EOF
}

destination=""
cache=""
swift_root=""
offline=false
while (($#)); do
    case "$1" in
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --cache) cache="${2:?missing value for --cache}"; shift 2 ;;
        --swift-root) swift_root="${2:?missing value for --swift-root}"; shift 2 ;;
        --offline) offline=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$destination" && -n "$cache" && -n "$swift_root" ]] || { usage >&2; exit 2; }

for tool in bzip2 curl file getconf grep install ldd make mktemp mv realpath sha256sum tar; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || {
    printf 'this Binutils lock supports Linux x86_64 only\n' >&2
    exit 1
}
getconf GNU_LIBC_VERSION >/dev/null 2>&1 || {
    printf 'this Binutils lock requires a glibc host\n' >&2
    exit 1
}

verify_hash() {
    local expected="$1" path="$2"
    printf '%s  %s\n' "$expected" "$path" | sha256sum --check --status
}

verify_swift_tool() {
    local relative="$1" expected="$2" path
    path="$swift_root/usr/bin/$relative"
    [[ -f "$path" && -x "$path" ]] || {
        printf 'required Swift build tool is missing: %s\n' "$path" >&2
        return 1
    }
    verify_hash "$expected" "$path" || {
        printf 'Swift build tool hash mismatch: %s\n' "$path" >&2
        return 1
    }
}

[[ -f "$swift_root/.dotnet-ios-linux-swift" && ! -L "$swift_root/.dotnet-ios-linux-swift" ]] || {
    printf 'Swift root is not owned by eng/toolchain/setup-swift.sh: %s\n' "$swift_root" >&2
    exit 1
}
grep -Fqx "swift_sha256=$swift_sha256" "$swift_root/.dotnet-ios-linux-swift" || {
    printf 'Swift root has different provenance: %s\n' "$swift_root" >&2
    exit 1
}
verify_swift_tool clang "$clang_sha256"
verify_swift_tool clang++ "$clang_sha256"
verify_swift_tool ld.lld "$lld_sha256"
verify_swift_tool llvm-ar "$llvm_ar_sha256"
verify_swift_tool llvm-ranlib "$llvm_ar_sha256"
verify_swift_tool llvm-nm "$llvm_nm_sha256"
verify_swift_tool llvm-objcopy "$llvm_objcopy_sha256"
verify_swift_tool llvm-objdump "$llvm_objdump_sha256"

marker="version=$version
archive_sha256=$archive_sha256
source_date_epoch=$source_date_epoch
swift_sha256=$swift_sha256
clang_sha256=$clang_sha256
lld_sha256=$lld_sha256
llvm_ar_sha256=$llvm_ar_sha256
llvm_nm_sha256=$llvm_nm_sha256
llvm_objcopy_sha256=$llvm_objcopy_sha256
llvm_objdump_sha256=$llvm_objdump_sha256
objcopy_sha256=$objcopy_sha256
strip_sha256=$strip_sha256
readelf_sha256=$readelf_sha256
copying_sha256=$copying_sha256
host=Linux x86_64 glibc"

verify_stage() {
    local root="$1" tool output
    for tool in objcopy strip readelf; do
        [[ -f "$root/bin/$tool" && ! -L "$root/bin/$tool" && -x "$root/bin/$tool" ]] || {
            printf 'staged Binutils tool is missing or invalid: %s\n' "$root/bin/$tool" >&2
            return 1
        }
    done
    verify_hash "$objcopy_sha256" "$root/bin/objcopy" || return 1
    verify_hash "$strip_sha256" "$root/bin/strip" || return 1
    verify_hash "$readelf_sha256" "$root/bin/readelf" || return 1
    verify_hash "$copying_sha256" "$root/share/licenses/binutils/COPYING3" || return 1
    output="$("$root/bin/objcopy" --version | head -n1)"
    [[ "$output" == "GNU objcopy (GNU Binutils) 2.47.20260726" ]] || {
        printf 'unexpected staged objcopy version: %s\n' "$output" >&2
        return 1
    }
    output="$("$root/bin/strip" --version | head -n1)"
    [[ "$output" == "GNU strip (GNU Binutils) 2.47.20260726" ]] || {
        printf 'unexpected staged strip version: %s\n' "$output" >&2
        return 1
    }
    output="$("$root/bin/readelf" --version | head -n1)"
    [[ "$output" == "GNU readelf (GNU Binutils) 2.47.20260726" ]] || {
        printf 'unexpected staged readelf version: %s\n' "$output" >&2
        return 1
    }
    for tool in objcopy strip readelf; do
        "$root/bin/readelf" -h "$root/bin/$tool" |
            grep -Eq '^  Machine:[[:space:]]+Advanced Micro Devices X86-64$' || return 1
        if ldd "$root/bin/$tool" | grep -q 'not found'; then
            ldd "$root/bin/$tool" | grep 'not found' >&2
            return 1
        fi
    done
}

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
    verify_stage "$destination"
    printf 'binutils_root=%s\n' "$(realpath "$destination")"
    printf 'binutils_version=%s\nreused=true\n' "$version"
    exit 0
fi

[[ ! -L "$cache" ]] || { printf 'cache directory must not be a symlink: %s\n' "$cache" >&2; exit 1; }
mkdir -p "$cache"
archive="$cache/$archive_name"
download_verified "$archive_url" sha256sum "$archive_sha256" "$archive" "$offline"

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.binutils.XXXXXX")"
build_root="$(mktemp -d "$cache/.binutils-build.XXXXXX")"
cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
    [[ -z "${build_root:-}" || ! -e "$build_root" ]] || rm -rf -- "$build_root"
}
trap cleanup EXIT

mkdir -p "$build_root/source" "$build_root/build" "$stage/bin" "$stage/share/licenses/binutils"
tar -xjf "$archive" --strip-components=1 --no-same-owner --no-same-permissions \
    -C "$build_root/source"
verify_hash "$copying_sha256" "$build_root/source/COPYING3" || {
    printf 'Binutils source license hash mismatch\n' >&2
    exit 1
}

prefix_map="$build_root=/usr/src/dotnet-ios-linux/binutils-2.47"
(
    export PATH="$swift_root/usr/bin:/usr/bin:/bin"
    export CC="$swift_root/usr/bin/clang"
    export CXX="$swift_root/usr/bin/clang++"
    export LD="$swift_root/usr/bin/ld.lld"
    export AR="$swift_root/usr/bin/llvm-ar"
    export RANLIB="$swift_root/usr/bin/llvm-ranlib"
    export NM="$swift_root/usr/bin/llvm-nm"
    export OBJDUMP="$swift_root/usr/bin/llvm-objdump"
    export OBJCOPY="$swift_root/usr/bin/llvm-objcopy"
    export STRIP=/usr/bin/true
    export MAKEINFO=/usr/bin/true
    export CFLAGS="-O2 -g0 -ffile-prefix-map=$prefix_map -fdebug-prefix-map=$prefix_map"
    export CXXFLAGS="$CFLAGS"
    export LDFLAGS="-fuse-ld=lld -Wl,--build-id=none"
    export SOURCE_DATE_EPOCH="$source_date_epoch"
    cd "$build_root/build"
    "$build_root/source/configure" \
        --prefix=/usr/lib/dotnet-ios-linux/binutils-2.47 \
        --disable-nls --disable-werror --disable-gdb --disable-gdbserver \
        --disable-gprof --disable-gprofng --disable-gas --disable-ld --disable-gold \
        --disable-sim --disable-libdecnumber --disable-readline --disable-libctf \
        --without-zstd --enable-shared=no --enable-static=yes
    make --jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 1)" all-binutils
)

install -m755 "$build_root/build/binutils/objcopy" "$stage/bin/objcopy"
install -m755 "$build_root/build/binutils/strip-new" "$stage/bin/strip"
install -m755 "$build_root/build/binutils/readelf" "$stage/bin/readelf"
install -m644 "$build_root/source/COPYING3" "$stage/share/licenses/binutils/COPYING3"
printf '%s\n' "$marker" > "$stage/$marker_name"
verify_stage "$stage"

mv --no-clobber --no-target-directory "$stage" "$destination"
[[ ! -e "$stage" ]] || {
    printf 'Binutils destination appeared during publication; refusing to replace it: %s\n' "$destination" >&2
    exit 1
}
stage=""
rm -rf -- "$build_root"
build_root=""
verify_stage "$destination"

printf 'binutils_root=%s\n' "$(realpath "$destination")"
printf 'binutils_version=%s\nreused=false\n' "$version"
