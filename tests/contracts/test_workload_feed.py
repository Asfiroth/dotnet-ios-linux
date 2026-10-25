#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
MODULE_PATH = REPOSITORY / "eng" / "workload" / "build-local-feed.py"
SPEC = importlib.util.spec_from_file_location("build_local_feed", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
feed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feed)


def official_files() -> dict[str, bytes]:
    manifest = {
        "version": feed.MANIFEST_VERSION,
        "workloads": {
            "ios": {
                "description": "iOS",
                "packs": ["Microsoft.iOS.Sdk.net10.0_27.0"],
                "extends": ["microsoft-net-runtime-ios", "microsoft-net-runtime-ios-net9"],
            }
        },
        "packs": {
            "Microsoft.iOS.Sdk.net10.0_27.0": {
                "kind": "sdk",
                "version": feed.MANIFEST_VERSION,
            }
        },
    }
    nuspec = f"<package><metadata><id>{feed.MANIFEST_ID}</id><version>{feed.MANIFEST_VERSION}</version></metadata></package>"
    return {
        f"{feed.MANIFEST_ID}.nuspec": nuspec.encode(),
        "Icon.png": b"icon",
        "LICENSE": b"license",
        "data/WorkloadDependencies.json": b"{}\n",
        "data/WorkloadManifest.json": (json.dumps(manifest) + "\n").encode(),
        "data/WorkloadManifest.targets": b"<Project>\n</Project>\n",
    }


def companion_files(workload_id: str) -> dict[str, bytes]:
    package_id = feed.COMPANION_MANIFESTS[workload_id]["id"]
    manifest = {
        "version": feed.MANIFEST_VERSION,
        "workloads": {
            workload_id: {
                "description": workload_id,
                "packs": [f"Example.{workload_id}.Sdk"],
                "extends": [f"microsoft-net-runtime-{workload_id}"],
            }
        },
        "packs": {f"Example.{workload_id}.Sdk": {"kind": "sdk", "version": feed.MANIFEST_VERSION}},
    }
    nuspec = f"<package><metadata><id>{package_id}</id><version>{feed.MANIFEST_VERSION}</version></metadata></package>"
    return {
        f"{package_id}.nuspec": nuspec.encode(),
        "Icon.png": b"icon",
        "LICENSE": b"license",
        "data/WorkloadDependencies.json": b"{}\n",
        "data/WorkloadManifest.json": (json.dumps(manifest) + "\n").encode(),
        "data/WorkloadManifest.targets": b"<Project>\n</Project>\n",
    }


class WorkloadFeedContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-feed-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_derived_manifest_is_linux_net10_physical_device_graph(self) -> None:
        derived = feed.derive_manifest(official_files())
        manifest = json.loads(derived["data/WorkloadManifest.json"])
        ios = manifest["workloads"]["ios"]
        self.assertEqual(["linux-x64"], ios["platforms"])
        self.assertNotIn("extends", ios)
        self.assertEqual(feed.LINUX_IOS_PACKS, ios["packs"])
        self.assertIn("Microsoft.iOS.Sdk.net10.0_26.0", ios["packs"])
        self.assertIn("Microsoft.NETCore.App.Runtime.Mono.net10.ios-arm64", ios["packs"])
        self.assertNotIn("Microsoft.iOS.Sdk.net9.0_18.0", ios["packs"])
        self.assertEqual(
            {"kind": "sdk", "version": feed.PROJECT_VERSION},
            manifest["packs"][feed.AOT_PACK_ID],
        )

    def test_derived_targets_import_both_project_packs_for_ios_only(self) -> None:
        targets = feed.derive_manifest(official_files())["data/WorkloadManifest.targets"].decode()
        self.assertIn('Sdk="DotnetIosLinux.Sdk"', targets)
        self.assertIn(
            'Sdk="DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64"', targets
        )
        self.assertIn("IsOSPlatform('linux')", targets)
        self.assertIn("'$(TargetPlatformIdentifier)' == 'iOS'", targets)
        for identifier in ("MacCatalyst", "tvOS", "macOS", "watchOS"):
            self.assertNotIn(f"'$(TargetPlatformIdentifier)' == '{identifier}'", targets)
        self.assertIn("<Project>", targets)

    def test_companion_derivatives_are_linux_registration_only(self) -> None:
        for workload_id in feed.COMPANION_MANIFESTS:
            with self.subTest(workload_id=workload_id):
                derived = feed.derive_companion_manifest(companion_files(workload_id), workload_id)
                manifest = json.loads(derived["data/WorkloadManifest.json"])
                workload = manifest["workloads"][workload_id]
                self.assertEqual(["linux-x64"], workload["platforms"])
                self.assertNotIn("extends", workload)
                marker = json.loads(derived["data/dotnet-ios-linux.json"])
                self.assertEqual("installed-unqualified", marker["supportClassification"])
                self.assertEqual(workload_id, marker["workloadId"])

    def test_project_sdk_package_is_deterministic(self) -> None:
        first = self.root / "first.nupkg"
        second = self.root / "second.nupkg"
        feed.package_sdk(REPOSITORY, first)
        feed.package_sdk(REPOSITORY, second)
        self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
        with zipfile.ZipFile(first) as archive:
            self.assertIn("Sdk/Sdk.props", archive.namelist())
            self.assertIn("Sdk/Sdk.targets", archive.namelist())
            self.assertIn("LICENSE", archive.namelist())

    def test_aot_pack_uses_the_canonical_runtime_verifier(self) -> None:
        root = self.root / "aot"
        root.mkdir()
        with mock.patch.object(feed.AOT_PACKAGER, "verify_published_aot_pack") as verify:
            feed.verify_aot_pack(root)
            verify.assert_called_once_with(root)

            verify.side_effect = SystemExit("wrong AOT pack")
            with self.assertRaises(SystemExit):
                feed.verify_aot_pack(root)

    def test_nuget_source_mapping_prefers_exact_project_ids(self) -> None:
        config = feed.nuget_config(self.root / "feed")
        self.assertIn(f'<package pattern="{feed.MANIFEST_ID}" />', config)
        for spec in feed.COMPANION_MANIFESTS.values():
            self.assertIn('<package pattern="%s" />' % spec["id"], config)
        self.assertIn(f'<package pattern="{feed.SDK_PACK_ID}" />', config)
        self.assertIn(f'<package pattern="{feed.AOT_PACK_ID}" />', config)
        self.assertIn('<package pattern="*" />', config)


if __name__ == "__main__":
    unittest.main()
