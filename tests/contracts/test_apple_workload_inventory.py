#!/usr/bin/env python3

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
INVENTORY = REPOSITORY / "eng" / "install" / "apple-workload-inventory.py"


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class AppleWorkloadInventoryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="apple-workload-inventory-contract-")
        self.root = Path(self.temporary.name) / "dotnet"
        self.root.mkdir()
        self.band = "10.0.400"
        self.workload_set = "10.0.401.1"
        write(
            self.root / f"metadata/workloads/X64/{self.band}/InstallState/default.json",
            json.dumps({"workloadVersion": self.workload_set}) + "\n",
        )
        self.mapping: dict[str, str] = {}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_manifest(
        self,
        package_id: str,
        version: str,
        workloads: dict,
        dependencies: dict | None = None,
        *,
        trailing_comma: bool = False,
    ) -> None:
        self.mapping[package_id] = f"{version}/10.0.100"
        directory = self.root / "sdk-manifests/10.0.100" / package_id.lower() / version
        manifest = json.dumps({"version": version, "workloads": workloads}, indent=2)
        if trailing_comma:
            manifest = manifest.replace("\n  }\n}", ",\n  }\n}").replace("\n}", "\n}")
        write(directory / "WorkloadManifest.json", manifest + "\n")
        if dependencies is not None:
            write(directory / "WorkloadDependencies.json", json.dumps(dependencies, indent=2) + "\n")

    def write_mapping(self) -> None:
        write(
            self.root
            / f"sdk-manifests/{self.band}/workloadsets/{self.workload_set}/microsoft.net.workloads.workloadset.json",
            json.dumps(self.mapping, indent=2) + "\n",
        )

    def add_marker(self, workload: str) -> None:
        write(
            self.root / f"metadata/workloads/{self.band}/InstalledWorkloads/{workload}",
            "installed\n",
        )

    def run_inventory(self, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INVENTORY), "--root", str(self.root)],
            check=check,
            capture_output=True,
            text=True,
        )

    def add_graph(self) -> None:
        apple_dependencies = lambda alias, package: {
            package: {
                "workload": {"alias": [alias], "version": "1.0.0"},
                "xcode": {"recommendedVersion": "27.0"},
            }
        }
        self.add_manifest(
            "Microsoft.NET.Sdk.iOS",
            "27.0.10722",
            {"ios": {"packs": ["Microsoft.iOS.Sdk"]}},
            apple_dependencies("ios", "microsoft.net.sdk.ios"),
            trailing_comma=True,
        )
        self.add_manifest(
            "Microsoft.NET.Sdk.MacCatalyst",
            "27.0.10722",
            {"maccatalyst": {"packs": ["Microsoft.MacCatalyst.Sdk"]}},
            apple_dependencies("maccatalyst", "microsoft.net.sdk.maccatalyst"),
        )
        self.add_manifest(
            "Microsoft.NET.Sdk.macOS",
            "27.0.10722",
            {"macos": {"packs": ["Microsoft.macOS.Sdk"]}},
            apple_dependencies("macos", "microsoft.net.sdk.macos"),
        )
        self.add_manifest(
            "Microsoft.NET.Sdk.tvOS",
            "27.0.10722",
            {"tvos": {"packs": ["Microsoft.tvOS.Sdk"]}},
            apple_dependencies("tvos", "microsoft.net.sdk.tvos"),
        )
        self.add_manifest(
            "Microsoft.NET.Sdk.Android",
            "36.1.69",
            {"android": {"packs": ["Microsoft.Android.Sdk"]}},
        )
        self.add_manifest(
            "Microsoft.NET.Sdk.Maui",
            "10.0.110",
            {
                "maui": {"extends": ["maui-mobile", "maui-desktop"]},
                "apple-aggregate": {"extends": ["maccatalyst", "tvos"]},
                "maui-mobile": {"extends": ["maui-ios", "maui-android"]},
                "maui-desktop": {"extends": ["maui-maccatalyst", "maui-windows"]},
                "maui-core": {"abstract": True, "packs": ["Microsoft.Maui.Core"]},
                "maui-ios": {"extends": ["maui-core", "ios"]},
                "maui-android": {"extends": ["maui-core", "android"]},
                "maui-maccatalyst": {"extends": ["maui-core", "maccatalyst"]},
                "maui-windows": {
                    "extends": ["maui-core"],
                    "packs": ["Microsoft.Maui.Windows"],
                },
            },
        )
        self.write_mapping()

    def test_classifies_installed_markers_from_manifest_metadata_and_extends(self) -> None:
        self.add_graph()
        for workload in (
            "android",
            "apple-aggregate",
            "ios",
            "maccatalyst",
            "macos",
            "maui",
            "maui-ios",
            "maui-maccatalyst",
            "maui-desktop",
            "tvos",
        ):
            self.add_marker(workload)

        result = self.run_inventory()
        value = json.loads(result.stdout)
        classifications = {
            item["workloadId"]: item["classification"] for item in value["workloadRegistrations"]
        }

        self.assertEqual("linux-ios-runtime", classifications["ios"])
        self.assertEqual("ios-derived-unqualified", classifications["maui-ios"])
        self.assertEqual("reference-only", classifications["maccatalyst"])
        self.assertEqual("reference-only", classifications["apple-aggregate"])
        self.assertEqual("reference-only", classifications["maui-maccatalyst"])
        self.assertEqual("mixed-unqualified", classifications["maui-desktop"])
        self.assertEqual("reference-only", classifications["macos"])
        self.assertEqual("reference-only", classifications["tvos"])
        self.assertEqual("mixed-unqualified", classifications["maui"])
        self.assertEqual("non-apple", classifications["android"])
        self.assertEqual(["ios"], value["linuxRuntimeWorkloads"])
        self.assertEqual(
            ["apple-aggregate", "maccatalyst", "macos", "maui-maccatalyst", "tvos"],
            value["referenceOnlyWorkloads"],
        )
        self.assertEqual(["maui", "maui-desktop", "maui-ios"], value["unqualifiedAppleWorkloads"])
        self.assertEqual([], value["unknownWorkloads"])
        self.assertEqual("10.0.401.1", value["workloadRegistrations"][0]["workloadSet"])
        self.assertEqual("10.0.400", value["workloadRegistrations"][0]["workloadSetFeatureBand"])

    def test_unresolved_installed_workload_fails_closed(self) -> None:
        self.add_manifest(
            "Microsoft.NET.Sdk.iOS",
            "27.0.10722",
            {"ios": {"packs": ["Microsoft.iOS.Sdk"]}},
            {"microsoft.net.sdk.ios": {"workload": {"alias": ["ios"]}, "xcode": {}}},
        )
        self.write_mapping()
        self.add_marker("mystery")

        result = self.run_inventory(check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot safely classify", result.stderr)
        self.assertIn("mystery", result.stderr)

    def test_case_colliding_workload_set_manifest_ids_fail_closed(self) -> None:
        self.mapping = {
            "Microsoft.NET.Sdk.iOS": "27.0.10722/10.0.100",
            "microsoft.net.sdk.ios": "27.0.10722/10.0.100",
        }
        self.write_mapping()
        self.add_marker("ios")

        result = self.run_inventory(check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("case-colliding keys", result.stderr)

    def test_malformed_xcode_metadata_fails_closed(self) -> None:
        self.add_manifest(
            "Microsoft.NET.Sdk.iOS",
            "27.0.10722",
            {"ios": {"packs": ["Microsoft.iOS.Sdk"]}},
            {"microsoft.net.sdk.ios": {"workload": {"alias": ["ios"]}, "xcode": "27.0"}},
        )
        self.write_mapping()
        self.add_marker("ios")

        result = self.run_inventory(check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid Xcode metadata", result.stderr)

    def test_malformed_dependency_record_fails_closed(self) -> None:
        self.add_manifest(
            "Microsoft.NET.Sdk.iOS",
            "27.0.10722",
            {"ios": {"packs": ["Microsoft.iOS.Sdk"]}},
            {"microsoft.net.sdk.ios": None},
        )
        self.write_mapping()
        self.add_marker("ios")

        result = self.run_inventory(check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("workload dependency is not an object", result.stderr)

    def test_dangling_metadata_root_fails_closed(self) -> None:
        metadata = self.root / "metadata"
        shutil.rmtree(metadata / "workloads")
        (metadata / "workloads").symlink_to(self.root / "missing-workloads")

        result = self.run_inventory(check=False)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("not a real directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
