#!/usr/bin/env python3
"""Validate an iPhoneOS SDK and expose it through a minimal Xcode bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path


MARKER = "dotnet-ios-linux.json"


def fail(message: str) -> "None":
    print(f"synthetic Xcode: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_plist(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        fail(f"cannot read {path}: {error}")
    if not isinstance(value, dict):
        fail(f"plist root must be a dictionary: {path}")
    return value


def write_plist(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        plistlib.dump(value, stream, fmt=plistlib.FMT_XML, sort_keys=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sdk_version(settings: dict, source: Path) -> str:
    canonical = settings.get("CanonicalName")
    if not isinstance(canonical, str) or not canonical.startswith("iphoneos"):
        fail(f"SDK CanonicalName must start with 'iphoneos', got {canonical!r}")

    version = settings.get("Version")
    if not isinstance(version, str) or not version:
        version = canonical.removeprefix("iphoneos")
    if not version:
        match = re.fullmatch(r"iPhoneOS(.+)\.sdk", source.name)
        version = match.group(1) if match else ""
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", version):
        fail(f"cannot determine a valid SDK version from {source}")
    if canonical != f"iphoneos{version}":
        fail(f"SDK CanonicalName {canonical!r} does not match version {version!r}")
    return version


def sdk_build_version(source: Path, settings: dict) -> tuple[str, str]:
    settings_build = settings.get("ProductBuildVersion")
    if settings_build is not None and (not isinstance(settings_build, str) or not settings_build):
        fail("SDKSettings.plist ProductBuildVersion must be a non-empty string")

    system_version_path = source / "System" / "Library" / "CoreServices" / "SystemVersion.plist"
    system_build: str | None = None
    if system_version_path.is_file():
        system_version = read_plist(system_version_path)
        candidate = system_version.get("ProductBuildVersion")
        if candidate is not None and (not isinstance(candidate, str) or not candidate):
            fail("SystemVersion.plist ProductBuildVersion must be a non-empty string")
        system_build = candidate

    if settings_build and system_build and settings_build != system_build:
        fail("SDKSettings.plist and SystemVersion.plist ProductBuildVersion values disagree")
    if settings_build:
        return settings_build, "SDKSettings.plist"
    if system_build:
        return system_build, "System/Library/CoreServices/SystemVersion.plist"
    fail("SDK lacks ProductBuildVersion in SDKSettings.plist and SystemVersion.plist")


def validate_sdk(source: Path, requested_version: str | None) -> tuple[dict, str, str, str]:
    if not source.is_dir():
        fail(f"SDK directory does not exist: {source}")

    required = [
        source / "SDKSettings.plist",
        source / "System" / "Library" / "Frameworks" / "UIKit.framework",
        source / "usr" / "lib" / "libSystem.tbd",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        fail("SDK is incomplete; missing: " + ", ".join(missing))

    settings = read_plist(source / "SDKSettings.plist")
    defaults = settings.get("DefaultProperties")
    if not isinstance(defaults, dict):
        fail("SDKSettings.plist lacks a DefaultProperties dictionary")
    families = defaults.get("SUPPORTED_DEVICE_FAMILIES")
    if families is None:
        fail("SDKSettings.plist lacks SUPPORTED_DEVICE_FAMILIES")
    sdk_build, sdk_build_source = sdk_build_version(source, settings)

    version = sdk_version(settings, source)
    if requested_version and requested_version != version:
        fail(f"requested SDK {requested_version} does not match source SDK {version}")
    return settings, version, sdk_build, sdk_build_source


def dtxcode(version: str) -> str:
    numbers = [int(value) for value in version.split(".")]
    major = numbers[0]
    minor = numbers[1] if len(numbers) > 1 else 0
    patch = numbers[2] if len(numbers) > 2 else 0
    if minor > 9 or patch > 9:
        fail(f"cannot encode Xcode version as DTXcode: {version}")
    return str(major * 100 + minor * 10 + patch)


def install_executable(source: Path, destination: Path) -> None:
    if not source.is_file():
        fail(f"shim is missing: {source}")
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_activation(path: Path, developer: Path, dotnet_root: Path | None) -> None:
    lines = [
        "# Generated by eng/toolchain/build-synthetic-xcode.py",
        f"export DEVELOPER_DIR={shlex.quote(str(developer))}",
    ]
    if dotnet_root:
        lines.extend(
            [
                f"export DOTNET_ROOT={shlex.quote(str(dotnet_root))}",
                'export PATH="$DOTNET_ROOT:$DEVELOPER_DIR/usr/bin:$PATH"',
            ]
        )
    else:
        lines.append('export PATH="$DEVELOPER_DIR/usr/bin:$PATH"')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_owned_bundle(path: Path) -> None:
    marker = path / "Contents" / MARKER
    if not marker.is_file():
        fail(f"refusing to replace unowned directory: {path}")


def build(args: argparse.Namespace) -> None:
    source = args.sdk.expanduser().resolve()
    output = args.output.expanduser().resolve()
    shim_root = args.shims.expanduser().resolve()
    settings, version, sdk_build, sdk_build_source = validate_sdk(source, args.sdk_version)
    for label, value in (("Xcode", args.xcode_build), ("platform", args.platform_build)):
        if not re.fullmatch(r"[0-9]+[A-Z][0-9A-Za-z]+", value):
            fail(f"invalid {label} build identifier: {value!r}")

    replace_output = output.exists()
    if replace_output:
        if not args.replace:
            fail(f"output already exists (pass --replace for a project-owned bundle): {output}")
        require_owned_bundle(output)
    if output.name != "Xcode.app":
        fail("--output must name an Xcode.app directory")
    activation = args.activation.expanduser().resolve() if args.activation else None
    if activation and activation.exists() and not args.replace:
        fail(f"activation file already exists: {activation}")

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".Xcode.app.stage-", dir=output.parent))
    try:
        contents = stage / "Contents"
        developer = contents / "Developer"
        platform = developer / "Platforms" / "iPhoneOS.platform"
        sdk_directory = platform / "Developer" / "SDKs"
        versioned_sdk = sdk_directory / f"iPhoneOS{version}.sdk"
        sdk_directory.mkdir(parents=True)

        if args.mode == "copy":
            shutil.copytree(source, versioned_sdk, symlinks=True)
        else:
            versioned_sdk.symlink_to(source, target_is_directory=True)
        (sdk_directory / "iPhoneOS.sdk").symlink_to(versioned_sdk.name, target_is_directory=True)

        version_plist = {
            "CFBundleShortVersionString": args.xcode_version,
            "CFBundleVersion": args.xcode_build,
            "ProductBuildVersion": args.xcode_build,
        }
        info_plist = {
            "CFBundleIdentifier": "com.apple.dt.Xcode",
            "CFBundleName": "Xcode",
            "CFBundleShortVersionString": args.xcode_version,
            "CFBundleVersion": args.xcode_build,
            "DTXcode": dtxcode(args.xcode_version),
        }
        platform_info = {
            "CFBundleIdentifier": "com.apple.platform.iphoneos",
            "CFBundleName": "iPhoneOS",
            "CFBundleShortVersionString": version,
            "AdditionalInfo": {
                "DTPlatformName": "iphoneos",
                "DTPlatformVersion": version,
                "DTPlatformBuild": args.platform_build,
            },
        }
        platform_version = {
            "CFBundleShortVersionString": version,
            "CFBundleVersion": args.platform_build,
            "ProductBuildVersion": args.platform_build,
        }
        write_plist(contents / "version.plist", version_plist)
        write_plist(contents / "Info.plist", info_plist)
        write_plist(platform / "Info.plist", platform_info)
        write_plist(platform / "version.plist", platform_version)

        macos = contents / "MacOS"
        macos.mkdir(parents=True)
        install_executable(shim_root / "Xcode", macos / "Xcode")

        developer_bin = developer / "usr" / "bin"
        developer_bin.mkdir(parents=True)
        install_executable(shim_root / "xcrun", developer_bin / "xcrun")
        install_executable(shim_root / "xcodebuild", developer_bin / "xcodebuild")

        toolchain_bin = developer / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "bin"
        toolchain_bin.mkdir(parents=True)
        for tool in ("clang", "clang++", "mdimport"):
            (toolchain_bin / tool).symlink_to("../../../../usr/bin/xcrun")

        marker = {
            "schemaVersion": 1,
            "owner": "dotnet-ios-linux",
            "xcodeVersion": args.xcode_version,
            "xcodeBuild": args.xcode_build,
            "sdkVersion": version,
            "sdkCanonicalName": settings["CanonicalName"],
            "sdkBuild": sdk_build,
            "sdkBuildSource": sdk_build_source,
            "sdkSettingsSha256": sha256(source / "SDKSettings.plist"),
            "sdkSource": str(source),
            "sdkMode": args.mode,
            "platformBuild": args.platform_build,
        }
        (contents / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")

        if replace_output:
            backup = stage.with_name(stage.name + ".previous")
            os.replace(output, backup)
            try:
                os.replace(stage, output)
            except BaseException:
                os.replace(backup, output)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(stage, output)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    if activation:
        dotnet_root = args.dotnet_root.expanduser().resolve() if args.dotnet_root else None
        write_activation(activation, output / "Contents" / "Developer", dotnet_root)

    print(f"synthetic_xcode={output}")
    print(f"developer_dir={output / 'Contents' / 'Developer'}")
    print(f"sdk_version={version}")
    print(f"sdk_mode={args.mode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True, help="source iPhoneOS<version>.sdk")
    parser.add_argument("--output", type=Path, required=True, help="destination Xcode.app")
    parser.add_argument("--xcode-version", required=True)
    parser.add_argument("--xcode-build", required=True)
    parser.add_argument("--platform-build", required=True, help="ProductBuildVersion from iPhoneOS.platform/version.plist")
    parser.add_argument("--sdk-version", help="expected SDK version")
    parser.add_argument("--mode", choices=("symlink", "copy"), default="symlink")
    parser.add_argument(
        "--shims",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "src" / "shims",
    )
    parser.add_argument("--activation", type=Path, help="write a shell activation file")
    parser.add_argument("--dotnet-root", type=Path, help="optional DOTNET_ROOT for the activation file")
    parser.add_argument("--replace", action="store_true", help="replace only a bundle carrying the project marker")
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
