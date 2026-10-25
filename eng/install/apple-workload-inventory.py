#!/usr/bin/env python3
"""Read-only classification of installed Apple workloads in a .NET root.

InstalledWorkloads markers establish presence.  The selected workload set and
its manifests establish only the workload family and support boundary; they do
not create install intent.  This tool deliberately does not invoke ``dotnet``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


MAX_METADATA_BYTES = 16 * 1024 * 1024
WORKLOAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
FEATURE_BAND = re.compile(r"\d+\.\d+\.\d+\Z")
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}\Z")


def fail(message: str) -> NoReturn:
    raise SystemExit(f"Apple workload inventory failed: {message}")


def root_path(raw: Path) -> Path:
    candidate = raw.expanduser()
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
        fail(f".NET root must be an absolute real directory: {raw}")
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        fail(f"cannot resolve .NET root: {error}")


def safe_component(value: str, label: str) -> str:
    if SAFE_COMPONENT.fullmatch(value) is None:
        fail(f"invalid {label}: {value!r}")
    return value


def object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"metadata contains a duplicate JSON key: {key!r}")
        result[key] = value
    return result


def remove_trailing_commas(text: str) -> str:
    """Accept the macios JSON-with-trailing-commas dialect without touching strings."""

    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        if in_string:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            result.append(character)
            index += 1
            continue
        if character == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        result.append(character)
        index += 1
    if in_string or escaped:
        fail("metadata contains an unterminated JSON string")
    return "".join(result)


@dataclass
class SourceTracker:
    root: Path
    files: dict[str, str]
    root_fd: int = field(init=False)
    root_identity: tuple[int, int] = field(init=False)

    def __post_init__(self) -> None:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.root_fd = os.open(self.root, flags)
        except OSError as error:
            fail(f"cannot open .NET root without following links: {self.root}: {error}")
        details = os.fstat(self.root_fd)
        if not stat.S_ISDIR(details.st_mode):
            os.close(self.root_fd)
            fail(f".NET root is not a directory after opening: {self.root}")
        self.root_identity = (details.st_dev, details.st_ino)

    def close(self) -> None:
        os.close(self.root_fd)

    def assert_root_identity(self) -> None:
        try:
            details = self.root.lstat()
        except OSError as error:
            fail(f"cannot restat .NET root after inspection: {error}")
        if (
            stat.S_ISLNK(details.st_mode)
            or (details.st_dev, details.st_ino) != self.root_identity
        ):
            fail(".NET root changed during read-only inspection; rerun preflight")

    def open_relative(self, relative: PurePosixPath, label: str) -> int:
        if relative.is_absolute() or not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            fail(f"{label} has an unsafe path below the .NET root: {relative}")
        descriptor = os.dup(self.root_fd)
        try:
            for index, part in enumerate(relative.parts):
                flags = os.O_RDONLY | os.O_CLOEXEC
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                if index != len(relative.parts) - 1:
                    flags |= os.O_DIRECTORY
                opened = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = opened
            return descriptor
        except OSError as error:
            os.close(descriptor)
            fail(f"cannot open {label} safely: {relative}: {error}")

    def read(self, path: Path, label: str) -> bytes:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            fail(f"{label} is outside the .NET root: {path}")
        try:
            descriptor = self.open_relative(PurePosixPath(relative.as_posix()), label)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    fail(f"{label} is not a regular file: {path}")
                if opened.st_size > MAX_METADATA_BYTES:
                    fail(f"{label} exceeds the {MAX_METADATA_BYTES} byte safety limit: {path}")
                data = stream.read(MAX_METADATA_BYTES + 1)
        except OSError as error:
            fail(f"cannot read {label}: {path}: {error}")
        if len(data) > MAX_METADATA_BYTES:
            fail(f"{label} exceeds the {MAX_METADATA_BYTES} byte safety limit: {path}")
        self.files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
        return data

    def json(self, path: Path, label: str, *, relaxed: bool = False) -> dict[str, Any]:
        raw = self.read(path, label)
        try:
            text = raw.decode("utf-8-sig")
            value = json.loads(
                remove_trailing_commas(text) if relaxed else text,
                object_pairs_hook=object_without_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            fail(f"cannot parse {label}: {path}: {error}")
        if not isinstance(value, dict):
            fail(f"{label} must contain a JSON object: {path}")
        return value


def casefolded_mapping(value: object, label: str) -> dict[str, tuple[str, Any]]:
    if not isinstance(value, dict):
        fail(f"{label} must be a JSON object")
    result: dict[str, tuple[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            fail(f"{label} has a non-string key")
        folded = key.casefold()
        if folded in result:
            fail(f"{label} has case-colliding keys: {result[folded][0]!r}, {key!r}")
        result[folded] = (key, item)
    return result


def directory_for_casefolded_name(parent: Path, name: str, label: str) -> Path | None:
    if parent.is_symlink() or not parent.is_dir():
        return None
    matches: list[Path] = []
    try:
        children = list(parent.iterdir())
    except OSError as error:
        fail(f"cannot enumerate {label}: {parent}: {error}")
    for child in children:
        if child.name.casefold() == name.casefold():
            matches.append(child)
    if len(matches) > 1:
        fail(f"{label} has case-colliding directories for {name!r}: {parent}")
    if not matches:
        return None
    result = matches[0]
    if result.is_symlink() or not result.is_dir():
        fail(f"{label} is not a real directory: {result}")
    return result


def workload_set_feature_band(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.[A-Za-z0-9.+-]+)?", version)
    if match is None:
        fail(f"installed workload set has an invalid version: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch // 100 * 100}"


def marker_feature_band(scope: tuple[str, ...]) -> str:
    candidates = [part for part in scope if FEATURE_BAND.fullmatch(part)]
    if len(candidates) != 1:
        fail(f"workload marker scope lacks one SDK feature band: {'/'.join(scope)!r}")
    return candidates[0]


def find_markers(root: Path, tracker: SourceTracker) -> list[dict[str, str]]:
    metadata = root / "metadata" / "workloads"
    if not os.path.lexists(metadata):
        return []
    if metadata.is_symlink() or not metadata.is_dir():
        fail(f"workload metadata root is not a real directory: {metadata}")
    result: list[dict[str, str]] = []
    for directory in sorted(metadata.rglob("InstalledWorkloads")):
        if directory.is_symlink() or not directory.is_dir():
            fail(f"workload marker directory is not a real directory: {directory}")
        scope = directory.relative_to(metadata).parts[:-1]
        band = marker_feature_band(scope)
        for marker in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if marker.is_symlink() or not marker.is_file():
                fail(f"workload marker is not a regular file: {marker}")
            if WORKLOAD_ID.fullmatch(marker.name) is None:
                fail(f"workload marker has an invalid ID: {marker}")
            tracker.read(marker, "workload marker")
            result.append(
                {
                    "workloadId": marker.name,
                    "markerPath": marker.relative_to(root).as_posix(),
                    "metadataScope": "/".join(scope),
                    "sdkFeatureBand": band,
                }
            )
    return sorted(result, key=lambda item: (item["workloadId"].casefold(), item["markerPath"]))


def selected_workload_set(root: Path, tracker: SourceTracker, sdk_band: str) -> tuple[str, str]:
    metadata = root / "metadata" / "workloads"
    candidates = [metadata / sdk_band / "InstallState" / "default.json"]
    if metadata.is_dir() and not metadata.is_symlink():
        candidates.extend(metadata.glob(f"*/{sdk_band}/InstallState/default.json"))
    states: dict[str, list[Path]] = {}
    for path in sorted(set(candidates)):
        if not os.path.lexists(path):
            continue
        value = tracker.json(path, "workload installation state")
        version = value.get("workloadVersion")
        if not isinstance(version, str):
            fail(f"workload installation state lacks workloadVersion: {path}")
        workload_set_feature_band(version)
        states.setdefault(version, []).append(path)
    if not states:
        fail(f"no workload installation state selects SDK feature band {sdk_band}")
    if len(states) != 1:
        choices = ", ".join(sorted(states))
        fail(f"conflicting workload-set selections for SDK feature band {sdk_band}: {choices}")
    version = next(iter(states))
    return version, workload_set_feature_band(version)


@dataclass(frozen=True)
class ManifestReference:
    package_id: str
    version: str
    feature_band: str


class ManifestGraph:
    def __init__(self, root: Path, tracker: SourceTracker, workload_set_band: str, workload_set: str):
        self.root = root
        self.tracker = tracker
        self.manifests: dict[str, ManifestReference] = {}
        self._manifest_cache: dict[str, dict[str, Any] | None] = {}
        self._definitions: dict[str, tuple[str, dict[str, Any]]] = {}
        self.apple_roots: dict[str, dict[str, str]] = {}
        path = (
            root
            / "sdk-manifests"
            / safe_component(workload_set_band, "workload-set feature band")
            / "workloadsets"
            / safe_component(workload_set, "workload-set version")
            / "microsoft.net.workloads.workloadset.json"
        )
        mapping = casefolded_mapping(
            tracker.json(path, "workload-set mapping"), "workload-set mapping"
        )
        for folded, (package_id, raw_identity) in mapping.items():
            if not isinstance(raw_identity, str):
                fail(f"workload-set mapping has a non-string manifest identity for {package_id!r}")
            parts = raw_identity.split("/")
            if len(parts) != 2:
                fail(f"workload-set mapping has an invalid manifest identity for {package_id!r}")
            version, feature_band = parts
            self.manifests[folded] = ManifestReference(
                package_id=safe_component(package_id, "manifest package ID"),
                version=safe_component(version, "manifest version"),
                feature_band=safe_component(feature_band, "manifest feature band"),
            )
        if not self.manifests:
            fail("workload-set mapping is empty")
        self.discover_apple_roots()
        # Parse every available selected manifest before classifying a marker so
        # duplicate workload definitions cannot be resolved by lookup order.
        for reference in self.manifests.values():
            self.manifest(reference)

    def manifest_directory(self, reference: ManifestReference) -> Path | None:
        band = self.root / "sdk-manifests" / reference.feature_band
        package = directory_for_casefolded_name(band, reference.package_id, "manifest package")
        if package is None:
            return None
        return directory_for_casefolded_name(package, reference.version, "manifest version")

    def manifest(self, reference: ManifestReference) -> dict[str, Any] | None:
        key = f"{reference.package_id.casefold()}/{reference.version}/{reference.feature_band}"
        if key in self._manifest_cache:
            return self._manifest_cache[key]
        directory = self.manifest_directory(reference)
        if directory is None:
            self._manifest_cache[key] = None
            return None
        path = directory / "WorkloadManifest.json"
        if not os.path.lexists(path):
            self._manifest_cache[key] = None
            return None
        value = self.tracker.json(path, "workload manifest", relaxed=True)
        workloads = casefolded_mapping(value.get("workloads"), f"workload manifest workloads in {path}")
        for folded, (name, definition) in workloads.items():
            if not isinstance(definition, dict):
                fail(f"workload definition is not an object: {name!r} in {path}")
            extends = definition.get("extends", [])
            if not isinstance(extends, list) or not all(
                isinstance(item, str) and WORKLOAD_ID.fullmatch(item) for item in extends
            ):
                fail(f"workload {name!r} has invalid extends metadata")
            packs = definition.get("packs", [])
            if not isinstance(packs, list) or not all(
                isinstance(item, str) and item for item in packs
            ):
                fail(f"workload {name!r} has invalid pack metadata")
            if "abstract" in definition and not isinstance(definition["abstract"], bool):
                fail(f"workload {name!r} has invalid abstract metadata")
            existing = self._definitions.get(folded)
            if existing is not None and existing[0] != name:
                fail(f"workload definitions case-collide: {existing[0]!r}, {name!r}")
            if existing is not None:
                fail(f"workload is defined by multiple selected manifests: {name!r}")
            self._definitions[folded] = (name, definition)
        self._manifest_cache[key] = value
        return value

    def discover_apple_roots(self) -> None:
        for reference in self.manifests.values():
            directory = self.manifest_directory(reference)
            if directory is None:
                continue
            dependency_path = directory / "WorkloadDependencies.json"
            if not os.path.lexists(dependency_path):
                continue
            dependencies = casefolded_mapping(
                self.tracker.json(dependency_path, "workload dependencies", relaxed=True),
                f"workload dependencies in {dependency_path}",
            )
            manifest = self.manifest(reference)
            if manifest is None:
                fail(f"Apple workload dependencies lack a matching manifest: {directory}")
            workloads = casefolded_mapping(
                manifest.get("workloads"), f"workload manifest workloads in {directory}"
            )
            for _folded, (_name, dependency) in dependencies.items():
                if not isinstance(dependency, dict):
                    fail(f"workload dependency is not an object: {dependency_path}")
                if "xcode" not in dependency:
                    continue
                if not isinstance(dependency["xcode"], dict):
                    fail(f"Apple manifest dependency has invalid Xcode metadata: {dependency_path}")
                workload = dependency.get("workload")
                if not isinstance(workload, dict):
                    fail(f"Apple manifest dependency lacks a workload record: {dependency_path}")
                aliases = workload.get("alias")
                if not isinstance(aliases, list) or not aliases or not all(
                    isinstance(alias, str) and WORKLOAD_ID.fullmatch(alias) for alias in aliases
                ):
                    fail(f"Apple manifest dependency has invalid workload aliases: {dependency_path}")
                for alias in aliases:
                    folded_alias = alias.casefold()
                    if folded_alias not in workloads:
                        fail(f"Apple manifest alias is absent from its workload manifest: {alias!r}")
                    if folded_alias in self.apple_roots:
                        fail(f"multiple selected Apple manifests declare workload alias {alias!r}")
                    self.apple_roots[folded_alias] = {
                        "workloadId": workloads[folded_alias][0],
                        "manifestId": reference.package_id,
                        "manifestVersion": reference.version,
                        "manifestFeatureBand": reference.feature_band,
                    }

    def definition_for(self, workload: str) -> tuple[str, dict[str, Any]] | None:
        folded = workload.casefold()
        if folded in self._definitions:
            return self._definitions[folded]
        for reference in self.manifests.values():
            self.manifest(reference)
            if folded in self._definitions:
                return self._definitions[folded]
        return None

    def closure(self, workload: str) -> tuple[list[str], list[str], list[str]]:
        apple: set[str] = set()
        non_apple: set[str] = set()
        unresolved: set[str] = set()
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(current: str) -> None:
            folded = current.casefold()
            if folded in visiting:
                fail(f"workload extends graph has a cycle at {current!r}")
            if folded in visited:
                return
            visited.add(folded)
            if folded in self.apple_roots:
                apple.add(self.apple_roots[folded]["workloadId"])
                return
            definition = self.definition_for(current)
            if definition is None:
                unresolved.add(current)
                return
            name, value = definition
            extends = value.get("extends", [])
            packs = value.get("packs", [])
            if value.get("abstract") is not True and (not extends or packs):
                non_apple.add(name)
            if not extends:
                return
            visiting.add(folded)
            try:
                for extension in extends:
                    visit(extension)
            finally:
                visiting.remove(folded)

        visit(workload)
        return sorted(apple, key=str.casefold), sorted(non_apple, key=str.casefold), sorted(
            unresolved, key=str.casefold
        )


def classification(
    graph: ManifestGraph, workload: str, linux_runtime_workload: str
) -> tuple[str, list[str], list[str], list[str]]:
    apple, non_apple, unresolved = graph.closure(workload)
    runtime = linux_runtime_workload.casefold()
    apple_folded = {item.casefold() for item in apple}
    if unresolved:
        return "unknown", apple, non_apple, unresolved
    if not apple:
        return "non-apple", apple, non_apple, unresolved
    if non_apple:
        return "mixed-unqualified", apple, non_apple, unresolved
    if runtime in apple_folded:
        if len(apple) > 1:
            return "mixed-unqualified", apple, non_apple, unresolved
        if workload.casefold() == runtime:
            return "linux-ios-runtime", apple, non_apple, unresolved
        return "ios-derived-unqualified", apple, non_apple, unresolved
    return "reference-only", apple, non_apple, unresolved


def inspect(root: Path, linux_runtime_workload: str) -> tuple[dict[str, Any], dict[str, str]]:
    tracker = SourceTracker(root=root, files={})
    try:
        markers = find_markers(root, tracker)
        graphs: dict[str, tuple[str, str, ManifestGraph]] = {}
        for band in sorted({item["sdkFeatureBand"] for item in markers}):
            workload_set, workload_set_band = selected_workload_set(root, tracker, band)
            graphs[band] = (
                workload_set,
                workload_set_band,
                ManifestGraph(root, tracker, workload_set_band, workload_set),
            )
        records: list[dict[str, Any]] = []
        for marker in markers:
            workload_set, workload_set_band, graph = graphs[marker["sdkFeatureBand"]]
            state, apple, non_apple, unresolved = classification(
                graph, marker["workloadId"], linux_runtime_workload
            )
            records.append(
                {
                    **marker,
                    "workloadSet": workload_set,
                    "workloadSetFeatureBand": workload_set_band,
                    "classification": state,
                    "appleRootWorkloads": apple,
                    "nonAppleLeafWorkloads": non_apple,
                    "unresolvedWorkloads": unresolved,
                }
            )
        baseline = sorted({item["workloadId"] for item in records}, key=str.casefold)
        apple_records = [item for item in records if item["appleRootWorkloads"]]
        groups = {
            "installedAppleWorkloads": sorted(
                {item["workloadId"] for item in apple_records}, key=str.casefold
            ),
            "linuxRuntimeWorkloads": sorted(
                {
                    item["workloadId"]
                    for item in records
                    if item["classification"] == "linux-ios-runtime"
                },
                key=str.casefold,
            ),
            "referenceOnlyWorkloads": sorted(
                {
                    item["workloadId"]
                    for item in records
                    if item["classification"] == "reference-only"
                },
                key=str.casefold,
            ),
            "unqualifiedAppleWorkloads": sorted(
                {
                    item["workloadId"]
                    for item in records
                    if item["classification"] in {"ios-derived-unqualified", "mixed-unqualified"}
                },
                key=str.casefold,
            ),
            "unknownWorkloads": sorted(
                {item["workloadId"] for item in records if item["classification"] == "unknown"},
                key=str.casefold,
            ),
        }
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "dotnetRoot": str(root),
            "baselineWorkloads": baseline,
            "workloadRegistrations": records,
            **groups,
            "preservationPolicy": "All observed workload markers are preserved. Reference-only and unqualified workloads are inventory only and never install intent.",
        }
        tracker.assert_root_identity()
        return result, dict(sorted(tracker.files.items()))
    finally:
        tracker.close()


def emit(value: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    for key in (
        "baselineWorkloads",
        "installedAppleWorkloads",
        "linuxRuntimeWorkloads",
        "referenceOnlyWorkloads",
        "unqualifiedAppleWorkloads",
        "unknownWorkloads",
    ):
        print(f"{key}={','.join(value[key])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="active .NET root; read only")
    parser.add_argument(
        "--linux-runtime-workload",
        default="ios",
        help="the one workload granted Linux runtime/device support",
    )
    parser.add_argument("--format", choices=("json", "key-value"), default="json")
    args = parser.parse_args()
    if WORKLOAD_ID.fullmatch(args.linux_runtime_workload) is None:
        fail(f"Linux runtime workload ID is invalid: {args.linux_runtime_workload!r}")
    root = root_path(args.root)
    value, before = inspect(root, args.linux_runtime_workload)
    _after_value, after = inspect(root, args.linux_runtime_workload)
    if before != after:
        fail("workload metadata changed during read-only inspection; rerun preflight")
    if value["unknownWorkloads"]:
        fail(
            "cannot safely classify installed workload markers: "
            + ", ".join(value["unknownWorkloads"])
        )
    emit(value, args.format)


if __name__ == "__main__":
    main()
