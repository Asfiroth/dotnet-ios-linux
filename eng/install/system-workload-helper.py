#!/usr/bin/python3 -I
"""Root-owned lifecycle helper for a workload in a non-writable host .NET root."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import NoReturn


OWNER = "dotnet-ios-linux"
PROTOCOL_VERSION = 1
MAX_BUNDLE_BYTES = 16 * 1024 * 1024 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 512
RUNNING_HELPER = Path(__file__).absolute()
INSTALLED_ROOT = RUNNING_HELPER.parents[2]
HELPER_VERSION = INSTALLED_ROOT.name
INSTALLED_HELPER = INSTALLED_ROOT / "eng/install/system-workload-helper.py"
INSTALLED_TRANSACTION = INSTALLED_ROOT / "eng/install/workload-transaction.py"
INSTALLED_ATOMIC = INSTALLED_ROOT / "eng/common/atomic.py"
TESTING = os.geteuid() != 0 and os.environ.get("DOTNET_IOS_LINUX_HELPER_TESTING") == "1"
if TESTING:
    STATE_BASE = Path(os.environ["DOTNET_IOS_LINUX_HELPER_STATE"])
    LOCK_BASE = Path(os.environ["DOTNET_IOS_LINUX_HELPER_LOCKS"])
    TRUST_LOCK = Path(os.environ["DOTNET_IOS_LINUX_HELPER_TRUST_LOCK"])
else:
    STATE_BASE = Path("/var/lib/dotnet-ios-linux")
    LOCK_BASE = Path("/run/lock/dotnet-ios-linux")
    TRUST_LOCK = (
        Path("/usr/local/share/dotnet-ios-linux/versions")
        / HELPER_VERSION
        / "toolchain.lock.json"
    )


def fail(message: str) -> NoReturn:
    raise SystemExit(f"system workload helper failed: {message}")


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {label}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} must contain an object")
    return value


def load_transaction_module():
    path = Path(__file__).resolve().with_name("workload-transaction.py")
    spec = importlib.util.spec_from_file_location("system_workload_transaction", path)
    if spec is None or spec.loader is None:
        fail("cannot load the installed workload transaction module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_root_owned_path(path: Path, *, executable: bool = False) -> None:
    if not path.is_absolute():
        fail(f"privileged path is not absolute: {path}")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        try:
            details = current.lstat()
        except OSError as error:
            fail(f"cannot inspect privileged path {current}: {error}")
        if stat.S_ISLNK(details.st_mode):
            fail(f"privileged path must not contain symlinks: {current}")
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
            fail(f"privileged path is not root-owned and immutable: {current}")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        fail(f"privileged file is not regular: {path}")
    if executable and not os.access(path, os.X_OK):
        fail(f"privileged file is not executable: {path}")


def validate_root_owned_directory(path: Path) -> None:
    if not path.is_absolute():
        fail(f"privileged directory is not absolute: {path}")
    current = Path("/")
    paths = [current]
    for part in path.parts[1:]:
        current /= part
        paths.append(current)
    for current in paths:
        try:
            details = current.lstat()
        except OSError as error:
            fail(f"cannot inspect privileged directory {current}: {error}")
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            fail(f"privileged directory chain is unsafe: {current}")
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
            fail(f"privileged directory is not root-owned and immutable: {current}")


def helper_content_version() -> str:
    files = (
        ("eng/install/system-workload-helper.py", INSTALLED_HELPER),
        ("eng/install/workload-transaction.py", INSTALLED_TRANSACTION),
        ("eng/common/atomic.py", INSTALLED_ATOMIC),
        ("toolchain.lock.json", TRUST_LOCK),
    )
    value = hashlib.sha256()
    for label, path in files:
        value.update(label.encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
    return value.hexdigest()


def require_privileged_runtime() -> None:
    if TESTING:
        return
    if os.geteuid() != 0:
        fail("this installed helper must run as root")
    expected_parent = Path("/usr/local/lib/dotnet-ios-linux/versions")
    if (
        RUNNING_HELPER != INSTALLED_HELPER
        or INSTALLED_ROOT.parent != expected_parent
        or re.fullmatch(r"[0-9a-f]{64}", HELPER_VERSION) is None
    ):
        fail("helper must run from an immutable versioned installed path")
    validate_root_owned_path(INSTALLED_HELPER, executable=True)
    validate_root_owned_path(INSTALLED_TRANSACTION)
    validate_root_owned_path(INSTALLED_ATOMIC)
    validate_root_owned_path(TRUST_LOCK)
    if helper_content_version() != HELPER_VERSION:
        fail("installed helper content does not match its versioned path")


def validate_identifier(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:
        fail("installation ID is invalid")
    return value


def validate_version(value: str, label: str) -> str:
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+){2,3}(?:[-.][A-Za-z0-9.-]+)?", value) is None:
        fail(f"{label} is invalid")
    return value


def validate_root(root: Path, dotnet: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or not dotnet.is_absolute():
        fail("host .NET paths must be absolute")
    if not TESTING:
        validate_root_owned_directory(root)
        validate_root_owned_path(dotnet, executable=True)
    try:
        resolved_root = root.resolve(strict=True)
        resolved_dotnet = dotnet.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve host .NET paths: {error}")
    if root.is_symlink() or not resolved_root.is_dir():
        fail("host .NET root must be a real directory")
    if resolved_dotnet != resolved_root / "dotnet" or not os.access(resolved_dotnet, os.X_OK):
        fail("host dotnet executable does not belong to the selected root")
    if not TESTING:
        validate_root_owned_directory(resolved_root)
        validate_root_owned_path(resolved_dotnet, executable=True)
    return resolved_root, resolved_dotnet


def validate_system_tree(root: Path, sdk_version: str) -> None:
    if TESTING:
        return
    sdk_feature_band = ".".join(sdk_version.split(".")[:2]) + f".{int(sdk_version.split('.')[2]) // 100 * 100}"
    required = [root / "dotnet", root / "sdk" / sdk_version]
    managed_trees = [
        root / "host",
        root / "shared",
        root / "sdk-manifests" / "10.0.100",
        root / "sdk-manifests" / sdk_feature_band,
        root / "metadata" / "workloads",
        root / "packs",
        root / "template-packs",
        root / "library-packs",
    ]
    for path in required:
        if not path.exists():
            fail(f"required system .NET path is missing: {path}")
    for path in required + managed_trees:
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            if not os.path.lexists(current):
                break
            details = current.lstat()
            if stat.S_ISLNK(details.st_mode):
                fail(f"system .NET path contains a symlink: {current}")
            if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
                fail(f"system .NET path is not root-owned and immutable: {current}")
    for tree in [root / "sdk" / sdk_version] + [
        item for item in managed_trees if os.path.lexists(item)
    ]:
        if tree.is_symlink() or not tree.is_dir():
            fail(f"system .NET tree is unsafe: {tree}")
        for path in tree.rglob("*"):
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                fail(f"system .NET tree contains a symlink: {path}")
            if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
                fail(f"system .NET tree entry is not root-owned and immutable: {path}")


def root_id(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]


def trusted_lock() -> tuple[dict, str]:
    if TRUST_LOCK.is_symlink() or not TRUST_LOCK.is_file():
        fail(f"installed trust lock is missing or unsafe: {TRUST_LOCK}")
    if not TESTING:
        details = TRUST_LOCK.stat()
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
            fail("installed trust lock is not root-owned and immutable")
    value = load_json(TRUST_LOCK, "installed trust lock")
    if value.get("schemaVersion") != 1:
        fail("unsupported installed trust lock")
    return value, digest(TRUST_LOCK)


def expected_packages(lock: dict) -> dict[str, dict]:
    try:
        workload_records = lock["components"]["linuxIosWorkload"]["packages"]
        workload_set = lock["components"]["workloadSet"]
    except (KeyError, TypeError):
        fail("installed trust lock lacks workload packages")
    records: list[dict] = []
    for item in workload_records:
        if not isinstance(item, dict):
            fail("installed trust lock contains an invalid package record")
        filename = item.get("feedFile")
        if not isinstance(filename, str):
            package_id = item.get("id")
            version = item.get("version")
            if not isinstance(package_id, str) or not isinstance(version, str):
                fail("installed trust lock package identity is incomplete")
            filename = f"{package_id.lower()}.{version.lower()}.nupkg"
        records.append(
            {
                "id": item.get("id"),
                "version": item.get("version"),
                "file": filename,
                "sha256": item.get("sha256"),
                "sha512Base64": item.get("sha512Base64"),
                "origin": item.get("origin"),
            }
        )
    set_id = workload_set.get("packageId")
    set_version = workload_set.get("packageVersion")
    records.append(
        {
            "id": set_id,
            "version": set_version,
            "file": f"{str(set_id).lower()}.{str(set_version).lower()}.nupkg",
            "sha256": workload_set.get("sha256"),
            "sha512Base64": workload_set.get("sha512Base64"),
            "origin": "nuget.org",
        }
    )
    result: dict[str, dict] = {}
    for record in records:
        filename = record["file"]
        if not isinstance(filename, str) or filename in result:
            fail("installed trust lock has duplicate or invalid package filenames")
        if not all(isinstance(record.get(key), str) and record[key] for key in ("id", "version", "sha256", "sha512Base64")):
            fail("installed trust lock has an incomplete package record")
        result[filename] = record
    return result


def safe_member_name(raw: str) -> PurePosixPath:
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"bundle contains an unsafe path: {raw!r}")
    return value


def extract_bundle(bundle: Path, destination: Path, lock: dict, lock_sha256: str) -> tuple[dict, str]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(bundle, flags)
        with os.fdopen(descriptor, "rb") as bundle_stream:
            details = os.fstat(bundle_stream.fileno())
            if not stat.S_ISREG(details.st_mode):
                fail("workload bundle must be a regular non-symlink file")
            if details.st_size > MAX_BUNDLE_BYTES:
                fail("workload bundle exceeds the size limit")
            bundle_hash = hashlib.sha256()
            for block in iter(lambda: bundle_stream.read(1024 * 1024), b""):
                bundle_hash.update(block)
            bundle_stream.seek(0)
            with tarfile.open(fileobj=bundle_stream, mode="r:") as archive:
                members: list[tarfile.TarInfo] = []
                for member in archive:
                    members.append(member)
                    if len(members) > MAX_BUNDLE_MEMBERS:
                        fail("workload bundle contains too many members")
                names = [safe_member_name(member.name) for member in members]
                if len(names) != len(set(names)):
                    fail("workload bundle contains duplicate members")
                by_name = {name.as_posix(): member for name, member in zip(names, members, strict=True)}
                manifest_member = by_name.get("bundle.json")
                if manifest_member is None or not manifest_member.isfile() or manifest_member.size > 1024 * 1024:
                    fail("workload bundle has no valid manifest")
                stream = archive.extractfile(manifest_member)
                if stream is None:
                    fail("cannot read workload bundle manifest")
                try:
                    manifest = json.loads(stream.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    fail(f"cannot decode workload bundle manifest: {error}")
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("schemaVersion") != 1
                    or manifest.get("owner") != OWNER
                    or manifest.get("toolchainLockSha256") != lock_sha256
                ):
                    fail("workload bundle manifest has different provenance")
                expected = expected_packages(lock)
                records = manifest.get("packages")
                if not isinstance(records, list):
                    fail("workload bundle manifest has no package list")
                actual: dict[str, dict] = {}
                for record in records:
                    if not isinstance(record, dict) or not isinstance(record.get("file"), str):
                        fail("workload bundle contains an invalid package record")
                    filename = record["file"]
                    if filename in actual:
                        fail("workload bundle contains duplicate package records")
                    actual[filename] = record
                if actual != expected:
                    fail("workload bundle package inventory does not match the installed trust lock")
                expected_members = {"bundle.json"} | {f"packages/{name}" for name in expected}
                if set(by_name) != expected_members:
                    fail("workload bundle contains missing or unexpected members")
                packages = destination / "packages"
                packages.mkdir(parents=True, mode=0o700)
                for filename, record in expected.items():
                    member = by_name[f"packages/{filename}"]
                    if not member.isfile() or member.size > MAX_PACKAGE_BYTES:
                        fail(f"bundle package member is invalid: {filename}")
                    reader = archive.extractfile(member)
                    if reader is None:
                        fail(f"cannot read bundle package: {filename}")
                    output = packages / filename
                    sha256 = hashlib.sha256()
                    sha512 = hashlib.sha512()
                    with output.open("xb") as writer:
                        for block in iter(lambda: reader.read(1024 * 1024), b""):
                            sha256.update(block)
                            sha512.update(block)
                            writer.write(block)
                    import base64

                    actual_sha512 = base64.b64encode(sha512.digest()).decode("ascii")
                    if sha256.hexdigest() != record["sha256"] or actual_sha512 != record["sha512Base64"]:
                        fail(f"bundle package failed integrity validation: {filename}")
                    output.chmod(0o600)
                (destination / "bundle.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                return manifest, bundle_hash.hexdigest()
    except (OSError, tarfile.TarError) as error:
        fail(f"cannot inspect workload bundle: {error}")


def nuget_config(packages: Path) -> str:
    source = html.escape(str(packages), quote=True)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="dotnet-ios-linux-offline" value="{source}" />
  </packageSources>
</configuration>
"""


