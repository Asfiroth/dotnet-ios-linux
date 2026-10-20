#!/usr/bin/env python3
"""Validate private development assets and sign a copied iOS app bundle."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn


EXPECTED_RCODESIGN_VERSION = "apple-codesign 0.29.0"
EXPECTED_RCODESIGN_SHA256 = "dab9a7465f96aba3c81e793775510f745b91a46b6418e89f7317b5d8fc7bcea2"
EXPECTED_PYMOBILEDEVICE3_VERSION = "11.26.0"
LC_CODE_SIGNATURE = 0x1D
TEAM_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
BUNDLE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]+$")


class SigningError(Exception):
    """An expected, safely reportable signing preflight failure."""


def fail(message: str) -> NoReturn:
    print(f"sign-ios-app: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an existing P12 development identity and provisioning profile, "
            "then sign a private copy of an unsigned iOS app."
        )
    )
    parser.add_argument("--config", required=True, type=Path, help="mode-600 private JSON config")
    parser.add_argument("--app", required=True, type=Path, help="unsigned source .app")
    parser.add_argument("--output", required=True, type=Path, help="new signed .app path")
    parser.add_argument("--rcodesign-root", required=True, type=Path)
    parser.add_argument(
        "--pymobiledevice3-root",
        type=Path,
        help=(
            "when supplied, require exactly one USB device and prove that the profile "
            "authorizes it without printing its identifier"
        ),
    )
    return parser.parse_args()


def run_private(
    command: list[str],
    *,
    input_data: bytes | None = None,
    failure: str,
) -> bytes:
    try:
        result = subprocess.run(
            command,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise SigningError(f"{failure}: {type(error).__name__}") from error
    if result.returncode != 0:
        raise SigningError(failure)
    return result.stdout


def require_private_file(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise SigningError(f"cannot access {label}: {type(error).__name__}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise SigningError(f"{label} is not a regular file")
    if metadata.st_uid != os.getuid():
        raise SigningError(f"{label} is not owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SigningError(f"{label} must not be accessible by group or other users")
    return resolved


def load_config(path: Path) -> dict:
    config_path = require_private_file(path, "signing config")
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SigningError(f"cannot read signing config: {type(error).__name__}") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise SigningError("signing config must use schemaVersion 1")
    identity = value.get("identity")
    if not isinstance(identity, dict):
        raise SigningError("signing config identity must be an object")
    required = {
        "identity.p12": identity.get("p12"),
        "identity.passwordFile": identity.get("passwordFile"),
        "provisioningProfile": value.get("provisioningProfile"),
        "expectedTeamIdentifier": value.get("expectedTeamIdentifier"),
        "expectedBundleIdentifier": value.get("expectedBundleIdentifier"),
    }
    if any(not isinstance(item, str) or not item for item in required.values()):
        raise SigningError("signing config has missing or invalid required values")
    if not TEAM_PATTERN.fullmatch(required["expectedTeamIdentifier"]):
        raise SigningError("configured team identifier must be 10 uppercase letters or digits")
    if not BUNDLE_PATTERN.fullmatch(required["expectedBundleIdentifier"]):
        raise SigningError("configured bundle identifier is invalid")
    return value


def app_metadata(app: Path) -> tuple[str, Path]:
    try:
        app = app.expanduser().resolve(strict=True)
    except OSError as error:
        raise SigningError(f"cannot access source app: {type(error).__name__}") from error
    if not app.is_dir() or app.suffix != ".app":
        raise SigningError("source app must be an existing .app directory")
    info_path = app / "Info.plist"
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        raise SigningError(f"cannot read app Info.plist: {type(error).__name__}") from error
    if not isinstance(info, dict):
        raise SigningError("app Info.plist root is not a dictionary")
    bundle_id = info.get("CFBundleIdentifier")
    executable_name = info.get("CFBundleExecutable")
    if not isinstance(bundle_id, str) or not BUNDLE_PATTERN.fullmatch(bundle_id):
        raise SigningError("app has an invalid CFBundleIdentifier")
    if not isinstance(executable_name, str) or Path(executable_name).name != executable_name:
        raise SigningError("app has an invalid CFBundleExecutable")
    executable = app / executable_name
    if not executable.is_file():
        raise SigningError("app executable is missing")
    return bundle_id, executable


def has_code_signature_load_command(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(32)
            if len(header) != 32:
                raise SigningError("app executable has a truncated Mach-O header")
            magic = header[:4]
            if magic == b"\xcf\xfa\xed\xfe":
                endian = "<"
            elif magic == b"\xfe\xed\xfa\xcf":
                endian = ">"
            else:
                raise SigningError("app executable is not a 64-bit Mach-O file")
            _, _, _, _, command_count, command_bytes, _, _ = struct.unpack(
                f"{endian}IiiIIIII", header
            )
            if command_count > 4096 or command_bytes > 64 * 1024 * 1024:
                raise SigningError("app executable has unreasonable Mach-O load commands")
            commands = stream.read(command_bytes)
    except OSError as error:
        raise SigningError(f"cannot inspect app executable: {type(error).__name__}") from error
    offset = 0
    for _ in range(command_count):
        if offset + 8 > len(commands):
            raise SigningError("app executable has truncated Mach-O load commands")
        command, size = struct.unpack_from(f"{endian}II", commands, offset)
        if size < 8 or offset + size > len(commands):
            raise SigningError("app executable has an invalid Mach-O load command")
        if command == LC_CODE_SIGNATURE:
            return True
        offset += size
    return False


def extract_profile(openssl: str, profile: Path, output: Path) -> dict:
    raw = run_private(
        [
            openssl,
            "cms",
            "-verify",
            "-binary",
            "-inform",
            "DER",
            "-noverify",
            "-in",
            str(profile),
        ],
        failure="provisioning profile CMS verification failed",
    )
    try:
        value = plistlib.loads(raw)
    except plistlib.InvalidFileException as error:
        raise SigningError("provisioning profile payload is not a plist") from error
    if not isinstance(value, dict):
        raise SigningError("provisioning profile payload is not a dictionary")
    with output.open("wb") as stream:
        plistlib.dump(value, stream, fmt=plistlib.FMT_XML, sort_keys=True)
    output.chmod(0o600)
    return value


def extract_p12_identity(
    openssl: str,
    p12: Path,
    password_file: Path,
    certificate_pem: Path,
    certificate_der: Path,
    private_key_pem: Path,
) -> bytes:
    passin = f"file:{password_file}"
    run_private(
        [
            openssl,
            "pkcs12",
            "-legacy",
            "-in",
            str(p12),
            "-clcerts",
            "-nokeys",
            "-passin",
            passin,
            "-out",
            str(certificate_pem),
        ],
        failure="P12 certificate extraction failed",
    )
    run_private(
        [
            openssl,
            "pkcs12",
            "-legacy",
            "-in",
            str(p12),
            "-nocerts",
            "-nodes",
            "-passin",
            passin,
            "-out",
            str(private_key_pem),
        ],
        failure="P12 private-key extraction failed",
    )
    certificate_pem.chmod(0o600)
    private_key_pem.chmod(0o600)
    certificate_der.write_bytes(
        run_private(
            [openssl, "x509", "-in", str(certificate_pem), "-outform", "DER"],
            failure="P12 certificate conversion failed",
        )
    )
    certificate_der.chmod(0o600)
    certificate_public_key = run_private(
        [openssl, "x509", "-in", str(certificate_pem), "-pubkey", "-noout"],
        failure="cannot read certificate public key",
    )
    certificate_public_der = run_private(
        [openssl, "pkey", "-pubin", "-outform", "DER"],
        input_data=certificate_public_key,
        failure="cannot normalize certificate public key",
    )
    private_public_der = run_private(
        [openssl, "pkey", "-in", str(private_key_pem), "-pubout", "-outform", "DER"],
        failure="cannot derive private-key public key",
    )
    if not hashlib.sha256(certificate_public_der).digest() == hashlib.sha256(
        private_public_der
    ).digest():
        raise SigningError("P12 private key does not match its certificate")
    run_private(
        [openssl, "x509", "-in", str(certificate_pem), "-checkend", "0", "-noout"],
        failure="P12 certificate is expired",
    )
    return certificate_der.read_bytes()


def normalize_password_file(source: Path, destination: Path) -> None:
    try:
        password = source.read_bytes().rstrip(b"\r\n")
    except OSError as error:
        raise SigningError(f"cannot read P12 password file: {type(error).__name__}") from error
    if not password or b"\x00" in password or b"\r" in password or b"\n" in password:
        raise SigningError("P12 password file must contain exactly one non-empty text line")
    destination.write_bytes(password)
    destination.chmod(0o600)


def profile_covers_bundle(application_identifier: str, prefixes: list, bundle_id: str) -> bool:
    for raw_prefix in prefixes:
        if not isinstance(raw_prefix, str):
            continue
        prefix = raw_prefix.rstrip(".")
        marker = f"{prefix}."
        if not application_identifier.startswith(marker):
            continue
        pattern = application_identifier[len(marker) :]
        if pattern == bundle_id:
            return True
        if "*" in pattern and fnmatch.fnmatchcase(bundle_id, pattern):
            return True
    return False


def derive_signing_entitlements(entitlements: dict, prefixes: list, bundle_id: str) -> dict:
    application_identifier = entitlements["application-identifier"]
    selected_prefix = None
    for raw_prefix in prefixes:
        if not isinstance(raw_prefix, str):
            continue
        prefix = raw_prefix.rstrip(".")
        marker = f"{prefix}."
        if not application_identifier.startswith(marker):
            continue
        pattern = application_identifier[len(marker) :]
        if pattern == bundle_id or ("*" in pattern and fnmatch.fnmatchcase(bundle_id, pattern)):
            selected_prefix = prefix
            break
    if selected_prefix is None:
        raise SigningError("cannot derive concrete application entitlements from the profile")
    derived = dict(entitlements)
    concrete_application_identifier = f"{selected_prefix}.{bundle_id}"
    derived["application-identifier"] = concrete_application_identifier
    groups = derived.get("keychain-access-groups")
    if isinstance(groups, list):
        concrete_groups = []
        for group in groups:
            if (
                isinstance(group, str)
                and "*" in group
                and fnmatch.fnmatchcase(concrete_application_identifier, group)
            ):
                concrete_groups.append(concrete_application_identifier)
            else:
                concrete_groups.append(group)
        derived["keychain-access-groups"] = concrete_groups
    return derived


def validate_profile(
    profile: dict,
    certificate_der: bytes,
    expected_team: str,
    bundle_id: str,
) -> dict:
    teams = profile.get("TeamIdentifier")
    if not isinstance(teams, list) or expected_team not in teams:
        raise SigningError("provisioning profile does not match the configured team")
    expiration = profile.get("ExpirationDate")
    if not isinstance(expiration, datetime):
        raise SigningError("provisioning profile has no valid expiration date")
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    if expiration <= datetime.now(timezone.utc):
        raise SigningError("provisioning profile is expired")
    entitlements = profile.get("Entitlements")
    if not isinstance(entitlements, dict):
        raise SigningError("provisioning profile has no entitlements dictionary")
    if entitlements.get("com.apple.developer.team-identifier") != expected_team:
        raise SigningError("profile entitlements do not match the configured team")
    if entitlements.get("get-task-allow") is not True:
        raise SigningError("profile is not an iOS development profile")
    application_identifier = entitlements.get("application-identifier")
    prefixes = profile.get("ApplicationIdentifierPrefix")
    if not isinstance(application_identifier, str) or not isinstance(prefixes, list):
        raise SigningError("profile has invalid application-identifier metadata")
    if not profile_covers_bundle(application_identifier, prefixes, bundle_id):
        raise SigningError("provisioning profile does not cover the configured bundle identifier")
    devices = profile.get("ProvisionedDevices")
    if not isinstance(devices, list) or not devices or not all(
        isinstance(device, str) and device for device in devices
    ):
        raise SigningError("profile has no valid provisioned-device list")
    certificates = profile.get("DeveloperCertificates")
    if not isinstance(certificates, list) or certificate_der not in certificates:
        raise SigningError("P12 certificate is not included in the provisioning profile")
    return derive_signing_entitlements(entitlements, prefixes, bundle_id)


def validate_connected_device(pymobiledevice3_root: Path, profile_payload: Path) -> None:
    root = pymobiledevice3_root.expanduser().resolve()
    python = root / "bin" / "python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise SigningError("pymobiledevice3 Python is missing")
    marker = root / ".dotnet-ios-linux-pymobiledevice3"
    if not marker.is_file() or f"pymobiledevice3={EXPECTED_PYMOBILEDEVICE3_VERSION}" not in marker.read_text(
        encoding="utf-8"
    ).splitlines():
        raise SigningError("pymobiledevice3 stage has unexpected provenance")
    probe = r'''
import asyncio, logging, plistlib, sys
from importlib.metadata import version
from pymobiledevice3 import usbmux

async def main():
    if version("pymobiledevice3") != "11.26.0":
        print("version=false")
        return
    devices = [device for device in await usbmux.list_devices() if device.is_usb]
    print(f"usb_count={len(devices)}")
    if len(devices) != 1:
        return
    with open(sys.argv[1], "rb") as stream:
        allowed = plistlib.load(stream).get("ProvisionedDevices", [])
    print(f"authorized={'true' if devices[0].serial in allowed else 'false'}")

logging.disable(logging.CRITICAL)
asyncio.run(main())
'''
    output = run_private(
        [str(python), "-c", probe, str(profile_payload)],
        failure="connected-device authorization probe failed",
    ).decode("utf-8", errors="strict")
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if values.get("version", "true") != "true":
        raise SigningError("pymobiledevice3 version mismatch")
    if values.get("usb_count") != "1":
        raise SigningError("exactly one USB device is required for profile validation")
    if values.get("authorized") != "true":
        raise SigningError("connected USB device is not authorized by the provisioning profile")


def sign_entity(
    rcodesign: Path,
    entity: Path,
    private_key_pem: Path,
    certificate_pem: Path,
    team: str,
    entitlements: Path | None = None,
) -> None:
    command = [
        str(rcodesign),
        "--config-file",
        "/dev/null",
        "sign",
        "--pem-file",
        str(private_key_pem),
        "--pem-file",
        str(certificate_pem),
        "--team-name",
        team,
        "--timestamp-url",
        "none",
    ]
    if entitlements is not None:
        command.extend(["--entitlements-xml-file", str(entitlements)])
    command.append(str(entity))
    run_private(command, failure="rcodesign signing operation failed")


def execute(arguments: argparse.Namespace) -> None:
    config = load_config(arguments.config)
    identity = config["identity"]
    p12 = require_private_file(Path(identity["p12"]), "P12 identity")
    password_file = require_private_file(Path(identity["passwordFile"]), "P12 password file")
    profile_path = require_private_file(
        Path(config["provisioningProfile"]), "provisioning profile"
    )
    expected_team = config["expectedTeamIdentifier"]
    expected_bundle = config["expectedBundleIdentifier"]

    rcodesign_root = arguments.rcodesign_root.expanduser().resolve()
    rcodesign = rcodesign_root / "rcodesign"
    marker = rcodesign_root / ".dotnet-ios-linux-rcodesign"
    if not rcodesign.is_file() or not os.access(rcodesign, os.X_OK) or not marker.is_file():
        raise SigningError("rcodesign stage is missing or unowned")
    if hashlib.sha256(rcodesign.read_bytes()).hexdigest() != EXPECTED_RCODESIGN_SHA256:
        raise SigningError("rcodesign executable hash mismatch")
    version = run_private(
        [str(rcodesign), "--version"], failure="rcodesign version check failed"
    ).decode("utf-8", errors="strict").strip()
    if version != EXPECTED_RCODESIGN_VERSION:
        raise SigningError("rcodesign version mismatch")
    openssl = shutil.which("openssl")
    if openssl is None:
        raise SigningError("openssl is required")

    source_app = arguments.app.expanduser().resolve()
    bundle_id, executable = app_metadata(source_app)
    if bundle_id != expected_bundle:
        raise SigningError("source app bundle identifier does not match the private config")
    if has_code_signature_load_command(executable):
        raise SigningError("source app is already signed; an unsigned source is required")
    if (source_app / "_CodeSignature").exists() or (source_app / "embedded.mobileprovision").exists():
        raise SigningError("source app contains prior signing material")
    plug_ins = source_app / "PlugIns"
    if plug_ins.is_dir() and any(plug_ins.glob("*.appex")):
        raise SigningError("app extensions require separate profiles and are not supported by this gate")

    output = arguments.output.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise SigningError("signed output already exists; refusing to replace it")
    if output.suffix != ".app":
        raise SigningError("signed output must end in .app")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".sign-ios-app.", dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        temporary_root.chmod(0o700)
        profile_payload = temporary_root / "profile.plist"
        certificate_pem = temporary_root / "certificate.pem"
        certificate_der = temporary_root / "certificate.der"
        private_key_pem = temporary_root / "private-key.pem"
        entitlements_path = temporary_root / "entitlements.plist"
        normalized_password = temporary_root / "p12-password.txt"
        normalize_password_file(password_file, normalized_password)
        profile = extract_profile(openssl, profile_path, profile_payload)
        certificate = extract_p12_identity(
            openssl,
            p12,
            normalized_password,
            certificate_pem,
            certificate_der,
            private_key_pem,
        )
        entitlements = validate_profile(profile, certificate, expected_team, bundle_id)
        if arguments.pymobiledevice3_root is not None:
            validate_connected_device(arguments.pymobiledevice3_root, profile_payload)
        with entitlements_path.open("wb") as stream:
            plistlib.dump(entitlements, stream, fmt=plistlib.FMT_XML, sort_keys=True)
        entitlements_path.chmod(0o600)

        staged_app = temporary_root / output.name
        shutil.copytree(source_app, staged_app, symlinks=True)
        for path in staged_app.rglob("*"):
            if path.is_symlink():
                continue
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        shutil.copy2(profile_path, staged_app / "embedded.mobileprovision")

        frameworks = staged_app / "Frameworks"
        if frameworks.is_dir():
            for framework in sorted(frameworks.glob("*.framework")):
                sign_entity(
                    rcodesign, framework, private_key_pem, certificate_pem, expected_team
                )
            for library in sorted(frameworks.glob("*.dylib")):
                sign_entity(
                    rcodesign, library, private_key_pem, certificate_pem, expected_team
                )
        sign_entity(
            rcodesign,
            staged_app,
            private_key_pem,
            certificate_pem,
            expected_team,
            entitlements_path,
        )
        _, signed_executable = app_metadata(staged_app)
        if not has_code_signature_load_command(signed_executable):
            raise SigningError("signed executable has no LC_CODE_SIGNATURE")
        if not (staged_app / "_CodeSignature" / "CodeResources").is_file():
            raise SigningError("signed app has no CodeResources seal")
        run_private(
            [
                str(rcodesign),
                "--config-file",
                "/dev/null",
                "verify",
                str(signed_executable),
            ],
            failure="rcodesign executable verification failed",
        )
        os.replace(staged_app, output)

    print(f"signed_app={output}")
    print("bundle_identifier_match=true")
    print("team_identifier_match=true")
    print("profile_development=true")
    print("profile_certificate_match=true")
    print(
        "connected_device_authorized=true"
        if arguments.pymobiledevice3_root is not None
        else "connected_device_authorized=not-checked"
    )
    print("signature_verified=true")


def main() -> int:
    try:
        execute(parse_arguments())
    except SigningError as error:
        fail(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
