#!/usr/bin/env python3
"""Contracts for direct Apple registration planning and lock selection."""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
FEED = REPOSITORY / "eng" / "workload" / "build-local-feed.py"
VERIFIER = REPOSITORY / "eng" / "install" / "verify-workload-packages.py"
RESOLVER = REPOSITORY / "eng" / "install" / "resolve-install-context.py"
INSTALLER = REPOSITORY / "install.sh"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


feed = load_module(FEED, "apple_workload_registration_feed")
verifier = load_module(VERIFIER, "apple_workload_registration_verifier")
resolver = load_module(RESOLVER, "apple_workload_registration_resolver")


class AppleWorkloadRegistrationContracts(unittest.TestCase):
    def test_plan_scoped_feed_exposes_only_selected_local_derivatives(self) -> None:
        config = feed.nuget_config(Path("/tmp/feed"), {"macos"})
        self.assertIn(f'<package pattern="{feed.COMPANION_MANIFESTS["macos"]["id"]}" />', config)
        self.assertNotIn(feed.MANIFEST_ID, config)
        self.assertNotIn(feed.SDK_PACK_ID, config)
        self.assertNotIn(feed.AOT_PACK_ID, config)
        for workload_id in ("maccatalyst", "tvos"):
            self.assertNotIn(feed.COMPANION_MANIFESTS[workload_id]["id"], config)

    def test_verifier_selects_only_requested_lock_graph(self) -> None:
        lock = {
            "components": {
                "linuxIosWorkload": {"packages": [{"id": "IosPack", "version": "1.0"}]},
                "appleCompanionWorkloads": {
                    "workloads": [
                        {
                            "workloadId": "macos",
                            "packages": [{"id": "MacPack", "version": "2.0"}],
                            "manifest": {
                                "id": "Microsoft.NET.Sdk.macOS.Manifest-10.0.100",
                                "version": "2.0",
                                "sha256": "a" * 64,
                                "sha512Base64": "placeholder",
                                "feedFile": "macos.nupkg",
                            },
                        }
                    ]
                },
            }
        }
        records = verifier.selected_records(lock, {"macos"})
        self.assertEqual(
            ["MacPack", "Microsoft.NET.Sdk.macOS.Manifest-10.0.100"],
            [item["id"] for item in records],
        )

    def test_resolver_requires_every_direct_manifest_mapping(self) -> None:
        with tempfile.TemporaryDirectory(prefix="apple-workload-resolver-") as raw:
            root = Path(raw)
            workloadsets = root / "sdk-manifests/10.0.100/workloadsets"
            expected = {
                "microsoft.net.sdk.ios": "27.0/10.0.100",
                "microsoft.net.sdk.macos": "27.0/10.0.100",
                "microsoft.net.sdk.maccatalyst": "27.0/10.0.100",
                "microsoft.net.sdk.tvos": "27.0/10.0.100",
            }
            for version, mapping in (
                ("10.0.401.1", expected),
                ("10.0.402.1", {**expected, "microsoft.net.sdk.tvos": "wrong/10.0.100"}),
            ):
                path = workloadsets / version
                path.mkdir(parents=True)
                (path / "microsoft.net.workloads.workloadset.json").write_text(
                    json.dumps(mapping), encoding="utf-8"
                )
            selected = resolver.select_workload(root, "10.0.100", expected, "fallback")
            self.assertEqual(
                {"workload_version": "10.0.401.1", "workload_source": "installed-compatible"},
                selected,
            )

    def test_installer_has_an_explicit_keep_existing_protection_boundary(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("--keep-existing", installer)
        self.assertIn("ios|macos|maccatalyst|tvos", installer)
        self.assertIn("assert_kept_registrations_exist", installer)
        self.assertIn("--protect-workload", installer)
        self.assertIn("managed and protected workload IDs overlap", (REPOSITORY / "eng/install/workload-transaction.py").read_text())

    def test_installer_invokes_workload_verifier_through_python(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        direct_invocation = re.compile(
            r'^\s*"\$script_dir/eng/install/verify-workload-packages\.py"',
            re.MULTILINE,
        )

        self.assertNotRegex(installer, direct_invocation)
        self.assertEqual(
            2,
            installer.count('python3 "$script_dir/eng/install/verify-workload-packages.py"'),
        )


if __name__ == "__main__":
    unittest.main()