def environment(state: Path, root: Path) -> dict[str, str]:
    home = state / "home"
    cli_home = state / "dotnet-cli-home"
    packages = state / "nuget-packages"
    temporary = state / "tmp"
    for path in (home, cli_home, packages, temporary):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "HOME": str(home),
        "DOTNET_ROOT": str(root),
        "DOTNET_CLI_HOME": str(cli_home),
        "NUGET_PACKAGES": str(packages),
        "TMPDIR": str(temporary),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true",
        "NUGET_CERT_REVOCATION_MODE": "offline",
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "socks5://127.0.0.1:9",
        "all_proxy": "socks5://127.0.0.1:9",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }


def isolate_network() -> None:
    clone_newnet = 0x40000000
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(clone_newnet) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    lock_fd: int | None = None,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    if not TESTING:
        executable = Path(command[0])
        validate_root_owned_directory(executable.parent)
        validate_root_owned_path(executable, executable=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            preexec_fn=None if TESTING else isolate_network,
            pass_fds=() if lock_fd is None else (lock_fd,),
        )
    except (OSError, subprocess.SubprocessError) as error:
        fail(f"cannot start isolated privileged command: {error}")
    if result.returncode and not allow_failure:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        fail(f"privileged command failed with exit code {result.returncode}")
    return result


