#!/usr/bin/env python3
"""Build a locked, plan-scoped local Apple-workload feed."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import publish_directory_noreplace


PACKAGER_PATH = Path(__file__).resolve().parents[1] / "runtime" / "package-aot-pack.py"
PACKAGER_SPEC = importlib.util.spec_from_file_location("dotnet_ios_linux_aot_packager", PACKAGER_PATH)
assert PACKAGER_SPEC is not None and PACKAGER_SPEC.loader is not None
AOT_PACKAGER = importlib.util.module_from_spec(PACKAGER_SPEC)
PACKAGER_SPEC.loader.exec_module(AOT_PACKAGER)


PROJECT_VERSION = "0.1.0-preview.1"
MANIFEST_ID = "Microsoft.NET.Sdk.iOS.Manifest-10.0.100"
MANIFEST_VERSION = "27.0.10722"
MANIFEST_SHA512 = "E67WwID69ZQmMd0fs8OFeExzyso/7rJk7QfIkK+fs+cEQj6e2s/hUA/iPtP36Li08YZVp8L7iY6Uk+zmfdU5Ww=="
COMPANION_MANIFESTS = {
    "macos": {
        "id": "Microsoft.NET.Sdk.macOS.Manifest-10.0.100",
        "sha512": "8WkzZbAVFxEpmyy1ZN4Rphgiz1ztwj5PgjyCtQ3v3Cw3b7a+kdOMPXrW7t4Etg+sNHsJ0JnNJz7vvlErRpfOIw==",
    },
    "maccatalyst": {
        "id": "Microsoft.NET.Sdk.MacCatalyst.Manifest-10.0.100",
        "sha512": "sjKXMijKU7PPIPUPfwf6+F3q1sxv5/gIVC6uc/sv/EnUnmTVWsUbHFxLNFVuIogbMH2sXUO1IHkwbont7AC+0A==",
    },
    "tvos": {
        "id": "Microsoft.NET.Sdk.tvOS.Manifest-10.0.100",
        "sha512": "zpEboVYprwxJcL/k15szLXtleBqJdF86wMEJvla6GUJkyX2ONgqatZPKYExvAmiMvKG+ltobdPoCiV0xLjHJSw==",
    },
}
SDK_PACK_ID = "DotnetIosLinux.Sdk"
AOT_PACK_ID = "DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64"
RUNTIME_COMMIT = "4271d88e0aebf3d04f188f1334c2220d80555ef6"
MARKER = ".dotnet-ios-linux-feed.json"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
LINUX_IOS_PACKS = [
    "Microsoft.iOS.Sdk.net10.0_27.0",
    # macios intentionally selects the previous net10 SDK for library projects
    # without an explicit TargetPlatformVersion (for example AotProbe).
    "Microsoft.iOS.Sdk.net10.0_26.0",
    "Microsoft.iOS.Ref.net10.0_27.0",
    "Microsoft.iOS.Runtime.ios.net10.0_27.0",
    "Microsoft.iOS.Runtime.ios-arm64.net10.0_27.0",
    "Microsoft.iOS.Templates.net9",
    "Microsoft.NETCore.App.Runtime.Mono.net10.ios-arm64",
    "Microsoft.NET.Runtime.MonoAOTCompiler.Task.net10",
    "Microsoft.NET.Runtime.MonoTargets.Sdk.net10",
    SDK_PACK_ID,
    AOT_PACK_ID,
]
def fail(message: str) -> "None":
    raise SystemExit(f"local workload feed: {message}")


def digest(path: Path, algorithm: str) -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sha512_base64(path: Path) -> str:
    return base64.b64encode(bytes.fromhex(digest(path, "sha512"))).decode("ascii")


def relaxed_json(data: bytes, label: str) -> dict:
    # Workload manifests emitted by macios use JSON-with-trailing-commas, which
    # the .NET workload reader accepts. Emit strict JSON in the derived package.
    text = data.decode("utf-8-sig")
    text = re.sub(r",(?=\s*[}\]])", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        fail(f"cannot parse {label}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} root must be an object")
    return value


def read_nupkg(path: Path, package_id: str, sha512: str) -> dict[str, bytes]:
    if not path.is_file():
        fail(f"official manifest package is missing: {path}")
    actual = sha512_base64(path)
    if actual != sha512:
        fail(f"official manifest SHA-512 mismatch: expected {sha512}, got {actual}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                fail("official manifest package contains duplicate archive members")
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                fail("official manifest package contains an unsafe archive path")
            return {name: archive.read(name) for name in names if not name.endswith("/")}
    except zipfile.BadZipFile as error:
        fail(f"official manifest package is not a valid ZIP archive: {error}")


def nuspec_metadata(data: bytes) -> tuple[str, str]:
    text = data.decode("utf-8-sig")
    package_id = re.search(r"<id>([^<]+)</id>", text)
    version = re.search(r"<version>([^<]+)</version>", text)
    if package_id is None or version is None:
        fail("official manifest nuspec lacks an ID or version")
    return package_id.group(1), version.group(1)


def manifest_files(files: dict[str, bytes], package_id: str, workload_id: str) -> tuple[dict, dict, str]:
    nuspec_name = f"{package_id}.nuspec"
    required = {
        nuspec_name,
        "Icon.png",
        "LICENSE",
        "data/WorkloadDependencies.json",
        "data/WorkloadManifest.json",
        "data/WorkloadManifest.targets",
    }
    missing = sorted(required - files.keys())
    if missing:
        fail("official manifest package is incomplete: " + ", ".join(missing))
    if nuspec_metadata(files[nuspec_name]) != (package_id, MANIFEST_VERSION):
        fail("official manifest package identity does not match the lock")

    manifest = relaxed_json(files["data/WorkloadManifest.json"], "WorkloadManifest.json")
    if manifest.get("version") != MANIFEST_VERSION:
        fail("official workload manifest version does not match the lock")
    workloads = manifest.get("workloads")
    packs = manifest.get("packs")
    if not isinstance(workloads, dict) or not isinstance(packs, dict):
        fail("official workload manifest lacks workload or pack maps")
    workload = workloads.get(workload_id)
    if not isinstance(workload, dict) or not isinstance(workload.get("packs"), list):
        fail(f"official workload manifest lacks the {workload_id} workload pack list")
    return manifest, workload, nuspec_name


def derive_manifest(files: dict[str, bytes]) -> dict[str, bytes]:
    manifest, ios, nuspec_name = manifest_files(files, MANIFEST_ID, "ios")
    packs = manifest["packs"]

    extensions = ios.get("extends")
    if not isinstance(extensions, list) or "microsoft-net-runtime-ios" not in extensions:
        fail("official ios workload does not extend the expected net10 runtime workload")
    # The official AOT workload has no linux-x64 alias for its iOS cross packs,
    # and extending either official iOS runtime workload causes the CLI to mark
    # the parent workload unsupported. Reference the exact net10 physical-device
    # runtime/tooling packs directly and replace only the AOT compiler.
    ios["packs"] = list(LINUX_IOS_PACKS)
    ios.pop("extends", None)
    ios["platforms"] = ["linux-x64"]

    for package_id in (SDK_PACK_ID, AOT_PACK_ID):
        if package_id in packs:
            fail(f"official workload manifest unexpectedly already defines {package_id}")
        packs[package_id] = {"kind": "sdk", "version": PROJECT_VERSION}

    upstream_targets = files["data/WorkloadManifest.targets"].decode("utf-8-sig")
    if not upstream_targets.lstrip().startswith("<Project>"):
        fail("official WorkloadManifest.targets has an unexpected root")
    imports = f"""
  <!-- Locally generated by dotnet-ios-linux from the hash-verified official manifest. -->
  <ImportGroup Condition="$([MSBuild]::IsOSPlatform('linux')) And '$(TargetPlatformIdentifier)' == 'iOS' And $([MSBuild]::VersionEquals($(TargetFrameworkVersion), '10.0'))">
    <Import Project="Sdk.props" Sdk="{SDK_PACK_ID}" />
    <Import Project="Sdk.props" Sdk="{AOT_PACK_ID}" />
    <Import Project="Sdk.targets" Sdk="{SDK_PACK_ID}" />
  </ImportGroup>
