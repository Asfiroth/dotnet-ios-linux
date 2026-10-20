#!/usr/bin/env python3
"""Build the locked project packs and locally derived iOS workload manifest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
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


PROJECT_VERSION = "0.1.0-preview.1"
MANIFEST_ID = "Microsoft.NET.Sdk.iOS.Manifest-10.0.100"
MANIFEST_VERSION = "27.0.10722"
MANIFEST_SHA512 = "E67WwID69ZQmMd0fs8OFeExzyso/7rJk7QfIkK+fs+cEQj6e2s/hUA/iPtP36Li08YZVp8L7iY6Uk+zmfdU5Ww=="
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
EXPECTED_AOT_FILES = {
    "mono-aot-cross": "c85a0aad43764e9d17077f23faf6e638e79b47b315263116b2350038d977866d",
    "libc++.so.1": "9233ff02fd30aba191b9a936eeb351d78682616fce2db29c08bbd1c3a581ca9c",
    "libc++abi.so.1": "80bef63649f325c706768ecf9f29598429c712721b58c990117e86c826dbe534",
    "llc": "8b53230508c94cda4ee541be27e7f9957becd32d5e2522f3be1bbe4c61079662",
    "opt": "99464211ff7ba2175408c935d17ca375a99c564e88b476581e9b3d59af81d011",
}


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


def read_nupkg(path: Path) -> dict[str, bytes]:
    if not path.is_file():
        fail(f"official manifest package is missing: {path}")
    actual = sha512_base64(path)
    if actual != MANIFEST_SHA512:
        fail(f"official manifest SHA-512 mismatch: expected {MANIFEST_SHA512}, got {actual}")
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


def derive_manifest(files: dict[str, bytes]) -> dict[str, bytes]:
    nuspec_name = f"{MANIFEST_ID}.nuspec"
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
    if nuspec_metadata(files[nuspec_name]) != (MANIFEST_ID, MANIFEST_VERSION):
        fail("official manifest package identity does not match the lock")

    manifest = relaxed_json(files["data/WorkloadManifest.json"], "WorkloadManifest.json")
    if manifest.get("version") != MANIFEST_VERSION:
        fail("official workload manifest version does not match the lock")
    workloads = manifest.get("workloads")
    packs = manifest.get("packs")
    if not isinstance(workloads, dict) or not isinstance(packs, dict):
        fail("official workload manifest lacks workload or pack maps")
    ios = workloads.get("ios")
    if not isinstance(ios, dict) or not isinstance(ios.get("packs"), list):
        fail("official workload manifest lacks the ios workload pack list")

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


def verify_project_nuspec(path: Path, package_id: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as error:
        fail(f"cannot read project nuspec {path}: {error}")
    if nuspec_metadata(data) != (package_id, PROJECT_VERSION):
        fail(f"project nuspec identity mismatch: {path}")
    return data


def verify_aot_pack(root: Path) -> None:
    required = [root / "Sdk" / "Sdk.props", root / "tools" / "mono-aot-cross", root / "provenance.json"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        fail("AOT pack is incomplete: " + ", ".join(missing))
    try:
        provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read AOT provenance: {error}")
    if provenance.get("packageId") != AOT_PACK_ID:
        fail("AOT provenance has the wrong package ID")
    if provenance.get("runtimeCommit") != RUNTIME_COMMIT:
        fail("AOT provenance has the wrong runtime commit")
    if provenance.get("llvmMode") != "llvm":
        fail("AOT workload package must be LLVM-enabled")
    recorded = provenance.get("files")
    if recorded != EXPECTED_AOT_FILES:
        fail("AOT provenance does not contain the locked compiler and LLVM output hashes")
    for name, expected in recorded.items():
        path = root / "tools" / name
        if not path.is_file() or digest(path, "sha256") != expected:
            fail(f"AOT payload does not match provenance: {name}")


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


def nuget_config(feed: Path) -> str:
    source = html.escape(str(feed), quote=True)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="dotnet-ios-linux" value="{source}" />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="dotnet-ios-linux">
      <package pattern="{MANIFEST_ID}" />
      <package pattern="{SDK_PACK_ID}" />
      <package pattern="{AOT_PACK_ID}" />
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
    parser.add_argument("--official-manifest", type=Path, required=True)
    parser.add_argument("--aot-pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[2]
    official = args.official_manifest.expanduser().resolve()
    aot_root = args.aot_pack.expanduser().resolve()
    output = args.output.expanduser().absolute()
    provenance = {
        "schemaVersion": 1,
        "owner": "dotnet-ios-linux",
        "projectVersion": PROJECT_VERSION,
        "officialManifestSha512Base64": MANIFEST_SHA512,
        "aotProvenanceSha256": digest(aot_root / "provenance.json", "sha256")
        if (aot_root / "provenance.json").is_file()
        else None,
    }
    if output.exists() or output.is_symlink():
        if output.is_symlink():
            fail(f"existing output must not be a symlink: {output}")
        verify_existing(output, provenance)
        print(f"feed={output}")
        print("reused=true")
        return

    official_files = read_nupkg(official)
    verify_aot_pack(aot_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".workload-feed-", dir=output.parent))
    try:
        sdk_nupkg = stage / f"{SDK_PACK_ID}.{PROJECT_VERSION}.nupkg"
        aot_nupkg = stage / f"{AOT_PACK_ID}.{PROJECT_VERSION}.nupkg"
        manifest_nupkg = stage / f"{MANIFEST_ID}.{MANIFEST_VERSION}.nupkg"
        package_sdk(repository, sdk_nupkg)
        package_aot(repository, aot_root, aot_nupkg)
        write_nupkg(manifest_nupkg, derive_manifest(official_files))
        packages = [
            package_record(sdk_nupkg, SDK_PACK_ID, PROJECT_VERSION, "project"),
            package_record(aot_nupkg, AOT_PACK_ID, PROJECT_VERSION, "local-build"),
            package_record(manifest_nupkg, MANIFEST_ID, MANIFEST_VERSION, "local-derivative"),
        ]
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
        (stage / "NuGet.Config").write_text(nuget_config(output), encoding="utf-8")
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
