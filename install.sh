#!/usr/bin/env bash
# User-invoked bootstrap for the locked Linux-to-iOS .NET toolchain.
set -Eeuo pipefail
umask 077

((EUID != 0)) || {
    printf 'do not run install.sh as root or through sudo; run it as the target user\n' >&2
    exit 1
}

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly lock_path="$script_dir/toolchain.lock.json"
readonly context_resolver="$script_dir/eng/install/resolve-install-context.py"
. "$script_dir/eng/install/download-verified.sh"

usage() {
    cat <<'EOF'
Usage:
  ./install.sh install --xcode-archive /path/to/Xcode.xip [options]
  ./install.sh doctor
  ./install.sh preflight [--rollback-snapshot /absolute/path]
  ./install.sh uninstall [--yes] [--purge-cache]

Install options:
  --xcode-archive PATH  Required user-supplied Xcode archive compatible with
                        the selected iOS workload.
  --aot-pack PATH       Reuse a locally built LLVM AOT pack layout.
  --jobs COUNT          Runtime compiler build parallelism.
  --offline             Forbid external downloads; require complete caches.
  --keep-existing ID    Preserve an existing direct Apple workload registration
                        instead of replacing it. Repeat for ios, macos,
                        maccatalyst, or tvos. Kept registrations remain
                        external and unqualified by this project.
  --yes                 Do not prompt before host-root or privilege changes.
  --dry-run             Print paths and planned operations without changing them.
  --rollback-snapshot PATH
                        Create a verified full-root rollback snapshot at this
                        new external path. Valid only with preflight.

The host SDK/workload root is resolved from the real dotnet command. Durable
components are installed below ${XDG_DATA_HOME:-~/.local/share}/dotnet-ios-linux;
downloads and builds are kept below ${XDG_CACHE_HOME:-~/.cache}/dotnet-ios-linux.
Shell startup files, credentials, pairing records, devices, and signing state
are never modified.

The installer resolves the real host dotnet executable and uses its containing
SDK/workload root. Do not run this complete script with sudo; it requests
permission only for the constrained helper when the selected root is not writable.
EOF
}

command_name="install"
if (($#)) && [[ "$1" =~ ^(install|setup|doctor|preflight|uninstall)$ ]]; then
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
rollback_snapshot=""
declare -a keep_existing_workloads=()
keep_existing_supplied=false
while (($#)); do
    case "$1" in
        --xcode-archive) xcode_archive="${2:?missing value for --xcode-archive}"; shift 2 ;;
        --aot-pack) aot_pack="${2:?missing value for --aot-pack}"; shift 2 ;;
        --jobs) jobs="${2:?missing value for --jobs}"; jobs_supplied=true; shift 2 ;;
        --offline) offline=true; shift ;;
        --yes) assume_yes=true; yes_supplied=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        --rollback-snapshot) rollback_snapshot="${2:?missing value for --rollback-snapshot}"; shift 2 ;;
        --keep-existing) keep_existing_workloads+=("${2:?missing value for --keep-existing}"); keep_existing_supplied=true; shift 2 ;;
        --purge-cache) purge_cache=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { printf -- '--jobs must be a positive integer\n' >&2; exit 2; }
case "$command_name" in
    install)
        [[ "$purge_cache" == false ]] || { printf -- '--purge-cache is valid only with uninstall\n' >&2; exit 2; }
        [[ -z "$rollback_snapshot" ]] || {
            printf -- '--rollback-snapshot is valid only with preflight\n' >&2
            exit 2
        }
        ;;
    doctor)
        if [[ -n "$xcode_archive" || -n "$aot_pack" || "$offline" == true || "$dry_run" == true ||
              "$purge_cache" == true || "$yes_supplied" == true || "$jobs_supplied" == true ||
               -n "$rollback_snapshot" || "$keep_existing_supplied" == true ]]; then
            printf 'doctor does not accept lifecycle options\n' >&2
            exit 2
        fi
        ;;
    preflight)
        if [[ -n "$xcode_archive" || -n "$aot_pack" || "$offline" == true || "$dry_run" == true ||
               "$purge_cache" == true || "$yes_supplied" == true || "$jobs_supplied" == true || "$keep_existing_supplied" == true ]]; then
            printf 'preflight accepts only --rollback-snapshot\n' >&2
            exit 2
        fi
        ;;
    uninstall)
        if [[ -n "$xcode_archive" || -n "$aot_pack" || "$offline" == true || "$dry_run" == true ||
               "$jobs_supplied" == true || -n "$rollback_snapshot" || "$keep_existing_supplied" == true ]]; then
            printf 'install-only options were supplied to uninstall\n' >&2
            exit 2
        fi
        ;;
esac

declare -a direct_apple_workloads=(ios macos maccatalyst tvos)
declare -a managed_workloads=()
declare -a kept_workloads=()
normalise_workload_plan() {
    local item existing supported=false
    kept_workloads=()
    for item in "${keep_existing_workloads[@]}"; do
        case "$item" in ios|macos|maccatalyst|tvos) ;; *)
            printf '%s is not a direct managed Apple workload for --keep-existing\n' "$item" >&2
            return 1 ;; esac
        for existing in "${kept_workloads[@]}"; do
            [[ "$existing" != "$item" ]] || {
                printf 'duplicate --keep-existing workload: %s\n' "$item" >&2; return 1;
            }
        done
        kept_workloads+=("$item")
    done
    managed_workloads=()
    for item in "${direct_apple_workloads[@]}"; do
        supported=false
        for existing in "${kept_workloads[@]}"; do [[ "$existing" == "$item" ]] && supported=true; done
        [[ "$supported" == true ]] || managed_workloads+=("$item")
    done
}
normalise_workload_plan
requested_kept_workloads_csv="$(IFS=,; printf '%s' "${kept_workloads[*]}")"

