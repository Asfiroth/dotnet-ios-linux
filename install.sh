#!/usr/bin/env bash
# Rootless bootstrap for the locked Linux-to-iOS .NET toolchain.
set -Eeuo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly lock_path="$script_dir/toolchain.lock.json"
readonly context_resolver="$script_dir/eng/install/resolve-install-context.py"
. "$script_dir/eng/install/download-verified.sh"

usage() {
    cat <<'EOF'
Usage:
  ./install.sh install --xcode-archive /path/to/Xcode.xip [options]
  ./install.sh doctor
  ./install.sh uninstall [--yes] [--purge-cache]

Install options:
  --xcode-archive PATH  Required user-supplied Xcode archive compatible with
                        the selected iOS workload.
  --aot-pack PATH       Reuse a locally built LLVM AOT pack layout.
  --jobs COUNT          Runtime compiler build parallelism.
  --offline             Forbid external downloads; require complete caches.
  --yes                 Do not prompt before canonical-root changes.
  --dry-run             Print paths and planned operations without changing them.

The canonical SDK root is ~/.dotnet. Durable components are installed below
${XDG_DATA_HOME:-~/.local/share}/dotnet-ios-linux; downloads and builds are
kept below ${XDG_CACHE_HOME:-~/.cache}/dotnet-ios-linux. Shell startup files,
credentials, pairing records, devices, and signing state are never modified.
EOF
}

command_name="install"
if (($#)) && [[ "$1" =~ ^(install|setup|doctor|uninstall)$ ]]; then
    command_name="$1"
    [[ "$command_name" != setup ]] || command_name="install"
    shift
fi
xcode_archive=""
aot_pack=""
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1')"
jobs_supplied=false
offline=false
assume_yes=false
yes_supplied=false
dry_run=false
purge_cache=false
while (($#)); do
    case "$1" in
        --xcode-archive) xcode_archive="${2:?missing value for --xcode-archive}"; shift 2 ;;
        --aot-pack) aot_pack="${2:?missing value for --aot-pack}"; shift 2 ;;
        --jobs) jobs="${2:?missing value for --jobs}"; jobs_supplied=true; shift 2 ;;
        --offline) offline=true; shift ;;
        --yes) assume_yes=true; yes_supplied=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        --purge-cache) purge_cache=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { printf -- '--jobs must be a positive integer\n' >&2; exit 2; }
case "$command_name" in
    install)
        [[ "$purge_cache" == false ]] || { printf -- '--purge-cache is valid only with uninstall\n' >&2; exit 2; }
        ;;
    doctor)
        if [[ -n "$xcode_archive" || -n "$aot_pack" || "$offline" == true || "$dry_run" == true ||
              "$purge_cache" == true || "$yes_supplied" == true || "$jobs_supplied" == true ]]; then
            printf 'doctor does not accept lifecycle options\n' >&2
            exit 2
        fi
        ;;
    uninstall)
        if [[ -n "$xcode_archive" || -n "$aot_pack" || "$offline" == true || "$dry_run" == true ||
              "$jobs_supplied" == true ]]; then
            printf 'install-only options were supplied to uninstall\n' >&2
            exit 2
        fi
        ;;
esac

