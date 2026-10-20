#!/usr/bin/env bash
set -euo pipefail

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly repository="https://github.com/joshuaswarren/xtool.git"
readonly revision="f0a1f90efdbb0dc023e276ff529da92618da7a87"
readonly tree="b9ad5b46fcd0c89951413bee95fa94078accf10f"
readonly upstream_package_resolved_sha256="43e1c442dbb52cb3117837c2da4f115266d5f2f2834fee8bffa3112519c85d7c"
readonly build_package_resolved="$script_dir/xtool-Package.resolved.lock"
readonly build_package_resolved_sha256="978f904cce959c45792fa57baafb4f6f8cbbdda95fbb2d3feb9e25c43b29e89e"
readonly license_sha256="04cf87035e25606762ba596ec29ecac450a281f3d210a8042c91f49645c2e695"
readonly swift_sha256="50863678e3bafd91fcbc94c0bb76610bab37c1ee01e335fc7d8616904e03e19a"
readonly xadi_revision="2aad968e4806b8571f6f2bed22ecec00b0322f95"
readonly xadi_artifact_sha256="2fc1aeb058f6067d23e2f2217a2ea0ec843057e1d82c13db37e30b8219d8d469"
readonly marker_name=".dotnet-ios-linux-xtool"

usage() {
    cat <<'EOF'
Usage: setup-xtool.sh --source-cache <directory> --swift-root <directory> --destination <directory>

Acquires and builds the exact xtool 1.20.1-derived revision selected by the
Xcode 27 backend. Source, build scratch, and output are isolated; no executable
or authentication state is written to ~/.local. The project-owned complete
SwiftPM resolution is enforced without modifying the upstream source cache.
EOF
}

source_cache=""
swift_root=""
destination=""
while (($#)); do
    case "$1" in
        --source-cache) source_cache="${2:?missing value for --source-cache}"; shift 2 ;;
        --swift-root) swift_root="${2:?missing value for --swift-root}"; shift 2 ;;
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$source_cache" && -n "$swift_root" && -n "$destination" ]] || { usage >&2; exit 2; }

for tool in bsdtar file git ldd objcopy readelf sha256sum strip; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
printf '%s  %s\n' "$build_package_resolved_sha256" "$build_package_resolved" |
    sha256sum --check --status || {
        printf 'xtool dependency lock hash mismatch: %s\n' "$build_package_resolved" >&2
        exit 1
    }

[[ -x "$swift_root/usr/bin/swift" ]] || {
    printf 'staged Swift is missing: %s\n' "$swift_root" >&2
    exit 1
}
[[ -f "$swift_root/.dotnet-ios-linux-swift" ]] || {
    printf 'Swift root is not owned by eng/toolchain/setup-swift.sh: %s\n' "$swift_root" >&2
    exit 1
}
grep -qx "swift_sha256=$swift_sha256" "$swift_root/.dotnet-ios-linux-swift" || {
    printf 'Swift root has different provenance: %s\n' "$swift_root" >&2
    exit 1
}
[[ "$("$swift_root/usr/bin/swift" --version | head -n1)" == "Swift version 6.4 (swift-6.4-RELEASE)" ]] || {
    printf 'Swift 6.4.0 is required\n' >&2
    exit 1
}

if [[ -e "$source_cache" ]]; then
    [[ -d "$source_cache/.git" ]] || {
        printf 'xtool source cache is not a git checkout: %s\n' "$source_cache" >&2
        exit 1
    }
    actual="$(git -C "$source_cache" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] || {
        printf 'xtool source is at %s, expected %s; refusing to reset it\n' "$actual" "$revision" >&2
        exit 1
    }
    [[ -z "$(git -C "$source_cache" status --porcelain)" ]] || {
        printf 'xtool source has local changes; refusing to clean it: %s\n' "$source_cache" >&2
        exit 1
    }
else
    mkdir -p "$(dirname "$source_cache")"
    git clone --filter=blob:none --no-checkout "$repository" "$source_cache"
    git -C "$source_cache" checkout --detach "$revision"
fi

[[ "$(git -C "$source_cache" rev-parse 'HEAD^{tree}')" == "$tree" ]] || {
    printf 'xtool source tree mismatch\n' >&2
    exit 1
}
printf '%s  %s\n' "$upstream_package_resolved_sha256" "$source_cache/Package.resolved" |
    sha256sum --check --status
printf '%s  %s\n' "$license_sha256" "$source_cache/LICENSE.md" |
    sha256sum --check --status

provenance_marker="xtool_revision=$revision
xtool_tree=$tree
swift_sha256=$swift_sha256
package_resolved_sha256=$build_package_resolved_sha256"

verify_xtool() {
    local root="$1" version
    version="$(env -u LD_LIBRARY_PATH "$root/xtool" --version)"
    [[ "$version" == *"$revision"* || "$version" == *"${revision:0:7}"* ]] || {
        printf 'unexpected xtool version: %s\n' "$version" >&2
        return 1
    }
    env -u LD_LIBRARY_PATH "$root/xtool" --help >/dev/null
    if env -u LD_LIBRARY_PATH ldd "$root/xtool" | grep -q 'not found'; then
        env -u LD_LIBRARY_PATH ldd "$root/xtool" | grep 'not found' >&2
        return 1
    fi
    readelf -d "$root/xtool" | grep -Fq 'Library runpath: [$ORIGIN]' || {
        printf 'xtool is missing its relocatable $ORIGIN runpath\n' >&2
        return 1
    }
}