[[ -n "${HOME:-}" && "$HOME" == /* ]] || { printf 'HOME must be an absolute path\n' >&2; exit 1; }
if [[ -n "${XDG_DATA_HOME:-}" && "$XDG_DATA_HOME" != /* ]]; then
    printf 'XDG_DATA_HOME must be an absolute path\n' >&2
    exit 1
fi
if [[ -n "${XDG_CACHE_HOME:-}" && "$XDG_CACHE_HOME" != /* ]]; then
    printf 'XDG_CACHE_HOME must be an absolute path\n' >&2
    exit 1
fi

need() { command -v "$1" >/dev/null || { printf 'required host command is missing: %s\n' "$1" >&2; exit 1; }; }

sync_path_state() {
    python3 - "$script_dir/eng/common" "$1" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from atomic import fsync_path_state
fsync_path_state(Path(sys.argv[2]))
PY
}
need realpath
host_dotnet="$(command -v dotnet || true)"
[[ -n "$host_dotnet" ]] || {
    printf 'required host command is missing: dotnet\n' >&2
    printf 'install a compatible .NET SDK and ensure dotnet is on PATH\n' >&2
    exit 1
}
if ! dotnet_executable="$(realpath -e "$host_dotnet")"; then
    printf 'cannot resolve the host dotnet executable: %s\n' "$host_dotnet" >&2
    exit 1
fi
readonly dotnet_executable
readonly dotnet_root="$(dirname "$dotnet_executable")"
readonly data_root="${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux"
readonly cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux"
readonly cache_marker="$cache_root/.dotnet-ios-linux-cache.json"
readonly marker="$data_root/.dotnet-ios-linux.json"
readonly receipt="$data_root/receipts/install.json"
readonly sdk_receipt="$data_root/receipts/dotnet-sdk.json"
readonly workload_transaction="$data_root/receipts/workload-transaction"
readonly host_root_snapshotter="$script_dir/eng/install/host-root-snapshot.py"
readonly apple_workload_inventory="$script_dir/eng/install/apple-workload-inventory.py"
readonly control_root="$data_root/control"
readonly bundle_builder="$script_dir/eng/workload/build-workload-bundle.py"
readonly system_helper_source="$script_dir/eng/install/system-workload-helper.py"
readonly transaction_source="$script_dir/eng/install/workload-transaction.py"
readonly atomic_source="$script_dir/eng/common/atomic.py"
readonly system_helper_base="/usr/local/lib/dotnet-ios-linux/versions"
readonly system_trust_base="/usr/local/share/dotnet-ios-linux/versions"
system_helper_version=""
installed_system_helper=""
installed_transaction=""
installed_atomic=""
installed_trust_lock=""

[[ ! -L "$data_root" ]] || { printf 'durable data root must not be a symlink: %s\n' "$data_root" >&2; exit 1; }
[[ ! -L "$cache_root" ]] || { printf 'cache root must not be a symlink: %s\n' "$cache_root" >&2; exit 1; }
[[ -x "$dotnet_executable" && "$dotnet_executable" == "$dotnet_root/dotnet" ]] || {
    printf 'resolved host dotnet executable has an unsupported layout: %s\n' "$dotnet_executable" >&2
    exit 1
}
[[ ! -L "$dotnet_root" ]] || { printf 'host SDK root must not be a symlink: %s\n' "$dotnet_root" >&2; exit 1; }

for tool in python3 flock mktemp mv realpath sha256sum sha512sum; do need "$tool"; done
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required to run the bootstrap")
PY

if [[ "$command_name" == preflight ]]; then
    if [[ -n "$rollback_snapshot" ]]; then
        python3 "$host_root_snapshotter" snapshot --root "$dotnet_root" \
            --output "$rollback_snapshot"
        python3 "$apple_workload_inventory" --root "$dotnet_root" --format key-value
        python3 "$host_root_snapshotter" verify --root "$dotnet_root" \
            --snapshot "$rollback_snapshot"
    else
        python3 "$apple_workload_inventory" --root "$dotnet_root"
    fi
    exit 0
fi

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
readonly binutils_version="${lock_values[binutils_version]}"
readonly xtool_revision_short="${lock_values[xtool_revision_short]}"
readonly python_version="${lock_values[python_version]}"
readonly pymobiledevice3_version="${lock_values[pymobiledevice3_version]}"
readonly rcodesign_version="${lock_values[rcodesign_version]}"
readonly sdk_archive="$cache_root/downloads/${lock_values[sdk_archive]}"
readonly manifest_archive="$cache_root/downloads/${lock_values[manifest_archive]}"
readonly workload_set_archive="$cache_root/downloads/${lock_values[workload_package_archive]}"
readonly macos_manifest_url="${lock_values[macos_manifest_url]}"
readonly macos_manifest_sha512="${lock_values[macos_manifest_sha512]}"
readonly macos_manifest_sha512_base64="${lock_values[macos_manifest_sha512_base64]}"
readonly macos_manifest_archive="$cache_root/downloads/${lock_values[macos_manifest_archive]}"
readonly maccatalyst_manifest_url="${lock_values[maccatalyst_manifest_url]}"
readonly maccatalyst_manifest_sha512="${lock_values[maccatalyst_manifest_sha512]}"
readonly maccatalyst_manifest_sha512_base64="${lock_values[maccatalyst_manifest_sha512_base64]}"
readonly maccatalyst_manifest_archive="$cache_root/downloads/${lock_values[maccatalyst_manifest_archive]}"
readonly tvos_manifest_url="${lock_values[tvos_manifest_url]}"
readonly tvos_manifest_sha512="${lock_values[tvos_manifest_sha512]}"
readonly tvos_manifest_sha512_base64="${lock_values[tvos_manifest_sha512_base64]}"
readonly tvos_manifest_archive="$cache_root/downloads/${lock_values[tvos_manifest_archive]}"
lock_sha256="$(sha256sum -b "$lock_path" | cut -d' ' -f1)"
readonly current_system_helper_version="$(python3 - \
    "$system_helper_source" "$transaction_source" "$atomic_source" "$lock_path" <<'PY'
import hashlib, sys
labels=(
    "eng/install/system-workload-helper.py",
    "eng/install/workload-transaction.py",
    "eng/common/atomic.py",
    "toolchain.lock.json",
)
value=hashlib.sha256()
for label, raw in zip(labels, sys.argv[1:], strict=True):
    value.update(label.encode("utf-8")+b"\0")
    with open(raw,"rb") as stream:
        for block in iter(lambda: stream.read(1024*1024),b""):
            value.update(block)
print(value.hexdigest())
PY
)"

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
privilege_mode=""
system_transaction_id=""
workload_bundle=""
system_helper_transaction_active=false
workload_manifest=""
managed_workloads_csv=""
kept_workloads_csv=""
user_root_lock_acquired=false
shared_consumers=0
declare -a bundle_package_paths=()

set_system_helper_paths() {
    local version="$1"
    [[ "$version" =~ ^[0-9a-f]{64}$ ]] || {
        printf 'invalid system helper version in installation context\n' >&2
        return 1
    }
    system_helper_version="$version"
    installed_system_helper="$system_helper_base/$version/eng/install/system-workload-helper.py"
    installed_transaction="$system_helper_base/$version/eng/install/workload-transaction.py"
    installed_atomic="$system_helper_base/$version/eng/common/atomic.py"
    installed_trust_lock="$system_trust_base/$version/toolchain.lock.json"
}

set_install_paths() {
    install_id="$project_version-sdk-$sdk_version"
    feed="$data_root/feeds/$install_id"
    toolchain="$data_root/toolchains/$install_id"
}

workload_manifest_id() {
    local package_id
    case "$1" in
        ios) package_id="${lock_values[manifest_id]}" ;;
        macos) package_id="${lock_values[macos_manifest_id]}" ;;
        maccatalyst) package_id="${lock_values[maccatalyst_manifest_id]}" ;;
        tvos) package_id="${lock_values[tvos_manifest_id]}" ;;
        *) return 1 ;;
    esac
    printf '%s\n' "${package_id%.Manifest-*}"
}

workload_manifest_identity() {
    case "$1" in
        ios) printf '%s/%s\n' "$manifest_version" "$manifest_suffix" ;;
        macos) printf '%s/%s\n' "${lock_values[macos_manifest_version]}" "$manifest_suffix" ;;
        maccatalyst) printf '%s/%s\n' "${lock_values[maccatalyst_manifest_version]}" "$manifest_suffix" ;;
        tvos) printf '%s/%s\n' "${lock_values[tvos_manifest_version]}" "$manifest_suffix" ;;
        *) return 1 ;;
    esac
}

workload_csv() {
    local IFS=,
    printf '%s' "$*"
}

set_workload_plan_strings() {
    managed_workloads_csv="$(workload_csv "${managed_workloads[@]}")"
    kept_workloads_csv="$(workload_csv "${kept_workloads[@]}")"
}

workload_is_managed() {
    [[ ",${managed_workloads_csv}," == *,"$1",* ]]
}

assert_kept_registrations_exist() {
    ((${#kept_workloads[@]})) || return 0
    local workload descriptors=""
    for workload in "${kept_workloads[@]}"; do
        descriptors+="${workload}:$(workload_manifest_id "$workload"):$(workload_manifest_identity "$workload"),"
    done
    python3 - "$dotnet_root" "$sdk_feature_band" "$descriptors" <<'PY'
import json, re, sys
from pathlib import Path
root=Path(sys.argv[1]); sdk_band=sys.argv[2]
requested=[]
for raw in (item for item in sys.argv[3].split(',') if item):
    workload, manifest_id, identity = raw.split(':', 2)
    version, manifest_band = identity.split('/', 1)
    requested.append((workload, manifest_id, version, manifest_band))

def child(parent, name):
    if parent.is_symlink() or not parent.is_dir(): return None
    matches=[item for item in parent.iterdir() if item.name.casefold()==name.casefold()]
    if len(matches)>1: raise SystemExit(f'--keep-existing metadata case-collides under {parent}')
    if not matches: return None
    result=matches[0]
    if result.is_symlink() or not result.is_dir():
        raise SystemExit(f'--keep-existing metadata is not a real directory: {result}')
    return result

def safe_directory(path, label):
    if path.is_symlink() or not path.is_dir():
        raise SystemExit(f'--keep-existing {label} is not a real directory: {path}')
    return path

def marker_paths(workload):
    metadata=safe_directory(root/'metadata', 'metadata root')
    workloads_root=safe_directory(metadata/'workloads', 'workload metadata root')
    found=[]
    for directory, directories, files in __import__('os').walk(workloads_root, topdown=True, followlinks=False):
        current=Path(directory)
        if any((current/name).is_symlink() for name in [*directories,*files]):
            raise SystemExit(f'--keep-existing workload metadata contains a symlink: {current}')
        if current.name.casefold() != 'installedworkloads':
            continue
        scope=current.relative_to(workloads_root).parts[:-1]
        if scope.count(sdk_band) != 1:
            continue
        for name in files:
            marker=current/name
            if name.casefold() == workload.casefold() and marker.is_file():
                found.append(marker)
    return found

for workload, manifest_id, version, manifest_band in requested:
    markers=marker_paths(workload)
    if len(markers) != 1:
        raise SystemExit('requested --keep-existing workload is not registered for the selected SDK feature band: '+workload)
    registry=child(root/'metadata'/'workloads'/'InstalledManifests'/'v1', manifest_id)
    records=[]
    if registry:
        for candidate_version in registry.iterdir():
            if candidate_version.is_symlink() or not candidate_version.is_dir(): continue
            for candidate_band in candidate_version.iterdir():
                leaf=candidate_band/sdk_band
                if candidate_band.is_symlink() or not candidate_band.is_dir(): continue
                if leaf.is_file() and not leaf.is_symlink(): records.append((candidate_version.name,candidate_band.name))
    if records != [(version, manifest_band)]:
        raise SystemExit(
            f'--keep-existing {workload} is not the exact locked manifest identity; refusing to reconcile an external registration'
        )
    manifest_root=child(safe_directory(root/'sdk-manifests'/manifest_band, 'SDK manifest feature band'), manifest_id)
    manifest_version=child(manifest_root, version) if manifest_root else None
    manifest=manifest_version/'WorkloadManifest.json' if manifest_version else None
    try:
        value=json.loads(re.sub(r',(?=\s*[}\]])','',manifest.read_text(encoding='utf-8-sig')))
        if not isinstance(value.get('workloads'),dict) or workload not in value['workloads']:
            raise ValueError('direct workload is missing')
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f'--keep-existing {workload} has no safe matching manifest: {error}')
PY
}

xcode_is_compatible() {
    "$context_resolver" xcode-compatible --actual "$1" --recommended "$2" >/dev/null
}

dotnet_root_requires_elevation() {
    local path
    [[ -w "$dotnet_root" ]] || return 0
    for path in sdk sdk-manifests packs template-packs metadata; do
        if [[ -e "$dotnet_root/$path" && ! -w "$dotnet_root/$path" ]]; then
            return 0
        fi
    done
    return 1
}

resolve_privilege_mode() {
    if dotnet_root_requires_elevation; then
        privilege_mode="system-helper"
        system_transaction_id="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        set_system_helper_paths "$current_system_helper_version"
        [[ "$sdk_source" != installer ]] || {
            printf 'the non-writable host .NET root lacks a compatible SDK in feature band %s\n' \
                "$sdk_feature_band" >&2
            printf 'install a compatible SDK through the host package manager before continuing\n' >&2
            return 1
        }
        [[ "$workload_set_version" == "$workload_fallback_version" ]] || {
            printf 'system-root installation requires locked workload set %s, found %s\n' \
                "$workload_fallback_version" "$workload_set_version" >&2
            return 1
        }
        ((${#kept_workloads[@]} == 0)) || {
            printf '%s\n' '--keep-existing is not supported for a system root; the iOS-only helper cannot protect external Apple registrations' >&2
            return 1
        }
        # The privileged helper deliberately remains on its proven iOS-only
        # transaction until it has equivalent aggregate restoration semantics.
        managed_workloads=(ios)
        set_workload_plan_strings
    else
        privilege_mode="user"
        system_transaction_id=""
    fi
}

resolve_new_install_context() {
    read_resolved_pairs install_context sdk --root "$dotnet_root" \
        --feature-band "$sdk_feature_band" --fallback "$sdk_fallback_version"
    sdk_version="${install_context[sdk_version]}"
    sdk_source="${install_context[sdk_source]}"
    read_resolved_pairs install_context workload --root "$dotnet_root" \
        --feature-band "$sdk_feature_band" \
        --manifest "$(workload_manifest_id ios)=$manifest_version/$manifest_suffix" \
        --manifest "$(workload_manifest_id macos)=${lock_values[macos_manifest_version]}/$manifest_suffix" \
        --manifest "$(workload_manifest_id maccatalyst)=${lock_values[maccatalyst_manifest_version]}/$manifest_suffix" \
        --manifest "$(workload_manifest_id tvos)=${lock_values[tvos_manifest_version]}/$manifest_suffix" \
        --fallback "$workload_fallback_version"
    workload_set_version="${install_context[workload_version]}"
    workload_source="${install_context[workload_source]}"
    workload_manifest="$manifest_version/$manifest_suffix"
    set_workload_plan_strings
    set_install_paths
    resolve_privilege_mode
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
mode=value.get("privilegeMode","user")
if mode not in ("user","system-helper"):
    raise SystemExit("installation context has an invalid privilege mode")
sys.stdout.buffer.write(b"privilegeMode\0"+mode.encode()+b"\0")
transaction=value.get("systemTransactionId")
if transaction is not None:
    if not isinstance(transaction,str) or not transaction or "\x00" in transaction:
        raise SystemExit("installation context has an invalid system transaction ID")
    sys.stdout.buffer.write(b"systemTransactionId\0"+transaction.encode()+b"\0")
helper=value.get("systemHelperVersion")
if helper is not None:
    if not isinstance(helper,str) or len(helper) != 64 or any(c not in "0123456789abcdef" for c in helper):
        raise SystemExit("installation context has an invalid system helper version")
    sys.stdout.buffer.write(b"systemHelperVersion\0"+helper.encode()+b"\0")
manifest=value.get("workloadManifest")
if manifest is not None:
    if not isinstance(manifest,str) or not manifest or "\x00" in manifest:
        raise SystemExit("installation context has an invalid workload manifest")
    sys.stdout.buffer.write(b"workloadManifest\0"+manifest.encode()+b"\0")
for key in ("managedWorkloads", "keptWorkloads"):
    items=value.get(key)
    if items is None:
        # Receipts created before direct companion registration managed only iOS.
        items=["ios"] if key == "managedWorkloads" else []
    if not isinstance(items,list) or not all(isinstance(item,str) and item in {"ios","macos","maccatalyst","tvos"} for item in items):
        raise SystemExit(f"installation context has invalid {key}")
    if len(items) != len(set(items)):
        raise SystemExit(f"installation context has duplicate {key}")
    sys.stdout.buffer.write(key.encode()+b"\0"+','.join(items).encode()+b"\0")
for key in ("sdkSource","workloadSource"):
    item=value.get(key)
    if isinstance(item,str) and item:
        sys.stdout.buffer.write(key.encode()+b"\0"+item.encode()+b"\0")
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
    sdk_source="${install_context[sdkSource]:-receipt}"
    workload_source="${install_context[workloadSource]:-receipt}"
    privilege_mode="${install_context[privilegeMode]}"
    system_transaction_id="${install_context[systemTransactionId]:-}"
    workload_manifest="${install_context[workloadManifest]:-$manifest_version/$manifest_suffix}"
    if [[ -v 'install_context[managedWorkloads]' ]]; then
        managed_workloads=()
        if [[ -n "${install_context[managedWorkloads]}" ]]; then
            IFS=',' read -r -a managed_workloads <<< "${install_context[managedWorkloads]}"
        fi
    else
        # Receipts created before direct companion registration owned iOS only.
        managed_workloads=(ios)
    fi
    if [[ -n "${install_context[keptWorkloads]:-}" ]]; then
        IFS=',' read -r -a kept_workloads <<< "${install_context[keptWorkloads]}"
    else
        kept_workloads=()
    fi
    set_workload_plan_strings
    if [[ "$privilege_mode" == system-helper ]]; then
        set_system_helper_paths "${install_context[systemHelperVersion]:-}"
    fi
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

read_marker_phase() {
    python3 - "$marker" <<'PY'
import json, sys
from pathlib import Path
value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if value.get("owner") != "dotnet-ios-linux":
    raise SystemExit("installation marker has a different owner")
print(value.get("uninstallPhase", ""))
PY
}

set_marker_state() {
    local expected_status="$1" new_status="$2" phase="$3"
    python3 - "$marker" "$expected_status" "$new_status" "$phase" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
path=Path(sys.argv[1])
value=json.loads(path.read_text(encoding="utf-8"))
if value.get("owner") != "dotnet-ios-linux" or value.get("status") != sys.argv[2]:
    raise SystemExit("installation marker changed during lifecycle transition")
value["status"]=sys.argv[3]
if sys.argv[4]: value["uninstallPhase"]=sys.argv[4]
else: value.pop("uninstallPhase",None)
descriptor,name=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
temporary=Path(name)
try:
    with os.fdopen(descriptor,"w",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
except BaseException:
    temporary.unlink(missing_ok=True)
    raise
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

ensure_owned_cache_directory() {
    local directory="$1" parent
    [[ "$directory" == "$cache_root/"* ]] || {
        printf 'refusing to create a cache directory outside %s: %s\n' "$cache_root" "$directory" >&2
        return 1
    }
    parent="$(dirname "$directory")"
    [[ -d "$parent" && ! -L "$parent" ]] || {
        printf 'cache directory parent is unsafe: %s\n' "$parent" >&2
        return 1
    }
    if [[ -e "$directory" || -L "$directory" ]]; then
        [[ -d "$directory" && ! -L "$directory" ]] || {
            printf 'cache directory is unsafe: %s\n' "$directory" >&2
            return 1
        }
    else
        mkdir "$directory"
    fi
}

verify_compiler_cache() {
    local compiler_root="$1" llvm_root="$2"
    "$script_dir/eng/runtime/package-aot-pack.py" \
        --verify-compiler-cache --compiler "$compiler_root/install/bin/mono-aot-cross" \
        --runtime-commit "$runtime_commit" --llvm-root "$llvm_root" >/dev/null
}

verify_aot_pack_cache() {
    local aot_pack_root="$1"
    "$script_dir/eng/runtime/package-aot-pack.py" --verify-aot-pack "$aot_pack_root" >/dev/null
}

discard_build_cache() {
    local cache_entry="$1" expected_prefix="$2"
    python3 - "$script_dir/eng/common" "$cache_root" "$cache_entry" "$expected_prefix" <<'PY'
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from atomic import durable_remove

cache_root = Path(sys.argv[2])
candidate = Path(sys.argv[3])
expected_prefix = sys.argv[4]
expected_parent = cache_root / "build"
if candidate.parent != expected_parent or not candidate.name.startswith(expected_prefix):
    raise SystemExit(f"refusing to discard a project build cache outside {expected_parent}: {candidate}")
if not os.path.lexists(candidate):
    raise SystemExit(0)
if expected_parent.is_symlink() or not expected_parent.is_dir():
    raise SystemExit(f"project build-cache parent is unsafe: {expected_parent}")
tombstone = expected_parent / f".{candidate.name}.rejected-{uuid.uuid4().hex}"
durable_remove(candidate, tombstone)
PY
}

build_and_publish_compiler_cache() {
    local compiler_root="$1" runtime_source="$2" llvm_root="$3" stage
    if [[ -e "$cache_root/build" || -L "$cache_root/build" ]]; then
        [[ -d "$cache_root/build" && ! -L "$cache_root/build" ]] || {
            printf 'project build-cache directory is unsafe: %s\n' "$cache_root/build" >&2
            return 1
        }
    else
        mkdir "$cache_root/build"
    fi
    stage="$(mktemp -d "$cache_root/build/.${compiler_root##*/}.stage.XXXXXX")"
    if ! "$script_dir/eng/runtime/build-mono-aot-cross.sh" --source "$runtime_source" \
        --build "$stage/build" --install "$stage/install" --llvm-root "$llvm_root" --jobs "$jobs"; then
        rm -rf -- "$stage"
        return 1
    fi
    if ! "$script_dir/eng/runtime/package-aot-pack.py" --verify-compiler-cache \
        --compiler "$stage/install/bin/mono-aot-cross" --runtime-commit "$runtime_commit" \
        --llvm-root "$llvm_root"; then
        rm -rf -- "$stage"
        return 1
    fi
    if ! python3 - "$script_dir/eng/common" "$stage" "$compiler_root" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from atomic import publish_directory_noreplace

publish_directory_noreplace(Path(sys.argv[2]), Path(sys.argv[3]))
PY
    then
        rm -rf -- "$stage"
        return 1
    fi
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
    local expected_status="${1:-complete}"
    local actual_sdk actual_workload actual_xcode actual_sdk_version swift_major_minor helper_output workload_list workload expected_manifest
    [[ -f "$marker" && -f "$receipt" ]] || { printf 'dotnet-ios-linux is not installed under %s\n' "$data_root" >&2; return 1; }
    [[ "$(read_marker_status)" == "$expected_status" ]] || { printf 'installation is incomplete; rerun install\n' >&2; return 1; }
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
    if [[ "$privilege_mode" == system-helper ]]; then
        install_system_helper
        helper_output="$(run_system_helper verify \
            --sdk-version "$sdk_version" --workload-version "$workload_set_version" \
            --workload-manifest "$workload_manifest" \
            --ownership-token "$system_transaction_id")"
        grep -Fxq 'system_workload=ok' <<<"$helper_output" || {
            printf 'system workload helper did not confirm the installation\n' >&2
            return 1
        }
        grep -Fxq 'ios_installed=true' <<<"$helper_output" || {
            printf 'the pinned ios workload is not installed\n' >&2
            return 1
        }
        actual_workload="$(sed -n 's/^workload_set=//p' <<<"$helper_output")"
    else
        if ((${#managed_workloads[@]})); then
            "$script_dir/eng/install/workload-transaction.py" verify \
                --root "$dotnet_root" --transaction "$workload_transaction" >/dev/null
        fi
        actual_workload="$(cd "$control_root" && "$dotnet_root/dotnet" workload --version)"
        workload_list="$(cd "$control_root" && "$dotnet_root/dotnet" workload list)"
        for workload in "${managed_workloads[@]}"; do
            expected_manifest="$(workload_manifest_identity "$workload")"
            awk -v workload="$workload" -v expected="$expected_manifest" \
                '$1 == workload && $2 == expected { found=1 } END { exit !found }' <<<"$workload_list" || {
                printf 'the managed %s workload is not installed at %s\n' "$workload" "$expected_manifest" >&2; return 1;
            }
        done
        for workload in "${kept_workloads[@]}"; do
            awk -v workload="$workload" '$1 == workload { found=1 } END { exit !found }' <<<"$workload_list" || {
                printf 'the kept external %s workload is no longer registered\n' "$workload" >&2; return 1;
            }
        done
    fi
    if ((${#managed_workloads[@]})); then
        verify_workload_args=()
        for workload in "${managed_workloads[@]}"; do verify_workload_args+=(--workload "$workload"); done
        python3 "$script_dir/eng/install/verify-workload-packages.py" --root "$dotnet_root" \
            --feed "$feed" --lock "$lock_path" --dotnet "$dotnet_root/dotnet" "${verify_workload_args[@]}" >/dev/null
    fi
    actual_sdk="$(cd "$control_root" && "$dotnet_root/dotnet" --version)"
    [[ "$actual_sdk" == "$sdk_version" ]] || {
        printf 'selected .NET SDK is %s, expected installed SDK %s\n' "$actual_sdk" "$sdk_version" >&2
        return 1
    }
    [[ "$actual_workload" == "$workload_set_version" ]] || {
        printf 'selected workload set is %s, expected %s\n' "$actual_workload" "$workload_set_version" >&2
        return 1
    }
    if [[ ",${managed_workloads_csv}," == *,ios,* ]]; then
        "$dotnet_root/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/$project_version/tools/mono-aot-cross" --version |
            grep -Eq "LLVM:[[:space:]]+yes\\($((llvm_major * 100))\\)" || {
                printf 'LLVM-enabled iOS AOT compiler validation failed\n' >&2; return 1;
            }
    fi
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

acquire_system_privilege() {
    [[ "$privilege_mode" == system-helper ]] || return 0
    local sudo_executable
    sudo_executable="$(trusted_host_executable sudo)"
    if "$sudo_executable" -n -v >/dev/null 2>&1; then
        return
    fi
    [[ -t 0 ]] || {
        printf 'system .NET root requires sudo, but no interactive terminal or cached authorization is available\n' >&2
        return 1
    }
    printf 'The host .NET root %s requires elevated workload access.\n' "$dotnet_root"
    printf 'Only its constrained bootstrap and the root-owned workload helper will use sudo.\n'
    confirm "Authorize the constrained system workload helper?"
    "$sudo_executable" -v
}

trusted_host_executable() {
    local name="$1" candidate
    candidate="$(command -v "$name" || true)"
    [[ -n "$candidate" ]] || {
        printf 'required host command is missing: %s\n' "$name" >&2
        return 1
    }
    [[ -x /usr/bin/python3 ]] || {
        printf 'privileged operations require /usr/bin/python3\n' >&2
        return 1
    }
    /usr/bin/python3 -I - "$candidate" <<'PY'
import os, stat, sys
from pathlib import Path
path=Path(sys.argv[1]).resolve(strict=True)
current=Path("/")
for part in path.parts[1:]:
    current /= part
    details=current.lstat()
    if stat.S_ISLNK(details.st_mode) or details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
        raise SystemExit(f"privileged executable path is not root-owned and immutable: {current}")
details=path.stat()
if not stat.S_ISREG(details.st_mode) or not os.access(path,os.X_OK):
    raise SystemExit(f"privileged executable is not a regular executable: {path}")
print(path)
PY
}

trusted_installed_file() {
    /usr/bin/python3 -I - "$1" <<'PY'
import stat, sys
from pathlib import Path
path=Path(sys.argv[1])
current=Path("/")
for part in path.parts[1:]:
    current /= part
    details=current.lstat()
    if stat.S_ISLNK(details.st_mode) or details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
        raise SystemExit(1)
if not stat.S_ISREG(path.stat().st_mode):
    raise SystemExit(1)
PY
}

validate_python_runtime_trust() {
    /usr/bin/python3 -I - <<'PY'
import os, stat, sys
from pathlib import Path
paths={Path(sys.executable).resolve(strict=True)}
paths.update(Path(item).resolve(strict=True) for item in sys.path if item and Path(item).exists())
for root in sorted(paths):
    candidates=(root,) if root.is_file() else (root,*root.rglob("*"))
    for path in candidates:
        details=path.lstat()
        if stat.S_ISLNK(details.st_mode):
            target=path.resolve(strict=True)
            details=target.stat()
            path=target
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
            raise SystemExit(f"privileged Python runtime path is not root-owned and immutable: {path}")
PY
}

system_helper_matches_sources() {
    local source destination
    local -a pairs=(
        "$system_helper_source" "$installed_system_helper"
        "$transaction_source" "$installed_transaction"
        "$atomic_source" "$installed_atomic"
        "$lock_path" "$installed_trust_lock"
    )
    ((${#pairs[@]} % 2 == 0))
    while ((${#pairs[@]})); do
        source="${pairs[0]}"
        destination="${pairs[1]}"
        pairs=("${pairs[@]:2}")
        [[ -f "$destination" && ! -L "$destination" ]] || return 1
        trusted_installed_file "$destination" || return 1
        [[ "$(sha256sum -b "$source" | cut -d' ' -f1)" == \
           "$(sha256sum -b "$destination" | cut -d' ' -f1)" ]] || return 1
    done
}

system_helper_is_trusted() {
    local destination
    for destination in "$installed_system_helper" "$installed_transaction" \
        "$installed_atomic" "$installed_trust_lock"; do
        [[ -f "$destination" && ! -L "$destination" ]] || return 1
        trusted_installed_file "$destination" || return 1
    done
    /usr/bin/python3 -I - "$system_helper_version" "$installed_system_helper" \
        "$installed_transaction" "$installed_atomic" "$installed_trust_lock" <<'PY'
import hashlib, sys
labels=(
    "eng/install/system-workload-helper.py",
    "eng/install/workload-transaction.py",
    "eng/common/atomic.py",
    "toolchain.lock.json",
)
value=hashlib.sha256()
for label, raw in zip(labels,sys.argv[2:],strict=True):
    value.update(label.encode("utf-8")+b"\0")
    with open(raw,"rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): value.update(block)
if value.hexdigest() != sys.argv[1]: raise SystemExit(1)
PY
}

install_system_helper() {
    [[ "$privilege_mode" == system-helper ]] || return 0
    acquire_system_privilege
    local install_executable ln_executable rm_executable sudo_executable sync_executable
    sudo_executable="$(trusted_host_executable sudo)"
    sync_executable="$(trusted_host_executable sync)"
    sync_system_helper_files() {
        local path
        for path in "$installed_transaction" "$installed_atomic" \
            "$installed_trust_lock" "$installed_system_helper"; do
            "$sudo_executable" -- "$sync_executable" -f "$path"
            "$sudo_executable" -- "$sync_executable" -f "$(dirname "$path")"
        done
    }
    if [[ "$system_helper_version" != "$current_system_helper_version" ]]; then
        system_helper_is_trusted || {
            printf 'installed helper version %s is unavailable or unsafe; use the matching checkout\n' \
                "$system_helper_version" >&2
            return 1
        }
        sync_system_helper_files
        return
    fi
    if system_helper_matches_sources; then
        sync_system_helper_files
        return
    fi
    install_executable="$(trusted_host_executable install)"
    ln_executable="$(trusted_host_executable ln)"
    rm_executable="$(trusted_host_executable rm)"
    [[ -x /usr/bin/python3 ]] || {
        printf 'the privileged helper requires /usr/bin/python3\n' >&2
        return 1
    }
    validate_python_runtime_trust
    "$sudo_executable" -- "$install_executable" -d -o root -g root -m 0755 \
        "$(dirname "$installed_system_helper")" "$(dirname "$installed_atomic")" \
        "$(dirname "$installed_trust_lock")"
    publish_system_file() {
        local source="$1" destination="$2" mode="$3" stage
        if [[ -e "$destination" ]]; then
            [[ -f "$destination" && ! -L "$destination" ]] || return 1
            [[ "$(sha256sum -b "$source" | cut -d' ' -f1)" == \
               "$(sha256sum -b "$destination" | cut -d' ' -f1)" ]] || return 1
            "$sudo_executable" -- "$sync_executable" -f "$destination"
            "$sudo_executable" -- "$sync_executable" -f "$(dirname "$destination")"
            return
        fi
        stage="$destination.stage.$UID.$BASHPID.$RANDOM"
        "$sudo_executable" -- "$install_executable" -o root -g root -m "$mode" \
            "$source" "$stage"
        "$sudo_executable" -- "$sync_executable" -f "$stage"
        if ! "$sudo_executable" -- "$ln_executable" -- "$stage" "$destination"; then
            if [[ ! -f "$destination" || -L "$destination" ]]; then
                "$sudo_executable" -- "$rm_executable" -f -- "$stage"
                return 1
            fi
        fi
        "$sudo_executable" -- "$sync_executable" -f "$(dirname "$destination")"
        "$sudo_executable" -- "$rm_executable" -f -- "$stage"
        "$sudo_executable" -- "$sync_executable" -f "$(dirname "$destination")"
        [[ "$(sha256sum -b "$source" | cut -d' ' -f1)" == \
           "$(sha256sum -b "$destination" | cut -d' ' -f1)" ]]
    }
    publish_system_file "$transaction_source" "$installed_transaction" 0644
    publish_system_file "$atomic_source" "$installed_atomic" 0644
    publish_system_file "$lock_path" "$installed_trust_lock" 0644
    # Publish the executable last so the version is callable only after all of
    # its immutable dependencies are visible.
    publish_system_file "$system_helper_source" "$installed_system_helper" 0755
    system_helper_matches_sources || {
        printf 'installed system workload helper failed source integrity verification\n' >&2
        return 1
    }
}

run_system_helper() {
    local action="$1"
    shift
    local sudo_executable
    validate_python_runtime_trust
    acquire_system_privilege
    system_helper_is_trusted || {
        printf 'installed system workload helper is unavailable or failed integrity validation\n' >&2
        return 1
    }
    sudo_executable="$(trusted_host_executable sudo)"
    "$sudo_executable" -- "$installed_system_helper" "$action" \
        --root "$dotnet_root" --dotnet "$dotnet_executable" "$@"
}

print_shell_setup() {
    local shell_name startup_file
    shell_name="${SHELL:-}"
    shell_name="${shell_name##*/}"
    case "$shell_name" in
        bash) startup_file="$HOME/.bashrc" ;;
        zsh) startup_file="$HOME/.zshrc" ;;
        *)
            printf '\nTo make run-ios-app.sh available from any directory, add this line to your shell startup file:\n'
            printf '  export PATH=%q:"$PATH"\n' "$script_dir"
            printf 'Then reload that file or start a new shell.\n'
            return
            ;;
    esac

    printf '\nTo make run-ios-app.sh available from any directory in %s, add this line to %q:\n' \
        "$shell_name" "$startup_file"
    printf '  export PATH=%q:"$PATH"\n' "$script_dir"
    printf 'Then reload %q:\n' "$startup_file"
    printf '  source %q\n' "$startup_file"
}

acquire_user_root_lock() {
    [[ "$privilege_mode" == user ]] || return 0
    [[ "$user_root_lock_acquired" == false ]] || return 0
    local root_lock="$dotnet_root/.dotnet-ios-linux.lifecycle.lock"
    [[ ! -e "$root_lock" || -f "$root_lock" ]] || {
        printf 'host .NET lifecycle lock is not a regular file: %s\n' "$root_lock" >&2
        return 1
    }
    [[ ! -L "$root_lock" ]] || {
        printf 'host .NET lifecycle lock must not be a symlink: %s\n' "$root_lock" >&2
        return 1
    }
    exec 8>"$root_lock"
    flock -n 8 || {
        printf 'another workload lifecycle operation is active for %s\n' "$dotnet_root" >&2
        return 1
    }
    user_root_lock_acquired=true
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
dotnet_executable=$dotnet_executable
dotnet_sdk=$sdk_version
dotnet_sdk_source=$sdk_source
workload_set=$workload_set_version
workload_set_source=$workload_source
privilege_mode=$privilege_mode
sudo_required=$([[ "$privilege_mode" == system-helper ]] && printf true || printf false)
managed_workloads=$managed_workloads_csv
kept_workloads=$kept_workloads_csv
registration_scope=$([[ "$privilege_mode" == system-helper ]] && printf system-root-ios-only || printf writable-root-direct-apple)
data_root=$data_root
cache_root=$cache_root
xcode_archive=$(basename "$xcode_archive")
offline=$offline
runtime_source=$([[ -n "$aot_pack" ]] && printf reused-local-aot-pack || printf build-pinned-source-with-llvm)
planned=compatible SDK selection; Xcode/workload compatibility check; Xcode SDK import; host-tool staging; pinned direct Apple workload registration; activation
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
for cache_directory in \
    "$cache_root/downloads" "$cache_root/build" "$cache_root/sources" "$cache_root/nuget" \
    "$cache_root/nuget/packages" "$cache_root/nuget/llvm" "$cache_root/dotnet-cli-home" \
    "$cache_root/workload-bundles" "$cache_root/workload-temp" "$cache_root/swiftpm" \
    "$cache_root/uv" "$cache_root/downloads/workload-bundle-packages"; do
    ensure_owned_cache_directory "$cache_directory"
done

if [[ "$command_name" == doctor ]]; then
    [[ -f "$receipt" ]] || { printf 'dotnet-ios-linux is not installed under %s\n' "$data_root" >&2; exit 1; }
    load_owned_context "$receipt"
    acquire_user_root_lock
    doctor
    exit 0
fi

if [[ "$command_name" == uninstall ]]; then
    [[ -f "$marker" ]] || { printf 'dotnet-ios-linux is not installed\n'; exit 0; }
    marker_status="$(read_marker_status)"
    [[ "$marker_status" == complete || "$marker_status" == uninstalling ]] || {
        printf 'installation is incomplete; rerun install to recover it before uninstalling\n' >&2
        exit 1
    }
    [[ -f "$receipt" ]] || { printf 'installation receipt is missing: %s\n' "$receipt" >&2; exit 1; }
    load_owned_context "$receipt"
    acquire_user_root_lock
    confirm "Restore replaced managed Apple workloads and remove durable dotnet-ios-linux data?"
    if [[ "$marker_status" == complete ]]; then
        "$script_dir/eng/install/durable-assets.py" verify --root "$data_root" --receipt "$receipt"
        set_marker_state complete uninstalling prepared
        uninstall_phase=prepared
    else
        uninstall_phase="$(read_marker_phase)"
        [[ "$uninstall_phase" =~ ^(prepared|workload-removed|sdk-removed|assets-removed)$ ]] || {
            printf 'installation has an invalid uninstall phase: %s\n' "$uninstall_phase" >&2
            exit 1
        }
    fi
    activate_environment
    if [[ "$uninstall_phase" == prepared ]]; then
        if [[ "$privilege_mode" == system-helper ]]; then
            install_system_helper
            run_system_helper uninstall \
                --sdk-version "$sdk_version" --workload-version "$workload_set_version" \
                --workload-manifest "$workload_manifest" \
                --ownership-token "$system_transaction_id"
        else
            if [[ -d "$workload_transaction" ]]; then
                transaction_status="$("$script_dir/eng/install/workload-transaction.py" status \
                    --root "$dotnet_root" --transaction "$workload_transaction")"
                if [[ "$transaction_status" == committed ]]; then
                    "$script_dir/eng/install/workload-transaction.py" prepare-uninstall \
                        --root "$dotnet_root" --transaction "$workload_transaction"
                elif [[ "$transaction_status" != uninstalling && "$transaction_status" != restoring && \
                        "$transaction_status" != restored ]]; then
                    printf 'cannot uninstall a workload transaction in state %s\n' "$transaction_status" >&2
                    exit 1
                fi
                if [[ "$transaction_status" != restored ]]; then
                    "$script_dir/eng/install/workload-transaction.py" restore-retain \
                        --root "$dotnet_root" --transaction "$workload_transaction"
                fi
            fi
        fi
        set_marker_state uninstalling uninstalling workload-removed
        uninstall_phase=workload-removed
    fi
    if [[ "$uninstall_phase" == workload-removed ]]; then
        if [[ "$privilege_mode" == user && -d "$workload_transaction" ]]; then
            shared_consumers="$("$script_dir/eng/install/workload-transaction.py" shared-consumers \
                --root "$dotnet_root" --transaction "$workload_transaction")"
        fi
        if [[ -f "$sdk_receipt" ]]; then
            if ((shared_consumers > 0)); then
                "$script_dir/eng/install/install-dotnet-sdk.py" release \
                    --root "$dotnet_root" --receipt "$sdk_receipt" --sdk-version "$sdk_version"
            else
                "$script_dir/eng/install/install-dotnet-sdk.py" uninstall \
                    --root "$dotnet_root" --receipt "$sdk_receipt" --sdk-version "$sdk_version"
            fi
        else
            sync_path_state "$sdk_receipt"
        fi
        set_marker_state uninstalling uninstalling sdk-removed
        uninstall_phase=sdk-removed
    fi
    if [[ "$uninstall_phase" == sdk-removed ]]; then
        "$script_dir/eng/install/durable-assets.py" uninstall --allow-missing \
            --root "$data_root" --receipt "$receipt"
        set_marker_state uninstalling uninstalling assets-removed
        uninstall_phase=assets-removed
    fi
    if [[ "$uninstall_phase" == assets-removed && "$privilege_mode" == user ]]; then
        "$script_dir/eng/install/workload-transaction.py" finalize \
            --root "$dotnet_root" --transaction "$workload_transaction"
    fi
    python3 - "$script_dir/eng/common" "$marker" "$receipt" \
        "$data_root/receipts" "$data_root" <<'PY'
import errno, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from atomic import fsync_directory, fsync_path_state
for raw in sys.argv[2:4]:
    path=Path(raw)
    if os.path.lexists(path):
        path.unlink()
        fsync_directory(path.parent)
    else:
        fsync_path_state(path)
for raw in sys.argv[4:]:
    path=Path(raw)
    try:
        path.rmdir()
        fsync_directory(path.parent)
    except FileNotFoundError:
        fsync_path_state(path)
    except OSError as error:
        if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            raise
PY
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
    if [[ "$keep_existing_supplied" == true && "$requested_kept_workloads_csv" != "$kept_workloads_csv" ]]; then
        printf 'the requested --keep-existing policy differs from the completed installation; uninstall before changing workload ownership\n' >&2
        exit 2
    fi
    acquire_user_root_lock
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
        if [[ "$keep_existing_supplied" == true && "$requested_kept_workloads_csv" != "$kept_workloads_csv" ]]; then
            printf 'the requested --keep-existing policy differs from the completed installation; uninstall before changing workload ownership\n' >&2
            exit 2
        fi
        doctor
        exit 0
    fi
    [[ "$status" == installing || "$status" == verifying ]] || {
        printf 'unknown installation state: %s\n' "$status" >&2
        exit 1
    }
    if ! load_owned_context "$marker"; then
        printf 'interrupted installation lacks a reusable context; resolving it again\n' >&2
        resolve_new_install_context
    fi
    acquire_user_root_lock
    printf 'Recovering an interrupted installation...\n'
    shared_consumers=0
    if [[ "$privilege_mode" == system-helper ]]; then
        install_system_helper
        run_system_helper recover --ownership-token "$system_transaction_id"
    elif [[ -d "$workload_transaction" ]]; then
        # Keep the restored transaction long enough to query retained shared
        # workload-set consumers before deciding whether the SDK receipt may be
        # removed. A plain restore finalizes (deletes) it first.
        "$script_dir/eng/install/workload-transaction.py" restore-retain --root "$dotnet_root" --transaction "$workload_transaction"
        shared_consumers="$("$script_dir/eng/install/workload-transaction.py" shared-consumers \
            --root "$dotnet_root" --transaction "$workload_transaction")"
    fi
    if [[ -f "$sdk_receipt" ]]; then
        if ((shared_consumers > 0)); then
            "$script_dir/eng/install/install-dotnet-sdk.py" release --root "$dotnet_root" \
                --receipt "$sdk_receipt" --sdk-version "$sdk_version"
        else
            "$script_dir/eng/install/install-dotnet-sdk.py" uninstall --root "$dotnet_root" \
                --receipt "$sdk_receipt" --sdk-version "$sdk_version"
        fi
    else
        sync_path_state "$sdk_receipt"
    fi
    rm -rf -- "$data_root"
    # The recovered receipt may refer to an older content-addressed helper and
    # ownership token. Resolve a new context before starting a new transaction.
    resolve_new_install_context
    acquire_user_root_lock
else
    resolve_new_install_context
    acquire_user_root_lock
fi

assert_kept_registrations_exist

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

mkdir -p "$data_root/receipts" "$data_root/feeds" "$data_root/toolchains"
python3 - "$script_dir/eng/common" "$data_root" "$data_root/receipts" \
    "$data_root/feeds" "$data_root/toolchains" "$cache_root" "$cache_root/downloads" \
    "$cache_root/build" "$cache_root/sources" "$cache_root/nuget" \
    "$cache_root/nuget/packages" "$cache_root/nuget/llvm" "$cache_root/dotnet-cli-home" \
    "$cache_root/workload-bundles" "$cache_root/workload-temp" "$cache_root/swiftpm" \
    "$cache_root/uv" "$cache_root/downloads/workload-bundle-packages" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from atomic import fsync_path_state
for raw in sys.argv[2:]:
    fsync_path_state(Path(raw))
PY
python3 - "$marker" "$lock_sha256" "$dotnet_root" "$data_root" "$project_version" \
    "$install_id" "$sdk_version" "$workload_set_version" "$privilege_mode" \
    "$system_transaction_id" "$sdk_source" "$workload_source" "$workload_manifest" \
    "$system_helper_version" "$managed_workloads_csv" "$kept_workloads_csv" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
path=Path(sys.argv[1])
value={
 "schemaVersion":1,"owner":"dotnet-ios-linux","status":"installing",
 "projectVersion":sys.argv[5],"installId":sys.argv[6],
 "sdkVersion":sys.argv[7],"workloadSet":sys.argv[8],
 "privilegeMode":sys.argv[9],
 "sdkSource":sys.argv[11],"workloadSource":sys.argv[12],
  "workloadManifest":sys.argv[13],
 "toolchainLockSha256":sys.argv[2],"dotnetRoot":sys.argv[3],"dataRoot":sys.argv[4],
}
if sys.argv[10]: value["systemTransactionId"]=sys.argv[10]
if sys.argv[14]: value["systemHelperVersion"]=sys.argv[14]
for key,index in (("managedWorkloads",15),("keptWorkloads",16)):
    value[key]=[item for item in sys.argv[index].split(',') if item]
descriptor,name=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
temporary=Path(name)
try:
    with os.fdopen(descriptor,"w",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
except BaseException:
    temporary.unlink(missing_ok=True)
    raise
PY
installation_active=true
rollback_install() {
    local exit_code="${1:-$?}" rollback_complete=true shared_consumers=0
    trap - ERR INT TERM
    [[ "$installation_active" == true ]] || exit "$exit_code"
    printf 'Installation failed; restoring owned mutations...\n' >&2
    if [[ "$privilege_mode" == system-helper ]]; then
        if [[ "$system_helper_transaction_active" == true ]] && system_helper_is_trusted; then
            run_system_helper recover --ownership-token "$system_transaction_id" || rollback_complete=false
        elif [[ "$system_helper_transaction_active" == true ]]; then
            rollback_complete=false
            printf 'system workload helper is unavailable; preserving recovery state\n' >&2
        fi
    elif [[ -d "$workload_transaction" ]]; then
        if ! "$script_dir/eng/install/workload-transaction.py" restore-retain \
            --root "$dotnet_root" --transaction "$workload_transaction"; then
            rollback_complete=false
        else
            shared_consumers="$("$script_dir/eng/install/workload-transaction.py" shared-consumers \
                --root "$dotnet_root" --transaction "$workload_transaction")" || rollback_complete=false
        fi
    fi
    if [[ "$rollback_complete" == true && -f "$sdk_receipt" ]]; then
        if ((shared_consumers > 0)); then
            if ! "$script_dir/eng/install/install-dotnet-sdk.py" release --root "$dotnet_root" \
                --receipt "$sdk_receipt" --sdk-version "$sdk_version"; then
                rollback_complete=false
            fi
        elif ! "$script_dir/eng/install/install-dotnet-sdk.py" uninstall --root "$dotnet_root" \
            --receipt "$sdk_receipt" --sdk-version "$sdk_version"; then
            rollback_complete=false
        fi
    fi
    if [[ "$rollback_complete" == true ]]; then
        [[ ! -f "$marker" ]] || rm -rf -- "$data_root"
    else
        if [[ "$privilege_mode" == system-helper ]]; then
            printf 'privileged recovery is incomplete; durable receipts were preserved under %s\n' "$data_root" >&2
        else
            printf 'rollback is incomplete; durable receipts were preserved under %s\n' "$data_root" >&2
        fi
    fi
    exit "$exit_code"
}
trap rollback_install ERR INT TERM

if [[ "$sdk_source" == installer ]]; then
    download_verified "$sdk_url" sha512sum "$sdk_sha512" "$sdk_archive" "$offline"
fi
if workload_is_managed ios; then
    download_verified "$manifest_url" sha512sum "$manifest_sha512" "$manifest_archive" "$offline"
fi
if workload_is_managed macos; then
    download_verified "$macos_manifest_url" sha512sum "$macos_manifest_sha512" "$macos_manifest_archive" "$offline"
fi
if workload_is_managed maccatalyst; then
    download_verified "$maccatalyst_manifest_url" sha512sum "$maccatalyst_manifest_sha512" "$maccatalyst_manifest_archive" "$offline"
fi
if workload_is_managed tvos; then
    download_verified "$tvos_manifest_url" sha512sum "$tvos_manifest_sha512" "$tvos_manifest_archive" "$offline"
fi
if [[ "$workload_source" == installer || "$privilege_mode" == system-helper ]]; then
    download_verified "$workload_set_url" sha512sum "$workload_set_sha512" "$workload_set_archive" "$offline"
fi
if [[ "$privilege_mode" == system-helper ]]; then
    bundle_plan="$(mktemp "${TMPDIR:-/tmp}/dotnet-ios-linux-bundle-plan.XXXXXX")"
    if ! "$bundle_builder" plan --lock "$lock_path" --format null > "$bundle_plan"; then
        rm -f -- "$bundle_plan"
        false
    fi
    mapfile -d '' -t bundle_values < "$bundle_plan"
    rm -f -- "$bundle_plan"
    ((${#bundle_values[@]} > 0 && ${#bundle_values[@]} % 3 == 0)) || {
        printf 'workload bundle planner returned an invalid response\n' >&2
        false
    }
    readonly bundle_package_cache="$cache_root/downloads/workload-bundle-packages"
    mkdir -p "$bundle_package_cache"
    while ((${#bundle_values[@]})); do
        bundle_filename="${bundle_values[0]}"
        bundle_url="${bundle_values[1]}"
        bundle_sha256="${bundle_values[2]}"
        bundle_values=("${bundle_values[@]:3}")
        bundle_path="$bundle_package_cache/$bundle_filename"
        download_verified "$bundle_url" sha256sum "$bundle_sha256" "$bundle_path" "$offline"
        bundle_package_paths+=("$bundle_path")
    done
fi
manifest_hash_args=()
if workload_is_managed ios; then manifest_hash_args+=("$manifest_archive" "$manifest_sha512_base64"); fi
if workload_is_managed macos; then manifest_hash_args+=("$macos_manifest_archive" "$macos_manifest_sha512_base64"); fi
if workload_is_managed maccatalyst; then manifest_hash_args+=("$maccatalyst_manifest_archive" "$maccatalyst_manifest_sha512_base64"); fi
if workload_is_managed tvos; then manifest_hash_args+=("$tvos_manifest_archive" "$tvos_manifest_sha512_base64"); fi
if ((${#manifest_hash_args[@]})); then
python3 - "${manifest_hash_args[@]}" <<'PY'
import base64,hashlib,sys
for archive,expected in zip(sys.argv[1::2],sys.argv[2::2],strict=True):
    actual=base64.b64encode(hashlib.sha512(open(archive,'rb').read()).digest()).decode()
    if actual != expected: raise SystemExit(f"official workload manifest SHA-512 mismatch: {archive}: {actual}")
PY
fi

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
nuget_verify=()
if workload_is_managed ios; then nuget_verify+=("$manifest_archive"); fi
if workload_is_managed macos; then nuget_verify+=("$macos_manifest_archive"); fi
if workload_is_managed maccatalyst; then nuget_verify+=("$maccatalyst_manifest_archive"); fi
if workload_is_managed tvos; then nuget_verify+=("$tvos_manifest_archive"); fi
if [[ "$workload_source" == installer || "$privilege_mode" == system-helper ]]; then
    nuget_verify+=("$workload_set_archive")
fi
if [[ "$privilege_mode" == system-helper ]]; then
    nuget_verify+=("${bundle_package_paths[@]}")
fi
if ((${#nuget_verify[@]})); then
    NUGET_CERT_REVOCATION_MODE=offline "$dotnet_root/dotnet" nuget verify --all "${nuget_verify[@]}" >/dev/null
fi

if workload_is_managed ios && [[ -z "$aot_pack" ]]; then
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
    if [[ -e "$cache_root/build" || -L "$cache_root/build" ]]; then
        [[ -d "$cache_root/build" && ! -L "$cache_root/build" ]] || {
            printf 'project build-cache directory is unsafe: %s\n' "$cache_root/build" >&2
            false
        }
    fi
    if ! verify_compiler_cache "$compiler_root" "$llvm_root" >/dev/null 2>&1; then
        printf 'AOT compiler cache is absent or unverified; rebuilding it\n'
        discard_build_cache "$compiler_root" "mono-aot-"
        build_and_publish_compiler_cache "$compiler_root" "$runtime_source" "$llvm_root"
    fi
    aot_pack="$cache_root/build/aot-pack-$runtime_commit"
    if ! verify_aot_pack_cache "$aot_pack" >/dev/null 2>&1; then
        printf 'AOT package cache is absent or unverified; rebuilding it\n'
        discard_build_cache "$aot_pack" "aot-pack-"
        "$script_dir/eng/runtime/package-aot-pack.py" \
            --compiler "$compiler_root/install/bin/mono-aot-cross" \
            --runtime-commit "$runtime_commit" --llvm-root "$llvm_root" --output "$aot_pack"
    fi
fi

if ((${#managed_workloads[@]})); then
    feed_workload_args=()
    for workload in "${managed_workloads[@]}"; do feed_workload_args+=(--workload "$workload"); done
    feed_source_args=()
    if workload_is_managed ios; then
        feed_source_args+=(--official-manifest "$manifest_archive" --aot-pack "$aot_pack")
    fi
    if workload_is_managed macos; then feed_source_args+=(--official-companion-manifest "macos=$macos_manifest_archive"); fi
    if workload_is_managed maccatalyst; then feed_source_args+=(--official-companion-manifest "maccatalyst=$maccatalyst_manifest_archive"); fi
    if workload_is_managed tvos; then feed_source_args+=(--official-companion-manifest "tvos=$tvos_manifest_archive"); fi
    "$script_dir/eng/workload/build-local-feed.py" \
        "${feed_source_args[@]}" --output "$feed" "${feed_workload_args[@]}"
fi
if [[ "$privilege_mode" == system-helper ]]; then
    workload_bundle="$cache_root/workload-bundles/$install_id.tar"
    "$bundle_builder" build --lock "$lock_path" --feed "$feed" \
        --workload-set "$workload_set_archive" --package-cache "$bundle_package_cache" \
        --output "$workload_bundle"
fi

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
"$script_dir/eng/toolchain/setup-binutils.sh" --destination "$toolchain/binutils-$binutils_version" \
    --cache "$cache_root/downloads" --swift-root "$toolchain/swift-$swift_version" \
    "${offline_args[@]}"
if [[ "$offline" == true && ! -d "$cache_root/sources/xtool-$xtool_revision_short/.git" ]]; then
    printf 'xtool source is unavailable in offline mode\n' >&2
    false
fi
"$script_dir/eng/toolchain/setup-xtool.sh" --source-cache "$cache_root/sources/xtool-$xtool_revision_short" \
    --swift-root "$toolchain/swift-$swift_version" \
    --binutils-root "$toolchain/binutils-$binutils_version" \
    --destination "$toolchain/xtool-$xtool_revision_short" \
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

if [[ "$privilege_mode" == system-helper && ${#managed_workloads[@]} -gt 0 ]]; then
    install_system_helper
    system_helper_transaction_active=true
    run_system_helper install \
        --sdk-version "$sdk_version" --workload-version "$workload_set_version" \
        --workload-manifest "$workload_manifest" \
        --ownership-token "$system_transaction_id" --bundle "$workload_bundle"
elif ((${#managed_workloads[@]})); then
    # Revalidate immediately before the durable transaction is opened. The
    # early preflight gives operators a fast failure; this closes the lengthy
    # acquisition/build interval before CLI mutation.
    assert_kept_registrations_exist
    transaction_workload_args=()
    for workload in "${managed_workloads[@]}"; do
        transaction_workload_args+=(
            --workload "$workload:$(workload_manifest_id "$workload"):$(workload_manifest_identity "$workload")"
        )
    done
    for workload in "${kept_workloads[@]}"; do
        transaction_workload_args+=(
            --protect-workload "$workload:$(workload_manifest_id "$workload"):$(workload_manifest_identity "$workload")"
        )
    done
    "$script_dir/eng/install/workload-transaction.py" begin --root "$dotnet_root" \
        --transaction "$workload_transaction" --package-inventory "$lock_path" \
        --sdk-feature-band "$sdk_feature_band" --workload-version "$workload_set_version" \
        --control-global-json "$control_root/global.json" "${transaction_workload_args[@]}"
    (cd "$control_root" && "$dotnet_root/dotnet" workload install "${managed_workloads[@]}" \
        --configfile "$feed/NuGet.Config" --disable-parallel --temp-dir "$cache_root/workload-temp" --verbosity minimal)
    installed_workload_list="$(cd "$control_root" && "$dotnet_root/dotnet" workload list)"
    for workload in "${managed_workloads[@]}"; do
        expected_manifest="$(workload_manifest_identity "$workload")"
        awk -v workload="$workload" -v expected="$expected_manifest" \
            '$1 == workload && $2 == expected { found=1 } END { exit !found }' <<<"$installed_workload_list"
    done
fi
if [[ ",${managed_workloads_csv}," == *,ios,* ]]; then
    [[ -x "$dotnet_root/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/$project_version/tools/mono-aot-cross" ]]
fi
if ((${#managed_workloads[@]})); then
    verify_workload_args=()
    for workload in "${managed_workloads[@]}"; do verify_workload_args+=(--workload "$workload"); done
    python3 "$script_dir/eng/install/verify-workload-packages.py" --root "$dotnet_root" \
        --feed "$feed" --lock "$lock_path" --dotnet "$dotnet_root/dotnet" \
        "${verify_workload_args[@]}"
fi
read_resolved_pairs install_context recommended-xcode --root "$dotnet_root" --lock "$lock_path"
recommended_xcode_version="${install_context[recommended_xcode_version]}"
xcode_is_compatible "$xcode_version" "$recommended_xcode_version" || {
    printf 'Xcode %s is incompatible with installed iOS workload %s; expected Xcode %s\n' \
        "$xcode_version" "$manifest_version" "$recommended_xcode_version" >&2
    false
}
if [[ "$privilege_mode" == user && ${#managed_workloads[@]} -gt 0 ]]; then
    "$script_dir/eng/install/workload-transaction.py" commit \
        --root "$dotnet_root" --transaction "$workload_transaction"
fi

python3 - "$receipt" "$lock_sha256" "$dotnet_root" "$data_root" "$cache_root" \
    "$project_version" "$install_id" "$sdk_version" "$sdk_source" "$workload_set_version" \
    "$manifest_version" "$xcode_version" "$xcode_build" "$xcode_sdk_version" \
    "$xcode_sdk_build" "$xcode_archive_sha256" "$locked_xcode_sha256" \
    "$privilege_mode" "$system_transaction_id" "$workload_source" \
    "$workload_manifest" "$system_helper_version" "$managed_workloads_csv" "$kept_workloads_csv" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
receipt=Path(sys.argv[1])
value={
 "schemaVersion":1,"owner":"dotnet-ios-linux","status":"complete",
 "projectVersion":sys.argv[6],"installId":sys.argv[7],
 "toolchainLockSha256":sys.argv[2],"dotnetRoot":sys.argv[3],"dataRoot":sys.argv[4],
 "cacheRoot":sys.argv[5],"sdkVersion":sys.argv[8],"sdkSource":sys.argv[9],
  "workloadSet":sys.argv[10],"workloadIds":[item for item in sys.argv[23].split(',') if item],"manifestVersion":sys.argv[11],
 "privilegeMode":sys.argv[18],
 "workloadSource":sys.argv[20],
 "workloadManifest":sys.argv[21],
 "xcode":{"version":sys.argv[12],"build":sys.argv[13],"sdkVersion":sys.argv[14],
          "sdkBuild":sys.argv[15],"archiveSha256":sys.argv[16],
          "matchesLockedArchive":sys.argv[16] == sys.argv[17]},
 "credentialsStored":False,"pairingRecordsStored":False,"deviceIdentifiersStored":False,
}
if sys.argv[19]: value["systemTransactionId"]=sys.argv[19]
if sys.argv[22]: value["systemHelperVersion"]=sys.argv[22]
value["managedWorkloads"]=[item for item in sys.argv[23].split(',') if item]
value["keptWorkloads"]=[item for item in sys.argv[24].split(',') if item]
value["supportClassification"]={
  item:("linux-ios-runtime" if item == "ios" else "installed-unqualified")
  for item in value["managedWorkloads"]
}
value["externalWorkloads"]={item:"external-unqualified" for item in value["keptWorkloads"]}
receipt.parent.mkdir(parents=True,exist_ok=True)
descriptor,name=tempfile.mkstemp(prefix=f".{receipt.name}.",dir=receipt.parent)
temporary=Path(name)
try:
    with os.fdopen(descriptor,"w",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,receipt)
    directory=os.open(receipt.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
except BaseException:
    temporary.unlink(missing_ok=True)
    raise
PY
"$script_dir/eng/install/durable-assets.py" record --root "$data_root" --receipt "$receipt"
set_marker_state installing verifying ""
if ! doctor verifying; then
    rollback_install 1
fi
set_marker_state verifying complete ""
installation_active=false
trap - ERR INT TERM
printf 'install=passed\n'
printf 'Run: source %q\n' "$data_root/activate"
print_shell_setup