def parse_workload_list(output: str, expected_manifest: str | None = None) -> bool:
    for line in output.splitlines():
        columns = line.split()
        if columns[:1] != ["ios"]:
            continue
        if expected_manifest is None or columns[1:2] == [expected_manifest]:
            return True
    return False


def ios_registration_exists(root: Path) -> bool:
    metadata = root / "metadata" / "workloads"
    if not metadata.exists():
        return False
    if metadata.is_symlink() or not metadata.is_dir():
        fail(f"workload metadata root is unsafe: {metadata}")
    return any(
        candidate.is_file() and not candidate.is_symlink()
        for candidate in metadata.glob("**/InstalledWorkloads/ios")
    )


def state_paths(root: Path) -> tuple[Path, Path, Path]:
    identifier = root_id(root)
    state = STATE_BASE / "roots" / identifier
    lock = LOCK_BASE / f"{identifier}.lock"
    return state, state / "state.json", lock


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    parent_details = path.parent.lstat()
    if path.parent.is_symlink() or not path.parent.is_dir():
        fail(f"helper lock directory is unsafe: {path.parent}")
    if not TESTING and (
        parent_details.st_uid != 0 or stat.S_IMODE(parent_details.st_mode) & 0o022
    ):
        fail(f"helper lock directory is not root-owned and immutable: {path.parent}")
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode) or (
        not TESTING and (details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022)
    ):
        os.close(descriptor)
        fail(f"helper lock file is unsafe: {path}")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def read_state(
    path: Path, root: Path, *, allow_terminal_other_helper: bool = False
) -> dict:
    value = load_json(path, "system workload state")
    if value.get("owner") != OWNER or value.get("dotnetRoot") != str(root):
        fail("system workload state has different ownership")
    if value.get("schemaVersion") != PROTOCOL_VERSION:
        fail("system workload state uses an unsupported schema")
    expected_helper = "testing" if TESTING else HELPER_VERSION
    if value.get("helperVersion") != expected_helper:
        if not (
            allow_terminal_other_helper
            and value.get("status") in {"rolled-back", "removed"}
        ):
            fail("system workload state belongs to a different helper version")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            fail(f"system workload state is not a regular file: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value


def require_ownership(state: dict, ownership_token: str) -> None:
    if state.get("ownershipToken") != ownership_token:
        fail("system workload state belongs to a different installation")


def prune_state_payload(state_dir: Path) -> None:
    for name in (
        "bundle",
        "home",
        "dotnet-cli-home",
        "nuget-packages",
        "tmp",
        "control",
    ):
        path = state_dir / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif os.path.lexists(path):
            path.unlink()
    for path in state_dir.glob("bundle-stage-*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)


def retire_terminal_state(state_dir: Path, transaction) -> None:
    tombstone = state_dir.with_name(f".{state_dir.name}.dotnet-ios-linux-retired")
    if os.path.lexists(tombstone) and os.path.lexists(state_dir):
        fail("system state and retirement tombstone both exist")
    transaction.durable_remove(state_dir, tombstone)


def verify_sdk(
    dotnet: Path,
    sdk_version: str,
    control: Path,
    env: dict[str, str],
    lock_fd: int,
) -> None:
    actual = run(
        [str(dotnet), "--version"], cwd=control, env=env, lock_fd=lock_fd
    ).stdout.strip()
    if actual != sdk_version:
        fail(f"selected SDK is {actual}, expected {sdk_version}")


def verify_installation_unlocked(
    root: Path,
    dotnet: Path,
    sdk_version: str,
    workload_version: str,
    expected_manifest: str,
    ownership_token: str,
    state_dir: Path,
    state_path: Path,
    lock_fd: int,
) -> None:
    state = read_state(state_path, root)
    require_ownership(state, ownership_token)
    if state.get("status") != "committed":
        fail("system workload installation is not committed")
    if (
        state.get("sdkVersion") != sdk_version
        or state.get("workloadVersion") != workload_version
        or state.get("workloadManifest") != expected_manifest
    ):
        fail("system workload state records different versions")
    validate_system_tree(root, sdk_version)
    transaction = load_transaction_module()
    transaction.verify(root, state_dir / "workload-transaction")
    env = environment(state_dir, root)
    control = state_dir / "control"
    verify_sdk(dotnet, sdk_version, control, env, lock_fd)
    actual_workload = run(
        [str(dotnet), "workload", "--version"],
        cwd=control,
        env=env,
        lock_fd=lock_fd,
    ).stdout.strip()
    if actual_workload != workload_version:
        fail(f"selected workload set is {actual_workload}, expected {workload_version}")
    listed = run(
        [str(dotnet), "workload", "list"],
        cwd=control,
        env=env,
        lock_fd=lock_fd,
    )
    if not parse_workload_list(listed.stdout, expected_manifest):
        fail("the ios workload is not installed")
    print("system_workload=ok")
    print(f"workload_set={actual_workload}")
    print("ios_installed=true")


def verify_installation(
    root: Path,
    dotnet: Path,
    sdk_version: str,
    workload_version: str,
    expected_manifest: str,
    ownership_token: str,
) -> None:
    state_dir, state_path, lock_path = state_paths(root)
    descriptor = acquire_lock(lock_path)
    try:
        verify_installation_unlocked(
            root,
            dotnet,
            sdk_version,
            workload_version,
            expected_manifest,
            ownership_token,
            state_dir,
            state_path,
            descriptor,
        )
    finally:
        os.close(descriptor)


def install_workload(
    root: Path,
    dotnet: Path,
    sdk_version: str,
    workload_version: str,
    expected_manifest: str,
    ownership_token: str,
    bundle: Path,
) -> None:
    state_dir, state_path, lock_path = state_paths(root)
    descriptor = acquire_lock(lock_path)
    transaction = load_transaction_module()
    try:
        terminal_tombstone = state_dir.with_name(
            f".{state_dir.name}.dotnet-ios-linux-retired"
        )
        if os.path.lexists(terminal_tombstone):
            if os.path.lexists(state_dir):
                fail("system state and retirement tombstone both exist")
            transaction.durable_remove(state_dir, terminal_tombstone)
        if state_path.exists():
            existing = read_state(
                state_path, root, allow_terminal_other_helper=True
            )
            if existing.get("status") == "committed" and existing.get("ownershipToken") == ownership_token:
                verify_installation_unlocked(
                    root,
                    dotnet,
                    sdk_version,
                    workload_version,
                    expected_manifest,
                    ownership_token,
                    state_dir,
                    state_path,
                    descriptor,
                )
                print("reused=true")
                return
            if existing.get("status") in {"rolled-back", "removed"}:
                retire_terminal_state(state_dir, transaction)
            else:
                fail("a different or incomplete system workload transaction already exists; run recover")
        lock, lock_sha256 = trusted_lock()
        if lock["components"]["dotnetSdk"]["featureBand"] != ".".join(sdk_version.split(".")[:2]) + f".{int(sdk_version.split('.')[2]) // 100 * 100}":
            fail("selected SDK does not belong to the trusted feature band")
        if lock["components"]["workloadSet"]["cliVersion"] != workload_version:
            fail("selected workload set does not match the installed trust lock")
        validate_system_tree(root, sdk_version)
        state = {
            "schemaVersion": PROTOCOL_VERSION,
            "owner": OWNER,
            "status": "preparing",
            "ownershipToken": ownership_token,
            "helperVersion": HELPER_VERSION if not TESTING else "testing",
            "dotnetRoot": str(root),
            "dotnetExecutable": str(dotnet),
            "sdkVersion": sdk_version,
            "workloadVersion": workload_version,
            "workloadManifest": expected_manifest,
            "toolchainLockSha256": lock_sha256,
        }
        roots = state_dir.parent
        transaction.mkdir_durable(roots, mode=0o700)
        state_stage = Path(tempfile.mkdtemp(prefix=f".{state_dir.name}.prepare-", dir=roots))
        try:
            write_json(state_stage / "state.json", state)
            transaction.publish_directory_noreplace(state_stage, state_dir)
        except BaseException:
            shutil.rmtree(state_stage, ignore_errors=True)
            raise
        bundle_stage = Path(tempfile.mkdtemp(prefix="bundle-stage-", dir=state_dir))
        try:
            manifest, bundle_sha256 = extract_bundle(bundle, bundle_stage, lock, lock_sha256)
            transaction.fsync_tree(bundle_stage)
            bundle_final = state_dir / "bundle"
            os.replace(bundle_stage, bundle_final)
            transaction.fsync_directory(state_dir)
        except BaseException:
            shutil.rmtree(bundle_stage, ignore_errors=True)
            raise
        control = state_dir / "control"
        transaction.mkdir_durable(control, mode=0o700)
        write_json(
            control / "global.json",
            {
                "sdk": {
                    "version": sdk_version,
                    "rollForward": "disable",
                    "workloadVersion": workload_version,
                }
            },
        )
        config = control / "NuGet.Config"
        config.write_text(nuget_config(state_dir / "bundle" / "packages"), encoding="utf-8")
        transaction.fsync_file(config)
        transaction.fsync_directory(control)
        state["bundleSha256"] = bundle_sha256
        state["toolchainLockSha256"] = manifest["toolchainLockSha256"]
        write_json(state_path, state)
        env = environment(state_dir, root)
        verify_sdk(dotnet, sdk_version, control, env, descriptor)
        if ios_registration_exists(root):
            fail("an ios workload already exists without this installation ownership")
        manifest_version, manifest_band = expected_manifest.split("/", 1)
        transaction.begin(
            root,
            state_dir / "workload-transaction",
            [
                {"id": record["id"], "version": record["version"]}
                for record in manifest["packages"]
            ],
            sdk_feature_band=".".join(sdk_version.split(".")[:2])
            + f".{int(sdk_version.split('.')[2]) // 100 * 100}",
            workload_version=workload_version,
            workloads=[
                {
                    "workloadId": "ios",
                    "manifestId": "Microsoft.NET.Sdk.iOS",
                    "manifestVersion": manifest_version,
                    "manifestFeatureBand": manifest_band,
                }
            ],
            control_global_json=str(control / "global.json"),
        )
        state["status"] = "installing"
        write_json(state_path, state)
        try:
            common = [
                "--configfile",
                str(config),
                "--disable-parallel",
                "--temp-dir",
                str(state_dir / "tmp"),
                "--verbosity",
                "minimal",
            ]
            run(
                [str(dotnet), "workload", "install", "ios", *common],
                cwd=control,
                env=env,
                lock_fd=descriptor,
            )
            listed = run(
                [str(dotnet), "workload", "list"],
                cwd=control,
                env=env,
                lock_fd=descriptor,
            )
            if not parse_workload_list(listed.stdout, expected_manifest):
                fail("the ios workload was not reported after installation")
            transaction.commit(root, state_dir / "workload-transaction")
        except BaseException:
            try:
                transaction_path = state_dir / "workload-transaction"
                transaction.restore(root, transaction_path, verify_committed=False, retain=True)
                state["status"] = "rolled-back"
                write_json(state_path, state)
                transaction.finalize(root, transaction_path)
                prune_state_payload(state_dir)
            except BaseException as rollback_error:
                state["status"] = "needs-recovery"
                state["recoveryError"] = type(rollback_error).__name__
                write_json(state_path, state)
            raise
        state["status"] = "committed"
        write_json(state_path, state)
        print("system_workload=installed")
        print(f"ownership_token={ownership_token}")
        print("reused=false")
    finally:
        os.close(descriptor)


def uninstall_workload(
    root: Path,
    dotnet: Path,
    sdk_version: str,
    workload_version: str,
    expected_manifest: str,
    ownership_token: str,
) -> None:
    state_dir, state_path, lock_path = state_paths(root)
    descriptor = acquire_lock(lock_path)
    try:
        state = read_state(state_path, root)
        require_ownership(state, ownership_token)
        if state.get("status") == "removed":
            transaction_path = state_dir / "workload-transaction"
            transaction = load_transaction_module()
            transaction.finalize(root, transaction_path)
            prune_state_payload(state_dir)
            print("system_workload=removed")
            print("reused=true")
            return
        if state.get("status") not in {"committed", "uninstalling"}:
            fail("system workload state requires recovery before uninstall")
        if (
            state.get("sdkVersion") != sdk_version
            or state.get("workloadVersion") != workload_version
            or state.get("workloadManifest") != expected_manifest
        ):
            fail("system workload state records different versions")
        validate_system_tree(root, sdk_version)
        transaction = load_transaction_module()
        transaction_path = state_dir / "workload-transaction"
        if state.get("status") == "committed":
            state["status"] = "uninstalling"
            write_json(state_path, state)
        transaction_state = transaction.read_state(transaction_path, root)
        if transaction_state.get("status") == "committed":
            transaction.prepare_uninstall(root, transaction_path)
            transaction_state = transaction.read_state(transaction_path, root)
        elif transaction_state.get("status") not in {"uninstalling", "restoring", "restored"}:
            fail("workload transaction cannot resume uninstall")
        if transaction_state.get("status") != "restored":
            transaction.restore(root, transaction_path, verify_committed=True, retain=True)
        state["retainedUnits"] = transaction.retained_units(root, transaction_path)
        state["status"] = "removed"
        write_json(state_path, state)
        transaction.finalize(root, transaction_path)
        prune_state_payload(state_dir)
        print("system_workload=removed")
        print(f"retained_units={len(state['retainedUnits'])}")
        print("reused=false")
    finally:
        os.close(descriptor)


def recover(root: Path, ownership_token: str) -> None:
    state_dir, state_path, lock_path = state_paths(root)
    descriptor = acquire_lock(lock_path)
    try:
        if not state_path.exists():
            print("system_workload=absent")
            return
        state = read_state(state_path, root)
        require_ownership(state, ownership_token)
        transaction_path = state_dir / "workload-transaction"
        if state.get("status") in {"rolled-back", "removed"}:
            transaction = load_transaction_module()
            transaction.finalize(root, transaction_path)
            prune_state_payload(state_dir)
            print(f"system_workload={state['status']}")
            return
        if not transaction_path.exists():
            if state.get("status") == "preparing":
                state["status"] = "rolled-back"
                write_json(state_path, state)
                print(f"system_workload={state['status']}")
                return
            fail("system workload state has no recovery transaction")
        transaction = load_transaction_module()
        transaction.restore(root, transaction_path, verify_committed=False, retain=True)
        state["status"] = "rolled-back"
        write_json(state_path, state)
        transaction.finalize(root, transaction_path)
        prune_state_payload(state_dir)
        print("system_workload=rolled-back")
    finally:
        os.close(descriptor)


def main() -> None:
    os.umask(0o077)
    require_privileged_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "verify", "uninstall", "recover"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dotnet", type=Path, required=True)
    parser.add_argument("--sdk-version")
    parser.add_argument("--workload-version")
    parser.add_argument("--workload-manifest")
    parser.add_argument("--ownership-token")
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    root, dotnet = validate_root(args.root.expanduser().absolute(), args.dotnet.expanduser().absolute())
    if args.command == "recover":
        if args.ownership_token is None:
            fail("recover requires --ownership-token")
        recover(root, validate_identifier(args.ownership_token))
        return
    if (
        args.sdk_version is None
        or args.workload_version is None
        or args.workload_manifest is None
        or args.ownership_token is None
    ):
        fail("SDK, workload, manifest, and ownership-token arguments are required")
    sdk_version = validate_version(args.sdk_version, "SDK version")
    workload_version = validate_version(args.workload_version, "workload version")
    if re.fullmatch(r"[0-9A-Za-z._-]+/[0-9A-Za-z._-]+", args.workload_manifest) is None:
        fail("workload manifest identity is invalid")
    ownership_token = validate_identifier(args.ownership_token)
    if args.command == "install":
        if args.bundle is None:
            fail("install requires --bundle")
        install_workload(
            root,
            dotnet,
            sdk_version,
            workload_version,
            args.workload_manifest,
            ownership_token,
            args.bundle.expanduser().absolute(),
        )
    elif args.command == "verify":
        verify_installation(
            root, dotnet, sdk_version, workload_version, args.workload_manifest, ownership_token
        )
    else:
        uninstall_workload(
            root, dotnet, sdk_version, workload_version, args.workload_manifest, ownership_token
        )


if __name__ == "__main__":
    main()
