#!/usr/bin/env python3
"""Install and launch the signed probe without auto-pairing or identifier output."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import logging
import os
import plistlib
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn

from pymobiledevice3 import usbmux
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.remote.core_device.app_service import AppServiceService
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.mobile_image_mounter import PersonalizedImageMounter


EXPECTED_PYMOBILEDEVICE3_VERSION = "11.26.0"
EXPECTED_BUNDLE_IDENTIFIER = "dev.dotnetioslinux.helloios"


class DeviceWorkflowError(Exception):
    """An expected failure whose message contains no device data."""


def fail(message: str) -> NoReturn:
    print(f"device-ios-app: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_signing_module():
    path = Path(__file__).with_name("sign-ios-app.py")
    spec = importlib.util.spec_from_file_location("dotnet_ios_linux_signing", path)
    if spec is None or spec.loader is None:
        raise DeviceWorkflowError("cannot load signing validation module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIGNING = load_signing_module()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use pinned pymobiledevice3 without auto-pairing and without printing device "
            "identifiers. Install and launch require explicit confirmation flags."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    preflight = subparsers.add_parser("preflight", help="read-only app/device checks")
    preflight.add_argument("--app", required=True, type=Path)
    preflight.add_argument("--rcodesign-root", required=True, type=Path)

    install = subparsers.add_parser("install", help="install the signed app")
    install.add_argument("--app", required=True, type=Path)
    install.add_argument("--rcodesign-root", required=True, type=Path)
    install.add_argument(
        "--confirm-install",
        action="store_true",
        help="required acknowledgement that this action mutates the connected device",
    )

    launch = subparsers.add_parser("launch", help="launch the installed probe")
    launch.add_argument(
        "--confirm-launch",
        action="store_true",
        help="required acknowledgement that this action starts the app on the device",
    )
    return parser.parse_args()


def run_private(command: list[str], failure: str) -> bytes:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise DeviceWorkflowError(f"{failure}: {type(error).__name__}") from error
    if result.returncode != 0:
        raise DeviceWorkflowError(failure)
    return result.stdout


def validate_stage() -> None:
    if version("pymobiledevice3") != EXPECTED_PYMOBILEDEVICE3_VERSION:
        raise DeviceWorkflowError("pymobiledevice3 version mismatch")
    marker = Path(sys.prefix).resolve() / ".dotnet-ios-linux-pymobiledevice3"
    if not marker.is_file():
        raise DeviceWorkflowError("pymobiledevice3 environment is not project-owned")
    expected = f"pymobiledevice3={EXPECTED_PYMOBILEDEVICE3_VERSION}"
    try:
        marker_lines = marker.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise DeviceWorkflowError(f"cannot read pymobiledevice3 marker: {type(error).__name__}") from error
    if expected not in marker_lines:
        raise DeviceWorkflowError("pymobiledevice3 environment has unexpected provenance")


def decode_profile(profile: Path) -> dict:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise DeviceWorkflowError("openssl is required")
    payload = run_private(
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
        "embedded provisioning profile verification failed",
    )
    try:
        value = plistlib.loads(payload)
    except plistlib.InvalidFileException as error:
        raise DeviceWorkflowError("embedded provisioning profile payload is invalid") from error
    if not isinstance(value, dict):
        raise DeviceWorkflowError("embedded provisioning profile payload is invalid")
    return value


def validate_signed_app(app: Path, rcodesign_root: Path) -> tuple[Path, dict]:
    try:
        resolved = app.expanduser().resolve(strict=True)
    except OSError as error:
        raise DeviceWorkflowError(f"cannot access signed app: {type(error).__name__}") from error
    bundle_identifier, executable = SIGNING.app_metadata(resolved)
    if bundle_identifier != EXPECTED_BUNDLE_IDENTIFIER:
        raise DeviceWorkflowError("signed app has an unexpected bundle identifier")
    if not SIGNING.has_code_signature_load_command(executable):
        raise DeviceWorkflowError("app executable has no LC_CODE_SIGNATURE")
    if not (resolved / "_CodeSignature" / "CodeResources").is_file():
        raise DeviceWorkflowError("app has no CodeResources seal")
    profile_path = resolved / "embedded.mobileprovision"
    if not profile_path.is_file():
        raise DeviceWorkflowError("app has no embedded provisioning profile")
    rcodesign = rcodesign_root.expanduser().resolve() / "rcodesign"
    if not rcodesign.is_file() or not os.access(rcodesign, os.X_OK):
        raise DeviceWorkflowError("rcodesign executable is missing")
    if hashlib.sha256(rcodesign.read_bytes()).hexdigest() != SIGNING.EXPECTED_RCODESIGN_SHA256:
        raise DeviceWorkflowError("rcodesign executable hash mismatch")
    run_private(
        [str(rcodesign), "--config-file", "/dev/null", "verify", str(executable)],
        "app executable signature verification failed",
    )
    return resolved, decode_profile(profile_path)


async def sole_usb_device():
    devices = [device for device in await usbmux.list_devices() if device.is_usb]
    if len(devices) != 1:
        raise DeviceWorkflowError("exactly one USB device is required")
    return devices[0]


async def open_existing_pairing(device):
    lockdown = await create_using_usbmux(
        serial=device.serial,
        connection_type=device.connection_type,
        autopair=False,
    )
    if not lockdown.paired:
        await lockdown.close()
        raise DeviceWorkflowError("the connected USB device has no valid existing pairing")
    return lockdown


def profile_authorizes_device(profile: dict, serial: str) -> bool:
    devices = profile.get("ProvisionedDevices")
    return isinstance(devices, list) and serial in devices


async def personalized_image_is_mounted(lockdown) -> bool:
    async with PersonalizedImageMounter(lockdown) as mounter:
        return await mounter.is_image_mounted("Personalized")


async def preflight(app: Path, rcodesign_root: Path) -> tuple[Path, object, bool]:
    signed_app, profile = validate_signed_app(app, rcodesign_root)
    device = await sole_usb_device()
    if not profile_authorizes_device(profile, device.serial):
        raise DeviceWorkflowError("connected USB device is not authorized by the embedded profile")
    lockdown = await open_existing_pairing(device)
    try:
        mounted = await personalized_image_is_mounted(lockdown)
    finally:
        await lockdown.close()
    return signed_app, device, mounted


async def install_app(app: Path, rcodesign_root: Path) -> bool:
    signed_app, profile = validate_signed_app(app, rcodesign_root)
    device = await sole_usb_device()
    if not profile_authorizes_device(profile, device.serial):
        raise DeviceWorkflowError("connected USB device is not authorized by the embedded profile")
    lockdown = await open_existing_pairing(device)
    try:
        installation = InstallationProxyService(lockdown=lockdown)
        await installation.install_from_local(signed_app, developer=True)
        apps = await installation.get_apps(bundle_identifiers=[EXPECTED_BUNDLE_IDENTIFIER])
        return EXPECTED_BUNDLE_IDENTIFIER in apps
    finally:
        await lockdown.close()


async def launch_app() -> None:
    device = await sole_usb_device()
    lockdown = await open_existing_pairing(device)
    try:
        if not await personalized_image_is_mounted(lockdown):
            raise DeviceWorkflowError("a personalized Developer Disk Image is not mounted")
    finally:
        await lockdown.close()
    async with UserspaceRsdTunnel(
        serial=device.serial,
        autopair=False,
        remotepairing_fallback=False,
    ) as rsd:
        async with AppServiceService(rsd) as app_service:
            await app_service.launch_application(
                EXPECTED_BUNDLE_IDENTIFIER,
                arguments=[],
                kill_existing=True,
                start_suspended=False,
                environment={},
            )


async def execute(arguments: argparse.Namespace) -> None:
    validate_stage()
    if arguments.action == "preflight":
        _, _, mounted = await preflight(arguments.app, arguments.rcodesign_root)
        print("transport=usb")
        print("device_count=1")
        print("pairing=existing-valid")
        print("profile_device_authorized=true")
        print("app_signature_preflight=true")
        print(f"personalized_developer_image_mounted={'true' if mounted else 'false'}")
        return
    if arguments.action == "install":
        if not arguments.confirm_install:
            raise DeviceWorkflowError("install requires --confirm-install")
        if not await install_app(arguments.app, arguments.rcodesign_root):
            raise DeviceWorkflowError("installation completed but app query did not find the bundle")
        print("installation=success")
        print("installed_bundle_match=true")
        return
    if arguments.action == "launch":
        if not arguments.confirm_launch:
            raise DeviceWorkflowError("launch requires --confirm-launch")
        await launch_app()
        print("launch=success")
        print("launched_bundle_match=true")
        return
    raise DeviceWorkflowError("unsupported action")


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(execute(parse_arguments()))
    except DeviceWorkflowError as error:
        fail(str(error))
    except Exception as error:
        fail(f"operation failed without identifier output: {type(error).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
