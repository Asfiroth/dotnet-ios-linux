#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
IMPORTER = REPOSITORY / "eng" / "toolchain" / "import-xcode-sdk.py"
SDK_ROOT = (
    "Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk"
)


def plist(value: dict) -> bytes:
    return plistlib.dumps(value, fmt=plistlib.FMT_XML)


def cpio_entry(
    name: str,
    data: bytes = b"",
    *,
    mode: int = stat.S_IFREG | 0o644,
    device: int = 1,
    inode: int = 1,
    links: int = 1,
    modified: int = 1_700_000_000,
) -> bytes:
    encoded_name = name.encode() + b"\0"
    fields = (
        device,
        inode,
        mode,
        1000,
        1000,
        links,
        0,
        modified,
        len(encoded_name),
        len(data),
    )
    widths = (6, 6, 6, 6, 6, 6, 6, 11, 6, 11)
    header = b"070707" + b"".join(
        f"{value:0{width}o}".encode() for value, width in zip(fields, widths, strict=True)
    )
    return header + encoded_name + data


def directory(name: str, inode: int) -> bytes:
    return cpio_entry(name, mode=stat.S_IFDIR | 0o755, inode=inode)


def synthetic_xip(path: Path) -> None:
    shared_key = {"device": 7, "inode": 700, "links": 3}
    members = [
        directory("Xcode.app", 1),
        cpio_entry(
            "Xcode.app/Contents/version.plist",
            plist(
                {
                    "CFBundleShortVersionString": "27.0",
                    "ProductBuildVersion": "27A266a",
                }
            ),
            inode=2,
        ),
        cpio_entry(
            "Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/version.plist",
            plist(
                {
                    "CFBundleShortVersionString": "27.0",
                    "ProductBuildVersion": "24A430",
                }
            ),
            inode=3,
        ),
        cpio_entry(
            "Xcode.app/Contents/Developer/usr/lib/libShared.tbd",
            b"--- authentic hardlink data\n",
            **shared_key,
        ),
        directory(SDK_ROOT, 10),
        cpio_entry(
            f"{SDK_ROOT}/SDKSettings.plist",
            plist(
                {
                    "CanonicalName": "iphoneos27.0",
                    "Version": "27.0",
                    "DefaultProperties": {"SUPPORTED_DEVICE_FAMILIES": "1,2"},
                }
            ),
            inode=11,
        ),
        directory(f"{SDK_ROOT}/System", 12),
        directory(f"{SDK_ROOT}/System/Library", 13),
        directory(f"{SDK_ROOT}/System/Library/CoreServices", 14),
        cpio_entry(
            f"{SDK_ROOT}/System/Library/CoreServices/SystemVersion.plist",
            plist({"ProductBuildVersion": "24A430"}),
            inode=15,
        ),
        directory(f"{SDK_ROOT}/System/Library/Frameworks", 16),
        directory(f"{SDK_ROOT}/System/Library/Frameworks/UIKit.framework", 17),
        directory(f"{SDK_ROOT}/usr", 18),
        directory(f"{SDK_ROOT}/usr/lib", 19),
        cpio_entry(
            f"{SDK_ROOT}/usr/lib/libSystem.tbd", b"NULLcanary\0" + b"\0" * 16, **shared_key
        ),
        cpio_entry(f"{SDK_ROOT}/usr/lib/libSystem_alias.tbd", **shared_key),
        cpio_entry(
            f"{SDK_ROOT}/usr/lib/libCurrent.tbd",
            b"libSystem.tbd",
            mode=stat.S_IFLNK | 0o777,
            inode=701,
        ),
        cpio_entry("TRAILER!!!", inode=999),
    ]
    cpio = b"".join(members)
    content = b"pbzx" + struct.pack(">Q", 0) + struct.pack(">QQ", 0, len(cpio)) + cpio
    toc = (
        b"<xar><toc><file><name>Content</name><type>file</type><data>"
        + f"<offset>0</offset><length>{len(content)}</length>".encode()
        + b"</data></file></toc></xar>"
    )
    compressed_toc = zlib.compress(toc)
    header = struct.pack(">4sHHQQI", b"xar!", 28, 1, len(compressed_toc), len(toc), 0)
    path.write_bytes(header + compressed_toc + content)


class XcodeSdkImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-xip-")
        self.root = Path(self.temp.name)
        self.archive = self.root / "Xcode_27.xip"
        synthetic_xip(self.archive)
        self.archive_hash = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.output = self.root / "sdk" / "iPhoneOS27.0.sdk"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def command(self, *extra: str) -> list[str]:
        return [
            sys.executable,
            str(IMPORTER),
            "--archive",
            str(self.archive),
            "--archive-sha256",
            self.archive_hash,
            "--output",
            str(self.output),
            "--xcode-version",
            "27.0",
            "--xcode-build",
            "27A266a",
            "--platform-version",
            "27.0",
            "--platform-build",
            "24A430",
            "--sdk-version",
            "27.0",
            "--sdk-build",
            "24A430",
            *extra,
        ]

    def test_import_preserves_external_hardlink_data_and_symlinks(self) -> None:
        result = subprocess.run(self.command(), text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

        system = self.output / "usr" / "lib" / "libSystem.tbd"
        alias = self.output / "usr" / "lib" / "libSystem_alias.tbd"
        current = self.output / "usr" / "lib" / "libCurrent.tbd"
        self.assertEqual(b"--- authentic hardlink data\n", system.read_bytes())
        self.assertEqual(system.stat().st_ino, alias.stat().st_ino)
        self.assertTrue(current.is_symlink())
        self.assertEqual("libSystem.tbd", os.readlink(current))

        marker = json.loads(
            (self.output / ".dotnet-ios-linux-sdk.json").read_text(encoding="utf-8")
        )
        self.assertEqual(self.archive_hash, marker["archiveSha256"])
        self.assertEqual("24A430", marker["sdkBuild"])
        self.assertEqual("24A430", marker["platformBuild"])

    def test_expected_metadata_mismatch_fails_without_output(self) -> None:
        command = self.command()
        command[command.index("24A430", command.index("--platform-build"))] = "24A999"
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("expected platform build", result.stdout)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