"""
    targets = upstream_targets.replace("<Project>", "<Project>" + imports, 1)

    nuspec = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2011/08/nuspec.xsd">
  <metadata>
    <id>{MANIFEST_ID}</id>
    <version>{MANIFEST_VERSION}</version>
    <authors>Microsoft;Asfiroth</authors>
    <requireLicenseAcceptance>false</requireLicenseAcceptance>
    <license type="file">LICENSE</license>
    <icon>Icon.png</icon>
    <projectUrl>https://github.com/Asfiroth/dotnet-ios-linux</projectUrl>
    <description>Local Linux-capable derivative of the pinned Microsoft iOS workload manifest.</description>
    <copyright>Microsoft Corporation and dotnet-ios-linux contributors.</copyright>
    <packageTypes><packageType name="DotnetPlatform" /></packageTypes>
    <repository type="git" url="https://github.com/dotnet/macios" commit="d813e2baef17cd3a2bb5adc1e37610258d01cda3" />
  </metadata>
</package>
""".encode()
    marker = {
        "schemaVersion": 1,
        "owner": "dotnet-ios-linux",
        "derivation": "local",
        "officialPackageId": MANIFEST_ID,
        "officialPackageVersion": MANIFEST_VERSION,
        "officialPackageSha512Base64": MANIFEST_SHA512,
        "projectPackVersion": PROJECT_VERSION,
        "addedPacks": [SDK_PACK_ID, AOT_PACK_ID],
        "runtimeGraph": "direct-net10-physical-device",
        "platforms": ["linux-x64"],
    }
    return {
        nuspec_name: nuspec,
        "Icon.png": files["Icon.png"],
        "LICENSE": files["LICENSE"],
        "data/WorkloadDependencies.json": files["data/WorkloadDependencies.json"],
        "data/WorkloadManifest.json": (json.dumps(manifest, indent=2) + "\n").encode(),
        "data/WorkloadManifest.targets": targets.encode(),
        "data/dotnet-ios-linux.json": (json.dumps(marker, indent=2) + "\n").encode(),
    }


