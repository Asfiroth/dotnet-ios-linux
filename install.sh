#!/usr/bin/env bash
# Rootless bootstrap for the locked Linux-to-iOS .NET toolchain.
set -Eeuo pipefail
umask 077

readonly script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly project_version="0.1.0-preview.1"
readonly install_id="$project_version-sdk-10.0.402"
readonly sdk_version="10.0.402"
readonly sdk_sha512="1904a98dda2db34ba78ba3c4fb1b224455329af91c341d83547843c1839e112b04ae699ee92fa2449a8f62249196a938723078c0c60b9f65c88539d5687729c2"
readonly sdk_url="https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.402/dotnet-sdk-10.0.402-linux-x64.tar.gz"
readonly manifest_url="https://api.nuget.org/v3-flatcontainer/microsoft.net.sdk.ios.manifest-10.0.100/27.0.10722/microsoft.net.sdk.ios.manifest-10.0.100.27.0.10722.nupkg"
readonly manifest_sha512_base64="E67WwID69ZQmMd0fs8OFeExzyso/7rJk7QfIkK+fs+cEQj6e2s/hUA/iPtP36Li08YZVp8L7iY6Uk+zmfdU5Ww=="
readonly manifest_sha512="13aed6c080faf5942631dd1fb3c385784c73caca3feeb264ed07c890af9fb3e704423e9edacfe1500fe23ed3f7e8b8b4f18655a7c2fb898e9493ece67dd5395b"
readonly workload_set_url="https://api.nuget.org/v3-flatcontainer/microsoft.net.workloads.10.0.400/10.401.1/microsoft.net.workloads.10.0.400.10.401.1.nupkg"
readonly workload_set_sha512="ad2c4e4370d3cc8250f9d2cbc01d40491e4ddadb29a797814dc193a259b7d92ae827c27b2bbea4f11ec5c9f49a19feda78461623c4baadb95de87d285f67250c"
readonly xcode_sha256="6a270c53a5a0c5e0ac78125342d44c3cfff2716ec390373cd5e30834d80a67c3"
readonly runtime_commit="4271d88e0aebf3d04f188f1334c2220d80555ef6"
. "$script_dir/eng/install/download-verified.sh"

usage() {
    cat <<'EOF'
Usage:
  ./install.sh install --xcode-archive /path/to/Xcode_27.xip [options]
  ./install.sh doctor
  ./install.sh uninstall [--yes] [--purge-cache]

Install options:
  --xcode-archive PATH  Required user-supplied authentic Xcode 27 archive.
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
readonly feed="$data_root/feeds/$install_id"
readonly toolchain="$data_root/toolchains/$install_id"
readonly sdk_archive="$cache_root/downloads/dotnet-sdk-10.0.402-linux-x64.tar.gz"
readonly manifest_archive="$cache_root/downloads/microsoft.net.sdk.ios.manifest-10.0.100.27.0.10722.nupkg"
readonly workload_set_archive="$cache_root/downloads/microsoft.net.workloads.10.0.400.10.401.1.nupkg"

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

lock_sha256="$(sha256sum -b "$script_dir/toolchain.lock.json" | cut -d' ' -f1)"
python3 - "$script_dir/toolchain.lock.json" <<'PY'
import json, sys
from pathlib import Path
value=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
checks={
 "components.dotnetSdk.version":"10.0.402",
 "components.dotnetSdk.sha512":"1904a98dda2db34ba78ba3c4fb1b224455329af91c341d83547843c1839e112b04ae699ee92fa2449a8f62249196a938723078c0c60b9f65c88539d5687729c2",
 "components.workloadSet.cliVersion":"10.0.401.1",
 "components.iosManifest.version":"27.0.10722",
 "components.monoRuntime.sourceCommit":"4271d88e0aebf3d04f188f1334c2220d80555ef6",
 "components.appleSdk.archiveSha256":"6a270c53a5a0c5e0ac78125342d44c3cfff2716ec390373cd5e30834d80a67c3",
}
for path, expected in checks.items():
    current=value
    for part in path.split('.'):
        current=current[part]
    if current != expected:
        raise SystemExit(f"toolchain lock mismatch at {path}: {current!r} != {expected!r}")
PY

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
    export DOTNET_IOS_LINUX_CLANG="$toolchain/swift-6.4.0/usr/bin/clang"
    export DOTNET_IOS_LINUX_CLANGXX="$toolchain/swift-6.4.0/usr/bin/clang++"
    export PATH="$dotnet_root:$dotnet_root/tools:$data_root/bin:$toolchain/swift-6.4.0/usr/bin:$DEVELOPER_DIR/usr/bin:$PATH"
}

