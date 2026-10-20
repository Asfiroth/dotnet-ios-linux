#!/usr/bin/env bash
# Reference capture only. It does not copy an Apple SDK, pair a device, or create signing identities or profiles.
set -u -o pipefail
umask 077

usage() {
    cat <<'EOF'
Usage: capture-mac-reference.sh --output <directory> --dotnet <executable> [options]

Options:
  --project <project.csproj>          Build the iOS project after inventory.
  --clean                             Run clean before the build.
  --codesign-key <identity>           Standard CodesignKey selector for this invocation.
  --codesign-provision <name-or-uuid> Standard CodesignProvision selector for this invocation.

The supplied dotnet executable is invoked explicitly and its SDK root is
derived after resolving symlinks. The project is built for Release/ios-arm64
with --no-restore and a private binlog stored beneath --output.

When signing selectors are omitted, existing project properties remain in
effect and macios may use its normal automatic selection. The script redacts
selectors from its metadata and displayed command, but raw MSBuild output and
the binlog remain private because tools can disclose signing metadata. Pass
only a profile name or UUID: never pass profile files, paths, contents,
credentials, or private-key material.
EOF
}

resolve_symlink_path() {
    local candidate="$1"
    local target
    local base

    if [[ "$candidate" != */* ]]; then
        candidate="$(command -v "$candidate" 2>/dev/null || true)"
    fi
    [[ -n "$candidate" ]] || return 1

    while [[ -L "$candidate" ]]; do
        target="$(readlink "$candidate")"
        if [[ "$target" = /* ]]; then
            candidate="$target"
        else
            base="$(cd -P "$(dirname "$candidate")" && pwd)" || return 1
            candidate="$base/$target"
        fi
    done
    base="$(cd -P "$(dirname "$candidate")" && pwd)" || return 1
    printf '%s/%s\n' "$base" "$(basename "$candidate")"
}

sha256_value() {
    shasum -a 256 "$1" | awk '{print $1}'
}

validate_selector() {
    local option="$1"
    local value="$2"

    [[ -n "$value" ]] || { printf '%s must not be empty\n' "$option" >&2; exit 2; }
    case "$value" in
        *$'\n'*|*$'\r'*|*';'*)
            printf '%s must not contain control characters or semicolons\n' "$option" >&2
            exit 2
            ;;
    esac
}

output=""
dotnet_executable=""
project=""
codesign_key=""
codesign_provision=""
clean=0
while (($#)); do
    case "$1" in
        --output) output="${2:?missing value for --output}"; shift 2 ;;
        --dotnet) dotnet_executable="${2:?missing value for --dotnet}"; shift 2 ;;
        --project) project="${2:?missing value for --project}"; shift 2 ;;
        --codesign-key) codesign_key="${2:?missing value for --codesign-key}"; shift 2 ;;
        --codesign-provision) codesign_provision="${2:?missing value for --codesign-provision}"; shift 2 ;;
        --clean) clean=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

[[ -n "$output" && -n "$dotnet_executable" ]] || { usage >&2; exit 2; }
[[ ! -e "$output" || ! -L "$output" ]] || { printf 'output directory must not be a symlink: %s\n' "$output" >&2; exit 2; }
mkdir -p "$output" || exit 2
output="$(cd -P "$output" && pwd)" || exit 2
chmod 700 "$output" || exit 2

dotnet_executable="$(resolve_symlink_path "$dotnet_executable")" || { printf 'cannot resolve dotnet executable\n' >&2; exit 2; }
[[ -x "$dotnet_executable" ]] || { printf 'dotnet executable is not executable: %s\n' "$dotnet_executable" >&2; exit 2; }
dotnet_root="$(dirname "$dotnet_executable")"
[[ -d "$dotnet_root/sdk" ]] || { printf 'derived SDK root is invalid: %s\n' "$dotnet_root" >&2; exit 2; }

[[ -z "$codesign_key" ]] || validate_selector --codesign-key "$codesign_key"
[[ -z "$codesign_provision" ]] || validate_selector --codesign-provision "$codesign_provision"

project_dir="$PWD"
project_name=""
if [[ -n "$project" ]]; then
    [[ -f "$project" ]] || { printf 'project does not exist: %s\n' "$project" >&2; exit 2; }
    project_dir="$(cd -P "$(dirname "$project")" && pwd)" || exit 2
    project="$project_dir/$(basename "$project")"
    project_name="$(basename "$project" .csproj)"
fi

script_path="$(resolve_symlink_path "${BASH_SOURCE[0]}")" || { printf 'cannot resolve capture script\n' >&2; exit 2; }
report="$output/mac-reference.txt"
: > "$report"

capture_status=0
last_status=0

remember_status() {
    local status="$1"
    if (( status != 0 && capture_status == 0 )); then
        capture_status="$status"
    fi
}

record() {
    printf '\n$ %s\n' "$*" >> "$report"
    "$@" >> "$report" 2>&1
    last_status=$?
    printf '[exit %s]\n' "$last_status" >> "$report"
    remember_status "$last_status"
    return 0
}

record_in_project_dir() {
    local display="$1"
    shift
    printf '\n$ (cd <project> && %s)\n' "$display" >> "$report"
    (
        cd "$project_dir" || exit 1
        "$@"
    ) >> "$report" 2>&1
    last_status=$?
    printf '[exit %s]\n' "$last_status" >> "$report"
    remember_status "$last_status"
    return 0
}

record_note() {
    printf '\n[%s]\n' "$1" >> "$report"
}

hash_artifact() {
    local artifact="$1"
    local name
    local status

    name="$(basename "$artifact")"
    if [[ ! -f "$artifact" || -L "$artifact" ]]; then
        record_note "missing or unsafe expected artifact: $name"
        remember_status 1
        return 0
    fi
    (
        cd "$output" || exit 1
        shasum -a 256 "$name" > "$name.sha256"
    )
    status=$?
    if (( status != 0 )); then
        record_note "failed to hash artifact: $name"
        remember_status "$status"
    fi
    return 0
}

repository_root=""
repository_revision="<unavailable>"
repository_tree="<unavailable>"
repository_worktree="<unavailable>"
toolchain_lock_sha256="<unavailable>"
if git -C "$project_dir" rev-parse --show-toplevel >/dev/null 2>&1; then
    repository_root="$(git -C "$project_dir" rev-parse --show-toplevel)"
    repository_revision="$(git -C "$repository_root" rev-parse HEAD)"
    repository_tree="$(git -C "$repository_root" rev-parse HEAD^{tree})"
    if [[ -z "$(git -C "$repository_root" status --porcelain --untracked-files=no)" ]]; then
        repository_worktree="clean-tracked"
    else
        repository_worktree="dirty-tracked"
    fi
    if [[ -f "$repository_root/toolchain.lock.json" ]]; then
        toolchain_lock_sha256="$(sha256_value "$repository_root/toolchain.lock.json")"
    fi
fi

{
    printf 'captured_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'dotnet_executable=%s\n' "$dotnet_executable"
    printf 'dotnet_root=%s\n' "$dotnet_root"
    printf 'project_supplied=%s\n' "$([[ -n "$project" ]] && printf yes || printf no)"
    printf 'repository_revision=%s\n' "$repository_revision"
    printf 'repository_tree=%s\n' "$repository_tree"
    printf 'repository_worktree=%s\n' "$repository_worktree"
    printf 'capture_script_sha256=%s\n' "$(sha256_value "$script_path")"
    printf 'project_sha256=%s\n' "$([[ -n "$project" ]] && sha256_value "$project" || printf '<not-supplied>')"
    printf 'toolchain_lock_sha256=%s\n' "$toolchain_lock_sha256"
    printf 'codesign_key_override=%s\n' "$([[ -n "$codesign_key" ]] && printf '<redacted>' || printf '<none>')"
    printf 'codesign_provision_override=%s\n' "$([[ -n "$codesign_provision" ]] && printf '<redacted>' || printf '<none>')"
    for variable in DOTNET_ROOT DOTNET_ROOT_X64 DEVELOPER_DIR MD_APPLE_SDK_ROOT MSBuildSDKsPath; do
        value="${!variable-}"
        printf '%s=%s\n' "$variable" "${value:-<unset>}"
    done
} >> "$report"

record uname -a
record sw_vers
record xcode-select -p
record xcodebuild -version
record xcrun --sdk iphoneos --show-sdk-path
record xcrun --sdk iphoneos --show-sdk-version
record_in_project_dir 'dotnet --info' "$dotnet_executable" --info
record_in_project_dir 'dotnet --list-sdks' "$dotnet_executable" --list-sdks
record_in_project_dir 'dotnet --list-runtimes' "$dotnet_executable" --list-runtimes
record_in_project_dir 'dotnet workload --info' "$dotnet_executable" workload --info
record_in_project_dir 'dotnet workload list' "$dotnet_executable" workload list

manifest_list="$output/sdk-manifest-files.txt"
if [[ -d "$dotnet_root/sdk-manifests" ]]; then
    find "$dotnet_root/sdk-manifests" -type f | LC_ALL=C sort > "$manifest_list"
    last_status=$?
    remember_status "$last_status"
    hash_artifact "$manifest_list"
fi

pack_list="$output/pack-directories.txt"
if [[ -d "$dotnet_root/packs" ]]; then
    find "$dotnet_root/packs" -mindepth 2 -maxdepth 2 -type d -print | LC_ALL=C sort > "$pack_list"
    last_status=$?
    remember_status "$last_status"
    hash_artifact "$pack_list"
fi

if [[ -n "$project" ]]; then
    binlog_name="${project_name}-reference.binlog"
    binlog="$output/$binlog_name"
    build_display='dotnet build <project> -f net10.0-ios -r ios-arm64 -c Release --no-restore /bl:<capture-output>/'"$binlog_name"
    build_command=("$dotnet_executable" build "$project" -f net10.0-ios -r ios-arm64 -c Release --no-restore "/bl:$binlog")
    if [[ -n "$codesign_key" ]]; then
        build_command+=("-p:CodesignKey=$codesign_key")
        build_display="$build_display -p:CodesignKey=<redacted>"
    fi
    if [[ -n "$codesign_provision" ]]; then
        build_command+=("-p:CodesignProvision=$codesign_provision")
        build_display="$build_display -p:CodesignProvision=<redacted>"
    fi

    if ((clean)); then
        record_in_project_dir 'dotnet clean <project> -f net10.0-ios -r ios-arm64 -c Release' "$dotnet_executable" clean "$project" -f net10.0-ios -r ios-arm64 -c Release
        if (( last_status != 0 )); then
            record_note 'build skipped because clean failed; no binlog is accepted for this capture'
        fi
    fi
    if (( !clean || last_status == 0 )); then
        record_in_project_dir "$build_display" "${build_command[@]}"
        hash_artifact "$binlog"
    fi
fi

hash_artifact "$report"
printf 'report=<capture-output>/mac-reference.txt\n'
exit "$capture_status"