def derive_companion_manifest(files: dict[str, bytes], workload_id: str) -> dict[str, bytes]:
    spec = COMPANION_MANIFESTS[workload_id]
    package_id = spec["id"]
    manifest, workload, nuspec_name = manifest_files(files, package_id, workload_id)
    # macios exposes these workloads only through macOS-hosted Mono/AOT aliases.
    # For registration on Linux, retain the authentic direct packs but remove the
    # unsupported execution closure.  This package does not qualify a build or
    # runtime path; that limitation is encoded in the derivative marker/receipt.
    workload.pop("extends", None)
    workload["platforms"] = ["linux-x64"]
    marker = {
        "schemaVersion": 1,
        "owner": "dotnet-ios-linux",
        "derivation": "local",
        "officialPackageId": package_id,
        "officialPackageVersion": MANIFEST_VERSION,
        "officialPackageSha512Base64": spec["sha512"],
        "workloadId": workload_id,
        "supportClassification": "installed-unqualified",
        "removedUnsupportedExtends": True,
        "platforms": ["linux-x64"],
    }
    nuspec = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2011/08/nuspec.xsd">
  <metadata>
    <id>{package_id}</id>
    <version>{MANIFEST_VERSION}</version>
    <authors>Microsoft;Asfiroth</authors>
    <requireLicenseAcceptance>false</requireLicenseAcceptance>
    <license type="file">LICENSE</license>
    <icon>Icon.png</icon>
    <projectUrl>https://github.com/Asfiroth/dotnet-ios-linux</projectUrl>
    <description>Local Linux registration-only derivative of the pinned Microsoft {workload_id} workload manifest.</description>
    <copyright>Microsoft Corporation and dotnet-ios-linux contributors.</copyright>
    <packageTypes><packageType name="DotnetPlatform" /></packageTypes>
    <repository type="git" url="https://github.com/dotnet/macios" commit="d813e2baef17cd3a2bb5adc1e37610258d01cda3" />
  </metadata>
