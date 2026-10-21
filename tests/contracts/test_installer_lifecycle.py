#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
SDK_INSTALLER = REPOSITORY / "eng" / "install" / "install-dotnet-sdk.py"
WORKLOAD_TRANSACTION = REPOSITORY / "eng" / "install" / "workload-transaction.py"
DURABLE_ASSETS = REPOSITORY / "eng" / "install" / "durable-assets.py"
ACTIVATION_WRITER = REPOSITORY / "eng" / "install" / "write-activation.py"
DOWNLOAD_HELPER = REPOSITORY / "eng" / "install" / "download-verified.sh"
TOP_LEVEL_INSTALLER = REPOSITORY / "install.sh"
CONTEXT_RESOLVER = REPOSITORY / "eng" / "install" / "resolve-install-context.py"
SYSTEM_WORKLOAD_HELPER = REPOSITORY / "eng" / "install" / "system-workload-helper.py"
TEST_SDK_VERSION = "10.0.402"


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path: Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


class DotnetSdkMergeContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="dotnet-sdk-merge-contract-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "home" / ".dotnet"
        self.receipt = self.base / "data" / "receipts" / "dotnet-sdk.json"
        self.archive = self.base / "dotnet-sdk.tar.gz"
        payload = self.base / "payload"
        write(payload / "dotnet", "locked host\n", 0o755)
        write(payload / "LICENSE.txt", "locked license\n")
        write(payload / "sdk" / "10.0.402" / "sdk.dll", "locked sdk\n")
        write(payload / "host" / "fxr" / "10.0.13" / "libhostfxr.so", "locked fxr\n")
        with tarfile.open(self.archive, "w:gz") as archive:
            for path in sorted(payload.rglob("*"), key=lambda item: (len(item.parts), str(item))):
                archive.add(path, arcname=path.relative_to(payload).as_posix(), recursive=False)
        self.installer = load_script(SDK_INSTALLER, f"sdk_installer_{id(self)}")
        self.sdk_sha512 = hashlib.sha512(self.archive.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def install(self) -> None:
        self.installer.install(
            self.archive,
            self.root.absolute(),
            self.receipt.absolute(),
            TEST_SDK_VERSION,
            self.sdk_sha512,
        )

    def test_merge_and_uninstall_preserve_preexisting_sdk_and_workload(self) -> None:
        write(self.root / "dotnet", "existing host\n", 0o755)
        write(self.root / "sdk" / "9.0.100" / "existing.dll", "existing sdk\n")
        write(self.root / "packs" / "Existing.Workload" / "1.0" / "pack.txt", "existing pack\n")

        self.install()
        self.installer.verify(self.root.absolute(), self.receipt.absolute(), TEST_SDK_VERSION)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        retained = {item["path"]: item["reason"] for item in receipt["retainedFiles"]}
        self.assertEqual("existing-root", retained["dotnet"])
        self.assertEqual("existing host\n", (self.root / "dotnet").read_text(encoding="utf-8"))

        self.installer.rollback(
            self.root.absolute(), self.receipt.absolute(), TEST_SDK_VERSION, remove_receipt=True
        )
        self.assertFalse((self.root / "sdk" / "10.0.402").exists())
        self.assertEqual("existing sdk\n", (self.root / "sdk" / "9.0.100" / "existing.dll").read_text())
        self.assertEqual(
            "existing pack\n",
            (self.root / "packs" / "Existing.Workload" / "1.0" / "pack.txt").read_text(),
        )
        self.assertEqual("existing host\n", (self.root / "dotnet").read_text())

    def test_conflict_fails_before_canonical_root_mutation(self) -> None:
        write(self.root / "sdk" / "10.0.402" / "sdk.dll", "unowned conflict\n")

        with self.assertRaises(SystemExit):
            self.install()

        self.assertEqual("unowned conflict\n", (self.root / "sdk" / "10.0.402" / "sdk.dll").read_text())
        self.assertFalse((self.root / "host" / "fxr" / "10.0.13").exists())
        self.assertFalse(self.receipt.exists())

    def test_interrupted_install_receipt_recovers_reserved_partial_file(self) -> None:
        self.install()
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        receipt["status"] = "installing"
        temporary = self.root / receipt["temporaryFiles"][0]["path"]
        write(temporary, "partial")
        self.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

        self.install()

        self.installer.verify(self.root.absolute(), self.receipt.absolute(), TEST_SDK_VERSION)
        self.assertFalse(temporary.exists())
        self.assertEqual("committed", json.loads(self.receipt.read_text())["status"])

    def test_uninstall_preflights_all_owned_files_before_removal(self) -> None:
        self.install()
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        created = [self.root / item["path"] for item in receipt["createdFiles"]]
        changed = self.root / "sdk" / "10.0.402" / "sdk.dll"
        changed.write_text("user change\n", encoding="utf-8")

        with self.assertRaises(SystemExit):
            self.installer.rollback(
                self.root.absolute(), self.receipt.absolute(), TEST_SDK_VERSION, remove_receipt=True
            )

        self.assertTrue(self.receipt.exists())
        self.assertTrue(all(path.exists() for path in created))
        self.assertEqual("user change\n", changed.read_text())

    def test_forged_receipt_path_cannot_escape_dotnet_root(self) -> None:
        self.install()
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        receipt["createdFiles"][0]["path"] = "../../../outside"
        self.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(SystemExit):
            self.installer.rollback(
                self.root.absolute(), self.receipt.absolute(), TEST_SDK_VERSION, remove_receipt=True
            )

        self.assertTrue(self.receipt.exists())

    def test_rollback_replays_an_unlink_that_preceded_parent_fsync(self) -> None:
        self.install()
        original = self.installer.fsync_directory
        interrupted = False

        def fail_once(path):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise RuntimeError("simulated power loss before parent fsync")
            return original(path)

        with mock.patch.object(self.installer, "fsync_directory", side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                self.installer.rollback(
                    self.root.absolute(),
                    self.receipt.absolute(),
                    TEST_SDK_VERSION,
                    remove_receipt=True,
                )

        self.installer.rollback(
            self.root.absolute(),
            self.receipt.absolute(),
            TEST_SDK_VERSION,
            remove_receipt=True,
        )
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.root / "sdk/10.0.402").exists())

    def test_release_relinquishes_receipt_but_preserves_shared_sdk_files(self) -> None:
        self.install()
        created = [
            self.root / item["path"]
            for item in json.loads(self.receipt.read_text(encoding="utf-8"))["createdFiles"]
        ]

        self.installer.release(
            self.root.absolute(), self.receipt.absolute(), TEST_SDK_VERSION
        )

        self.assertFalse(self.receipt.exists())
        self.assertTrue(all(path.is_file() for path in created))


class WorkloadTransactionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="workload-transaction-contract-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "dotnet"
        self.transaction = self.base / "receipt" / "workload-transaction"
        self.package_inventory = self.base / "toolchain.lock.json"
        self.package_inventory.write_text(
            json.dumps(
                {
                    "components": {
                        "linuxIosWorkload": {
                            "packages": [{"id": "IosPack", "version": "2.0"}]
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        write(self.root / "packs" / "Existing" / "1.0" / "payload", "existing\n")
        write(self.root / "sdk-manifests" / "10.0.100" / "base" / "1.0" / "manifest", "base\n")
        write(
            self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "android",
            "existing\n",
        )
        write(
            self.root / "metadata" / "workloads" / "X64" / "10.0.400" / "history" / "before.json",
            "history-before\n",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_transaction(self, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        arguments = [
                sys.executable,
                str(WORKLOAD_TRANSACTION),
                command,
                "--root",
                str(self.root),
                "--transaction",
                str(self.transaction),
            ]
        if command == "begin":
            arguments.extend(
                [
                    "--package-inventory",
                    str(self.package_inventory),
                    "--sdk-feature-band",
                    "10.0.400",
                    "--workload-version",
                    "10.0.401.1",
                    "--workload-manifest",
                    "2.0/10.0.100",
                    "--workload-id",
                    "ios",
                    "--control-global-json",
                    str(self.base / "control/global.json"),
                ]
            )
        return subprocess.run(
            arguments,
            check=check,
            capture_output=True,
            text=True,
        )

    def add_ios_state(self) -> None:
        write(self.root / "packs" / "IosPack" / "2.0" / "payload", "ios\n")
        write(self.root / "template-packs" / "iospack.2.0.nupkg", "template\n")
        write(
            self.root
            / "sdk-manifests"
            / "10.0.100"
            / "Microsoft.NET.Sdk.iOS"
            / "2.0"
            / "manifest",
            "ios\n",
        )
        write(
            self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "ios",
            "installed\n",
        )
        write(
            self.root
            / "metadata/workloads/X64/10.0.400/globaljsonworkloadsets.json",
            json.dumps({str(self.base / "control/global.json"): "10.0.401.1"})
            + "\n",
        )

    def test_prepared_rollback_restores_original_state(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        write(
            self.root / "metadata" / "workloads" / "X64" / "10.0.400" / "history" / "failed.json",
            "failed-history\n",
        )

        self.run_transaction("restore")

        self.assertFalse((self.root / "packs" / "IosPack").exists())
        self.assertFalse((self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "ios").exists())
        self.assertTrue((self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "android").is_file())
        self.assertTrue((self.root / "metadata" / "workloads" / "X64" / "10.0.400" / "history" / "before.json").is_file())
        self.assertTrue((self.root / "metadata" / "workloads" / "X64" / "10.0.400" / "history" / "failed.json").is_file())

    def test_uninstall_preserves_workload_installed_after_ios(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        self.run_transaction("commit")

        write(self.root / "packs" / "Later" / "3.0" / "payload", "later\n")
        write(
            self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "wasm-tools",
            "later\n",
        )
        self.run_transaction("verify")
        self.run_transaction("prepare-uninstall")
        (self.root / "packs" / "IosPack" / "2.0" / "payload").unlink()
        (self.root / "packs" / "IosPack" / "2.0").rmdir()
        (self.root / "template-packs" / "iospack.2.0.nupkg").unlink()
        (self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "ios").unlink()

        self.run_transaction("restore")

        self.assertTrue((self.root / "packs" / "Later" / "3.0" / "payload").is_file())
        self.assertTrue((self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "wasm-tools").is_file())
        self.assertTrue((self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "android").is_file())
        self.assertFalse(
            (
                self.root
                / "sdk-manifests"
                / "10.0.100"
                / "Microsoft.NET.Sdk.iOS"
            ).exists()
        )
        self.assertFalse(self.transaction.exists())

    def test_modified_owned_pack_blocks_restore_before_any_removal(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        self.run_transaction("commit")
        changed = self.root / "packs" / "IosPack" / "2.0" / "payload"
        changed.write_text("changed\n", encoding="utf-8")

        result = self.run_transaction("restore", check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("changed since setup", result.stderr)
        self.assertTrue((self.root / "template-packs" / "iospack.2.0.nupkg").is_file())
        self.assertTrue((self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "ios").is_file())
        self.assertTrue(self.transaction.exists())

    def test_restoring_transaction_resumes_after_partial_metadata_restore(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        self.run_transaction("commit")
        state_path = self.transaction / "transaction.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        transaction_module = load_script(
            WORKLOAD_TRANSACTION, f"workload_transaction_{id(self)}"
        )
        state["restorePreviousStatus"] = "committed"
        state["restoreOps"] = transaction_module.build_restore_ops(
            self.root,
            state,
            state["createdUnits"],
            state["snapshotChanges"],
            state.get("sharedJsonPatches", []),
            remove_units=True,
        )
        state["restoreNext"] = 0
        state["status"] = "restoring"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        ios_marker = self.root / "metadata/workloads/10.0.400/InstalledWorkloads/ios"
        ios_marker.unlink()

        self.run_transaction("restore")

        self.assertFalse(self.transaction.exists())
        self.assertFalse((self.root / "packs/IosPack").exists())
        self.assertFalse(ios_marker.exists())
        self.assertTrue(
            (self.root / "metadata/workloads/10.0.400/InstalledWorkloads/android").is_file()
        )

    def test_restore_replays_operation_completed_before_cursor_advance(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        self.run_transaction("commit")
        transaction_module = load_script(
            WORKLOAD_TRANSACTION, f"workload_transaction_fault_{id(self)}"
        )
        original = transaction_module.execute_restore_op

        def interrupt_after_operation(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated termination before cursor advance")

        with mock.patch.object(
            transaction_module, "execute_restore_op", side_effect=interrupt_after_operation
        ):
            with self.assertRaises(RuntimeError):
                transaction_module.restore(
                    self.root.absolute(),
                    self.transaction.absolute(),
                    verify_committed=True,
                    retain=True,
                )

        state = json.loads((self.transaction / "transaction.json").read_text(encoding="utf-8"))
        self.assertEqual("restoring", state["status"])
        self.assertEqual(0, state["restoreNext"])
        transaction_module.restore(
            self.root.absolute(),
            self.transaction.absolute(),
            verify_committed=True,
            retain=False,
        )
        self.assertFalse(self.transaction.exists())
        self.assertFalse((self.root / "packs/IosPack").exists())

    def test_concurrent_unrelated_metadata_is_rejected_but_preserved_on_rollback(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        concurrent = (
            self.root
            / "metadata/workloads/10.0.400/InstalledWorkloads/wasm-tools"
        )
        write(concurrent, "concurrent\n")

        result = self.run_transaction("commit", check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("outside this installation", result.stderr)
        self.run_transaction("restore")
        self.assertTrue(concurrent.is_file())
        self.assertFalse(
            (
                self.root
                / "metadata/workloads/10.0.400/InstalledWorkloads/ios"
            ).exists()
        )
        self.assertFalse((self.root / "packs/IosPack").exists())

    def test_shared_global_json_restores_only_the_owned_key(self) -> None:
        shared = (
            self.root
            / "metadata/workloads/X64/10.0.400/globaljsonworkloadsets.json"
        )
        write(shared, json.dumps({"/other/global.json": "10.0.400.1"}) + "\n")
        self.run_transaction("begin")
        self.add_ios_state()
        shared.write_text(
            json.dumps(
                {
                    "/other/global.json": "10.0.499.1",
                    str(self.base / "control/global.json"): "10.0.401.1",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_transaction("commit", check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("key:/other/global.json", result.stderr)
        self.run_transaction("restore")
        self.assertEqual(
            {"/other/global.json": "10.0.499.1"},
            json.loads(shared.read_text(encoding="utf-8")),
        )

    def test_same_workload_set_consumer_preserves_exact_shared_resources(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        shared = (
            self.root
            / "metadata/workloads/X64/10.0.400/globaljsonworkloadsets.json"
        )
        write(
            shared,
            json.dumps(
                {
                    str(self.base / "control/global.json"): "10.0.401.1",
                    "/other/global.json": "10.0.401.1",
                }
            )
            + "\n",
        )

        result = self.run_transaction("commit", check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("key:/other/global.json", result.stderr)
        self.run_transaction("restore")
        self.assertTrue((self.root / "packs/IosPack/2.0/payload").is_file())
        self.assertTrue(
            (
                self.root
                / "sdk-manifests/10.0.100/Microsoft.NET.Sdk.iOS/2.0/manifest"
            ).is_file()
        )
        self.assertTrue(
            (
                self.root
                / "metadata/workloads/10.0.400/InstalledWorkloads/ios"
            ).is_file()
        )
        self.assertEqual(
            {"/other/global.json": "10.0.401.1"},
            json.loads(shared.read_text(encoding="utf-8")),
        )

    def test_restore_replays_atomic_replace_that_preceded_parent_fsync(self) -> None:
        owned = (
            self.root
            / "sdk-manifests/10.0.100/Microsoft.NET.Sdk.iOS/2.0/manifest"
        )
        write(owned, "before\n")
        self.run_transaction("begin")
        owned.write_text("after\n", encoding="utf-8")
        write(
            self.root
            / "metadata/workloads/X64/10.0.400/globaljsonworkloadsets.json",
            json.dumps({str(self.base / "control/global.json"): "10.0.401.1"})
            + "\n",
        )
        self.run_transaction("commit")
        transaction_module = load_script(
            WORKLOAD_TRANSACTION, f"workload_transaction_replace_fault_{id(self)}"
        )
        original = transaction_module.fsync_directory
        interrupted = False

        def fail_once(path):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise RuntimeError("simulated power loss before replace fsync")
            return original(path)

        with mock.patch.object(
            transaction_module, "fsync_directory", side_effect=fail_once
        ):
            with self.assertRaises(RuntimeError):
                transaction_module.restore(
                    self.root.absolute(),
                    self.transaction.absolute(),
                    verify_committed=True,
                    retain=True,
                )

        self.assertEqual("before\n", owned.read_text(encoding="utf-8"))
        transaction_module.restore(
            self.root.absolute(),
            self.transaction.absolute(),
            verify_committed=True,
            retain=False,
        )
        self.assertEqual("before\n", owned.read_text(encoding="utf-8"))

    def test_restore_holds_the_sdk_interoperable_shared_metadata_lock(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        self.run_transaction("commit")
        transaction_module = load_script(
            WORKLOAD_TRANSACTION, f"workload_transaction_lock_{id(self)}"
        )
        shared = (
            self.root
            / "metadata/workloads/X64/10.0.400/globaljsonworkloadsets.json"
        )
        original = transaction_module.execute_restore_op
        checked = False

        def assert_locked(*args, **kwargs):
            nonlocal checked
            if not checked:
                contender = os.open(shared, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(contender)
                checked = True
            return original(*args, **kwargs)

        with mock.patch.object(
            transaction_module, "execute_restore_op", side_effect=assert_locked
        ):
            transaction_module.restore(
                self.root.absolute(),
                self.transaction.absolute(),
                verify_committed=True,
                retain=False,
            )
        self.assertTrue(checked)

    def test_restoring_plan_stops_destructive_ops_for_a_late_shared_consumer(self) -> None:
        self.run_transaction("begin")
        self.add_ios_state()
        self.run_transaction("commit")
        transaction_module = load_script(
            WORKLOAD_TRANSACTION, f"workload_transaction_late_consumer_{id(self)}"
        )
        state_path = self.transaction / "transaction.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["restorePreviousStatus"] = "committed"
        state["restoreOps"] = transaction_module.build_restore_ops(
            self.root,
            state,
            state["createdUnits"],
            state["snapshotChanges"],
            state.get("sharedJsonPatches", []),
            remove_units=True,
        )
        state["restoreNext"] = 0
        state["status"] = "restoring"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        shared = (
            self.root
            / "metadata/workloads/X64/10.0.400/globaljsonworkloadsets.json"
        )
        shared.write_text(
            json.dumps(
                {
                    str(self.base / "control/global.json"): "10.0.401.1",
                    "/late/global.json": "10.0.401.1",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        transaction_module.restore(
            self.root.absolute(),
            self.transaction.absolute(),
            verify_committed=True,
            retain=False,
        )

        self.assertTrue((self.root / "packs/IosPack/2.0/payload").is_file())
        self.assertEqual(
            {"/late/global.json": "10.0.401.1"},
            json.loads(shared.read_text(encoding="utf-8")),
        )


class TopLevelInstallerContracts(unittest.TestCase):
    def run_context(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CONTEXT_RESOLVER), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_install_context_reuses_latest_sdk_patch_in_feature_band(self) -> None:
        with tempfile.TemporaryDirectory(prefix="install-context-sdk-") as temporary:
            root = Path(temporary)
            for version in ("10.0.402", "10.0.499", "10.0.500", "11.0.100"):
                (root / "sdk" / version).mkdir(parents=True)

            result = self.run_context(
                "sdk",
                "--root",
                str(root),
                "--feature-band",
                "10.0.400",
                "--fallback",
                "10.0.402",
            )

            self.assertEqual(
                {"sdk_source": "preexisting", "sdk_version": "10.0.499"},
                json.loads(result.stdout),
            )

    def test_install_context_selects_workload_set_matching_ios_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="install-context-workload-") as temporary:
            root = Path(temporary)
            workloadsets = root / "sdk-manifests" / "10.0.400" / "workloadsets"
            for version, ios in (
                ("10.0.401.1", "27.0.10722/10.0.100"),
                ("10.0.402.1", "27.1.10018/10.0.100"),
            ):
                path = workloadsets / version / "microsoft.net.workloads.workloadset.json"
                write(path, json.dumps({"Microsoft.NET.Sdk.iOS": ios}) + "\n")
            write(
                root / "metadata/workloads/X64/10.0.400/InstallState/default.json",
                json.dumps({"workloadVersion": "10.0.402.1"}) + "\n",
            )

            result = self.run_context(
                "workload",
                "--root",
                str(root),
                "--feature-band",
                "10.0.400",
                "--manifest-version",
                "27.0.10722",
                "--manifest-suffix",
                "10.0.100",
                "--fallback",
                "10.0.401.1",
            )

            self.assertEqual(
                {"workload_source": "installed-compatible", "workload_version": "10.0.401.1"},
                json.loads(result.stdout),
            )

    def test_xcode_compatibility_uses_workload_major_minor(self) -> None:
        compatible = self.run_context(
            "xcode-compatible", "--actual", "27.0.1", "--recommended", "27.0"
        )
        self.assertEqual("27.0", json.loads(compatible.stdout)["actual_xcode_family"])

        incompatible = self.run_context(
            "xcode-compatible",
            "--actual",
            "27.1",
            "--recommended",
            "27.0",
            check=False,
        )
        self.assertNotEqual(0, incompatible.returncode)
        self.assertIn("requires Xcode 27.0", incompatible.stderr)

    def test_recommended_xcode_is_read_from_selected_ios_sdk_pack(self) -> None:
        with tempfile.TemporaryDirectory(prefix="install-context-xcode-") as temporary:
            root = Path(temporary)
            lock = json.loads((REPOSITORY / "toolchain.lock.json").read_text(encoding="utf-8"))
            sdk_version = lock["components"]["appleSdk"]["iosSdkVersion"]
            package = next(
                item
                for item in lock["components"]["linuxIosWorkload"]["packages"]
                if item["id"].startswith("Microsoft.iOS.Sdk.")
                and item["id"].endswith(f"_{sdk_version}")
            )
            props = root / package["installedArchive"]
            props = props.parent / "targets" / "Microsoft.iOS.Sdk.Versions.props"
            write(
                props,
                "<Project><PropertyGroup><RecommendedXcodeVersion>27.0</RecommendedXcodeVersion>"
                "</PropertyGroup></Project>\n",
            )

            result = self.run_context(
                "recommended-xcode",
                "--root",
                str(root),
                "--lock",
                str(REPOSITORY / "toolchain.lock.json"),
            )

            self.assertEqual("27.0", json.loads(result.stdout)["recommended_xcode_version"])

    def test_verified_download_uses_reserved_file_and_no_clobber_publication(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verified-download-contract-") as temporary:
            base = Path(temporary)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            write(
                fake_curl,
                """#!/usr/bin/env bash
set -euo pipefail
output=""
while (($#)); do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'downloaded payload\n' > "$output"
if [[ -n "${COLLISION_OUTPUT:-}" ]]; then
  printf 'competing payload\n' > "$COLLISION_OUTPUT"
fi
""",
                0o755,
            )
            expected = hashlib.sha256(b"downloaded payload\n").hexdigest()
            output = base / "cache" / "archive.tar.gz"
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            command = '. "$1"; download_verified https://example.invalid/archive sha256sum "$2" "$3" false'

            subprocess.run(
                ["bash", "-c", command, "download-test", str(DOWNLOAD_HELPER), expected, str(output)],
                check=True,
                env=environment,
            )
            self.assertEqual(b"downloaded payload\n", output.read_bytes())
            self.assertEqual([], list(output.parent.glob("archive.tar.gz.part.*")))

            output.unlink()
            environment["COLLISION_OUTPUT"] = str(output)
            result = subprocess.run(
                ["bash", "-c", command, "download-test", str(DOWNLOAD_HELPER), expected, str(output)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(b"competing payload\n", output.read_bytes())
            self.assertEqual([], list(output.parent.glob("archive.tar.gz.part.*")))

    def test_verified_download_offline_and_symlink_inputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verified-download-offline-") as temporary:
            base = Path(temporary)
            output = base / "missing"
            command = '. "$1"; download_verified https://example.invalid/archive sha256sum "$2" "$3" true'
            result = subprocess.run(
                ["bash", "-c", command, "download-test", str(DOWNLOAD_HELPER), "0" * 64, str(output)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output.exists())

            target = base / "target"
            target.write_bytes(b"payload")
            output.symlink_to(target)
            expected = hashlib.sha256(target.read_bytes()).hexdigest()
            result = subprocess.run(
                ["bash", "-c", command, "download-test", str(DOWNLOAD_HELPER), expected, str(output)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("non-symlink", result.stderr)

            cache_target = base / "cache-target"
            cache_target.mkdir()
            cache_link = base / "cache-link"
            cache_link.symlink_to(cache_target, target_is_directory=True)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    command,
                    "download-test",
                    str(DOWNLOAD_HELPER),
                    "0" * 64,
                    str(cache_link / "archive"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("publication directory must not be a symlink", result.stderr)
            self.assertFalse((cache_target / "archive").exists())

    def test_dry_run_is_mutation_free_and_does_not_disclose_archive_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="installer-dry-run-contract-") as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            xcode = base / "private" / "Xcode_27.xip"
            write(xcode, "not inspected during dry-run\n")
            aot = base / "aot-pack"
            aot.mkdir()
            data = base / "data"
            cache = base / "cache"
            environment = os.environ.copy()
            environment.update({"HOME": str(home), "XDG_DATA_HOME": str(data), "XDG_CACHE_HOME": str(cache)})

            result = subprocess.run(
                [
                    str(TOP_LEVEL_INSTALLER),
                    "install",
                    "--xcode-archive",
                    str(xcode),
                    "--aot-pack",
                    str(aot),
                    "--offline",
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertIn("command=install", result.stdout)
            self.assertIn("xcode_archive=Xcode_27.xip", result.stdout)
            self.assertNotIn(str(xcode.parent), result.stdout)
            self.assertEqual([], list(home.iterdir()))
            self.assertFalse(data.exists())
            self.assertFalse(cache.exists())

    def test_dry_run_resolves_host_dotnet_and_reports_required_privilege(self) -> None:
        with tempfile.TemporaryDirectory(prefix="installer-host-dotnet-contract-") as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            host_root = base / "system dotnet"
            write(host_root / "dotnet", "#!/usr/bin/env bash\nexit 0\n", 0o755)
            (host_root / "sdk" / "10.0.499").mkdir(parents=True)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            (fake_bin / "dotnet").symlink_to(host_root / "dotnet")
            xcode = base / "Xcode_27.xip"
            write(xcode, "not inspected during dry-run\n")
            aot = base / "aot-pack"
            aot.mkdir()
            host_root.chmod(0o555)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "XDG_DATA_HOME": str(base / "data"),
                    "XDG_CACHE_HOME": str(base / "cache"),
                }
            )

            result = subprocess.run(
                [
                    str(TOP_LEVEL_INSTALLER),
                    "install",
                    "--xcode-archive",
                    str(xcode),
                    "--aot-pack",
                    str(aot),
                    "--dry-run",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertIn(f"dotnet_root={host_root}", result.stdout)
            self.assertIn(f"dotnet_executable={host_root / 'dotnet'}", result.stdout)
            self.assertIn("dotnet_sdk=10.0.499", result.stdout)
            self.assertIn("privilege_mode=system-helper", result.stdout)
            self.assertIn("sudo_required=true", result.stdout)
            self.assertEqual([], list(home.iterdir()))
            self.assertFalse((base / "data").exists())
            self.assertFalse((base / "cache").exists())

    def test_application_projects_remain_byte_identical(self) -> None:
        expected = {
            "tests/HelloIos/HelloIos.csproj": "c784ddf1e500e09b891d2d5b3f3c1bffef4a7402941807ee518c642f45104bac",
            "tests/AotProbe/AotProbe.csproj": "22df32a650b4e6361fbb96cb491d33358fa3efc60e70f6d06f9277999417d917",
        }
        for relative, digest in expected.items():
            self.assertEqual(digest, hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest())

    def test_command_specific_options_fail_closed_without_mutation(self) -> None:
        cases = [
            ("doctor", "--yes"),
            ("doctor", "--jobs", "2"),
            ("uninstall", "--offline"),
            ("install", "--purge-cache"),
        ]
        with tempfile.TemporaryDirectory(prefix="installer-options-contract-") as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "XDG_DATA_HOME": str(base / "data"),
                    "XDG_CACHE_HOME": str(base / "cache"),
                }
            )
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [str(TOP_LEVEL_INSTALLER), *arguments],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertEqual([], list(home.iterdir()))
                    self.assertFalse((base / "data").exists())
                    self.assertFalse((base / "cache").exists())

    def test_unowned_cache_root_is_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="installer-cache-ownership-") as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            cache_root = base / "cache" / "dotnet-ios-linux"
            write(cache_root / "unrelated", "preserve\n")
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "XDG_DATA_HOME": str(base / "data"),
                    "XDG_CACHE_HOME": str(base / "cache"),
                }
            )
            result = subprocess.run(
                [str(TOP_LEVEL_INSTALLER), "doctor"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("cache root exists without a valid ownership marker", result.stderr)
            self.assertEqual("preserve\n", (cache_root / "unrelated").read_text(encoding="utf-8"))
            self.assertFalse((cache_root / ".dotnet-ios-linux-cache.json").exists())

    def test_workload_install_relies_on_private_global_json_pin(self) -> None:
        installer = TOP_LEVEL_INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn('"$dotnet_root/dotnet" workload update \\\n', installer)
        self.assertIn('"$dotnet_root/dotnet" workload install ios \\\n', installer)
        self.assertIn('"workloadVersion":sys.argv[3]', installer)
        self.assertNotIn('"$dotnet_root/dotnet" workload uninstall ios', installer)
        self.assertNotIn(
            '"workload", "uninstall", "ios"',
            SYSTEM_WORKLOAD_HELPER.read_text(encoding="utf-8"),
        )

    def test_success_output_selects_bash_or_zsh_startup_guidance(self) -> None:
        installer = TOP_LEVEL_INSTALLER.read_text(encoding="utf-8")
        success_output = installer.split("printf 'install=passed\\n'", maxsplit=1)[1]

        self.assertIn('bash) startup_file="$HOME/.bashrc"', installer)
        self.assertIn('zsh) startup_file="$HOME/.zshrc"', installer)
        self.assertIn(
            "printf '  export PATH=%q:\"$PATH\"\\n' \"$script_dir\"",
            installer,
        )
        self.assertIn("print_shell_setup", success_output)

    def test_system_root_elevates_only_the_installed_helper(self) -> None:
        installer = TOP_LEVEL_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('host_dotnet="$(command -v dotnet || true)"', installer)
        self.assertIn('"$sudo_executable" -- "$installed_system_helper"', installer)
        self.assertIn('#!/usr/bin/python3 -I', SYSTEM_WORKLOAD_HELPER.read_text(encoding="utf-8"))
        self.assertLess(
            installer.index("((EUID != 0))"),
            installer.index('. "$script_dir/eng/install/download-verified.sh"'),
        )
        self.assertNotIn("sudo -E", installer)
        self.assertNotIn('"$sudo_executable" -- "$python_executable"', installer)
        self.assertNotIn("sudo ./install.sh", installer)
        fast_path = installer.index("if system_helper_matches_sources; then")
        self.assertIn(
            "sync_system_helper_files",
            installer[fast_path : fast_path + 120],
        )

    def test_activation_handles_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="activation contract ") as temporary:
            base = Path(temporary)
            data = base / "data root"
            dotnet = base / "home root" / ".dotnet"
            activation = data / "activate"
            subprocess.run(
                [
                    sys.executable,
                    str(ACTIVATION_WRITER),
                    "--output",
                    str(activation),
                    "--data-root",
                    str(data),
                    "--dotnet-root",
                    str(dotnet),
                    "--install-id",
                    "0.1.0-preview.1-sdk-10.0.402",
                    "--project-version",
                    "0.1.0-preview.1",
                    "--swift-version",
                    "6.4.0",
                ],
                check=True,
            )
            command = 'PATH=/usr/bin; source "$1"; printf "%s\\n%s\\n%s\\n%s\\n" "$DOTNET_IOS_LINUX_ROOT" "$DOTNET_ROOT" "$DOTNET_IOS_LINUX_CLANG" "$PATH"'
            result = subprocess.run(
                ["bash", "-c", command, "activation-test", str(activation)],
                check=True,
                capture_output=True,
                text=True,
            )
            values = result.stdout.splitlines()
            self.assertEqual(str(data.absolute()), values[0])
            self.assertEqual(str(dotnet.absolute()), values[1])
            self.assertEqual(
                str(data.absolute() / "toolchains/0.1.0-preview.1-sdk-10.0.402/swift-6.4.0/usr/bin/clang"),
                values[2],
            )
            self.assertNotIn("\\ ", values[3])


class DurableAssetContracts(unittest.TestCase):
    def test_record_flushes_asset_tree_before_receipt_update(self) -> None:
        with tempfile.TemporaryDirectory(prefix="durable-assets-record-fsync-") as temporary:
            root = Path(temporary) / "data"
            receipt = root / "receipts/install.json"
            write(root / "activate", "owned\n")
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                json.dumps({"owner": "dotnet-ios-linux", "dataRoot": str(root)})
                + "\n",
                encoding="utf-8",
            )
            durable = load_script(DURABLE_ASSETS, f"durable_assets_record_{id(root)}")
            calls: list[str] = []
            original_sync = durable.fsync_tree
            original_write = durable.write_receipt

            def sync_tree(path):
                calls.append("sync")
                return original_sync(path)

            def write_receipt(path, value):
                calls.append("receipt")
                return original_write(path, value)

            with mock.patch.object(
                durable, "fsync_tree", side_effect=sync_tree
            ), mock.patch.object(
                durable, "write_receipt", side_effect=write_receipt
            ):
                durable.record(root, receipt)
            self.assertEqual(["sync", "receipt"], calls)

    def test_uninstall_replays_an_unlink_that_preceded_parent_fsync(self) -> None:
        with tempfile.TemporaryDirectory(prefix="durable-assets-fsync-") as temporary:
            root = Path(temporary) / "data"
            receipt = root / "receipts/install.json"
            owned = root / "activate"
            write(owned, "owned\n")
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                json.dumps({"owner": "dotnet-ios-linux", "dataRoot": str(root)})
                + "\n",
                encoding="utf-8",
            )
            durable = load_script(DURABLE_ASSETS, f"durable_assets_{id(root)}")
            durable.record(root, receipt)
            original = durable.fsync_directory
            interrupted = False

            def fail_once(path):
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    raise RuntimeError("simulated power loss before parent fsync")
                return original(path)

            with mock.patch.object(durable, "fsync_directory", side_effect=fail_once):
                with self.assertRaises(RuntimeError):
                    durable.uninstall(root, receipt)

            durable.uninstall(root, receipt, allow_missing=True)
            self.assertFalse(owned.exists())

    def test_uninstall_refuses_modified_owned_file_and_preserves_unknown_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="durable-assets-contract-") as temporary:
            base = Path(temporary)
            root = base / "data"
            receipt = root / "receipts" / "install.json"
            write(root / "toolchains" / "locked" / "tool", "owned\n", 0o755)
            write(root / "activate", "owned activation\n")
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                json.dumps({"owner": "dotnet-ios-linux", "dataRoot": str(root)}) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, str(DURABLE_ASSETS), "record", "--root", str(root), "--receipt", str(receipt)],
                check=True,
                capture_output=True,
                text=True,
            )
            write(root / "notes.txt", "not owned\n")
            (root / "activate").write_text("changed\n", encoding="utf-8")

            refused = subprocess.run(
                [sys.executable, str(DURABLE_ASSETS), "uninstall", "--root", str(root), "--receipt", str(receipt)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, refused.returncode)
            self.assertTrue((root / "toolchains" / "locked" / "tool").is_file())

            (root / "activate").write_text("owned activation\n", encoding="utf-8")
            subprocess.run(
                [sys.executable, str(DURABLE_ASSETS), "uninstall", "--root", str(root), "--receipt", str(receipt)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertFalse((root / "activate").exists())
            self.assertFalse((root / "toolchains" / "locked" / "tool").exists())
            self.assertEqual("not owned\n", (root / "notes.txt").read_text())

            subprocess.run(
                [
                    sys.executable,
                    str(DURABLE_ASSETS),
                    "uninstall",
                    "--allow-missing",
                    "--root",
                    str(root),
                    "--receipt",
                    str(receipt),
                ],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