[[ -n "${HOME:-}" && "$HOME" == /* ]] || { printf 'HOME must be an absolute path\n' >&2; exit 1; }
if [[ -n "${XDG_DATA_HOME:-}" && "$XDG_DATA_HOME" != /* ]]; then
    printf 'XDG_DATA_HOME must be an absolute path\n' >&2
    exit 1
fi
if [[ -n "${XDG_CACHE_HOME:-}" && "$XDG_CACHE_HOME" != /* ]]; then
    printf 'XDG_CACHE_HOME must be an absolute path\n' >&2
    exit 1
fi

readonly data_root="${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux"
readonly cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux"
readonly dotnet_root="$HOME/.dotnet"
readonly cache_marker="$cache_root/.dotnet-ios-linux-cache.json"
readonly marker="$data_root/.dotnet-ios-linux.json"
readonly receipt="$data_root/receipts/install.json"
readonly sdk_receipt="$data_root/receipts/dotnet-sdk.json"
readonly workload_transaction="$data_root/receipts/workload-transaction"
readonly control_root="$data_root/control"

[[ ! -L "$data_root" ]] || { printf 'durable data root must not be a symlink: %s\n' "$data_root" >&2; exit 1; }
[[ ! -L "$cache_root" ]] || { printf 'cache root must not be a symlink: %s\n' "$cache_root" >&2; exit 1; }
[[ ! -L "$dotnet_root" ]] || { printf 'canonical SDK root must not be a symlink: %s\n' "$dotnet_root" >&2; exit 1; }

need() { command -v "$1" >/dev/null || { printf 'required host command is missing: %s\n' "$1" >&2; exit 1; }; }
for tool in python3 flock mktemp mv realpath sha256sum sha512sum; do need "$tool"; done
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required to run the bootstrap")
PY

declare -A lock_values=()
declare -A install_context=()
declare -a resolved_pairs=()
read_resolved_pairs() {
    local destination_name="$1" output key value
    shift
    local -n destination="$destination_name"
    output="$(mktemp "${TMPDIR:-/tmp}/dotnet-ios-linux-context.XXXXXX")"
    if ! "$context_resolver" --format null "$@" > "$output"; then
        rm -f -- "$output"
        return 1
    fi
    mapfile -d '' -t resolved_pairs < "$output"
    rm -f -- "$output"
    ((${#resolved_pairs[@]} > 0 && ${#resolved_pairs[@]} % 2 == 0)) || {
        printf 'install context resolver returned an invalid response\n' >&2
        return 1
    }
    destination=()
    while ((${#resolved_pairs[@]})); do
        key="${resolved_pairs[0]}"
        value="${resolved_pairs[1]}"
        destination["$key"]="$value"
        resolved_pairs=("${resolved_pairs[@]:2}")
    done
}

read_resolved_pairs lock_values lock --lock "$lock_path"
readonly project_version="${lock_values[project_version]}"
readonly sdk_fallback_version="${lock_values[sdk_fallback_version]}"
readonly sdk_feature_band="${lock_values[sdk_feature_band]}"
readonly sdk_url="${lock_values[sdk_url]}"
readonly sdk_sha512="${lock_values[sdk_sha512]}"
readonly workload_fallback_version="${lock_values[workload_fallback_version]}"
readonly workload_set_url="${lock_values[workload_package_url]}"
readonly workload_set_sha512="${lock_values[workload_package_sha512]}"
readonly manifest_url="${lock_values[manifest_url]}"
readonly manifest_sha512_base64="${lock_values[manifest_sha512_base64]}"
readonly manifest_sha512="${lock_values[manifest_sha512]}"
readonly manifest_version="${lock_values[manifest_version]}"
readonly manifest_suffix="${lock_values[manifest_suffix]}"
readonly runtime_commit="${lock_values[runtime_commit]}"
readonly llvm_version="${lock_values[llvm_version]}"
readonly llvm_major="${lock_values[llvm_major]}"
readonly supported_xcode_version="${lock_values[supported_xcode_version]}"
readonly supported_ios_sdk_version="${lock_values[supported_ios_sdk_version]}"
readonly locked_xcode_sha256="${lock_values[locked_xcode_sha256]}"
readonly swift_version="${lock_values[swift_version]}"
readonly xtool_revision_short="${lock_values[xtool_revision_short]}"
readonly python_version="${lock_values[python_version]}"
readonly pymobiledevice3_version="${lock_values[pymobiledevice3_version]}"
readonly rcodesign_version="${lock_values[rcodesign_version]}"
readonly sdk_archive="$cache_root/downloads/${lock_values[sdk_archive]}"
readonly manifest_archive="$cache_root/downloads/${lock_values[manifest_archive]}"
readonly workload_set_archive="$cache_root/downloads/${lock_values[workload_package_archive]}"
lock_sha256="$(sha256sum -b "$lock_path" | cut -d' ' -f1)"

install_id=""
sdk_version=""
sdk_source=""
workload_set_version=""
workload_source=""
feed=""
toolchain=""
xcode_archive_sha256=""
xcode_version=""
xcode_build=""
platform_version=""
platform_build=""
xcode_sdk_version=""
xcode_sdk_build=""
recommended_xcode_version=""

set_install_paths() {
    install_id="$project_version-sdk-$sdk_version"
    feed="$data_root/feeds/$install_id"
    toolchain="$data_root/toolchains/$install_id"
}

xcode_is_compatible() {
    "$context_resolver" xcode-compatible --actual "$1" --recommended "$2" >/dev/null
}

resolve_new_install_context() {
    read_resolved_pairs install_context sdk --root "$dotnet_root" \
        --feature-band "$sdk_feature_band" --fallback "$sdk_fallback_version"
    sdk_version="${install_context[sdk_version]}"
    sdk_source="${install_context[sdk_source]}"
    read_resolved_pairs install_context workload --root "$dotnet_root" \
        --feature-band "$sdk_feature_band" --manifest-version "$manifest_version" \
        --manifest-suffix "$manifest_suffix" --fallback "$workload_fallback_version"
    workload_set_version="${install_context[workload_version]}"
    workload_source="${install_context[workload_source]}"
    set_install_paths
}

load_owned_context() {
    local source="$1"
    mapfile -d '' -t resolved_pairs < <(python3 - "$source" "$dotnet_root" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1])
try: value=json.loads(path.read_text(encoding="utf-8"))
except Exception as error: raise SystemExit(f"cannot read installation context {path}: {error}")
if value.get("owner") != "dotnet-ios-linux" or value.get("dotnetRoot") != sys.argv[2]:
    raise SystemExit("installation context has different ownership")
required=("installId","sdkVersion","workloadSet")
for key in required:
    item=value.get(key)
    if not isinstance(item,str) or not item or "\x00" in item:
        raise SystemExit(f"installation context lacks {key}")
for key in required:
    sys.stdout.buffer.write(key.encode()+b"\0"+value[key].encode()+b"\0")
xcode=value.get("xcode",{})
for key in ("version","build","sdkVersion","sdkBuild"):
    item=xcode.get(key)
    if isinstance(item,str) and item:
        sys.stdout.buffer.write(("xcode."+key).encode()+b"\0"+item.encode()+b"\0")
PY
)
    ((${#resolved_pairs[@]} >= 6 && ${#resolved_pairs[@]} % 2 == 0)) || {
        printf 'installation context returned an invalid response\n' >&2
        return 1
    }
    install_context=()
    while ((${#resolved_pairs[@]})); do
        install_context["${resolved_pairs[0]}"]="${resolved_pairs[1]}"
        resolved_pairs=("${resolved_pairs[@]:2}")
    done
    install_id="${install_context[installId]}"
    sdk_version="${install_context[sdkVersion]}"
    workload_set_version="${install_context[workloadSet]}"
    sdk_source="receipt"
    workload_source="receipt"
    feed="$data_root/feeds/$install_id"
    toolchain="$data_root/toolchains/$install_id"
    xcode_version="${install_context[xcode.version]:-}"
    xcode_build="${install_context[xcode.build]:-}"
    xcode_sdk_version="${install_context[xcode.sdkVersion]:-}"
    xcode_sdk_build="${install_context[xcode.sdkBuild]:-}"
}

read_marker_status() {
    python3 - "$marker" <<'PY'
import json, sys
from pathlib import Path
try: value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception as error: raise SystemExit(f"cannot read installation marker: {error}")
if value.get("owner") != "dotnet-ios-linux": raise SystemExit("installation marker has a different owner")
print(value.get("status", ""))
PY
}

validate_cache_root() {
    [[ -d "$cache_root" && ! -L "$cache_root" && -f "$cache_marker" && ! -L "$cache_marker" ]] || {
        printf 'cache root exists without a valid ownership marker: %s\n' "$cache_root" >&2
        return 1
    }
    python3 - "$cache_marker" "$cache_root" <<'PY'
import json, sys
from pathlib import Path
try:
    value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    raise SystemExit(f"cannot read cache ownership marker: {error}")
if value != {"schemaVersion":1,"owner":"dotnet-ios-linux","cacheRoot":sys.argv[2]}:
    raise SystemExit("cache ownership marker does not match this project and path")
PY
}

initialize_cache_root() {
    local parent stage
    if [[ -e "$cache_root" || -L "$cache_root" ]]; then
        validate_cache_root
        return
    fi
    parent="$(dirname "$cache_root")"
    mkdir -p "$parent"
    stage="$(mktemp -d "$parent/.dotnet-ios-linux-cache.XXXXXX")"
    python3 - "$stage/.dotnet-ios-linux-cache.json" "$cache_root" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schemaVersion":1,
    "owner":"dotnet-ios-linux",
    "cacheRoot":sys.argv[2],
},indent=2)+"\n",encoding="utf-8")
PY
    mv --no-clobber --no-target-directory "$stage" "$cache_root"
    if [[ -e "$stage" ]]; then
        rm -rf -- "$stage"
    fi
    validate_cache_root
}

activate_environment() {
    export DOTNET_ROOT="$dotnet_root"
    export DOTNET_CLI_HOME="$cache_root/dotnet-cli-home"
    export NUGET_PACKAGES="$cache_root/nuget/packages"
    export DOTNET_CLI_TELEMETRY_OPTOUT=1
    export DOTNET_NOLOGO=1
    export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
    export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true
    export DEVELOPER_DIR="$data_root/Xcode.app/Contents/Developer"
    export DOTNET_IOS_LINUX_CLANG="$toolchain/swift-$swift_version/usr/bin/clang"
    export DOTNET_IOS_LINUX_CLANGXX="$toolchain/swift-$swift_version/usr/bin/clang++"
    export PATH="$dotnet_root:$dotnet_root/tools:$data_root/bin:$toolchain/swift-$swift_version/usr/bin:$DEVELOPER_DIR/usr/bin:$PATH"
}

doctor() {
    local actual_sdk actual_workload actual_xcode actual_sdk_version swift_major_minor
    [[ -f "$marker" && -f "$receipt" ]] || { printf 'dotnet-ios-linux is not installed under %s\n' "$data_root" >&2; return 1; }
    [[ "$(read_marker_status)" == complete ]] || { printf 'installation is incomplete; rerun install\n' >&2; return 1; }
    python3 - "$receipt" "$lock_sha256" <<'PY'
import json, sys
from pathlib import Path
value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("toolchainLockSha256") != sys.argv[2]:
    raise SystemExit("the installed toolchain belongs to a different toolchain.lock.json")
PY
    activate_environment
    "$script_dir/eng/install/durable-assets.py" verify --root "$data_root" --receipt "$receipt" >/dev/null
    if [[ -f "$sdk_receipt" ]]; then
        "$script_dir/eng/install/install-dotnet-sdk.py" verify --root "$dotnet_root" \
            --receipt "$sdk_receipt" --sdk-version "$sdk_version" >/dev/null
    else
        [[ -x "$dotnet_root/dotnet" && -d "$dotnet_root/sdk/$sdk_version" ]] || {
            printf 'the selected pre-existing .NET SDK is no longer available: %s\n' "$sdk_version" >&2
            return 1
        }
    fi
    "$script_dir/eng/install/workload-transaction.py" verify --root "$dotnet_root" --transaction "$workload_transaction" >/dev/null
    "$script_dir/eng/install/verify-workload-packages.py" --root "$dotnet_root" \
        --feed "$feed" --lock "$lock_path" --dotnet "$dotnet_root/dotnet" >/dev/null
    actual_sdk="$(cd "$control_root" && "$dotnet_root/dotnet" --version)"
    [[ "$actual_sdk" == "$sdk_version" ]] || {
        printf 'selected .NET SDK is %s, expected installed SDK %s\n' "$actual_sdk" "$sdk_version" >&2
        return 1
    }
    actual_workload="$(cd "$control_root" && "$dotnet_root/dotnet" workload --version)"
    [[ "$actual_workload" == "$workload_set_version" ]] || {
        printf 'selected workload set is %s, expected %s\n' "$actual_workload" "$workload_set_version" >&2
        return 1
    }
    (cd "$control_root" && "$dotnet_root/dotnet" workload list) |
        awk -v expected="$manifest_version/$manifest_suffix" \
            '$1 == "ios" && $2 == expected { found=1 } END { exit !found }' || {
        printf 'the pinned ios workload is not installed\n' >&2; return 1;
    }
    "$dotnet_root/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/$project_version/tools/mono-aot-cross" --version |
        grep -Eq "LLVM:[[:space:]]+yes\\($((llvm_major * 100))\\)" || {
            printf 'LLVM-enabled iOS AOT compiler validation failed\n' >&2; return 1;
        }
    swift_major_minor="${swift_version%.*}"
    "$toolchain/swift-$swift_version/usr/bin/swift" --version | head -n1 |
        grep -Fq "Swift version $swift_major_minor" || {
            printf 'Swift version validation failed\n' >&2; return 1;
        }
    env -u LD_LIBRARY_PATH "$toolchain/xtool-$xtool_revision_short/xtool" --help >/dev/null
    [[ "$("$toolchain/python-$python_version/bin/python3" -c 'import platform; print(platform.python_version())')" == "$python_version" ]]
    [[ "$("$toolchain/pymobiledevice3-$pymobiledevice3_version/bin/pymobiledevice3" version)" == "$pymobiledevice3_version" ]]
    [[ "$("$toolchain/rcodesign-$rcodesign_version/rcodesign" --version)" == "apple-codesign $rcodesign_version" ]]
    read_resolved_pairs install_context recommended-xcode --root "$dotnet_root" --lock "$lock_path"
    recommended_xcode_version="${install_context[recommended_xcode_version]}"
    xcode_is_compatible "$xcode_version" "$recommended_xcode_version" || {
        printf 'installed Xcode %s is incompatible with iOS workload %s; expected Xcode %s\n' \
            "$xcode_version" "$manifest_version" "$recommended_xcode_version" >&2
        return 1
    }
    actual_sdk_version="$("$DEVELOPER_DIR/usr/bin/xcrun" --sdk iphoneos --show-sdk-version)"
    [[ "$actual_sdk_version" == "$xcode_sdk_version" ]] || {
        printf 'synthetic Xcode SDK is %s, expected %s\n' "$actual_sdk_version" "$xcode_sdk_version" >&2
        return 1
    }
    actual_xcode="$("$DEVELOPER_DIR/usr/bin/xcodebuild" -version | head -n1)"
    [[ "$actual_xcode" == "Xcode $xcode_version" ]] || {
        printf 'synthetic Xcode reports %s, expected Xcode %s\n' "$actual_xcode" "$xcode_version" >&2
        return 1
    }
    printf 'doctor=passed\n'
    printf 'dotnet_root=%s\n' "$dotnet_root"
    printf 'data_root=%s\n' "$data_root"
    printf 'activation=%s\n' "$data_root/activate"
}
confirm() {
    local prompt="$1" answer
    [[ "$assume_yes" == false ]] || return 0
    [[ -t 0 ]] || { printf 'confirmation is required; rerun with --yes\n' >&2; exit 1; }
    read -r -p "$prompt [y/N] " answer
    [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == YES ]] || exit 1
}

if [[ "$command_name" == install && "$dry_run" == true ]]; then
    [[ -n "$xcode_archive" ]] || { printf -- '--xcode-archive is required for install\n' >&2; exit 2; }
    [[ -f "$xcode_archive" ]] || { printf 'Xcode archive is not a file: %s\n' "$xcode_archive" >&2; exit 1; }
    xcode_archive="$(realpath "$xcode_archive")"
    if [[ -n "$aot_pack" ]]; then
        [[ -d "$aot_pack" ]] || { printf 'AOT pack is not a directory: %s\n' "$aot_pack" >&2; exit 1; }
        aot_pack="$(realpath "$aot_pack")"
    fi
    resolve_new_install_context
    cat <<EOF
command=install
dotnet_root=$dotnet_root
dotnet_sdk=$sdk_version
dotnet_sdk_source=$sdk_source
workload_set=$workload_set_version
workload_set_source=$workload_source
data_root=$data_root
cache_root=$cache_root
xcode_archive=$(basename "$xcode_archive")
offline=$offline
runtime_source=$([[ -n "$aot_pack" ]] && printf reused-local-aot-pack || printf build-pinned-source-with-llvm)
planned=compatible SDK selection; Xcode/workload compatibility check; Xcode SDK import; host-tool staging; pinned iOS workload install; activation
EOF
    exit 0
fi

initialize_cache_root
[[ ! -e "$cache_root/install.lock" || -f "$cache_root/install.lock" ]] || {
    printf 'cache lifecycle lock is not a regular file: %s\n' "$cache_root/install.lock" >&2
    exit 1
}
[[ ! -L "$cache_root/install.lock" ]] || {
    printf 'cache lifecycle lock must not be a symlink: %s\n' "$cache_root/install.lock" >&2
    exit 1
}
exec 9>"$cache_root/install.lock"
flock -n 9 || { printf 'another dotnet-ios-linux lifecycle operation is active\n' >&2; exit 1; }

if [[ "$command_name" == doctor ]]; then
    [[ -f "$receipt" ]] || { printf 'dotnet-ios-linux is not installed under %s\n' "$data_root" >&2; exit 1; }
    load_owned_context "$receipt"
    doctor
    exit 0
fi

if [[ "$command_name" == uninstall ]]; then
    [[ -f "$marker" ]] || { printf 'dotnet-ios-linux is not installed\n'; exit 0; }
    [[ "$(read_marker_status)" == complete ]] || {
        printf 'installation is incomplete; rerun install to recover it before uninstalling\n' >&2
        exit 1
    }
    [[ -f "$receipt" ]] || { printf 'installation receipt is missing: %s\n' "$receipt" >&2; exit 1; }
    load_owned_context "$receipt"
    confirm "Remove the owned iOS workload and durable dotnet-ios-linux data?"
    "$script_dir/eng/install/durable-assets.py" verify --root "$data_root" --receipt "$receipt"
    activate_environment
    transaction_status="$("$script_dir/eng/install/workload-transaction.py" status \
        --root "$dotnet_root" --transaction "$workload_transaction")"
    if [[ "$transaction_status" == committed ]]; then
        "$script_dir/eng/install/workload-transaction.py" prepare-uninstall \
            --root "$dotnet_root" --transaction "$workload_transaction"
    elif [[ "$transaction_status" != uninstalling ]]; then
        printf 'cannot uninstall a workload transaction in state %s\n' "$transaction_status" >&2
        exit 1
    fi
    if ! (cd "$control_root" && "$dotnet_root/dotnet" workload uninstall ios); then
        if (cd "$control_root" && "$dotnet_root/dotnet" workload list) | grep -Eq '^ios[[:space:]]'; then
            printf 'workload uninstall failed while ios remains installed; no snapshots were restored\n' >&2
            exit 1
        fi
        printf 'workload uninstall returned an error after ios was removed; continuing ownership restoration\n' >&2
    fi
    "$script_dir/eng/install/workload-transaction.py" restore \
        --root "$dotnet_root" --transaction "$workload_transaction"
    if [[ -f "$sdk_receipt" ]]; then
        "$script_dir/eng/install/install-dotnet-sdk.py" uninstall \
            --root "$dotnet_root" --receipt "$sdk_receipt" --sdk-version "$sdk_version"
    fi
    "$script_dir/eng/install/durable-assets.py" uninstall --root "$data_root" --receipt "$receipt"
    rm -f -- "$marker" "$receipt"
    rmdir "$data_root/receipts" "$data_root" 2>/dev/null || true
    if [[ "$purge_cache" == true ]]; then
        validate_cache_root
        rm -rf -- "$cache_root"
    fi
    printf 'uninstall=passed\n'
    printf 'cache_preserved=%s\n' "$([[ "$purge_cache" == true ]] && printf false || printf true)"
    exit 0
fi

if [[ -f "$marker" && "$(read_marker_status)" == complete ]]; then
    [[ -f "$receipt" ]] || { printf 'installation receipt is missing: %s\n' "$receipt" >&2; exit 1; }
    load_owned_context "$receipt"
    doctor
    exit 0
fi

[[ -n "$xcode_archive" ]] || { printf -- '--xcode-archive is required for install\n' >&2; usage >&2; exit 2; }
[[ -f "$xcode_archive" ]] || { printf 'Xcode archive is not a file: %s\n' "$xcode_archive" >&2; exit 1; }
xcode_archive="$(realpath "$xcode_archive")"
if [[ -n "$aot_pack" ]]; then
    [[ -d "$aot_pack" ]] || { printf 'AOT pack is not a directory: %s\n' "$aot_pack" >&2; exit 1; }
    aot_pack="$(realpath "$aot_pack")"
fi

if [[ -e "$data_root" ]]; then
    [[ -f "$marker" ]] || { printf 'durable data root exists without an ownership marker: %s\n' "$data_root" >&2; exit 1; }
    status="$(read_marker_status)"
    if [[ "$status" == complete ]]; then
        load_owned_context "$receipt"
        doctor
        exit 0
    fi
    [[ "$status" == installing ]] || { printf 'unknown installation state: %s\n' "$status" >&2; exit 1; }
    if ! load_owned_context "$marker"; then
        printf 'interrupted installation lacks a reusable context; resolving it again\n' >&2
        resolve_new_install_context
    fi
    printf 'Recovering an interrupted installation...\n'
    if [[ -d "$workload_transaction" ]]; then
        "$script_dir/eng/install/workload-transaction.py" restore --root "$dotnet_root" --transaction "$workload_transaction"
    fi
    if [[ -f "$sdk_receipt" ]]; then
        "$script_dir/eng/install/install-dotnet-sdk.py" uninstall --root "$dotnet_root" \
            --receipt "$sdk_receipt" --sdk-version "$sdk_version"
    fi
    rm -rf -- "$data_root"
else
    resolve_new_install_context
fi

for tool in clang clang++ cmake curl file git ldd ln make ninja objcopy readelf strip tar; do need "$tool"; done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || { printf 'only Linux x86_64 is supported\n' >&2; exit 1; }
getconf GNU_LIBC_VERSION >/dev/null 2>&1 || { printf 'a glibc host is required; musl is not supported\n' >&2; exit 1; }
confirm "Install the locked toolchain into $dotnet_root and $data_root?"
read_resolved_pairs install_context xcode --archive "$xcode_archive"
xcode_archive_sha256="${install_context[xcode_archive_sha256]}"
xcode_version="${install_context[xcode_version]}"
xcode_build="${install_context[xcode_build]}"
platform_version="${install_context[platform_version]}"
platform_build="${install_context[platform_build]}"
xcode_sdk_version="${install_context[xcode_sdk_version]}"
xcode_sdk_build="${install_context[xcode_sdk_build]}"
xcode_is_compatible "$xcode_version" "$supported_xcode_version" || {
    printf 'Xcode %s is incompatible with iOS workload %s; expected Xcode %s\n' \
        "$xcode_version" "$manifest_version" "$supported_xcode_version" >&2
    exit 1
}
xcode_is_compatible "$xcode_sdk_version" "$supported_ios_sdk_version" || {
    printf 'Xcode %s contains iPhoneOS SDK %s; workload %s expects SDK %s\n' \
        "$xcode_version" "$xcode_sdk_version" "$manifest_version" "$supported_ios_sdk_version" >&2
    exit 1
}

if [[ "$offline" == true ]]; then
    export http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
    export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
    export all_proxy=socks5://127.0.0.1:9 ALL_PROXY=socks5://127.0.0.1:9
    export no_proxy=localhost,127.0.0.1 NO_PROXY=localhost,127.0.0.1
fi

mkdir -p "$data_root/receipts" "$cache_root/downloads" "$cache_root/build" "$cache_root/sources" \
    "$cache_root/nuget/packages" "$cache_root/dotnet-cli-home" "$data_root/feeds" "$data_root/toolchains"
python3 - "$marker" "$lock_sha256" "$dotnet_root" "$data_root" "$project_version" \
    "$install_id" "$sdk_version" "$workload_set_version" <<'PY'
import json, os, sys
from pathlib import Path
path=Path(sys.argv[1])
value={
 "schemaVersion":1,"owner":"dotnet-ios-linux","status":"installing",
 "projectVersion":sys.argv[5],"installId":sys.argv[6],
 "sdkVersion":sys.argv[7],"workloadSet":sys.argv[8],
 "toolchainLockSha256":sys.argv[2],"dotnetRoot":sys.argv[3],"dataRoot":sys.argv[4],
}
temporary=path.with_name(path.name+f".tmp.{os.getpid()}")
temporary.write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8"); os.replace(temporary,path)
PY
installation_active=true
rollback_install() {
    local exit_code="${1:-$?}"
    trap - ERR INT TERM
    [[ "$installation_active" == true ]] || exit "$exit_code"
    printf 'Installation failed; restoring owned mutations...\n' >&2
    if [[ -d "$workload_transaction" ]]; then
        "$script_dir/eng/install/workload-transaction.py" restore --root "$dotnet_root" --transaction "$workload_transaction" || true
    fi
    if [[ -f "$sdk_receipt" ]]; then
        "$script_dir/eng/install/install-dotnet-sdk.py" uninstall --root "$dotnet_root" \
            --receipt "$sdk_receipt" --sdk-version "$sdk_version" || true
    fi
    [[ ! -f "$marker" ]] || rm -rf -- "$data_root"
    exit "$exit_code"
}
trap rollback_install ERR INT TERM

if [[ "$sdk_source" == installer ]]; then
    download_verified "$sdk_url" sha512sum "$sdk_sha512" "$sdk_archive" "$offline"
fi
download_verified "$manifest_url" sha512sum "$manifest_sha512" "$manifest_archive" "$offline"
if [[ "$workload_source" == installer ]]; then
    download_verified "$workload_set_url" sha512sum "$workload_set_sha512" "$workload_set_archive" "$offline"
fi
python3 - "$manifest_archive" "$manifest_sha512_base64" <<'PY'
import base64,hashlib,sys
actual=base64.b64encode(hashlib.sha512(open(sys.argv[1],'rb').read()).digest()).decode()
if actual != sys.argv[2]: raise SystemExit(f"official iOS manifest SHA-512 mismatch: {actual}")
PY

if [[ "$sdk_source" == installer ]]; then
    "$script_dir/eng/install/install-dotnet-sdk.py" install --archive "$sdk_archive" \
        --root "$dotnet_root" --receipt "$sdk_receipt" --sdk-version "$sdk_version" \
        --archive-sha512 "$sdk_sha512"
else
    [[ -x "$dotnet_root/dotnet" && -d "$dotnet_root/sdk/$sdk_version" ]] || {
        printf 'selected .NET SDK %s is incomplete under %s\n' "$sdk_version" "$dotnet_root" >&2
        false
    }
fi
mkdir -p "$control_root"
python3 - "$control_root/global.json" "$sdk_version" "$workload_set_version" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"sdk":{
    "version":sys.argv[2],"rollForward":"disable","workloadVersion":sys.argv[3],
}},indent=2)+"\n",encoding="utf-8")
PY
activate_environment
[[ "$(cd "$control_root" && "$dotnet_root/dotnet" --version)" == "$sdk_version" ]]
nuget_verify=("$manifest_archive")
if [[ "$workload_source" == installer ]]; then
    nuget_verify+=("$workload_set_archive")
fi
NUGET_CERT_REVOCATION_MODE=offline "$dotnet_root/dotnet" nuget verify --all "${nuget_verify[@]}" >/dev/null

if [[ -z "$aot_pack" ]]; then
    llvm_root="$cache_root/build/llvm-$llvm_version"
    llvm_args=()
    [[ "$offline" == false ]] || llvm_args+=(--offline)
    "$script_dir/eng/runtime/acquire-llvm.py" --dotnet "$dotnet_root/dotnet" \
        --packages "$cache_root/nuget/llvm" --output "$llvm_root" "${llvm_args[@]}"
    runtime_source="$cache_root/sources/runtime-$runtime_commit"
    if [[ "$offline" == true && ! -d "$runtime_source/.git" ]]; then
        printf 'runtime source is unavailable in offline mode: %s\n' "$runtime_source" >&2
        false
    fi
    runtime_args=()
    [[ "$offline" == false ]] || runtime_args+=(--offline)
    "$script_dir/eng/runtime/acquire-runtime.sh" --destination "$runtime_source" "${runtime_args[@]}"
    "$script_dir/eng/runtime/apply-patches.sh" --source "$runtime_source"
    compiler_root="$cache_root/build/mono-aot-$runtime_commit"
    if [[ ! -x "$compiler_root/install/bin/mono-aot-cross" ]]; then
        "$script_dir/eng/runtime/build-mono-aot-cross.sh" --source "$runtime_source" \
            --build "$compiler_root/build" --install "$compiler_root/install" \
            --llvm-root "$llvm_root" --jobs "$jobs"
    fi
    aot_pack="$cache_root/build/aot-pack-$runtime_commit"
    if [[ ! -d "$aot_pack" ]]; then
        "$script_dir/eng/runtime/package-aot-pack.py" \
            --compiler "$compiler_root/install/bin/mono-aot-cross" \
            --runtime-commit "$runtime_commit" --llvm-root "$llvm_root" --output "$aot_pack"
    fi
fi

"$script_dir/eng/workload/build-local-feed.py" --official-manifest "$manifest_archive" \
    --aot-pack "$aot_pack" --output "$feed"

sdk_output="$data_root/apple-sdk/iPhoneOS$xcode_sdk_version.sdk"
if [[ ! -f "$sdk_output/.dotnet-ios-linux-sdk.json" ]]; then
    "$script_dir/eng/toolchain/import-xcode-sdk.py" --archive "$xcode_archive" \
        --archive-sha256 "$xcode_archive_sha256" --output "$sdk_output" \
        --xcode-version "$xcode_version" --xcode-build "$xcode_build" \
        --platform-version "$platform_version" --platform-build "$platform_build" \
        --sdk-version "$xcode_sdk_version" --sdk-build "$xcode_sdk_build"
fi
"$script_dir/eng/toolchain/build-synthetic-xcode.py" --sdk "$sdk_output" --sdk-version "$xcode_sdk_version" \
    --xcode-version "$xcode_version" --xcode-build "$xcode_build" --platform-build "$platform_build" \
    --output "$data_root/Xcode.app" --mode symlink

offline_args=()
[[ "$offline" == false ]] || offline_args+=(--offline)
"$script_dir/eng/toolchain/setup-swift.sh" --destination "$toolchain/swift-$swift_version" \
    --cache "$cache_root/downloads" "${offline_args[@]}"
if [[ "$offline" == true && ! -d "$cache_root/sources/xtool-$xtool_revision_short/.git" ]]; then
    printf 'xtool source is unavailable in offline mode\n' >&2
    false
fi
"$script_dir/eng/toolchain/setup-xtool.sh" --source-cache "$cache_root/sources/xtool-$xtool_revision_short" \
    --swift-root "$toolchain/swift-$swift_version" --destination "$toolchain/xtool-$xtool_revision_short" \
    --package-cache "$cache_root/swiftpm" "${offline_args[@]}"
"$script_dir/eng/toolchain/setup-python.sh" --destination "$toolchain/python-$python_version" \
    --cache "$cache_root/downloads" "${offline_args[@]}"
"$script_dir/eng/toolchain/setup-pymobiledevice3.sh" --destination "$toolchain/pymobiledevice3-$pymobiledevice3_version" \
    --python "$toolchain/python-$python_version/bin/python3" --uv "$toolchain/python-$python_version/bin/uv" \
    --cache "$cache_root/uv" "${offline_args[@]}"
"$script_dir/eng/toolchain/setup-rcodesign.sh" --destination "$toolchain/rcodesign-$rcodesign_version" \
    --cache "$cache_root/downloads" "${offline_args[@]}"

mkdir -p "$data_root/bin"
ln -s ../toolchains/"$install_id"/xtool-"$xtool_revision_short"/xtool "$data_root/bin/xtool"
ln -s ../toolchains/"$install_id"/pymobiledevice3-"$pymobiledevice3_version"/bin/pymobiledevice3 "$data_root/bin/pymobiledevice3"
ln -s ../toolchains/"$install_id"/rcodesign-"$rcodesign_version"/rcodesign "$data_root/bin/rcodesign"
"$script_dir/eng/install/write-activation.py" --output "$data_root/activate" \
    --data-root "$data_root" --dotnet-root "$dotnet_root" \
    --install-id "$install_id" --project-version "$project_version" --swift-version "$swift_version"

if [[ -e "$dotnet_root/metadata/workloads/$sdk_feature_band/InstalledWorkloads/ios" ]]; then
    printf 'an ios workload already exists without this installer ownership; refusing to replace it\n' >&2
    false
fi
"$script_dir/eng/install/workload-transaction.py" begin --root "$dotnet_root" --transaction "$workload_transaction"
mkdir -p "$cache_root/workload-temp"
(cd "$control_root" && "$dotnet_root/dotnet" workload update \
    --configfile "$feed/NuGet.Config" --disable-parallel --temp-dir "$cache_root/workload-temp" --verbosity minimal)
(cd "$control_root" && "$dotnet_root/dotnet" workload install ios \
    --configfile "$feed/NuGet.Config" --disable-parallel --temp-dir "$cache_root/workload-temp" --verbosity minimal)
(cd "$control_root" && "$dotnet_root/dotnet" workload list) |
    awk -v expected="$manifest_version/$manifest_suffix" \
        '$1 == "ios" && $2 == expected { found=1 } END { exit !found }'
[[ -x "$dotnet_root/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/$project_version/tools/mono-aot-cross" ]]
"$script_dir/eng/install/verify-workload-packages.py" --root "$dotnet_root" \
    --feed "$feed" --lock "$lock_path" \
    --dotnet "$dotnet_root/dotnet"
read_resolved_pairs install_context recommended-xcode --root "$dotnet_root" --lock "$lock_path"
recommended_xcode_version="${install_context[recommended_xcode_version]}"
xcode_is_compatible "$xcode_version" "$recommended_xcode_version" || {
    printf 'Xcode %s is incompatible with installed iOS workload %s; expected Xcode %s\n' \
        "$xcode_version" "$manifest_version" "$recommended_xcode_version" >&2
    false
}
"$script_dir/eng/install/workload-transaction.py" commit --root "$dotnet_root" --transaction "$workload_transaction"

python3 - "$receipt" "$lock_sha256" "$dotnet_root" "$data_root" "$cache_root" \
    "$project_version" "$install_id" "$sdk_version" "$sdk_source" "$workload_set_version" \
    "$manifest_version" "$xcode_version" "$xcode_build" "$xcode_sdk_version" \
    "$xcode_sdk_build" "$xcode_archive_sha256" "$locked_xcode_sha256" <<'PY'
import json, os, sys
from pathlib import Path
receipt=Path(sys.argv[1])
value={
 "schemaVersion":1,"owner":"dotnet-ios-linux","status":"complete",
 "projectVersion":sys.argv[6],"installId":sys.argv[7],
 "toolchainLockSha256":sys.argv[2],"dotnetRoot":sys.argv[3],"dataRoot":sys.argv[4],
 "cacheRoot":sys.argv[5],"sdkVersion":sys.argv[8],"sdkSource":sys.argv[9],
 "workloadSet":sys.argv[10],"workloadId":"ios","manifestVersion":sys.argv[11],
 "xcode":{"version":sys.argv[12],"build":sys.argv[13],"sdkVersion":sys.argv[14],
          "sdkBuild":sys.argv[15],"archiveSha256":sys.argv[16],
          "matchesLockedArchive":sys.argv[16] == sys.argv[17]},
 "credentialsStored":False,"pairingRecordsStored":False,"deviceIdentifiersStored":False,
}
receipt.parent.mkdir(parents=True,exist_ok=True)
temporary=receipt.with_name(receipt.name+f".tmp.{os.getpid()}")
temporary.write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8"); os.replace(temporary,receipt)
PY
"$script_dir/eng/install/durable-assets.py" record --root "$data_root" --receipt "$receipt"
python3 - "$marker" <<'PY'
import json, os, sys
from pathlib import Path
path=Path(sys.argv[1])
value=json.loads(path.read_text(encoding="utf-8"))
if value.get("owner") != "dotnet-ios-linux" or value.get("status") != "installing":
    raise SystemExit("installation marker changed during finalization")
value["status"]="complete"
temporary=path.with_name(path.name+f".tmp.{os.getpid()}")
temporary.write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8"); os.replace(temporary,path)
PY
if ! doctor; then
    rollback_install 1
fi
installation_active=false
trap - ERR INT TERM
printf 'install=passed\n'
printf 'Run: source %q\n' "$data_root/activate"
