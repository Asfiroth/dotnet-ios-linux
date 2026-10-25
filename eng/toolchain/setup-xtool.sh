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
readonly swift_ncurses_sha256="355b4cbbed880b0381a04c46617b7656e362585d52e9cf84a67e2009b749ff11"
readonly swift_libxml2_sha256="a2c9ae7b770da34860050c309f903221c67830c86e4a7e760692b803df95143a"
readonly binutils_archive_sha256="3068128c75cda9f898ccb4211d360246e8e195ffcc9dfb655b23ae23a54800e8"
readonly binutils_objcopy_sha256="33959aff8231f8e050b3603eeddf75bf66e54a41f8cdb382371c202d5be370e0"
readonly binutils_strip_sha256="705d6ecb90640da4844a9f2b847f077c8d721f1b613adc2f52b3c8daa243d49e"
readonly binutils_readelf_sha256="9e61a3bb5cbf44dd92504562e751f737e636464d5425c28967323ef7b6545234"
readonly xadi_revision="2aad968e4806b8571f6f2bed22ecec00b0322f95"
readonly xadi_artifact_sha256="2fc1aeb058f6067d23e2f2217a2ea0ec843057e1d82c13db37e30b8219d8d469"
readonly xtool_output_sha256="c13e2270e6e9d9cce21ba7daae1b51016fcbd50c1bfd2c6747b1ebd208cb168d"
readonly xadi_output_sha256="eb68bd3dad62f68eac166aad7dc4e8c931013f42a529a8f5d4c520e671375350"
readonly license_inventory_sha256="5d90a5c5dc3983b5ccb5d24ee0256b4406304db5a3a37246efecbc6ef66bc59f"
readonly marker_name=".dotnet-ios-linux-xtool"
readonly logical_scratch="/tmp/dotnet-ios-linux/.xtool-build-$revision"
readonly fixed_mount_runner="$script_dir/run-in-fixed-mount.sh"

usage() {
    cat <<'EOF'
Usage: setup-xtool.sh --source-cache <directory> --swift-root <directory>
       --binutils-root <directory> --destination <directory>
       [--package-cache <directory>] [--diagnostics-dir <new-directory>] [--offline]

Acquires and builds the exact xtool 1.20.1-derived revision selected by the
Xcode 27 backend. Source, build scratch, and output are isolated; no executable
or authentication state is written to ~/.local. The project-owned complete
SwiftPM resolution is enforced without modifying the upstream source cache.
EOF
}

source_cache=""
swift_root=""
binutils_root=""
destination=""
package_cache=""
diagnostics_dir=""
offline=false
while (($#)); do
    case "$1" in
        --source-cache) source_cache="${2:?missing value for --source-cache}"; shift 2 ;;
        --swift-root) swift_root="${2:?missing value for --swift-root}"; shift 2 ;;
        --binutils-root) binutils_root="${2:?missing value for --binutils-root}"; shift 2 ;;
        --destination) destination="${2:?missing value for --destination}"; shift 2 ;;
        --package-cache) package_cache="${2:?missing value for --package-cache}"; shift 2 ;;
        --diagnostics-dir) diagnostics_dir="${2:?missing value for --diagnostics-dir}"; shift 2 ;;
        --offline) offline=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$source_cache" && -n "$swift_root" && -n "$binutils_root" && -n "$destination" ]] || {
    usage >&2
    exit 2
}

for tool in awk cmp cp file find findmnt git head install ldd mkdir mktemp mount mv realpath sha256sum sort tar unshare xargs; do
    command -v "$tool" >/dev/null || { printf '%s is required\n' "$tool" >&2; exit 1; }