if [[ -e "$destination" ]]; then
    [[ -d "$destination" && -f "$destination/$marker_name" ]] || {
        printf 'existing destination is not owned by this setup: %s\n' "$destination" >&2
        exit 1
    }
    while IFS= read -r expected; do
        grep -Fqx "$expected" "$destination/$marker_name" || {
            printf 'existing destination has different provenance; refusing to replace it: %s\n' "$destination" >&2
            exit 1
        }
    done <<< "$provenance_marker"
    xtool_sha256="$(sha256sum "$destination/xtool" | cut -d' ' -f1)"
    xadi_sha256="$(sha256sum "$destination/libXADI.so" | cut -d' ' -f1)"
    grep -Fqx "xtool_sha256=$xtool_sha256" "$destination/$marker_name" || {
        printf 'existing destination failed its xtool integrity check: %s\n' "$destination" >&2
        exit 1
    }
    grep -Fqx "libXADI_sha256=$xadi_sha256" "$destination/$marker_name" || {
        printf 'existing destination failed its libXADI integrity check: %s\n' "$destination" >&2
        exit 1
    }
    verify_xtool "$destination"
    printf 'xtool_root=%s\n' "$(realpath "$destination")"
    printf 'xtool_revision=%s\n' "$revision"
    printf 'xtool_sha256=%s\nlibXADI_sha256=%s\nreused=true\n' \
        "$xtool_sha256" "$xadi_sha256"
    exit 0
fi

parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.xtool.XXXXXX")"
scratch="$parent/.xtool-build-$revision"
[[ ! -e "$scratch" ]] || {
    printf 'xtool build scratch already exists; another build may be active: %s\n' "$scratch" >&2
    rm -rf -- "$stage"
    exit 1
}
mkdir -m700 "$scratch"
cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
    [[ -z "${scratch:-}" || ! -e "$scratch" ]] || rm -rf -- "$scratch"
}
trap cleanup EXIT

export PATH="$swift_root/usr/bin:$PATH"
work="$scratch/source"
mkdir -p "$work"
git -C "$source_cache" archive "$revision" | bsdtar -xf - -C "$work"
cp "$build_package_resolved" "$work/Package.resolved"

prefix_map="$scratch=/usr/src/dotnet-ios-linux/xtool-build"
env -u LD_LIBRARY_PATH XTOOL_VERSION="commit $revision" \
    swift build --package-path "$work" --scratch-path "$scratch/build" \
    --force-resolved-versions -c release --product xtool \
    -Xswiftc -no-toolchain-stdlib-rpath \
    -Xswiftc -debug-prefix-map -Xswiftc "$prefix_map" \
    -Xswiftc -file-prefix-map -Xswiftc "$prefix_map" \
    -Xcc "-fdebug-prefix-map=$prefix_map" \
    -Xcc "-ffile-prefix-map=$prefix_map"
printf '%s  %s\n' "$build_package_resolved_sha256" "$work/Package.resolved" |
    sha256sum --check --status || {
        printf 'SwiftPM changed the enforced xtool dependency lock\n' >&2
        exit 1
    }
xadi_checkout="$scratch/build/checkouts/xadi"
[[ "$(git -C "$xadi_checkout" rev-parse HEAD)" == "$xadi_revision" ]] || {
    printf 'XADI source revision mismatch\n' >&2
    exit 1
}
grep -Fq "checksum: \"$xadi_artifact_sha256\"" "$xadi_checkout/Package.swift" || {
    printf 'XADI artifact checksum mismatch\n' >&2
    exit 1
}
bin="$(env -u LD_LIBRARY_PATH XTOOL_VERSION="commit $revision" \
    swift build --package-path "$work" --scratch-path "$scratch/build" \
    --force-resolved-versions -c release --show-bin-path)"
install -m755 "$bin/xtool" "$bin/libXADI.so" "$stage/"
runtime="$swift_root/usr/lib/swift/linux"
LD_LIBRARY_PATH="$runtime" ldd "$stage/xtool" |
    awk -v runtime="$runtime/" 'index($3, runtime) == 1 { print $3 }' |
    xargs -r install -m644 -t "$stage"
# Swift's gold-linker build ID varies despite byte-identical loadable sections.
# It is not used at runtime, so remove that note before hashing the staged tool.
objcopy --remove-section=.note.gnu.build-id "$stage/xtool"
strip --strip-unneeded "$stage/xtool" "$stage/libXADI.so"
xtool_sha256="$(sha256sum "$stage/xtool" | cut -d' ' -f1)"
xadi_sha256="$(sha256sum "$stage/libXADI.so" | cut -d' ' -f1)"
printf '%s\nxtool_sha256=%s\nlibXADI_sha256=%s\n' \
    "$provenance_marker" "$xtool_sha256" "$xadi_sha256" > "$stage/$marker_name"
verify_xtool "$stage"
mv "$stage" "$destination"
stage=""
verify_xtool "$destination"

printf 'xtool_root=%s\n' "$(realpath "$destination")"
printf 'xtool_revision=%s\n' "$revision"
printf 'xtool_sha256=%s\nlibXADI_sha256=%s\nreused=false\n' \
    "$xtool_sha256" "$xadi_sha256"
