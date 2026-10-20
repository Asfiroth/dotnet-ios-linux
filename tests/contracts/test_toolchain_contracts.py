#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
BUILDER = REPOSITORY / "eng" / "toolchain" / "build-synthetic-xcode.py"


def plist(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        plistlib.dump(value, stream)


class ToolchainContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-contract-")
        self.root = Path(self.temp.name)
        self.sdk = self.root / "source" / "iPhoneOS27.0.sdk"
        (self.sdk / "System" / "Library" / "Frameworks" / "UIKit.framework").mkdir(parents=True)
        lib = self.sdk / "usr" / "lib"
        lib.mkdir(parents=True)
        (lib / "libSystem.tbd").write_text("--- !tapi-tbd\n", encoding="utf-8")
        plist(
            self.sdk / "SDKSettings.plist",
            {
                "CanonicalName": "iphoneos27.0",
                "Version": "27.0",
                "ProductBuildVersion": "27A265",
                "DefaultProperties": {"SUPPORTED_DEVICE_FAMILIES": "1,2"},
            },
        )

        self.xcode = self.root / "install" / "Xcode.app"
        self.activation = self.root / "install" / "activate"
        self.run_command(
            [
                sys.executable,
                str(BUILDER),
                "--sdk",
                str(self.sdk),
                "--sdk-version",
                "27.0",
                "--xcode-version",
                "27.0",
                "--xcode-build",
                "27A266a",
                "--platform-build",
                "27A266a",
                "--output",
                str(self.xcode),
                "--activation",
                str(self.activation),
            ]
        )

        self.developer = self.xcode / "Contents" / "Developer"
        self.xcrun = self.developer / "usr" / "bin" / "xcrun"
        self.xcodebuild = self.developer / "usr" / "bin" / "xcodebuild"
        self.backend_log = self.root / "backend.json"
        self.backend = self.root / "fake-clang"
        self.backend.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['BACKEND_LOG'], 'w', encoding='utf-8') as stream:\n"
            "    json.dump(sys.argv[1:], stream)\n",
            encoding="utf-8",
        )
        self.backend.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "DEVELOPER_DIR": str(self.developer),
                "DOTNET_IOS_LINUX_CLANG": str(self.backend),
                "DOTNET_IOS_LINUX_CLANGXX": str(self.backend),
                "BACKEND_LOG": str(self.backend_log),
            }
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_command(
        self,
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        expected: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(expected, result.returncode, result.stderr)
        return result

    def backend_arguments(self) -> list[str]:
        return json.loads(self.backend_log.read_text(encoding="utf-8"))

    def test_bundle_contains_truthful_discovery_metadata(self) -> None:
        marker = json.loads((self.xcode / "Contents" / "dotnet-ios-linux.json").read_text())
        self.assertEqual("27.0", marker["sdkVersion"])
        self.assertEqual("27A265", marker["sdkBuild"])
        self.assertEqual("SDKSettings.plist", marker["sdkBuildSource"])
        self.assertEqual(str(self.sdk.resolve()), marker["sdkSource"])
        self.assertIn(f"export DEVELOPER_DIR={self.developer}", self.activation.read_text())

    def test_xcode_27_system_version_sdk_build_fallback(self) -> None:
        settings_path = self.sdk / "SDKSettings.plist"
        settings = plistlib.loads(settings_path.read_bytes())
        settings.pop("ProductBuildVersion")
        plist(settings_path, settings)
        plist(
            self.sdk / "System" / "Library" / "CoreServices" / "SystemVersion.plist",
            {"ProductBuildVersion": "24A430"},
        )
        fallback_xcode = self.root / "fallback" / "Xcode.app"
        self.run_command(
            [
                sys.executable,
                str(BUILDER),
                "--sdk",
                str(self.sdk),
                "--sdk-version",
                "27.0",
                "--xcode-version",
                "27.0",
                "--xcode-build",
                "27A266a",
                "--platform-build",
                "24A430",
                "--output",
                str(fallback_xcode),
            ]
        )
        marker = json.loads((fallback_xcode / "Contents" / "dotnet-ios-linux.json").read_text())
        self.assertEqual("24A430", marker["sdkBuild"])
        self.assertEqual(
            "System/Library/CoreServices/SystemVersion.plist", marker["sdkBuildSource"]
        )
        environment = self.environment | {
            "DEVELOPER_DIR": str(fallback_xcode / "Contents" / "Developer")
        }
        result = self.run_command(
            [
                str(fallback_xcode / "Contents" / "Developer" / "usr" / "bin" / "xcrun"),
                "--sdk",
                "iphoneos",
                "--show-sdk-build-version",
            ],
            environment=environment,
        )
        self.assertEqual("24A430", result.stdout.strip())

    def test_disagreeing_sdk_build_sources_fail_closed(self) -> None:
        plist(
            self.sdk / "System" / "Library" / "CoreServices" / "SystemVersion.plist",
            {"ProductBuildVersion": "24A430"},
        )
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--sdk",
                str(self.sdk),
                "--sdk-version",
                "27.0",
                "--xcode-version",
                "27.0",
                "--xcode-build",
                "27A266a",
                "--platform-build",
                "24A430",
                "--output",
                str(self.root / "mismatch" / "Xcode.app"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("ProductBuildVersion values disagree", result.stderr)

    def test_sdk_queries_use_synthetic_paths(self) -> None:
        result = self.run_command(
            [str(self.xcrun), "--sdk", "iphoneos", "--show-sdk-path"],
            environment=self.environment,
        )
        expected = self.developer / "Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS27.0.sdk"
        self.assertEqual(str(expected), result.stdout.strip())
        version = self.run_command(
            [str(self.xcrun), "--sdk", "iphoneos", "--show-sdk-version"],
            environment=self.environment,
        )
        self.assertEqual("27.0", version.stdout.strip())
        build = self.run_command(
            [str(self.xcrun), "--sdk", "iphoneos", "--show-sdk-build-version"],
            environment=self.environment,
        )
        self.assertEqual("27A265", build.stdout.strip())

    def test_tool_lookup_returns_controlled_entry_point(self) -> None:
        result = self.run_command([str(self.xcrun), "--find", "clang"], environment=self.environment)
        self.assertEqual(
            str(self.developer / "Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"),
            result.stdout.strip(),
        )

    def test_spotlight_indexing_is_explicitly_skipped_on_linux(self) -> None:
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        result = self.run_command(
            [str(self.xcrun), "mdimport", str(artifacts)], environment=self.environment
        )
        self.assertIn("Spotlight indexing is unavailable on Linux", result.stderr)

        missing = self.run_command(
            [str(self.xcrun), "mdimport", str(self.root / "missing")],
            environment=self.environment,
            expected=64,
        )
        self.assertIn("cannot resolve mdimport input", missing.stderr)

    def test_compile_injects_linux_driver_target_only(self) -> None:
        source = self.root / "path with spaces" / "probe.c"
        source.parent.mkdir()
        source.write_text("int probe;\n", encoding="utf-8")
        output = self.root / "probe.o"
        self.run_command(
            [
                str(self.xcrun),
                "clang",
                "-miphoneos-version-min=15.0",
                "-arch",
                "arm64",
                "-isysroot",
                str(self.sdk),
                "-o",
                str(output),
                "-c",
                str(source),
            ],
            environment=self.environment,
        )
        arguments = self.backend_arguments()
        self.assertEqual("--target=arm64-apple-ios15.0", arguments[0])
        self.assertNotIn("-fuse-ld=lld", arguments)
        self.assertIn(str(source), arguments)

    def test_link_injects_lld_without_rewriting_boundaries(self) -> None:
        object_file = self.root / "one object.o"
        self.run_command(
            [
                str(self.xcrun),
                "clang++",
                "-miphoneos-version-min=15.0",
                "-arch",
                "arm64",
                "-isysroot",
                str(self.sdk),
                str(object_file),
                "-o",
                str(self.root / "HelloIos"),
            ],
            environment=self.environment,
        )
        arguments = self.backend_arguments()
        self.assertEqual("--target=arm64-apple-ios15.0", arguments[0])
        self.assertEqual("-fuse-ld=lld", arguments[1])
        self.assertIn(str(object_file), arguments)

    def test_explicit_unsupported_target_fails_closed(self) -> None:
        result = self.run_command(
            [
                str(self.xcrun),
                "clang",
                "--target=x86_64-linux-gnu",
                "-arch",
                "arm64",
                "-miphoneos-version-min=15.0",
                "-isysroot",
                str(self.sdk),
                "-c",
                "probe.c",
            ],
            environment=self.environment,
            expected=64,
        )
        self.assertIn("unsupported explicit clang target", result.stderr)

    def test_conflicting_architecture_fails_closed(self) -> None:
        result = self.run_command(
            [
                str(self.xcrun),
                "clang",
                "--target=arm64-apple-ios15.0",
                "-arch",
                "x86_64",
                "-miphoneos-version-min=15.0",
                "-isysroot",
                str(self.sdk),
                "-c",
                "probe.c",
            ],
            environment=self.environment,
            expected=64,
        )
        self.assertIn("-arch arm64", result.stderr)

    def test_unselected_sysroot_fails_closed(self) -> None:
        other_sdk = self.root / "other.sdk"
        other_sdk.mkdir()
        result = self.run_command(
            [
                str(self.xcrun),
                "clang",
                "-arch",
                "arm64",
                "-miphoneos-version-min=15.0",
                "-isysroot",
                str(other_sdk),
                "-c",
                "probe.c",
            ],
            environment=self.environment,
            expected=64,
        )
        self.assertIn("unsupported sysroot", result.stderr)

    def test_unknown_tool_fails_closed(self) -> None:
        result = self.run_command(
            [str(self.xcrun), "codesign", "--version"],
            environment=self.environment,
            expected=64,
        )
        self.assertIn("unsupported tool", result.stderr)

    def test_xcodebuild_only_reports_declared_version(self) -> None:
        result = self.run_command([str(self.xcodebuild), "-version"], environment=self.environment)
        self.assertEqual("Xcode 27.0\nBuild version 27A266a\n", result.stdout)
        unsupported = self.run_command(
            [str(self.xcodebuild), "-project", "Anything.xcodeproj"],
            environment=self.environment,
            expected=64,
        )
        self.assertIn("unsupported xcodebuild operation", unsupported.stderr)

    def test_xcode_application_entry_point_fails_closed(self) -> None:
        launcher = self.xcode / "Contents" / "MacOS" / "Xcode"
        result = self.run_command([str(launcher)], expected=64)
        self.assertIn("launching the synthetic Xcode application is unsupported", result.stderr)


if __name__ == "__main__":
    unittest.main()