done
[[ -x "$fixed_mount_runner" && ! -L "$fixed_mount_runner" ]] || {
    printf 'fixed mount runner is missing or invalid: %s\n' "$fixed_mount_runner" >&2
    exit 1
}
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
grep -qx "ncurses_sha256=$swift_ncurses_sha256" "$swift_root/.dotnet-ios-linux-swift" || {
    printf 'Swift root has different ncurses provenance: %s\n' "$swift_root" >&2
    exit 1
}
grep -qx "libxml2_sha256=$swift_libxml2_sha256" "$swift_root/.dotnet-ios-linux-swift" || {
    printf 'Swift root has different libxml2 provenance: %s\n' "$swift_root" >&2
    exit 1
}
[[ "$("$swift_root/usr/bin/swift" --version | head -n1)" == "Swift version 6.4 (swift-6.4-RELEASE)" ]] || {
    printf 'Swift 6.4.0 is required\n' >&2
    exit 1
}

[[ -f "$binutils_root/.dotnet-ios-linux-binutils" &&
   ! -L "$binutils_root/.dotnet-ios-linux-binutils" ]] || {
    printf 'Binutils root is not owned by eng/toolchain/setup-binutils.sh: %s\n' "$binutils_root" >&2
    exit 1
}
for expected in \
    "archive_sha256=$binutils_archive_sha256" \
    "objcopy_sha256=$binutils_objcopy_sha256" \
    "strip_sha256=$binutils_strip_sha256" \
    "readelf_sha256=$binutils_readelf_sha256"; do
    grep -Fqx "$expected" "$binutils_root/.dotnet-ios-linux-binutils" || {
        printf 'Binutils root has different provenance: %s\n' "$expected" >&2
        exit 1
    }
done
objcopy_tool="$binutils_root/bin/objcopy"
strip_tool="$binutils_root/bin/strip"
readelf_tool="$binutils_root/bin/readelf"
for tool_and_hash in \
    "$objcopy_tool:$binutils_objcopy_sha256" \
    "$strip_tool:$binutils_strip_sha256" \
    "$readelf_tool:$binutils_readelf_sha256"; do
    tool_path="${tool_and_hash%:*}"
    tool_hash="${tool_and_hash##*:}"
    [[ -f "$tool_path" && ! -L "$tool_path" && -x "$tool_path" ]] || {
        printf 'staged Binutils tool is missing or invalid: %s\n' "$tool_path" >&2
        exit 1
    }
    printf '%s  %s\n' "$tool_hash" "$tool_path" | sha256sum --check --status || {
        printf 'staged Binutils tool hash mismatch: %s\n' "$tool_path" >&2
        exit 1
    }
done

verify_source_cache() {
    local root="$1" actual
    [[ ! -L "$root" && -d "$root/.git" ]] || {
        printf 'xtool source cache is not a real git checkout: %s\n' "$root" >&2
        return 1
    }
    actual="$(git -C "$root" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] || {
        printf 'xtool source is at %s, expected %s; refusing to reset it\n' "$actual" "$revision" >&2
        return 1
    }
    [[ -z "$(git -C "$root" status --porcelain)" ]] || {
        printf 'xtool source has local changes; refusing to clean it: %s\n' "$root" >&2
        return 1
    }
    [[ "$(git -C "$root" rev-parse 'HEAD^{tree}')" == "$tree" ]] || {
        printf 'xtool source tree mismatch\n' >&2
        return 1
    }
    printf '%s  %s\n' "$upstream_package_resolved_sha256" "$root/Package.resolved" |
        sha256sum --check --status
    printf '%s  %s\n' "$license_sha256" "$root/LICENSE.md" |
        sha256sum --check --status
}

if [[ -e "$source_cache" || -L "$source_cache" ]]; then
    verify_source_cache "$source_cache"
