#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
    @staticmethod
    def write_signing_config(path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "identity": {
                        "p12": str(path.parent / "missing-development.p12"),
                        "passwordFile": str(path.parent / "missing-password.txt"),
                    },
                    "provisioningProfile": str(path.parent / "missing.mobileprovision"),
                    "expectedTeamIdentifier": "TEAM000001",
                    "expectedBundleIdentifier": "dev.dotnetioslinux.helloios",
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)

    @staticmethod
    def runner_environment(root: Path) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HOME"] = str(root / "home")
        environment["XDG_DATA_HOME"] = str(root / "data")
        environment["XDG_CACHE_HOME"] = str(root / "cache")
        return environment

    def run_runner(
        self,
        root: Path,
        invocation_directory: Path,
        project: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(RUNNER),
                "--project",
                str(project),
                *arguments,
                "--confirm-install",
                "--confirm-launch",
            ],
            cwd=invocation_directory,
            env=self.runner_environment(root),
            text=True,
            capture_output=True,
            check=False,
        )

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

    def test_runner_discovers_signing_config_in_invocation_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-runner-") as temporary:
            root = Path(temporary)
            invocation_directory = root / "invocation"
            project_directory = root / "project"
            invocation_directory.mkdir()
            project_directory.mkdir()
            project = project_directory / "App.csproj"
            project.write_text("<Project />\n", encoding="utf-8")
            self.write_signing_config(invocation_directory / "ios-signing.json")

            result = self.run_runner(root, invocation_directory, project)

            self.assertEqual(1, result.returncode)
            self.assertIn("installed data root is missing", result.stderr)
            self.assertNotIn("signing configuration", result.stderr)

    def test_runner_does_not_fall_back_to_project_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-runner-") as temporary:
            root = Path(temporary)
            invocation_directory = root / "invocation"
            project_directory = root / "project"
            invocation_directory.mkdir()
            project_directory.mkdir()
            project = project_directory / "App.csproj"
            project.write_text("<Project />\n", encoding="utf-8")
            self.write_signing_config(project_directory / "ios-signing.json")

            result = self.run_runner(root, invocation_directory, project)

            self.assertEqual(2, result.returncode)
            self.assertIn(
                f"no signing configuration was found at {invocation_directory}/ios-signing.json",
                result.stderr,
            )
            self.assertIn("provide --signing-config", result.stderr)

    def test_explicit_signing_config_overrides_invocation_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-runner-") as temporary:
            root = Path(temporary)
            invocation_directory = root / "invocation"
            project_directory = root / "project"
            private_directory = root / "private"
            invocation_directory.mkdir()
            project_directory.mkdir()
            private_directory.mkdir()
            project = project_directory / "App.csproj"
            project.write_text("<Project />\n", encoding="utf-8")
            default_config = invocation_directory / "ios-signing.json"
            default_config.write_text("not json\n", encoding="utf-8")
            default_config.chmod(0o600)
            explicit_config = private_directory / "signing.json"
            self.write_signing_config(explicit_config)

            result = self.run_runner(
                root,
                invocation_directory,
                project,
                "--signing-config",
                str(explicit_config),
            )

            self.assertEqual(1, result.returncode)
            self.assertIn("installed data root is missing", result.stderr)
            self.assertNotIn("cannot read signing config", result.stderr)


if __name__ == "__main__":
    unittest.main()
