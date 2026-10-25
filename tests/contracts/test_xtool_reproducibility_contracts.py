#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
SETUP_XTOOL = REPOSITORY / "eng" / "toolchain" / "setup-xtool.sh"
SETUP_BINUTILS = REPOSITORY / "eng" / "toolchain" / "setup-binutils.sh"
MOUNT_RUNNER = REPOSITORY / "eng" / "toolchain" / "run-in-fixed-mount.sh"
INSTALLER = REPOSITORY / "install.sh"
RESOLVER = REPOSITORY / "eng" / "install" / "resolve-install-context.py"
LOCK = REPOSITORY / "toolchain.lock.json"


class XtoolReproducibilityContracts(unittest.TestCase):
    def test_hash_mismatch_can_retain_phase_level_forensics(self) -> None:
        script = SETUP_XTOOL.read_text(encoding="utf-8")

        self.assertIn("--diagnostics-dir <new-directory>", script)
        self.assertIn("schema=dotnet-ios-linux.xtool-normalization-forensics.v1", script)
        self.assertIn("objcopy_sha256=", script)
        self.assertIn("strip_sha256=", script)
        self.assertIn("pre-normalization", script)
        self.assertIn("build-id-removed", script)
        self.assertIn("swift-modhash-zeroed", script)
        self.assertIn("strip-unneeded", script)
        self.assertIn("runtime_validation=not-run-unverified", script)
        self.assertIn("retain_normalization_diagnostics", script)
        self.assertIn("normalization diagnostics retained at", script)
        self.assertIn("SHA256SUMS", script)
        self.assertIn("mv --no-clobber --no-target-directory", script)
        self.assertIn("preserve_scratch=true", script)
        self.assertIn("normalization evidence remains at", script)
        self.assertIn("normalized xtool output hashes do not match the lock", script)
        self.assertNotIn("record_normalization_runtime_validation", script)
        self.assertNotIn('LD_LIBRARY_PATH="$runtime" ldd', script)

    def test_normalization_uses_locked_tools_and_fixed_logical_path(self) -> None:
        script = SETUP_XTOOL.read_text(encoding="utf-8")

        self.assertIn("--binutils-root <directory>", script)
        self.assertIn('objcopy_tool="$binutils_root/bin/objcopy"', script)
        self.assertIn('strip_tool="$binutils_root/bin/strip"', script)
        self.assertIn('readelf_tool="$binutils_root/bin/readelf"', script)
        self.assertIn('"$objcopy_tool" --remove-section=.note.gnu.build-id', script)
        self.assertIn('"$strip_tool" --strip-unneeded', script)
        self.assertNotRegex(script, re.compile(r"^\s*(?:objcopy|strip)\s", re.MULTILINE))
        self.assertIn("unshare --user --map-root-user --mount", script)
        self.assertIn("/tmp/dotnet-ios-linux/.xtool-build-", script)
        self.assertNotIn("/tmp/opencode", script)
        self.assertIn("run-in-fixed-mount.sh", script)
        mount_runner = MOUNT_RUNNER.read_text(encoding="utf-8")
        self.assertIn("/proc/self/uid_map", mount_runner)
        self.assertIn('"$host_uid" != 0', mount_runner)
        self.assertIn('"$mapped_uids" == 1', mount_runner)
        self.assertIn("mount -t tmpfs", mount_runner)
        self.assertIn('tmpfs "$canonical_parent"', mount_runner)
        self.assertIn("mount --bind", mount_runner)

    def test_binutils_lock_matches_setup_and_installer_wiring(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        component = lock["components"]["hostToolchain"]["binutils"]
        setup = SETUP_BINUTILS.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        resolver = RESOLVER.read_text(encoding="utf-8")

        self.assertEqual("2.47", component["version"])
        self.assertIn(component["sha256"], setup)
        self.assertIn(component["stagedOutputs"]["objcopySha256"], setup)
        self.assertIn(component["stagedOutputs"]["stripSha256"], setup)
        self.assertIn(component["stagedOutputs"]["readelfSha256"], setup)
        self.assertIn("setup-binutils.sh", installer)
        self.assertIn('--binutils-root "$toolchain/binutils-$binutils_version"', installer)
        self.assertIn("components.hostToolchain.binutils.version", resolver)

    def test_diagnostics_option_is_exposed_without_starting_a_build(self) -> None:
        result = subprocess.run(
            [str(SETUP_XTOOL), "--help"], text=True, capture_output=True, check=False
        )

        self.assertEqual(0, result.returncode)
        self.assertIn("--diagnostics-dir <new-directory>", result.stdout)
        self.assertIn("--binutils-root <directory>", result.stdout)

        binutils = subprocess.run(
            [str(SETUP_BINUTILS), "--help"], text=True, capture_output=True, check=False
        )
        self.assertEqual(0, binutils.returncode)
        self.assertIn("--swift-root <directory>", binutils.stdout)


if __name__ == "__main__":
    unittest.main()