else
    [[ "$offline" == false ]] || {
        printf 'xtool source is unavailable in offline mode: %s\n' "$source_cache" >&2
        exit 1
    }
    source_parent="$(dirname "$source_cache")"
    mkdir -p "$source_parent"
    source_stage="$(mktemp -d "$source_parent/.xtool-source.XXXXXX")"
    rmdir "$source_stage"
    cleanup_source() { [[ -z "${source_stage:-}" || ! -e "$source_stage" ]] || rm -rf -- "$source_stage"; }
    trap cleanup_source EXIT
    git clone --filter=blob:none --no-checkout "$repository" "$source_stage"
    git -C "$source_stage" checkout --detach "$revision"
    verify_source_cache "$source_stage"
    mv --no-clobber --no-target-directory "$source_stage" "$source_cache"
    if [[ -e "$source_stage" ]]; then
        rm -rf -- "$source_stage"
        source_stage=""
        verify_source_cache "$source_cache"
    else
        source_stage=""
    fi
    trap - EXIT
fi

verify_source_cache "$source_cache"

provenance_marker="xtool_revision=$revision
xtool_tree=$tree
swift_sha256=$swift_sha256
swift_ncurses_sha256=$swift_ncurses_sha256
swift_libxml2_sha256=$swift_libxml2_sha256
binutils_archive_sha256=$binutils_archive_sha256
binutils_objcopy_sha256=$binutils_objcopy_sha256
binutils_strip_sha256=$binutils_strip_sha256
binutils_readelf_sha256=$binutils_readelf_sha256
logical_scratch=$logical_scratch
package_resolved_sha256=$build_package_resolved_sha256
transitive_license_files=55
transitive_notice_files=18
license_inventory_sha256=$license_inventory_sha256"

verify_xtool() {
    local root="$1" version path name license_count notice_count
    printf '%s  %s\n' "$xtool_output_sha256" "$root/xtool" | sha256sum --check --status || {
        printf 'xtool output hash mismatch: %s\n' "$root/xtool" >&2
        return 1
    }
    printf '%s  %s\n' "$xadi_output_sha256" "$root/libXADI.so" | sha256sum --check --status || {
        printf 'libXADI output hash mismatch: %s\n' "$root/libXADI.so" >&2
        return 1
    }
    while IFS= read -r path; do
        name="$(basename "$path")"
        [[ -f "$swift_root/usr/lib/swift/linux/$name" ]] || {
            printf 'unexpected private xtool runtime file: %s\n' "$path" >&2
            return 1
        }
        cmp -s "$path" "$swift_root/usr/lib/swift/linux/$name" || {
            printf 'private xtool runtime differs from locked Swift: %s\n' "$path" >&2
            return 1
        }
    done < <(find "$root" -maxdepth 1 -type f \
        ! -name xtool ! -name libXADI.so ! -name "$marker_name" -print | sort)
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
    "$readelf_tool" -d "$root/xtool" | grep -Fq 'Library runpath: [$ORIGIN]' || {
        printf 'xtool is missing its relocatable $ORIGIN runpath\n' >&2
        return 1
    }
    printf '%s  %s\n' "$license_sha256" "$root/share/licenses/xtool/LICENSE.md" |
        sha256sum --check --status || {
        printf 'xtool license hash mismatch\n' >&2
        return 1
    }
    license_count="$(find "$root/share/licenses/xtool/dependencies" -mindepth 2 -maxdepth 2 \
        -type f \( -name 'LICENSE*' -o -name 'COPYING*' \) | wc -l)"
    notice_count="$(find "$root/share/licenses/xtool/dependencies" -mindepth 2 -maxdepth 2 \
        -type f \( -name 'NOTICE*' -o -name 'NOTICES*' \) | wc -l)"
    [[ "$license_count" == 55 && "$notice_count" == 18 ]] || {
        printf 'xtool transitive license inventory mismatch: licenses=%s notices=%s\n' \
            "$license_count" "$notice_count" >&2
        return 1
    }
    printf '%s  %s\n' "$license_inventory_sha256" "$root/share/licenses/xtool/SHA256SUMS" |
        sha256sum --check --status || {
        printf 'xtool license inventory lock mismatch\n' >&2
        return 1
    }
    (cd "$root" && sha256sum --check --status share/licenses/xtool/SHA256SUMS) || {
        printf 'xtool license inventory content mismatch\n' >&2
        return 1
    }
}

