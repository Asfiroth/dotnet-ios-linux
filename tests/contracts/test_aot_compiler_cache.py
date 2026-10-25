#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
PACKAGER_PATH = REPOSITORY / "eng" / "runtime" / "package-aot-pack.py"
BUILD_SCRIPT = REPOSITORY / "eng" / "runtime" / "build-mono-aot-cross.sh"
INSTALLER = REPOSITORY / "install.sh"
FEED_BUILDER = REPOSITORY / "eng" / "workload" / "build-local-feed.py"
LOCK = REPOSITORY / "toolchain.lock.json"
SPEC = importlib.util.spec_from_file_location("package_aot_pack", PACKAGER_PATH)
assert SPEC is not None and SPEC.loader is not None
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


class AotCompilerCacheContracts(unittest.TestCase):
    runtime_commit = "4271d88e0aebf3d04f188f1334c2220d80555ef6"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aot-compiler-cache-contract-")
        self.root = Path(self.temporary.name) / "compiler-root"
        self.compiler = self.root / "install" / "bin" / "mono-aot-cross"
        self.compiler.parent.mkdir(parents=True)
        self.compiler.write_bytes(b"compiler-cache-fixture\n")
        self.compiler.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_provenance(self, **overrides: object) -> None:
        value: dict[str, object] = {
            "schemaVersion": 2,
            "state": "feasibility",
            "runtimeCommit": self.runtime_commit,
            "targetTriple": "aarch64-apple-darwin10",
            "llvmMode": "without-llvm",
            "matchedLlvmPackageVersion": None,
            "matchedLlvmSourceCommit": None,
            "compilerSha256": hashlib.sha256(self.compiler.read_bytes()).hexdigest(),
            "pathNormalization": {
                "source": "/usr/src/dotnet-runtime",
                "build": "/usr/build/dotnet-ios-linux/mono-aot",
                "install": "/usr/lib/dotnet-ios-linux/mono-aot",
                "llvm": None,
            },
        }
        value.update(overrides)
        (self.root / "install" / "aot-build-provenance.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def test_complete_matching_provenance_is_reusable(self) -> None:
        self.write_provenance()

        expected = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        with mock.patch.object(PACKAGER, "EXPECTED_COMPILER_SHA256", expected):
            compiler_sha256, provenance = PACKAGER.verify_compiler(
                self.compiler, self.runtime_commit, "without-llvm"
            )

        self.assertEqual(hashlib.sha256(self.compiler.read_bytes()).hexdigest(), compiler_sha256)
        self.assertEqual("feasibility", provenance["state"])

    def test_missing_or_tampered_provenance_is_not_reusable(self) -> None:
        expected = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        with mock.patch.object(PACKAGER, "EXPECTED_COMPILER_SHA256", expected):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                PACKAGER.verify_compiler(self.compiler, self.runtime_commit, "without-llvm")

            self.write_provenance(compilerSha256="0" * 64)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                PACKAGER.verify_compiler(self.compiler, self.runtime_commit, "without-llvm")

            self.write_provenance()
            self.compiler.chmod(0o700)
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                PACKAGER.verify_compiler(self.compiler, self.runtime_commit, "without-llvm")

            self.compiler.chmod(0o755)
            self.compiler.write_bytes(b"tampered\n")
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                PACKAGER.verify_compiler(self.compiler, self.runtime_commit, "without-llvm")

    def test_published_pack_must_have_the_complete_verified_payload(self) -> None:
        pack = self.root / "pack"
        tools = pack / "tools"
        tools.mkdir(parents=True)
        (pack / "Sdk").mkdir()
        sdk_source = (
            REPOSITORY
            / "src"
            / "packs"
            / "DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64"
            / "Sdk"
            / "Sdk.props"
        )
        (pack / "Sdk" / "Sdk.props").write_bytes(sdk_source.read_bytes())
        expected: dict[str, str] = {}
        for name in ("mono-aot-cross", "libc++.so.1", "libc++abi.so.1", "llc", "opt"):
            path = tools / name
            path.write_bytes(name.encode())
            if name in {"mono-aot-cross", "llc", "opt"}:
                path.chmod(0o755)
            expected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with (
            mock.patch.object(PACKAGER, "EXPECTED_AOT_FILES", expected),
            mock.patch.object(
                PACKAGER,
                "EXPECTED_AOT_MODES",
                {
                    name: (0o755 if name in {"mono-aot-cross", "llc", "opt"} else 0o644)
                    for name in expected
                },
            ),
            mock.patch.object(PACKAGER, "execute", return_value="fixture compiler\n"),
        ):
            (pack / "provenance.json").write_text(
                json.dumps(PACKAGER.aot_pack_provenance(["fixture compiler"]), indent=2) + "\n",
                encoding="utf-8",
            )
            PACKAGER.verify_published_aot_pack(pack)
            (tools / "opt").write_bytes(b"tampered")
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                PACKAGER.verify_published_aot_pack(pack)

    def test_build_and_installer_require_verified_cache_publication(self) -> None:
        build = BUILD_SCRIPT.read_text(encoding="utf-8")
        installer = INSTALLER.read_text(encoding="utf-8")
        feed = FEED_BUILDER.read_text(encoding="utf-8")
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        expected = "55dcaa7bf9dd71206ff8f69a26e0eacd79af7146b2a0f7a1143c3d9e0d3267e7"

        self.assertIn("-ffile-prefix-map=$source_root=$canonical_source_prefix", build)
        self.assertIn("SOURCE_DATE_EPOCH=0", build)
        self.assertIn('"state": "verified"', build)
        self.assertIn('"compilerSha256": compiler_sha256', build)
        self.assertIn("expected_compiler_sha256", build)
        self.assertIn(expected, build)
        self.assertIn(expected, PACKAGER_PATH.read_text(encoding="utf-8"))
        self.assertIn("AOT_PACKAGER.verify_published_aot_pack", feed)
        self.assertEqual(
            expected,
            lock["components"]["monoRuntime"]["compilerConfiguration"][
                "reproducibleCompilerSha256"
            ],
        )
        self.assertIn("verify_compiler_cache", installer)
        self.assertIn("verify_aot_pack_cache", installer)
        self.assertIn("build_and_publish_compiler_cache", installer)
        self.assertIn("publish_directory_noreplace", installer)
        self.assertIn("expected_parent.is_symlink()", installer)
        self.assertIn('! -L "$cache_root/build"', installer)
        self.assertIn('"$cache_root/nuget/llvm"', installer)
        self.assertIn('"$cache_root/workload-bundles"', installer)
        self.assertIn('"$cache_root/workload-temp"', installer)
        self.assertNotIn('[[ ! -x "$compiler_root/install/bin/mono-aot-cross" ]]', installer)


if __name__ == "__main__":
    unittest.main()