</package>
""".encode()
    return {
        nuspec_name: nuspec,
        "Icon.png": files["Icon.png"],
        "LICENSE": files["LICENSE"],
        "data/WorkloadDependencies.json": files["data/WorkloadDependencies.json"],
        "data/WorkloadManifest.json": (json.dumps(manifest, indent=2) + "\n").encode(),
        "data/WorkloadManifest.targets": files["data/WorkloadManifest.targets"],
        "data/dotnet-ios-linux.json": (json.dumps(marker, indent=2) + "\n").encode(),
    }


def verify_project_nuspec(path: Path, package_id: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as error:
        fail(f"cannot read project nuspec {path}: {error}")
    if nuspec_metadata(data) != (package_id, PROJECT_VERSION):
        fail(f"project nuspec identity mismatch: {path}")
    return data


def verify_aot_pack(root: Path) -> None:
    try:
        AOT_PACKAGER.verify_published_aot_pack(root)
    except SystemExit as error:
        fail(str(error))


def zip_info(name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.create_system = 3
    return info


def write_nupkg(path: Path, files: dict[str, tuple[bytes, bool] | bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            value = files[name]
            if isinstance(value, tuple):
                data, executable = value
            else:
                data, executable = value, False
            archive.writestr(zip_info(name, executable), data)


def package_sdk(repository: Path, output: Path) -> None:
    root = repository / "src" / "packs" / SDK_PACK_ID
    files: dict[str, tuple[bytes, bool] | bytes] = {
        f"{SDK_PACK_ID}.nuspec": verify_project_nuspec(root / f"{SDK_PACK_ID}.nuspec", SDK_PACK_ID),
        "LICENSE": (repository / "LICENSE").read_bytes(),
        "Sdk/Sdk.props": (root / "Sdk" / "Sdk.props").read_bytes(),
        "Sdk/Sdk.targets": (root / "Sdk" / "Sdk.targets").read_bytes(),
        "Sdk/DotnetIosLinux.Integration.targets": (
            repository / "src" / "msbuild" / "DotnetIosLinux.Integration.targets"
        ).read_bytes(),
    }
    write_nupkg(output, files)


def package_aot(repository: Path, aot_root: Path, output: Path) -> None:
    verify_aot_pack(aot_root)
    source = repository / "src" / "packs" / AOT_PACK_ID
    files: dict[str, tuple[bytes, bool] | bytes] = {
        f"{AOT_PACK_ID}.nuspec": verify_project_nuspec(source / f"{AOT_PACK_ID}.nuspec", AOT_PACK_ID),
        "LICENSE": (repository / "LICENSE").read_bytes(),
        "Sdk/Sdk.props": (aot_root / "Sdk" / "Sdk.props").read_bytes(),
        "provenance.json": (aot_root / "provenance.json").read_bytes(),
    }
    for path in sorted((aot_root / "tools").iterdir()):
        if path.is_symlink() or not path.is_file():
            fail(f"AOT tools payload must contain regular files only: {path}")
        files[f"tools/{path.name}"] = (path.read_bytes(), os.access(path, os.X_OK))
    write_nupkg(output, files)


def nuget_config(feed: Path, workloads: set[str] | None = None) -> str:
    workloads = workloads or {"ios", *COMPANION_MANIFESTS}
    source = html.escape(str(feed), quote=True)
    local_manifests = []
    if "ios" in workloads:
        local_manifests.append(MANIFEST_ID)
    local_manifests.extend(
        COMPANION_MANIFESTS[workload_id]["id"]
        for workload_id in sorted(workloads - {"ios"})
    )
    manifest_patterns = "\n".join(
        f'      <package pattern="{package_id}" />'
        for package_id in local_manifests
    )
    project_patterns = (
        f'      <package pattern="{SDK_PACK_ID}" />\n'
        f'      <package pattern="{AOT_PACK_ID}" />'
        if "ios" in workloads
        else ""
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="dotnet-ios-linux" value="{source}" />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="dotnet-ios-linux">
{manifest_patterns}
{project_patterns}
    </packageSource>
    <packageSource key="nuget.org">
      <package pattern="*" />
    </packageSource>
  </packageSourceMapping>
</configuration>
"""


def package_record(path: Path, package_id: str, version: str, origin: str) -> dict:
    return {
        "id": package_id,
        "version": version,
        "file": path.name,
        "sha256": digest(path, "sha256"),
        "sha512Base64": sha512_base64(path),
        "origin": origin,
    }


