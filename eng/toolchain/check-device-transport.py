#!/usr/bin/env python3
"""Report usbmux/lockdown readiness without emitting device identifiers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import PyMobileDevice3Exception
from pymobiledevice3.lockdown import create_using_usbmux


EXPECTED_VERSION = "11.26.0"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check pinned pymobiledevice3 USB transport without printing UDIDs, "
            "serials, names, pair records, or other device values."
        )
    )
    parser.add_argument(
        "--lockdown",
        action="store_true",
        help=(
            "also validate existing lockdown pairing; auto-pair remains disabled "
            "and a temporary private cache is used"
        ),
    )
    return parser.parse_args()


async def inspect_transport(validate_lockdown: bool) -> int:
    try:
        devices = await usbmux.list_devices()
    except Exception as error:  # The message may contain a socket path or identifier.
        print("transport=unavailable")
        print(f"error_type={type(error).__name__}")
        return 3

    usb_devices = [device for device in devices if device.is_usb]
    network_devices = [device for device in devices if device.is_network]
    print("transport=available")
    print(f"device_count={len(devices)}")
    print(f"usb_device_count={len(usb_devices)}")
    print(f"network_device_count={len(network_devices)}")

    if not validate_lockdown:
        print("lockdown=not-requested")
        return 0 if usb_devices else 4

    paired = 0
    unpaired = 0
    failed_by_type: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-pairing-") as cache:
        cache_path = Path(cache)
        cache_path.chmod(0o700)
        for device in usb_devices:
            client = None
            try:
                client = await create_using_usbmux(
                    serial=device.serial,
                    autopair=False,
                    connection_type=device.connection_type,
                    pairing_records_cache_folder=cache_path,
                )
                if client.paired:
                    paired += 1
                else:
                    unpaired += 1
            except (PyMobileDevice3Exception, OSError) as error:
                error_name = type(error).__name__
                failed_by_type[error_name] = failed_by_type.get(error_name, 0) + 1
            finally:
                if client is not None:
                    await client.close()

    print("lockdown=validated")
    print(f"lockdown_paired_count={paired}")
    print(f"lockdown_unpaired_count={unpaired}")
    print(f"lockdown_failed_count={sum(failed_by_type.values())}")
    for error_name in sorted(failed_by_type):
        print(f"lockdown_error_{error_name}={failed_by_type[error_name]}")
    return 0 if usb_devices and paired == len(usb_devices) else 5


def main() -> int:
    arguments = parse_arguments()
    actual_version = version("pymobiledevice3")
    if actual_version != EXPECTED_VERSION:
        print(
            f"pymobiledevice3 version mismatch: {actual_version} != {EXPECTED_VERSION}",
            file=sys.stderr,
        )
        return 2

    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(inspect_transport(arguments.lockdown))
    except Exception as error:  # Never allow a raw exception to disclose device data.
        print("probe=failed")
        print(f"error_type={type(error).__name__}")
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
