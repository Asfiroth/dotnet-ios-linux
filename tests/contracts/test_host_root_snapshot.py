#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
SNAPSHOTTER = REPOSITORY / "eng" / "install" / "host-root-snapshot.py"
INSTALLER = REPOSITORY / "install.sh"


def write(path: Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


class HostRootSnapshotContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="host-root-snapshot-contract-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "dotnet"
        write(self.root / "dotnet", "host executable\n", 0o755)
        write(self.root / "packs" / "Example.Pack" / "1.0" / "payload", "pack\n")
        write(
            self.root / "metadata" / "workloads" / "10.0.400" / "InstalledWorkloads" / "android",
            "installed\n",
        )
        write(
            self.root
            / "metadata"
            / "workloads"
            / "X64"
            / "10.0.400"
            / "InstalledWorkloads"
            / "maui-android",
            "installed\n",
        )
        write(
            self.root / "sdk" / "10.0.402" / "version.txt",
            "10.0.402\n",
        )
        (self.root / "sdk" / "current").symlink_to("10.0.402", target_is_directory=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_snapshotter(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SNAPSHOTTER), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_inventory_refuses_requested_workloads_that_would_invent_marker_state(self) -> None:
        result = self.run_snapshotter(
            "inventory",
            "--root",
            str(self.root),
            "--required-workload",
            "ios",
            "--required-workload",
            "tvos",
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("no longer accepts requested workload IDs", result.stderr)

    def test_snapshot_is_external_verified_and_preserves_the_full_tree(self) -> None:
        destination = self.base / "rollback" / "before-migration"
        destination.parent.mkdir()

        result = self.run_snapshotter(
            "snapshot", "--root", str(self.root), "--output", str(destination)
        )

        receipt = json.loads((destination / "snapshot.json").read_text(encoding="utf-8"))
        self.assertIn(f"rollback_snapshot={destination}", result.stdout)
        self.assertEqual(str(self.root), receipt["dotnetRoot"])
        self.assertEqual(["android", "maui-android"], receipt["workloadInventory"]["baselineWorkloads"])
        self.assertTrue((destination / "payload.tar").is_file())
        self.run_snapshotter("verify", "--root", str(self.root), "--snapshot", str(destination))

        entries = {entry["path"]: entry for entry in receipt["entries"]}
        self.assertEqual("file", entries["dotnet"]["kind"])
        self.assertEqual(0o755, entries["dotnet"]["mode"])
        self.assertEqual("symlink", entries["sdk/current"]["kind"])
        self.assertEqual("10.0.402", entries["sdk/current"]["target"])
        self.assertEqual("pack\n", (self.root / "packs/Example.Pack/1.0/payload").read_text())

    def test_snapshot_refuses_a_destination_inside_the_host_root(self) -> None:
        destination = self.root / "rollback"

        result = self.run_snapshotter(
            "snapshot", "--root", str(self.root), "--output", str(destination), check=False
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("outside the host .NET root", result.stderr)
        self.assertFalse(destination.exists())

    def test_verify_detects_any_post_snapshot_host_root_drift(self) -> None:
        destination = self.base / "rollback" / "before-migration"
        destination.parent.mkdir()
        self.run_snapshotter("snapshot", "--root", str(self.root), "--output", str(destination))
        write(self.root / "packs" / "Example.Pack" / "1.0" / "payload", "changed\n")

        result = self.run_snapshotter(
            "verify", "--root", str(self.root), "--snapshot", str(destination), check=False
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("no longer matches the rollback snapshot", result.stderr)

    def test_restore_replaces_the_changed_root_from_the_verified_snapshot(self) -> None:
        destination = self.base / "rollback" / "before-migration"
        destination.parent.mkdir()
        self.run_snapshotter("snapshot", "--root", str(self.root), "--output", str(destination))
        write(self.root / "packs" / "Example.Pack" / "1.0" / "payload", "changed\n")
        write(self.root / "packs" / "MigrationOnly" / "1.0" / "payload", "new\n")

        result = self.run_snapshotter(
            "restore",
            "--root",
            str(self.root),
            "--snapshot",
            str(destination),
            "--yes",
        )

        self.assertIn("rollback_restore=passed", result.stdout)
        self.assertEqual("pack\n", (self.root / "packs/Example.Pack/1.0/payload").read_text())
        self.assertFalse((self.root / "packs/MigrationOnly").exists())
        self.assertEqual("10.0.402", os.readlink(self.root / "sdk/current"))
        self.run_snapshotter("verify", "--root", str(self.root), "--snapshot", str(destination))

    def test_installer_preflight_is_read_only_without_snapshot_option(self) -> None:
        feature_band = "10.0.400"
        workload_set = "10.0.401.1"
        write(
            self.root
            / "metadata/workloads/X64/10.0.400/InstallState/default.json",
            json.dumps({"workloadVersion": workload_set}) + "\n",
        )
        write(
            self.root
            / "sdk-manifests/10.0.400/workloadsets/10.0.401.1/microsoft.net.workloads.workloadset.json",
            json.dumps(
                {
                    "Microsoft.NET.Sdk.Android": "36.1.69/10.0.100",
                    "Microsoft.NET.Sdk.Maui": "10.0.110/10.0.100",
                }
            )
            + "\n",
        )
        write(
            self.root
            / "sdk-manifests/10.0.100/microsoft.net.sdk.android/36.1.69/WorkloadManifest.json",
            json.dumps({"workloads": {"android": {"packs": ["Microsoft.Android.Sdk"]}}})
            + "\n",
        )
        write(
            self.root
            / "sdk-manifests/10.0.100/microsoft.net.sdk.maui/10.0.110/WorkloadManifest.json",
            json.dumps(
                {
                    "workloads": {
                        "maui-core": {"abstract": True, "packs": ["Microsoft.Maui.Core"]},
                        "maui-android": {"extends": ["maui-core", "android"]},
                    }
                }
            )
            + "\n",
        )
        home = self.base / "home"
        home.mkdir()
        fake_bin = self.base / "bin"
        fake_bin.mkdir()
        (fake_bin / "dotnet").symlink_to(self.root / "dotnet")
        data = self.base / "data"
        cache = self.base / "cache"
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "XDG_DATA_HOME": str(data),
                "XDG_CACHE_HOME": str(cache),
            }
        )

        result = subprocess.run(
            [str(INSTALLER), "preflight"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

        value = json.loads(result.stdout)
        self.assertEqual(str(self.root), value["dotnetRoot"])
        self.assertEqual(
            ["android", "maui-android"],
            value["baselineWorkloads"],
        )
        self.assertEqual([], value["installedAppleWorkloads"])
        self.assertEqual([], value["referenceOnlyWorkloads"])
        self.assertNotIn("desiredWorkloads", value)
        self.assertFalse(data.exists())
        self.assertFalse(cache.exists())

    def test_preflight_never_uses_a_tmpdir_inside_the_active_root(self) -> None:
        home = self.base / "home"
        home.mkdir()
        fake_bin = self.base / "bin"
        fake_bin.mkdir()
        (fake_bin / "dotnet").symlink_to(self.root / "dotnet")
        temporary_inside_root = self.root / "temporary"
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "TMPDIR": str(temporary_inside_root),
                "XDG_DATA_HOME": str(self.base / "data"),
                "XDG_CACHE_HOME": str(self.base / "cache"),
            }
        )

        result = subprocess.run(
            [str(INSTALLER), "preflight"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        # The fixture has markers but intentionally no manifest graph. The
        # important contract is that preflight fails without creating its
        # caller-controlled temporary directory under the active root.
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(temporary_inside_root.exists())


if __name__ == "__main__":
    unittest.main()
