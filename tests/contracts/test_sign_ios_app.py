#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
MODULE_PATH = REPOSITORY / "eng" / "toolchain" / "sign-ios-app.py"
SPEC = importlib.util.spec_from_file_location("sign_ios_app", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SIGNING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIGNING)


class SignIosAppContracts(unittest.TestCase):
    def test_profile_bundle_matching_supports_exact_and_wildcard_ids(self) -> None:
        self.assertTrue(
            SIGNING.profile_covers_bundle(
                "TEAM000001.dev.dotnetioslinux.helloios",
                ["TEAM000001"],
                "dev.dotnetioslinux.helloios",
            )
        )
        self.assertTrue(
            SIGNING.profile_covers_bundle(
                "TEAM000001.dev.dotnetioslinux.*",
                ["TEAM000001"],
                "dev.dotnetioslinux.helloios",
            )
        )
        self.assertFalse(
            SIGNING.profile_covers_bundle(
                "TEAM000001.dev.example.*",
                ["TEAM000001"],
                "dev.dotnetioslinux.helloios",
            )
        )

        derived = SIGNING.derive_signing_entitlements(
            {
                "application-identifier": "TEAM000001.dev.dotnetioslinux.*",
                "keychain-access-groups": ["TEAM000001.*", "TEAM000001.shared.fixed"],
            },
            ["TEAM000001"],
            "dev.dotnetioslinux.helloios",
        )
        self.assertEqual(
            "TEAM000001.dev.dotnetioslinux.helloios", derived["application-identifier"]
        )
        self.assertEqual(
            ["TEAM000001.dev.dotnetioslinux.helloios", "TEAM000001.shared.fixed"],
            derived["keychain-access-groups"],
        )

    def test_development_profile_contract(self) -> None:
        certificate = b"fixture-certificate"
        profile = {
            "ApplicationIdentifierPrefix": ["TEAM000001"],
            "DeveloperCertificates": [certificate],
            "Entitlements": {
                "application-identifier": "TEAM000001.dev.dotnetioslinux.helloios",
                "com.apple.developer.team-identifier": "TEAM000001",
                "get-task-allow": True,
            },
            "ExpirationDate": datetime.now(timezone.utc) + timedelta(days=1),
            "ProvisionedDevices": ["private-device-identifier"],
            "TeamIdentifier": ["TEAM000001"],
        }
        entitlements = SIGNING.validate_profile(
            profile, certificate, "TEAM000001", "dev.dotnetioslinux.helloios"
        )
        self.assertTrue(entitlements["get-task-allow"])

        profile["Entitlements"]["get-task-allow"] = False
        with self.assertRaisesRegex(SIGNING.SigningError, "not an iOS development profile"):
            SIGNING.validate_profile(
                profile, certificate, "TEAM000001", "dev.dotnetioslinux.helloios"
            )

        profile["Entitlements"]["get-task-allow"] = True
        profile["ExpirationDate"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.assertRaisesRegex(SIGNING.SigningError, "profile is expired"):
            SIGNING.validate_profile(
                profile, certificate, "TEAM000001", "dev.dotnetioslinux.helloios"
            )

        profile["ExpirationDate"] = datetime.now(timezone.utc) + timedelta(days=1)
        with self.assertRaisesRegex(SIGNING.SigningError, "certificate is not included"):
            SIGNING.validate_profile(
                profile, b"different-certificate", "TEAM000001", "dev.dotnetioslinux.helloios"
            )

        with self.assertRaisesRegex(SIGNING.SigningError, "does not match the configured team"):
            SIGNING.validate_profile(
                profile, certificate, "OTHERTEAM1", "dev.dotnetioslinux.helloios"
            )

    def test_macho_signature_load_command_detection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-macho-") as temporary:
            root = Path(temporary)
            signed = root / "signed"
            unsigned = root / "unsigned"
            signed_command = struct.pack("<II", SIGNING.LC_CODE_SIGNATURE, 8)
            other_command = struct.pack("<II", 0x32, 8)

            def write(path: Path, command: bytes) -> None:
                path.write_bytes(
                    struct.pack(
                        "<IiiIIIII",
                        0xFEEDFACF,
                        0x0100000C,
                        0,
                        2,
                        1,
                        len(command),
                        0,
                        0,
                    )
                    + command
                )

            write(signed, signed_command)
            write(unsigned, other_command)
            self.assertTrue(SIGNING.has_code_signature_load_command(signed))
            self.assertFalse(SIGNING.has_code_signature_load_command(unsigned))


if __name__ == "__main__":
    unittest.main()