if [[ -e "$destination" || -L "$destination" ]]; then
    [[ -d "$destination" && ! -L "$destination" && -f "$destination/$marker_name" &&
       ! -L "$destination/$marker_name" ]] || {
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
normalization_capture=""
normalization_metadata=""
preserve_scratch=false

prepare_normalization_capture() {
    [[ -n "$diagnostics_dir" ]] || return 0
    [[ ! -e "$diagnostics_dir" && ! -L "$diagnostics_dir" ]] || {
        printf 'diagnostics directory must not already exist: %s\n' "$diagnostics_dir" >&2
        return 1
    }
    diagnostics_parent="$(dirname "$diagnostics_dir")"
    [[ -d "$diagnostics_parent" && ! -L "$diagnostics_parent" ]] || {
        printf 'diagnostics parent must be a real directory: %s\n' "$diagnostics_parent" >&2
        return 1
    }
    normalization_capture="$scratch/normalization-forensics"
    normalization_metadata="$normalization_capture/normalization-metadata.txt"
    mkdir -m700 "$normalization_capture"
    {
        printf 'schema=dotnet-ios-linux.xtool-normalization-forensics.v1\n'
        printf 'xtool_revision=%s\n' "$revision"
        printf 'expected_xtool_sha256=%s\n' "$xtool_output_sha256"
        printf 'expected_libXADI_sha256=%s\n' "$xadi_output_sha256"
        printf 'swift_sha256=%s\n' "$swift_sha256"
        printf 'logical_scratch=%s\n' "$logical_scratch"
        printf 'objcopy_path=%s\n' "$objcopy_tool"
        printf 'objcopy_sha256=%s\n' "$binutils_objcopy_sha256"
        printf 'strip_path=%s\n' "$strip_tool"
        printf 'strip_sha256=%s\n' "$binutils_strip_sha256"
        printf 'readelf_path=%s\n' "$readelf_tool"
        printf 'readelf_sha256=%s\n' "$binutils_readelf_sha256"
        printf '[objcopy-version]\n'
        "$objcopy_tool" --version
        printf '[strip-version]\n'
        "$strip_tool" --version
        printf 'runtime_validation=not-run-unverified\n'
    } > "$normalization_metadata"
}

record_normalization_phase() {
    local phase="$1" phase_dir
    [[ -n "$normalization_capture" ]] || return 0
    phase_dir="$normalization_capture/$phase"
    mkdir -m700 "$phase_dir"
    install -m755 "$stage/xtool" "$phase_dir/xtool"
    install -m755 "$stage/libXADI.so" "$phase_dir/libXADI.so"
    if [[ "$phase" == "strip-unneeded" ]]; then
        while IFS= read -r runtime_file; do
            install -m644 "$runtime_file" "$phase_dir/$(basename "$runtime_file")"
        done < <(find "$stage" -maxdepth 1 -type f ! -name xtool ! -name libXADI.so -print | sort)
    fi
    {
        printf '\n[%s]\n' "$phase"
        printf 'xtool_sha256=%s\n' "$(sha256sum "$stage/xtool" | cut -d' ' -f1)"
        printf 'libXADI_sha256=%s\n' "$(sha256sum "$stage/libXADI.so" | cut -d' ' -f1)"
        printf '[xtool-file]\n'
        file -b "$stage/xtool"
        printf '[libXADI-file]\n'
        file -b "$stage/libXADI.so"
        printf '[xtool-elf-header]\n'
        "$readelf_tool" -W -h "$stage/xtool"
        printf '[xtool-elf-sections]\n'
        "$readelf_tool" -W -S "$stage/xtool"
        printf '[xtool-elf-dynamic]\n'
        "$readelf_tool" -W -d "$stage/xtool"
        printf '[xtool-elf-notes]\n'
        "$readelf_tool" -W -n "$stage/xtool"
        printf '[libXADI-elf-header]\n'
        "$readelf_tool" -W -h "$stage/libXADI.so"
        printf '[libXADI-elf-sections]\n'
        "$readelf_tool" -W -S "$stage/libXADI.so"
        printf '[libXADI-elf-dynamic]\n'
        "$readelf_tool" -W -d "$stage/libXADI.so"
        printf '[libXADI-elf-notes]\n'
        "$readelf_tool" -W -n "$stage/libXADI.so"
    } >> "$normalization_metadata"
}

retain_normalization_diagnostics() {
    local diagnostics_parent diagnostics_stage
    [[ -n "$normalization_capture" ]] || {
        printf 'rerun with --diagnostics-dir <new-directory> to retain normalization forensics\n' >&2
        return 0
    }
    diagnostics_parent="$(dirname "$diagnostics_dir")"
    diagnostics_stage="$(mktemp -d "$diagnostics_parent/.xtool-diagnostics.XXXXXX")" || {
        printf 'unable to create normalization diagnostics staging directory\n' >&2
        return 1
    }
    if ! cp -a -- "$normalization_capture" "$diagnostics_stage/normalization-forensics"; then
        rm -rf -- "$diagnostics_stage"
        printf 'unable to copy normalization diagnostics\n' >&2
        return 1
    fi
    (
        cd "$diagnostics_stage"
        find normalization-forensics -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
        sha256sum --check --status SHA256SUMS
    ) || {
        rm -rf -- "$diagnostics_stage"
        printf 'unable to verify normalization diagnostics checksums\n' >&2
        return 1
    }
    if ! mv --no-clobber --no-target-directory "$diagnostics_stage" "$diagnostics_dir"; then
        rm -rf -- "$diagnostics_stage"
        printf 'unable to retain normalization diagnostics: %s\n' "$diagnostics_dir" >&2
        return 1
    fi
    printf 'normalization diagnostics retained at %s\n' "$diagnostics_dir" >&2
}

cleanup() {
    [[ -z "${stage:-}" || ! -e "$stage" ]] || rm -rf -- "$stage"
    if [[ "${preserve_scratch:-false}" != true ]]; then
        [[ -z "${scratch:-}" || ! -e "$scratch" ]] || rm -rf -- "$scratch"
    fi
}
trap cleanup EXIT
prepare_normalization_capture

prepare_namespace_mountpoint() {
    local logical_parent
    [[ -d /tmp && ! -L /tmp ]] || {
        printf 'temporary directory is not a real directory: /tmp\n' >&2
        return 1
    }
    logical_parent="$(dirname "$logical_scratch")"
    if [[ ! -e "$logical_parent" && ! -L "$logical_parent" ]]; then
        mkdir -p -m755 "$logical_parent"
    fi
    [[ -d "$logical_parent" && ! -L "$logical_parent" ]] || {
        printf 'canonical namespace mountpoint is not a real directory: %s\n' \
            "$logical_parent" >&2
        return 1
    }
}

run_in_build_namespace() {
    unshare --user --map-root-user --mount --propagation private \
        "$fixed_mount_runner" "$scratch" "$logical_scratch" "$@"
}

prepare_namespace_mountpoint

export PATH="$swift_root/usr/bin:$PATH"
if [[ -z "$package_cache" ]]; then
    package_cache="$(dirname "$source_cache")/swiftpm-cache"
fi
[[ ! -L "$package_cache" ]] || { printf 'SwiftPM cache must not be a symlink: %s\n' "$package_cache" >&2; exit 1; }
mkdir -p "$package_cache"
work="$scratch/source"
mkdir -p "$work"
git -C "$source_cache" archive "$revision" | tar -xf - -C "$work"
cp "$build_package_resolved" "$work/Package.resolved"
namespace_work="$logical_scratch/source"
namespace_build="$logical_scratch/build"
run_in_build_namespace test -f "$namespace_work/Package.resolved"

prefix_map="$logical_scratch=/usr/src/dotnet-ios-linux/xtool-build"
toolchain_prefix_map="$swift_root=/usr/lib/dotnet-ios-linux/swift-6.4.0"
swift_arguments=(
    build --package-path "$namespace_work" --scratch-path "$namespace_build"
    --cache-path "$package_cache" --skip-update
    --force-resolved-versions -c release --product xtool \
    -Xswiftc -no-toolchain-stdlib-rpath \
    -Xswiftc -debug-prefix-map -Xswiftc "$prefix_map" \
    -Xswiftc -file-prefix-map -Xswiftc "$prefix_map" \
    -Xswiftc -debug-prefix-map -Xswiftc "$toolchain_prefix_map" \
    -Xswiftc -file-prefix-map -Xswiftc "$toolchain_prefix_map" \
    -Xcc "-fdebug-prefix-map=$prefix_map" \
    -Xcc "-ffile-prefix-map=$prefix_map" \
    -Xcc "-fdebug-prefix-map=$toolchain_prefix_map" \
    -Xcc "-ffile-prefix-map=$toolchain_prefix_map"
)
swift_environment=(env -u LD_LIBRARY_PATH XTOOL_VERSION="commit $revision")
if [[ "$offline" == true ]]; then
    # SwiftPM has no strict offline switch. Block every supported proxy route;
    # the complete shared cache must satisfy the locked resolution.
    swift_environment+=(
        http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
        HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
        all_proxy=socks5://127.0.0.1:9 ALL_PROXY=socks5://127.0.0.1:9
        no_proxy=localhost,127.0.0.1 NO_PROXY=localhost,127.0.0.1
    )
fi
run_in_build_namespace "${swift_environment[@]}" swift "${swift_arguments[@]}"
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
logical_bin="$(run_in_build_namespace env -u LD_LIBRARY_PATH XTOOL_VERSION="commit $revision" \
    swift build --package-path "$namespace_work" --scratch-path "$namespace_build" \
    --cache-path "$package_cache" --skip-update \
    --force-resolved-versions -c release --show-bin-path)"
[[ "$logical_bin" == "$logical_scratch/"* ]] || {
    printf 'SwiftPM returned a bin path outside the fixed logical build root: %s\n' "$logical_bin" >&2
    exit 1
}
physical_bin="$scratch/${logical_bin#"$logical_scratch/"}"
[[ -f "$physical_bin/xtool" && -x "$physical_bin/xtool" &&
   -f "$physical_bin/libXADI.so" ]] || {
    printf 'SwiftPM release output is incomplete: %s\n' "$physical_bin" >&2
    exit 1
}
install -m755 "$physical_bin/xtool" "$physical_bin/libXADI.so" "$stage/"
license_root="$stage/share/licenses/xtool"
mkdir -p "$license_root/dependencies"
install -m644 "$source_cache/LICENSE.md" "$license_root/LICENSE.md"
license_count=0
notice_count=0
while IFS= read -r checkout; do
    name="$(basename "$checkout")"
    destination_licenses="$license_root/dependencies/$name"
    mkdir -p "$destination_licenses"
    while IFS= read -r license_file; do
        install -m644 "$license_file" "$destination_licenses/$(basename "$license_file")"
        ((license_count += 1))
    done < <(find "$checkout" -maxdepth 1 -type f \( -name 'LICENSE*' -o -name 'COPYING*' \) -print | sort)
    while IFS= read -r notice_file; do
        install -m644 "$notice_file" "$destination_licenses/$(basename "$notice_file")"
        ((notice_count += 1))
    done < <(find "$checkout" -maxdepth 1 -type f \( -name 'NOTICE*' -o -name 'NOTICES*' \) -print | sort)
done < <(find "$scratch/build/checkouts" -mindepth 1 -maxdepth 1 -type d -print | sort)
[[ "$license_count" == 55 && "$notice_count" == 18 ]] || {
    printf 'resolved xtool dependency license inventory is incomplete: licenses=%s notices=%s\n' \
        "$license_count" "$notice_count" >&2
    exit 1
}
(cd "$stage" && find share/licenses -type f ! -name SHA256SUMS -print0 |
    sort -z | xargs -0 sha256sum) > "$license_root/SHA256SUMS"
printf '%s  %s\n' "$license_inventory_sha256" "$license_root/SHA256SUMS" |
    sha256sum --check --status || {
    printf 'resolved xtool license inventory differs from the lock\n' >&2
    exit 1
}
runtime="$swift_root/usr/lib/swift/linux"
copy_private_runtime_closure() {
    local current needed candidate destination_name
    local -a queue=("$stage/xtool" "$stage/libXADI.so")
    declare -A inspected=()
    while ((${#queue[@]})); do
        current="${queue[0]}"
        queue=("${queue[@]:1}")
        [[ -z "${inspected[$current]:-}" ]] || continue
        inspected["$current"]=1
        while IFS= read -r needed; do
            [[ -n "$needed" && "$needed" == "$(basename "$needed")" ]] || {
                printf 'invalid ELF dependency name: %s\n' "$needed" >&2
                return 1
            }
            candidate="$runtime/$needed"
            [[ -e "$candidate" && ! -d "$candidate" ]] || continue
            destination_name="$stage/$needed"
            if [[ ! -e "$destination_name" ]]; then
                install -m644 "$candidate" "$destination_name"
            fi
            queue+=("$destination_name")
        done < <("$readelf_tool" -d "$current" | awk -F'[][]' '/NEEDED/ { print $2 }')
    done
}
copy_private_runtime_closure
# Swift's gold-linker build ID and compiler module hash vary despite otherwise
# byte-identical loadable sections. Neither is consumed by this standalone
# executable at runtime, so normalize both before hashing the staged tool.
record_normalization_phase pre-normalization
"$objcopy_tool" --remove-section=.note.gnu.build-id "$stage/xtool"
record_normalization_phase build-id-removed
head -c 16 /dev/zero > "$scratch/swift-modhash"
"$objcopy_tool" --update-section .swift_modhash="$scratch/swift-modhash" "$stage/xtool"
record_normalization_phase swift-modhash-zeroed
"$strip_tool" --strip-unneeded "$stage/xtool" "$stage/libXADI.so"
record_normalization_phase strip-unneeded
xtool_sha256="$(sha256sum "$stage/xtool" | cut -d' ' -f1)"
xadi_sha256="$(sha256sum "$stage/libXADI.so" | cut -d' ' -f1)"
[[ "$xtool_sha256" == "$xtool_output_sha256" && "$xadi_sha256" == "$xadi_output_sha256" ]] || {
    printf 'normalized xtool output hashes do not match the lock: xtool=%s libXADI=%s\n' \
        "$xtool_sha256" "$xadi_sha256" >&2
    if ! retain_normalization_diagnostics; then
        preserve_scratch=true
        printf 'normalization evidence remains at %s\n' "$normalization_capture" >&2
    fi
    exit 1
}
printf '%s\nxtool_sha256=%s\nlibXADI_sha256=%s\n' \
    "$provenance_marker" "$xtool_sha256" "$xadi_sha256" > "$stage/$marker_name"
verify_xtool "$stage"
mv --no-clobber --no-target-directory "$stage" "$destination"
[[ ! -e "$stage" ]] || {
    printf 'xtool destination appeared during publication; refusing to replace it: %s\n' "$destination" >&2
    exit 1
}
stage=""
verify_xtool "$destination"

printf 'xtool_root=%s\n' "$(realpath "$destination")"
printf 'xtool_revision=%s\n' "$revision"
printf 'xtool_sha256=%s\nlibXADI_sha256=%s\nreused=false\n' \
    "$xtool_sha256" "$xadi_sha256"
