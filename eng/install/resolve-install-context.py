#!/usr/bin/env python3
"""Resolve lock-backed installer inputs and compatible installed .NET state."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "eng" / "toolchain"))

from xip_archive import inspect_metadata  # noqa: E402


def fail(message: str) -> "None":
    raise SystemExit(f"install context: {message}")


def required_string(value: Any, path: str) -> str:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            fail(f"toolchain lock is missing {path}")
        current = current[part]
    if not isinstance(current, str) or not current or any(ord(char) < 32 for char in current):
        fail(f"toolchain lock has an invalid string at {path}")
    return current


def package_by_key(lock: dict, key: str) -> dict:
    try:
        packages = lock["components"]["linuxIosWorkload"]["packages"]
    except (KeyError, TypeError):
        fail("toolchain lock is missing the Linux iOS package inventory")
    if not isinstance(packages, list):
        fail("Linux iOS package inventory is not a list")
    matches = [item for item in packages if isinstance(item, dict) and item.get("key") == key]
    if len(matches) != 1:
        fail(f"Linux iOS package inventory must contain exactly one {key!r} record")
    return matches[0]


def decode_sha512(value: str, path: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as error:
        fail(f"toolchain lock has invalid base64 at {path}: {error}")
    if len(decoded) != 64:
        fail(f"toolchain lock SHA-512 at {path} is not 64 bytes")
    return decoded.hex()


def archive_name(url: str, label: str) -> str:
    name = Path(urlparse(url).path).name
    if not name or name in {".", ".."}:
        fail(f"cannot derive the {label} archive name from {url!r}")
    return name


def safe_relative_path(value: str, label: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        fail(f"toolchain lock has an unsafe {label} path: {value!r}")
    return Path(*path.parts)


def load_lock(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path}: {error}")
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        fail("unsupported toolchain lock schema")
    return value


def lock_values(path: Path) -> dict[str, str]:
    lock = load_lock(path)
    project_sdk = package_by_key(lock, "project-sdk")
    project_aot = package_by_key(lock, "project-aot")
    project_version = required_string(project_sdk, "version")
    if required_string(project_aot, "version") != project_version:
        fail("project SDK and AOT package versions disagree")

    sdk_url = required_string(lock, "components.dotnetSdk.url")
    workload_id = required_string(lock, "components.workloadSet.packageId")
    workload_package_version = required_string(lock, "components.workloadSet.packageVersion")
    workload_url = (
        "https://api.nuget.org/v3-flatcontainer/"
        f"{workload_id.lower()}/{workload_package_version}/"
        f"{workload_id.lower()}.{workload_package_version}.nupkg"
    )
    manifest_url = required_string(lock, "components.iosManifest.source")
    manifest_sha512_base64 = required_string(lock, "components.iosManifest.sha512Base64")
    workload_sha512_base64 = required_string(lock, "components.workloadSet.sha512Base64")
    xtool_revision = required_string(lock, "components.hostToolchain.xtool.revision")

    values = {
        "project_version": project_version,
        "sdk_fallback_version": required_string(lock, "components.dotnetSdk.version"),
        "sdk_feature_band": required_string(lock, "components.dotnetSdk.featureBand"),
        "sdk_url": sdk_url,
        "sdk_archive": archive_name(sdk_url, ".NET SDK"),
        "sdk_sha512": required_string(lock, "components.dotnetSdk.sha512"),
        "workload_fallback_version": required_string(lock, "components.workloadSet.cliVersion"),
        "workload_package_url": workload_url,
        "workload_package_archive": archive_name(workload_url, "workload-set"),
        "workload_package_sha512": decode_sha512(
            workload_sha512_base64, "components.workloadSet.sha512Base64"
        ),
        "manifest_id": required_string(lock, "components.iosManifest.id"),
        "manifest_version": required_string(lock, "components.iosManifest.version"),
        "manifest_suffix": required_string(
            lock, "components.workloadSet.declaredManifestPackageSuffix"
        ),
        "manifest_url": manifest_url,
        "manifest_archive": archive_name(manifest_url, "iOS manifest"),
        "manifest_sha512_base64": manifest_sha512_base64,
        "manifest_sha512": decode_sha512(
            manifest_sha512_base64, "components.iosManifest.sha512Base64"
        ),
        "runtime_commit": required_string(lock, "components.monoRuntime.sourceCommit"),
        "llvm_version": required_string(lock, "components.monoRuntime.llvm.packageVersion"),
        "llvm_major": str(lock["components"]["monoRuntime"]["llvm"]["major"]),
        "supported_xcode_version": required_string(lock, "components.appleSdk.xcodeVersion"),
        "supported_ios_sdk_version": required_string(lock, "components.appleSdk.iosSdkVersion"),
        "locked_xcode_sha256": required_string(lock, "components.appleSdk.archiveSha256"),
        "swift_version": required_string(
            lock, "components.hostToolchain.swift.version"
        ),
        "binutils_version": required_string(
            lock, "components.hostToolchain.binutils.version"
        ),
        "xtool_revision": xtool_revision,
        "xtool_revision_short": xtool_revision[:7],
        "python_version": required_string(
            lock, "components.hostToolchain.portablePython.version"
        ),
        "pymobiledevice3_version": required_string(
            lock, "components.hostToolchain.pymobiledevice3.version"
        ),
        "rcodesign_version": required_string(
            lock, "components.hostToolchain.rcodesign.version"
        ),
    }
    try:
        companions = lock["components"]["appleCompanionWorkloads"]["workloads"]
    except (KeyError, TypeError) as error:
        fail(f"toolchain lock lacks companion manifest records: {error}")
    if not isinstance(companions, list):
        fail("companion manifest records are not a list")
    expected_ids = {"macos", "maccatalyst", "tvos"}
    seen: set[str] = set()
    for workload in companions:
        if not isinstance(workload, dict):
            fail("companion manifest record is invalid")
        workload_id = workload.get("workloadId")
        manifest = workload.get("manifest")
        if workload_id not in expected_ids or workload_id in seen or not isinstance(manifest, dict):
            fail("companion manifest records do not describe each direct workload exactly once")
        seen.add(workload_id)
        package_id = required_string(manifest, "id")
        version = required_string(manifest, "version")
        sha512_base64 = required_string(manifest, "officialSha512Base64")
        url = (
            "https://api.nuget.org/v3-flatcontainer/"
            f"{package_id.lower()}/{version}/{package_id.lower()}.{version}.nupkg"
        )
        values[f"{workload_id}_manifest_id"] = package_id
        values[f"{workload_id}_manifest_version"] = version
        values[f"{workload_id}_manifest_url"] = url
        values[f"{workload_id}_manifest_archive"] = archive_name(url, f"{workload_id} manifest")
        values[f"{workload_id}_manifest_sha512_base64"] = sha512_base64
        values[f"{workload_id}_manifest_sha512"] = decode_sha512(
            sha512_base64, f"components.appleCompanionWorkloads.{workload_id}.manifest.officialSha512Base64"
        )
    if seen != expected_ids:
        fail("companion manifest records are incomplete")
    for key in ("sdk_sha512", "locked_xcode_sha256"):
        expected_length = 128 if key == "sdk_sha512" else 64
        if re.fullmatch(rf"[0-9a-f]{{{expected_length}}}", values[key]) is None:
            fail(f"toolchain lock has an invalid digest for {key}")
    return values


def parse_sdk_version(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def major_minor_version(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.\d+)?", value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def check_xcode_compatibility(actual: str, recommended: str) -> dict[str, str]:
    actual_family = major_minor_version(actual)
    recommended_family = major_minor_version(recommended)
    if actual_family is None or recommended_family is None:
        fail(f"invalid Xcode compatibility versions: {actual!r}, {recommended!r}")
    if actual_family != recommended_family:
        fail(f"Xcode {actual} is incompatible; the selected iOS workload requires Xcode {recommended}")
    return {
        "actual_xcode_family": f"{actual_family[0]}.{actual_family[1]}",
        "recommended_xcode_family": f"{recommended_family[0]}.{recommended_family[1]}",
    }


def sdk_feature_band(version: tuple[int, int, int]) -> str:
    major, minor, patch = version
    return f"{major}.{minor}.{patch // 100 * 100}"


def select_sdk(root: Path, feature_band: str, fallback: str) -> dict[str, str]:
    candidates: list[tuple[tuple[int, int, int], str]] = []
    sdk_root = root / "sdk"
    if sdk_root.is_dir() and not sdk_root.is_symlink():
        for path in sdk_root.iterdir():
            parsed = parse_sdk_version(path.name)
            if (
                parsed is not None
                and path.is_dir()
                and not path.is_symlink()
                and sdk_feature_band(parsed) == feature_band
            ):
                candidates.append((parsed, path.name))
    if candidates:
        return {"sdk_version": max(candidates)[1], "sdk_source": "preexisting"}
    parsed_fallback = parse_sdk_version(fallback)
    if parsed_fallback is None or sdk_feature_band(parsed_fallback) != feature_band:
        fail("fallback SDK version does not belong to the locked feature band")
    return {"sdk_version": fallback, "sdk_source": "installer"}


def workload_mapping(path: Path) -> dict[str, str] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {str(key).lower(): item for key, item in value.items() if isinstance(item, str)}


def version_key(value: str) -> tuple:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"[.-]", value)
    )


def select_workload(
    root: Path,
    feature_band: str,
    manifests: dict[str, str],
    fallback: str,
) -> dict[str, str]:
    if not manifests:
        fail("at least one workload manifest identity is required")
    workloadsets = root / "sdk-manifests" / feature_band / "workloadsets"
    matches: list[str] = []
    if workloadsets.is_dir() and not workloadsets.is_symlink():
        for path in workloadsets.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            mapping = workload_mapping(path / "microsoft.net.workloads.workloadset.json")
            if mapping is not None and all(
                mapping.get(package_id.casefold()) == identity
                for package_id, identity in manifests.items()
            ):
                matches.append(path.name)

    preferred: str | None = None
    state = root / "metadata" / "workloads" / "X64" / feature_band / "InstallState" / "default.json"
    try:
        state_value = json.loads(state.read_text(encoding="utf-8"))
        candidate = state_value.get("workloadVersion")
        if isinstance(candidate, str) and candidate in matches:
            preferred = candidate
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    if preferred is not None:
        return {"workload_version": preferred, "workload_source": "current"}
    if matches:
        return {
            "workload_version": max(matches, key=version_key),
            "workload_source": "installed-compatible",
        }
    return {"workload_version": fallback, "workload_source": "installer"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xcode_values(archive: Path) -> dict[str, str]:
    if not archive.is_file() or archive.is_symlink():
        fail(f"Xcode archive is not a regular file: {archive}")
    try:
        metadata = inspect_metadata(archive)
    except (EOFError, OSError, UnicodeDecodeError, ValueError) as error:
        fail(f"cannot inspect Xcode archive: {error}")
    return {
        "xcode_archive_sha256": sha256(archive),
        "xcode_version": metadata.xcode_version,
        "xcode_build": metadata.xcode_build,
        "platform_version": metadata.platform_version,
        "platform_build": metadata.platform_build,
        "xcode_sdk_version": metadata.sdk_version,
        "xcode_sdk_build": metadata.sdk_build,
    }


def recommended_xcode(root: Path, lock_path: Path) -> dict[str, str]:
    lock = load_lock(lock_path)
    sdk_version = required_string(lock, "components.appleSdk.iosSdkVersion")
    try:
        packages = lock["components"]["linuxIosWorkload"]["packages"]
    except (KeyError, TypeError):
        fail("toolchain lock is missing the Linux iOS package inventory")
    candidates = [
        item
        for item in packages
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].startswith("Microsoft.iOS.Sdk.")
        and item["id"].endswith(f"_{sdk_version}")
        and isinstance(item.get("installedArchive"), str)
    ]
    if len(candidates) != 1:
        fail(
            f"cannot identify one installed Microsoft.iOS.Sdk pack for iPhoneOS {sdk_version}"
        )
    archive = root / safe_relative_path(candidates[0]["installedArchive"], "installed archive")
    props = archive.parent / "targets" / "Microsoft.iOS.Sdk.Versions.props"
    if props.is_symlink() or not props.is_file():
        fail(f"selected iOS workload SDK pack lacks {props.name}: {props}")
    try:
        document = ET.parse(props)
    except (OSError, ET.ParseError) as error:
        fail(f"cannot read {props}: {error}")
    values = [
        (element.text or "").strip()
        for element in document.iter()
        if element.tag.rsplit("}", 1)[-1] == "RecommendedXcodeVersion"
    ]
    values = [value for value in values if value]
    if len(set(values)) != 1:
        fail(f"{props} must declare exactly one RecommendedXcodeVersion")
    version = values[0]
    if re.fullmatch(r"\d+(?:\.\d+){0,2}", version) is None:
        fail(f"selected iOS workload declares an invalid Xcode version: {version!r}")
    return {
        "recommended_xcode_version": version,
        "recommended_xcode_source": str(props),
    }


def emit(value: dict[str, str], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, sort_keys=True))
        return
    stream = sys.stdout.buffer
    for key, item in value.items():
        stream.write(key.encode("utf-8") + b"\0" + item.encode("utf-8") + b"\0")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "null"), default="json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lock = subparsers.add_parser("lock")
    lock.add_argument("--lock", type=Path, required=True)

    sdk = subparsers.add_parser("sdk")
    sdk.add_argument("--root", type=Path, required=True)
    sdk.add_argument("--feature-band", required=True)
    sdk.add_argument("--fallback", required=True)

    workload = subparsers.add_parser("workload")
    workload.add_argument("--root", type=Path, required=True)
    workload.add_argument("--feature-band", required=True)
    workload.add_argument("--manifest-version")  # Legacy iOS-only form.
    workload.add_argument("--manifest-suffix")  # Legacy iOS-only form.
    workload.add_argument("--manifest", action="append", default=[], metavar="ID=VERSION/BAND")
    workload.add_argument("--fallback", required=True)

    xcode = subparsers.add_parser("xcode")
    xcode.add_argument("--archive", type=Path, required=True)

    recommended = subparsers.add_parser("recommended-xcode")
    recommended.add_argument("--root", type=Path, required=True)
    recommended.add_argument("--lock", type=Path, required=True)

    compatibility = subparsers.add_parser("xcode-compatible")
    compatibility.add_argument("--actual", required=True)
    compatibility.add_argument("--recommended", required=True)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    if args.command == "lock":
        value = lock_values(args.lock.expanduser().resolve())
    elif args.command == "sdk":
        value = select_sdk(args.root.expanduser().absolute(), args.feature_band, args.fallback)
    elif args.command == "workload":
        manifests: dict[str, str] = {}
        for raw in args.manifest:
            package_id, separator, identity = raw.partition("=")
            if separator != "=" or not package_id or "/" not in identity:
                fail("--manifest must be PACKAGE_ID=VERSION/FEATURE-BAND")
            folded = package_id.casefold()
            if folded in manifests:
                fail(f"duplicate workload manifest package ID: {package_id}")
            manifests[folded] = identity
        if not manifests:
            if args.manifest_version is None or args.manifest_suffix is None:
                fail("workload requires --manifest or --manifest-version and --manifest-suffix")
            manifests = {"microsoft.net.sdk.ios": f"{args.manifest_version}/{args.manifest_suffix}"}
        value = select_workload(
            args.root.expanduser().absolute(),
            args.feature_band,
            manifests,
            args.fallback,
        )
    elif args.command == "xcode":
        value = xcode_values(args.archive.expanduser().resolve())
    elif args.command == "recommended-xcode":
        value = recommended_xcode(
            args.root.expanduser().absolute(), args.lock.expanduser().resolve()
        )
    else:
        value = check_xcode_compatibility(args.actual, args.recommended)
    emit(value, args.format)


if __name__ == "__main__":
    main()