doctor() {
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
    "$script_dir/eng/install/install-dotnet-sdk.py" verify --root "$dotnet_root" --receipt "$sdk_receipt" >/dev/null
    "$script_dir/eng/install/workload-transaction.py" verify --root "$dotnet_root" --transaction "$workload_transaction" >/dev/null
    "$script_dir/eng/install/verify-workload-packages.py" --root "$dotnet_root" \
        --feed "$feed" --lock "$script_dir/toolchain.lock.json" \
        --dotnet "$dotnet_root/dotnet" >/dev/null
    [[ "$($dotnet_root/dotnet --version)" == "$sdk_version" ]] || { printf 'selected .NET SDK is not %s\n' "$sdk_version" >&2; return 1; }
    (cd "$control_root" && "$dotnet_root/dotnet" workload list) | grep -Eq '^ios[[:space:]]+27\.0\.10722/10\.0\.100' || {
        printf 'the pinned ios workload is not installed\n' >&2; return 1;
    }
    "$dotnet_root/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/$project_version/tools/mono-aot-cross" --version |
        grep -Eq 'LLVM:[[:space:]]+yes\(1900\)' || { printf 'LLVM-enabled iOS AOT compiler validation failed\n' >&2; return 1; }
    [[ "$("$toolchain/swift-6.4.0/usr/bin/swift" --version | head -n1)" == "Swift version 6.4 (swift-6.4-RELEASE)" ]]
    env -u LD_LIBRARY_PATH "$toolchain/xtool-f0a1f90/xtool" --help >/dev/null
    [[ "$("$toolchain/python-3.14.8/bin/python3" -c 'import platform; print(platform.python_version())')" == 3.14.8 ]]
    [[ "$("$toolchain/pymobiledevice3-11.26.0/bin/pymobiledevice3" version)" == 11.26.0 ]]
    [[ "$("$toolchain/rcodesign-0.29.0/rcodesign" --version)" == "apple-codesign 0.29.0" ]]
    [[ "$("$DEVELOPER_DIR/usr/bin/xcrun" --sdk iphoneos --show-sdk-version)" == 27.0 ]]
    [[ "$($DEVELOPER_DIR/usr/bin/xcodebuild -version | head -n1)" == "Xcode 27.0" ]]
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
    cat <<EOF
command=install
dotnet_root=$dotnet_root
data_root=$data_root
cache_root=$cache_root
xcode_archive=$(basename "$xcode_archive")
offline=$offline
runtime_source=$([[ -n "$aot_pack" ]] && printf reused-local-aot-pack || printf build-pinned-source-with-llvm)
planned=SDK merge; Xcode SDK import; Swift/xtool/Python/pymobiledevice3/rcodesign staging; pinned workload install; activation
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
    doctor
    exit 0
fi

if [[ "$command_name" == uninstall ]]; then
    [[ -f "$marker" ]] || { printf 'dotnet-ios-linux is not installed\n'; exit 0; }
    [[ "$(read_marker_status)" == complete ]] || {
        printf 'installation is incomplete; rerun install to recover it before uninstalling\n' >&2
        exit 1
    }
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
    "$script_dir/eng/install/install-dotnet-sdk.py" uninstall \
        --root "$dotnet_root" --receipt "$sdk_receipt"
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
        doctor
        exit 0
    fi
    [[ "$status" == installing ]] || { printf 'unknown installation state: %s\n' "$status" >&2; exit 1; }
    printf 'Recovering an interrupted installation...\n'
    if [[ -d "$workload_transaction" ]]; then
        "$script_dir/eng/install/workload-transaction.py" restore --root "$dotnet_root" --transaction "$workload_transaction"
    fi
    if [[ -f "$sdk_receipt" ]]; then
        "$script_dir/eng/install/install-dotnet-sdk.py" uninstall --root "$dotnet_root" --receipt "$sdk_receipt"
    fi
    rm -rf -- "$data_root"
fi

for tool in clang clang++ cmake curl file git ldd ln make ninja objcopy readelf strip tar; do need "$tool"; done
[[ "$(uname -s) $(uname -m)" == "Linux x86_64" ]] || { printf 'only Linux x86_64 is supported\n' >&2; exit 1; }
getconf GNU_LIBC_VERSION >/dev/null 2>&1 || { printf 'a glibc host is required; musl is not supported\n' >&2; exit 1; }
confirm "Install the locked toolchain into $dotnet_root and $data_root?"
printf '%s  %s\n' "$xcode_sha256" "$xcode_archive" | sha256sum --check --status || {
    printf 'Xcode archive SHA-256 mismatch: %s\n' "$xcode_archive" >&2
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
python3 - "$marker" "$lock_sha256" "$dotnet_root" "$data_root" <<'PY'
import json, os, sys
from pathlib import Path
path=Path(sys.argv[1])
value={"schemaVersion":1,"owner":"dotnet-ios-linux","status":"installing","projectVersion":"0.1.0-preview.1","toolchainLockSha256":sys.argv[2],"dotnetRoot":sys.argv[3],"dataRoot":sys.argv[4]}
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
        "$script_dir/eng/install/install-dotnet-sdk.py" uninstall --root "$dotnet_root" --receipt "$sdk_receipt" || true
    fi
    [[ ! -f "$marker" ]] || rm -rf -- "$data_root"
    exit "$exit_code"
}
trap rollback_install ERR INT TERM

download_verified "$sdk_url" sha512sum "$sdk_sha512" "$sdk_archive" "$offline"
download_verified "$manifest_url" sha512sum "$manifest_sha512" "$manifest_archive" "$offline"
download_verified "$workload_set_url" sha512sum "$workload_set_sha512" "$workload_set_archive" "$offline"
python3 - "$manifest_archive" "$manifest_sha512_base64" <<'PY'
import base64,hashlib,sys
actual=base64.b64encode(hashlib.sha512(open(sys.argv[1],'rb').read()).digest()).decode()
if actual != sys.argv[2]: raise SystemExit(f"official iOS manifest SHA-512 mismatch: {actual}")
PY

"$script_dir/eng/install/install-dotnet-sdk.py" install --archive "$sdk_archive" \
    --root "$dotnet_root" --receipt "$sdk_receipt"
mkdir -p "$control_root"
cat > "$control_root/global.json" <<'EOF'
{
  "sdk": {
    "version": "10.0.402",
    "rollForward": "disable",
    "workloadVersion": "10.0.401.1"
  }
}
EOF
activate_environment
[[ "$(cd "$control_root" && "$dotnet_root/dotnet" --version)" == "$sdk_version" ]]
NUGET_CERT_REVOCATION_MODE=offline "$dotnet_root/dotnet" nuget verify --all \
    "$manifest_archive" "$workload_set_archive" >/dev/null

if [[ -z "$aot_pack" ]]; then
    llvm_root="$cache_root/build/llvm-19.1.0-alpha.1.25461.1"
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

sdk_output="$data_root/apple-sdk/iPhoneOS27.0.sdk"
if [[ ! -f "$sdk_output/.dotnet-ios-linux-sdk.json" ]]; then
    "$script_dir/eng/toolchain/import-xcode-sdk.py" --archive "$xcode_archive" \
        --archive-sha256 "$xcode_sha256" --output "$sdk_output" \
        --xcode-version 27.0 --xcode-build 27A266a \
        --platform-version 27.0 --platform-build 24A430 \
        --sdk-version 27.0 --sdk-build 24A430
fi
"$script_dir/eng/toolchain/build-synthetic-xcode.py" --sdk "$sdk_output" --sdk-version 27.0 \
    --xcode-version 27.0 --xcode-build 27A266a --platform-build 24A430 \
    --output "$data_root/Xcode.app" --mode symlink

offline_args=()
[[ "$offline" == false ]] || offline_args+=(--offline)
"$script_dir/eng/toolchain/setup-swift.sh" --destination "$toolchain/swift-6.4.0" \
    --cache "$cache_root/downloads" "${offline_args[@]}"
if [[ "$offline" == true && ! -d "$cache_root/sources/xtool-f0a1f90/.git" ]]; then
    printf 'xtool source is unavailable in offline mode\n' >&2
    false
fi
"$script_dir/eng/toolchain/setup-xtool.sh" --source-cache "$cache_root/sources/xtool-f0a1f90" \
    --swift-root "$toolchain/swift-6.4.0" --destination "$toolchain/xtool-f0a1f90" \
    --package-cache "$cache_root/swiftpm" "${offline_args[@]}"
"$script_dir/eng/toolchain/setup-python.sh" --destination "$toolchain/python-3.14.8" \
    --cache "$cache_root/downloads" "${offline_args[@]}"
"$script_dir/eng/toolchain/setup-pymobiledevice3.sh" --destination "$toolchain/pymobiledevice3-11.26.0" \
    --python "$toolchain/python-3.14.8/bin/python3" --uv "$toolchain/python-3.14.8/bin/uv" \
    --cache "$cache_root/uv" "${offline_args[@]}"
"$script_dir/eng/toolchain/setup-rcodesign.sh" --destination "$toolchain/rcodesign-0.29.0" \
    --cache "$cache_root/downloads" "${offline_args[@]}"

mkdir -p "$data_root/bin"
ln -s ../toolchains/"$install_id"/xtool-f0a1f90/xtool "$data_root/bin/xtool"
ln -s ../toolchains/"$install_id"/pymobiledevice3-11.26.0/bin/pymobiledevice3 "$data_root/bin/pymobiledevice3"
ln -s ../toolchains/"$install_id"/rcodesign-0.29.0/rcodesign "$data_root/bin/rcodesign"
"$script_dir/eng/install/write-activation.py" --output "$data_root/activate" \
    --data-root "$data_root" --dotnet-root "$dotnet_root" \
    --install-id "$install_id" --project-version "$project_version"

if [[ -e "$dotnet_root/metadata/workloads/10.0.400/InstalledWorkloads/ios" ]]; then
    printf 'an ios workload already exists without this installer ownership; refusing to replace it\n' >&2
    false
fi
"$script_dir/eng/install/workload-transaction.py" begin --root "$dotnet_root" --transaction "$workload_transaction"
mkdir -p "$cache_root/workload-temp"
(cd "$control_root" && "$dotnet_root/dotnet" workload update \
    --configfile "$feed/NuGet.Config" --disable-parallel --temp-dir "$cache_root/workload-temp" --verbosity minimal)
(cd "$control_root" && "$dotnet_root/dotnet" workload install ios \
    --configfile "$feed/NuGet.Config" --disable-parallel --temp-dir "$cache_root/workload-temp" --verbosity minimal)
(cd "$control_root" && "$dotnet_root/dotnet" workload list) | grep -Eq '^ios[[:space:]]+27\.0\.10722/10\.0\.100'
[[ -x "$dotnet_root/packs/DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64/$project_version/tools/mono-aot-cross" ]]
"$script_dir/eng/install/verify-workload-packages.py" --root "$dotnet_root" \
    --feed "$feed" --lock "$script_dir/toolchain.lock.json" \
    --dotnet "$dotnet_root/dotnet"
"$script_dir/eng/install/workload-transaction.py" commit --root "$dotnet_root" --transaction "$workload_transaction"

python3 - "$receipt" "$lock_sha256" "$dotnet_root" "$data_root" "$cache_root" <<'PY'
import json, os, sys
receipt, lock_hash, dotnet, data, cache = map(Path, sys.argv[1:])
value={
 "schemaVersion":1,"owner":"dotnet-ios-linux","status":"complete",
 "projectVersion":"0.1.0-preview.1","installId":"0.1.0-preview.1-sdk-10.0.402",
 "toolchainLockSha256":str(lock_hash),"dotnetRoot":str(dotnet),"dataRoot":str(data),
 "cacheRoot":str(cache),"sdkVersion":"10.0.402","workloadSet":"10.0.401.1",
 "workloadId":"ios","manifestVersion":"27.0.10722",
 "xcode":{"version":"27.0","build":"27A266a","sdkVersion":"27.0","sdkBuild":"24A430"},
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
