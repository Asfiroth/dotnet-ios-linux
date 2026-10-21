#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
DEVICE_MODULE_PATH = REPOSITORY / "eng" / "toolchain" / "device-ios-app.py"
RUNNER = REPOSITORY / "run-ios-app.sh"


def load_device_module():
    pymobiledevice3 = types.ModuleType("pymobiledevice3")
    pymobiledevice3.__path__ = []
    usbmux = types.ModuleType("pymobiledevice3.usbmux")
    pymobiledevice3.usbmux = usbmux

    lockdown = types.ModuleType("pymobiledevice3.lockdown")
    lockdown.create_using_usbmux = object()
    app_service = types.ModuleType("pymobiledevice3.remote.core_device.app_service")
    app_service.AppServiceService = object
    userspace_tunnel = types.ModuleType("pymobiledevice3.remote.userspace_tunnel")
    userspace_tunnel.UserspaceRsdTunnel = object
    installation_proxy = types.ModuleType("pymobiledevice3.services.installation_proxy")
    installation_proxy.InstallationProxyService = object
    image_mounter = types.ModuleType("pymobiledevice3.services.mobile_image_mounter")
    image_mounter.PersonalizedImageMounter = object

    fake_modules = {
        "pymobiledevice3": pymobiledevice3,
        "pymobiledevice3.usbmux": usbmux,
        "pymobiledevice3.lockdown": lockdown,
        "pymobiledevice3.remote": types.ModuleType("pymobiledevice3.remote"),
        "pymobiledevice3.remote.core_device": types.ModuleType(
            "pymobiledevice3.remote.core_device"
        ),
        "pymobiledevice3.remote.core_device.app_service": app_service,
        "pymobiledevice3.remote.userspace_tunnel": userspace_tunnel,
        "pymobiledevice3.services": types.ModuleType("pymobiledevice3.services"),
        "pymobiledevice3.services.installation_proxy": installation_proxy,
        "pymobiledevice3.services.mobile_image_mounter": image_mounter,
    }
    spec = importlib.util.spec_from_file_location("device_ios_app", DEVICE_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, fake_modules):
        spec.loader.exec_module(module)
    return module


DEVICE = load_device_module()


class DeviceRunnerContracts(unittest.TestCase):
    def test_device_workflow_accepts_application_bundle_identifier(self) -> None:
        self.assertEqual(
            "com.example.unoapp",
            DEVICE.validate_bundle_identifier("com.example.unoapp"),
        )
        with self.assertRaisesRegex(DEVICE.DeviceWorkflowError, "bundle identifier is invalid"):
            DEVICE.validate_bundle_identifier("not a bundle/id")

    def test_runner_help_states_scope_and_debug_boundary(self) -> None:
        result = subprocess.run(
            [str(RUNNER), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--confirm-install", result.stdout)
        self.assertIn("does not attach a\ndebugger", result.stdout)

    def test_runner_requires_explicit_device_confirmations(self) -> None:
        result = subprocess.run(
            [
                str(RUNNER),
                "--project",
                "/missing/App.csproj",
                "--signing-config",
                "/missing/signing.json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--confirm-install is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
