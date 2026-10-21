#!/usr/bin/env python3

from __future__ import annotations

import base64
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
BUNDLE_PATH = REPOSITORY / "eng" / "workload" / "build-workload-bundle.py"
HELPER_PATH = REPOSITORY / "eng" / "install" / "system-workload-helper.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUNDLE = load_module(BUNDLE_PATH, "build_workload_bundle")


def hashes(value: bytes) -> tuple[str, str]:
    return (
        hashlib.sha256(value).hexdigest(),
        base64.b64encode(hashlib.sha512(value).digest()).decode("ascii"),
    )


def write(path: Path, value: bytes | str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def fixture(base: Path) -> tuple[Path, Path, Path, Path, Path]:
    local_bytes = b"local package\n"
    official_bytes = b"official package\n"
    workload_bytes = b"workload set\n"
    local_sha256, local_sha512 = hashes(local_bytes)
    official_sha256, official_sha512 = hashes(official_bytes)
    workload_sha256, workload_sha512 = hashes(workload_bytes)

    local_filename = "DotnetIosLinux.Sdk.1.0.0.nupkg"
    official_filename = "example.official.2.0.0.nupkg"
    workload_filename = "microsoft.net.workloads.10.0.400.10.401.1.nupkg"
    feed = base / "feed"
    write(feed / local_filename, local_bytes)
    write(
        feed / "feed-lock.json",
        json.dumps(
            {
                "packages": [
                    {
                        "id": "DotnetIosLinux.Sdk",
                        "version": "1.0.0",
                        "file": local_filename,
                        "sha256": local_sha256,
                        "sha512Base64": local_sha512,
                        "origin": "project",
                    }
                ]
            }
        ),
    )
    package_cache = base / "packages"
    write(package_cache / official_filename, official_bytes)
    workload_set = base / workload_filename
    write(workload_set, workload_bytes)
    lock = base / "toolchain.lock.json"
    write(
        lock,
        json.dumps(
            {
                "schemaVersion": 1,
                "components": {
                    "dotnetSdk": {"featureBand": "10.0.400"},
                    "workloadSet": {
                        "packageId": "Microsoft.NET.Workloads.10.0.400",
                        "packageVersion": "10.401.1",
                        "cliVersion": "10.0.401.1",
                        "sha256": workload_sha256,
                        "sha512Base64": workload_sha512,
                    },
                    "linuxIosWorkload": {
                        "workloadId": "ios",
                        "packages": [
                            {
                                "id": "DotnetIosLinux.Sdk",
                                "version": "1.0.0",
                                "origin": "project",
                                "feedFile": local_filename,
                                "sha256": local_sha256,
                                "sha512Base64": local_sha512,
                            },
                            {
                                "id": "Example.Official",
                                "version": "2.0.0",
                                "origin": "nuget.org",
                                "sha256": official_sha256,
                                "sha512Base64": official_sha512,
                            },
                        ],
                    },
                },
            },
            indent=2,
        )
        + "\n",
    )
    output = base / "workload-bundle.tar"
    return lock, feed, workload_set, package_cache, output


def build_fixture_bundle(base: Path) -> tuple[Path, Path]:
    lock, feed, workload_set, package_cache, output = fixture(base)
    BUNDLE.build(lock, feed, workload_set, package_cache, output)
    return lock, output


def load_helper(base: Path, trust_lock: Path):
    environment = {
        "DOTNET_IOS_LINUX_HELPER_TESTING": "1",
        "DOTNET_IOS_LINUX_HELPER_STATE": str(base / "state"),
        "DOTNET_IOS_LINUX_HELPER_LOCKS": str(base / "locks"),
        "DOTNET_IOS_LINUX_HELPER_TRUST_LOCK": str(trust_lock),
    }
    with mock.patch.dict(os.environ, environment):
        return load_module(HELPER_PATH, f"system_workload_helper_{id(base)}")


class WorkloadBundleContracts(unittest.TestCase):
    def test_plan_and_bundle_are_complete_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workload-bundle-contract-") as temporary:
            base = Path(temporary)
            lock, feed, workload_set, package_cache, output = fixture(base)

            planned = subprocess.run(
                [sys.executable, str(BUNDLE_PATH), "plan", "--lock", str(lock)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("example.official.2.0.0.nupkg", planned.stdout)
            self.assertIn("https://api.nuget.org/v3-flatcontainer/", planned.stdout)

            BUNDLE.build(lock, feed, workload_set, package_cache, output)
            first_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            BUNDLE.build(lock, feed, workload_set, package_cache, output)
            self.assertEqual(first_hash, hashlib.sha256(output.read_bytes()).hexdigest())
            with tarfile.open(output, "r:") as archive:
                self.assertEqual(
                    {
                        "bundle.json",
                        "packages/DotnetIosLinux.Sdk.1.0.0.nupkg",
                        "packages/example.official.2.0.0.nupkg",
                        "packages/microsoft.net.workloads.10.0.400.10.401.1.nupkg",
                    },
                    set(archive.getnames()),
                )

    def test_existing_tampered_bundle_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workload-bundle-tamper-") as temporary:
            base = Path(temporary)
            lock, feed, workload_set, package_cache, output = fixture(base)
            BUNDLE.build(lock, feed, workload_set, package_cache, output)
            output.write_bytes(b"tampered")

            with self.assertRaises(SystemExit):
                BUNDLE.build(lock, feed, workload_set, package_cache, output)


class SystemWorkloadHelperContracts(unittest.TestCase):
    def test_workload_child_inherits_the_lifecycle_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-child-lock-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            lifecycle_lock = base / "locks/root.lock"
            ready = base / "child-ready"
            pid = os.fork()
            if pid == 0:
                try:
                    descriptor = helper.acquire_lock(lifecycle_lock)
                    helper.run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; import sys,time; "
                                "Path(sys.argv[1]).write_text('ready'); time.sleep(1.5)"
                            ),
                            str(ready),
                        ],
                        cwd=base,
                        env=os.environ.copy(),
                        lock_fd=descriptor,
                    )
                finally:
                    os._exit(0)

            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            contender = os.open(lifecycle_lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deadline = time.monotonic() + 3
                while True:
                    try:
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            self.fail("orphaned workload child did not release lifecycle lock")
                        time.sleep(0.02)
            finally:
                os.close(contender)

    def test_privileged_root_rejects_a_writable_ancestor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-ancestor-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            with self.assertRaises(SystemExit):
                helper.validate_root_owned_directory(base)

    def test_bundle_extraction_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-traversal-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            malicious = base / "malicious.tar"
            payload = base / "payload"
            write(payload, b"bad")
            with tarfile.open(malicious, "w") as archive:
                archive.add(payload, arcname="../escape")

            with self.assertRaises(SystemExit):
                helper.extract_bundle(
                    malicious,
                    base / "extract",
                    json.loads(lock.read_text(encoding="utf-8")),
                    hashlib.sha256(lock.read_bytes()).hexdigest(),
                )
            self.assertFalse((base / "escape").exists())

    def test_privileged_environment_is_allowlisted_and_offline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-environment-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            with mock.patch.dict(
                os.environ,
                {
                    "LD_PRELOAD": "/malicious/library.so",
                    "DOTNET_STARTUP_HOOKS": "/malicious/hook.dll",
                    "NUGET_PLUGIN_PATHS": "/malicious/plugin",
                },
            ):
                value = helper.environment(base / "state", base / "dotnet")

            self.assertNotIn("LD_PRELOAD", value)
            self.assertNotIn("DOTNET_STARTUP_HOOKS", value)
            self.assertNotIn("NUGET_PLUGIN_PATHS", value)
            self.assertEqual("http://127.0.0.1:9", value["HTTPS_PROXY"])
            self.assertEqual(str(base / "dotnet"), value["DOTNET_ROOT"])

            completed = subprocess.CompletedProcess(["true"], 0, "", "")
            with mock.patch.object(helper, "TESTING", False), mock.patch.object(
                helper.subprocess, "run", return_value=completed
            ) as invoked:
                helper.run(["/usr/bin/true"], cwd=base, env=value)
            self.assertIs(helper.isolate_network, invoked.call_args.kwargs["preexec_fn"])

    def test_install_verify_and_uninstall_use_root_owned_transaction_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-lifecycle-") as temporary:
            base = Path(temporary)
            lock, bundle = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            root = base / "dotnet"
            (root / "sdk" / "10.0.402").mkdir(parents=True)
            dotnet = root / "dotnet"
            write(
                dotnet,
                """#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd -P)"
if [[ "$1" == "--version" ]]; then printf '10.0.402\n'; exit 0; fi
[[ "$1" == workload ]]
case "$2" in
  --version) printf '10.0.401.1\n' ;;
  update) ;;
  install)
    mkdir -p "$root/packs/DotnetIosLinux.Sdk/1.0.0" "$root/metadata/workloads/10.0.400/InstalledWorkloads"
    printf 'fixture\n' > "$root/packs/DotnetIosLinux.Sdk/1.0.0/payload"
    printf 'installed\n' > "$root/metadata/workloads/10.0.400/InstalledWorkloads/ios"
    ;;
  list)
    if [[ -f "$root/metadata/workloads/10.0.400/InstalledWorkloads/ios" ]]; then
      printf 'ios 27.0.10722/10.0.100\n'
    fi
    ;;
  uninstall)
    if [[ ! -f "$root/noop-uninstall" ]]; then
      rm -f "$root/metadata/workloads/10.0.400/InstalledWorkloads/ios"
      if [[ ! -f "$root/retain-pack" ]]; then
        rm -rf "$root/packs/DotnetIosLinux.Sdk"
      fi
    fi
    ;;
  *) exit 2 ;;
esac
""",
                0o755,
            )

            helper.install_workload(
                root.resolve(),
                dotnet.resolve(),
                "10.0.402",
                "10.0.401.1",
                "27.0.10722/10.0.100",
                "fixture-install",
                bundle,
            )
            helper.verify_installation(
                root.resolve(),
                dotnet.resolve(),
                "10.0.402",
                "10.0.401.1",
                "27.0.10722/10.0.100",
                "fixture-install",
            )
            state_dir, state_path, _ = helper.state_paths(root.resolve())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("committed", state["status"])
            self.assertTrue((state_dir / "workload-transaction/transaction.json").is_file())

            helper.uninstall_workload(
                root.resolve(),
                dotnet.resolve(),
                "10.0.402",
                "10.0.401.1",
                "27.0.10722/10.0.100",
                "fixture-install",
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("removed", state["status"])
            self.assertFalse((root / "packs/DotnetIosLinux.Sdk").exists())
            self.assertEqual({}, state["retainedUnits"])
            self.assertFalse((state_dir / "bundle").exists())

    def test_recovery_rejects_a_different_ownership_token(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-ownership-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            root = base / "dotnet"
            root.mkdir()
            dotnet = root / "dotnet"
            write(dotnet, "#!/usr/bin/env bash\nexit 0\n", 0o755)
            state_dir, state_path, _ = helper.state_paths(root.resolve())
            state_dir.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "owner": "dotnet-ios-linux",
                        "schemaVersion": 1,
                        "dotnetRoot": str(root.resolve()),
                        "status": "preparing",
                        "ownershipToken": "owner-a",
                        "helperVersion": "testing",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                helper.recover(root.resolve(), "owner-b")

            self.assertEqual("preparing", json.loads(state_path.read_text())["status"])

    def test_state_reuse_stabilizes_file_and_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-state-fsync-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            root = base / "dotnet"
            root.mkdir()
            state_dir, state_path, _ = helper.state_paths(root.resolve())
            state_dir.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "owner": "dotnet-ios-linux",
                        "schemaVersion": 1,
                        "dotnetRoot": str(root.resolve()),
                        "status": "committed",
                        "helperVersion": "testing",
                    }
                ),
                encoding="utf-8",
            )
            original = os.fsync
            with mock.patch.object(helper.os, "fsync", wraps=original) as synced:
                helper.read_state(state_path, root.resolve())
            self.assertGreaterEqual(synced.call_count, 2)

    def test_new_helper_retires_only_terminal_old_version_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="system-helper-version-") as temporary:
            base = Path(temporary)
            lock, _ = build_fixture_bundle(base)
            helper = load_helper(base, lock)
            root = base / "dotnet"
            root.mkdir()
            state_dir, state_path, _ = helper.state_paths(root.resolve())
            state_dir.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "owner": "dotnet-ios-linux",
                        "dotnetRoot": str(root.resolve()),
                        "helperVersion": "old-version",
                        "status": "removed",
                    }
                ),
                encoding="utf-8",
            )
            state = helper.read_state(
                state_path, root.resolve(), allow_terminal_other_helper=True
            )
            transaction = helper.load_transaction_module()
            helper.retire_terminal_state(state_dir, transaction)
            self.assertEqual("removed", state["status"])
            self.assertFalse(state_dir.exists())

            state_dir.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "owner": "dotnet-ios-linux",
                        "dotnetRoot": str(root.resolve()),
                        "helperVersion": "old-version",
                        "status": "installing",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                helper.read_state(
                    state_path, root.resolve(), allow_terminal_other_helper=True
                )


if __name__ == "__main__":
    unittest.main()