def verify_existing(output: Path, expected: dict) -> None:
    marker = output / MARKER
    try:
        actual = json.loads(marker.read_text(encoding="utf-8"))
        feed_lock = json.loads((output / "feed-lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"existing output is not an owned workload feed: {output} ({error})")
    if actual != expected:
        fail(f"existing workload feed has different provenance: {output}")
    for package in feed_lock.get("packages", []):
        path = output / package["file"]
        if not path.is_file() or digest(path, "sha256") != package["sha256"]:
            fail(f"existing workload feed package failed integrity validation: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-manifest", type=Path)
    parser.add_argument(
        "--official-companion-manifest",
        action="append",
        default=[],
        metavar="WORKLOAD=PATH",
        help="hash-verified macos, maccatalyst, or tvos manifest archive",
    )
    parser.add_argument("--aot-pack", type=Path)
    parser.add_argument(
        "--workload",
        action="append",
        choices=("ios", *sorted(COMPANION_MANIFESTS)),
        default=[],
        help="workload whose local derivative is exposed (default: all direct Apple workloads)",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[2]
    workloads = set(args.workload) or {"ios", *COMPANION_MANIFESTS}
    if "ios" in workloads and args.official_manifest is None:
        parser.error("--official-manifest is required when ios is selected")
    if "ios" in workloads and args.aot_pack is None:
        parser.error("--aot-pack is required when ios is selected")
    official = args.official_manifest.expanduser().resolve() if args.official_manifest else None
    companions: dict[str, Path] = {}
    for raw in args.official_companion_manifest:
        workload_id, separator, raw_path = raw.partition("=")
        if separator != "=" or workload_id not in COMPANION_MANIFESTS or not raw_path:
            parser.error("--official-companion-manifest must be macos=PATH, maccatalyst=PATH, or tvos=PATH")
        if workload_id in companions:
            parser.error(f"duplicate companion manifest: {workload_id}")
        companions[workload_id] = Path(raw_path).expanduser().resolve()
    missing_companions = sorted((workloads - {"ios"}) - set(companions))
    if missing_companions:
        parser.error("missing companion manifests: " + ", ".join(missing_companions))
    aot_root = args.aot_pack.expanduser().resolve() if args.aot_pack else None
    output = args.output.expanduser().absolute()
    provenance = {
        "schemaVersion": 1,
        "owner": "dotnet-ios-linux",
        "projectVersion": PROJECT_VERSION,
        "workloads": sorted(workloads),
        "officialManifestSha512Base64": MANIFEST_SHA512 if "ios" in workloads else None,
        "officialCompanionManifestSha512Base64": {
            workload_id: COMPANION_MANIFESTS[workload_id]["sha512"]
            for workload_id in sorted(workloads - {"ios"})
        },
        "aotProvenanceSha256": (
            digest(aot_root / "provenance.json", "sha256")
            if aot_root is not None and (aot_root / "provenance.json").is_file()
            else None
        ),
    }
    if output.exists() or output.is_symlink():
        if output.is_symlink():
            fail(f"existing output must not be a symlink: {output}")
        verify_existing(output, provenance)
        print(f"feed={output}")
        print("reused=true")
        return

    official_files = read_nupkg(official, MANIFEST_ID, MANIFEST_SHA512) if official else None
    companion_files = {
        workload_id: read_nupkg(
            path, COMPANION_MANIFESTS[workload_id]["id"], COMPANION_MANIFESTS[workload_id]["sha512"]
        )
        for workload_id, path in companions.items()
    }
    if "ios" in workloads:
        assert aot_root is not None
        verify_aot_pack(aot_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".workload-feed-", dir=output.parent))
    try:
        packages: list[dict] = []
        if "ios" in workloads:
            sdk_nupkg = stage / f"{SDK_PACK_ID}.{PROJECT_VERSION}.nupkg"
            aot_nupkg = stage / f"{AOT_PACK_ID}.{PROJECT_VERSION}.nupkg"
            manifest_nupkg = stage / f"{MANIFEST_ID}.{MANIFEST_VERSION}.nupkg"
            package_sdk(repository, sdk_nupkg)
            assert aot_root is not None and official_files is not None
            package_aot(repository, aot_root, aot_nupkg)
            write_nupkg(manifest_nupkg, derive_manifest(official_files))
            packages.extend(
                [
                    package_record(sdk_nupkg, SDK_PACK_ID, PROJECT_VERSION, "project"),
                    package_record(aot_nupkg, AOT_PACK_ID, PROJECT_VERSION, "local-build"),
                    package_record(manifest_nupkg, MANIFEST_ID, MANIFEST_VERSION, "local-derivative"),
                ]
            )
        companion_nupkgs: dict[str, Path] = {}
        for workload_id in sorted(workloads - {"ios"}):
            package_id = COMPANION_MANIFESTS[workload_id]["id"]
            path = stage / f"{package_id}.{MANIFEST_VERSION}.nupkg"
            write_nupkg(path, derive_companion_manifest(companion_files[workload_id], workload_id))
            companion_nupkgs[workload_id] = path
        packages.extend(
            [
                package_record(
                    companion_nupkgs[workload_id],
                    COMPANION_MANIFESTS[workload_id]["id"],
                    MANIFEST_VERSION,
                    "local-derivative",
                )
                for workload_id in sorted(workloads - {"ios"})
            ]
        )
        feed_lock = {
            "schemaVersion": 1,
            "owner": "dotnet-ios-linux",
            "packages": packages,
            "officialManifest": {
                "id": MANIFEST_ID,
                "version": MANIFEST_VERSION,
                "sha512Base64": MANIFEST_SHA512,
            },
        }
        (stage / "feed-lock.json").write_text(json.dumps(feed_lock, indent=2) + "\n", encoding="utf-8")
        (stage / "NuGet.Config").write_text(nuget_config(output, workloads), encoding="utf-8")
        (stage / MARKER).write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        try:
            publish_directory_noreplace(stage, output)
        except FileExistsError:
            verify_existing(output, provenance)
            shutil.rmtree(stage)
            print(f"feed={output}")
            print("reused=true")
            return
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    print(f"feed={output}")
    print(f"project_version={PROJECT_VERSION}")
    print("reused=false")


if __name__ == "__main__":
    main()
