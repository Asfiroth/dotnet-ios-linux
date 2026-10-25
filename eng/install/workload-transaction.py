#!/usr/bin/env python3
"""Snapshot and restore only .NET workload paths changed by this project."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import (
    durable_remove,
    fsync_directory,
    fsync_file,
    fsync_path_state,
    fsync_tree,
    mkdir_durable,
    publish_directory_noreplace,
)


OWNER = "dotnet-ios-linux"
SCHEMA_VERSION = 3
STATE_NAME = "transaction.json"
UNIT_ROOTS = {"packs", "template-packs", "library-packs"}


def fail(message: str) -> "None":
    raise SystemExit(f"workload transaction failed: {message}")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def signature(path: Path) -> dict | None:
    if not os.path.lexists(path):
        return None
    mode = stat.S_IMODE(path.lstat().st_mode)
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path), "mode": mode}
    if path.is_dir():
        return {"kind": "directory", "mode": mode}
    if path.is_file():
        return {"kind": "file", "sha256": file_hash(path), "mode": mode}
    fail(f"unsupported filesystem entry: {path}")


def inventory(path: Path, *, ignore_history: bool = False) -> dict[str, dict]:
    if not os.path.lexists(path):
        return {}
    if path.is_symlink() or not path.is_dir():
        fail(f"inventory root is not a real directory: {path}")
    result: dict[str, dict] = {}
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        if ignore_history and "history" in relative.parts:
            continue
        result[relative.as_posix()] = signature(item) or {}
    return result


def tree_hash(path: Path) -> str | None:
    if not os.path.lexists(path):
        return None
    value = signature(path)
    if value is None:
        return None
    if value["kind"] != "directory":
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    digest = hashlib.sha256()
    for relative, item in inventory(path).items():
        digest.update(relative.encode() + b"\0")
        digest.update(json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def write_state(transaction: Path, value: dict) -> None:
    mkdir_durable(transaction, mode=0o700)
    path = transaction / STATE_NAME
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=transaction)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(transaction, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_state(transaction: Path, root: Path) -> dict:
    try:
        value = json.loads((transaction / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read transaction {transaction}: {error}")
    if value.get("owner") != OWNER:
        fail(f"transaction is not owned by {OWNER}: {transaction}")
    if value.get("schemaVersion") != SCHEMA_VERSION:
        fail(f"transaction uses an unsupported schema: {transaction}")
    if value.get("dotnetRoot") != str(root):
        fail(f"transaction belongs to a different .NET root: {transaction}")
    expected = expected_snapshot_paths(value)
    snapshots = value.get("snapshots")
    if not isinstance(snapshots, dict) or set(snapshots) != set(expected):
        fail("transaction lacks the expected workload snapshots")
    for label, relative in expected.items():
        details = snapshots.get(label)
        if not isinstance(details, dict) or details.get("path") != relative.as_posix():
            fail(f"transaction has an invalid {label} snapshot path")
    predecessor = value.get("predecessorUnits", {})
    if not isinstance(predecessor, dict):
        fail("transaction lacks predecessor workload snapshots")
    for raw, expected_hash in predecessor.items():
        if not isinstance(raw, str) or not isinstance(expected_hash, str):
            fail("transaction has an invalid predecessor workload snapshot")
        source = transaction / "before-units" / safe_relative(raw, UNIT_ROOTS)
        if tree_hash(source) != expected_hash:
            fail(f"predecessor workload snapshot changed: {source}")
    protected_predecessor = value.get("protectedPredecessorUnits", {})
    if not isinstance(protected_predecessor, dict) or not set(protected_predecessor).issubset(predecessor):
        fail("transaction has invalid protected predecessor workload snapshots")
    return value


def safe_relative(raw: str, allowed_roots: set[str] | None = None) -> Path:
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"unsafe receipt path: {raw!r}")
    if allowed_roots is not None and value.parts[0] not in allowed_roots:
        fail(f"receipt path is outside the workload unit roots: {raw!r}")
    return Path(*value.parts)


def require_safe_path(root: Path, relative: Path, label: str) -> Path:
    """Return a path below a real root after rejecting symlink ancestors.

    Workload metadata contains mutable state consumed by the CLI. Checking only
    a final leaf is insufficient: a symlinked ``metadata`` or ``sdk-manifests``
    ancestor would redirect snapshots and restoration outside the selected
    .NET root.
    """

    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        fail(f".NET root is not a real directory: {root}")
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        fail(f"unsafe {label} path: {relative}")
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        if not os.path.lexists(current):
            # Later components cannot exist when this one does not. It is safe
            # for a transaction to create the remaining path itself.
            break
        if current.is_symlink():
            fail(f"{label} has a symlink ancestor: {current}")
        if index < len(relative.parts) - 1 and not current.is_dir():
            fail(f"{label} ancestor is not a directory: {current}")
    return root / relative


def validate_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or "/" in value or "\0" in value:
        fail(f"invalid {label}: {value!r}")
    return value


def feature_band(version: str) -> str:
    parts = version.split(".")
    if len(parts) < 3 or not all(part.isdigit() for part in parts[:3]):
        fail(f"cannot derive a feature band from {version!r}")
    return f"{parts[0]}.{parts[1]}.{int(parts[2]) // 100 * 100}"


def expected_snapshot_paths(state: dict) -> dict[str, Path]:
    context = state.get("context")
    if not isinstance(context, dict):
        fail("transaction lacks workload ownership context")
    sdk_band = validate_component(context.get("sdkFeatureBand"), "SDK feature band")
    workloads = [
        *(context.get("workloads") or []),
        *(context.get("protectedWorkloads") or []),
        *(context.get("predecessorWorkloads") or []),
    ]
    if not isinstance(workloads, list) or not workloads:
        fail("transaction lacks managed workload descriptors")
    manifest_bands = sorted(
        {
            validate_component(item.get("manifestFeatureBand"), "manifest feature band")
            for item in workloads
            if isinstance(item, dict)
        }
    )
    if not manifest_bands:
        fail("transaction lacks a manifest feature band")
    workload_set_band = validate_component(
        context.get("workloadSetFeatureBand"), "workload-set feature band"
    )
    result = {"workloadMetadata": Path("metadata/workloads")}
    for index, manifest_band in enumerate(manifest_bands):
        label = "manifestBand" if len(manifest_bands) == 1 else f"manifestBand-{index}"
        result[label] = Path("sdk-manifests") / manifest_band
    if workload_set_band not in manifest_bands:
        result["workloadSetBand"] = Path("sdk-manifests") / workload_set_band
    if sdk_band != workload_set_band:
        fail("selected SDK and workload set use different feature bands")
    return result


def snapshot_items(state: dict):
    for label, relative in expected_snapshot_paths(state).items():
        yield label, relative, state["snapshots"][label]


def pack_units(root: Path) -> dict[str, str]:
    packs = require_safe_path(root, Path("packs"), "pack root")
    result: dict[str, str] = {}
    if not os.path.lexists(packs):
        return result
    if packs.is_symlink() or not packs.is_dir():
        fail(f"pack root is not a real directory: {packs}")
    for package in packs.iterdir():
        if package.is_symlink() or not package.is_dir():
            fail(f"pack package is not a real directory: {package}")
        for version in package.iterdir():
            if version.is_symlink() or not version.is_dir():
                fail(f"pack version is not a real directory: {version}")
            relative = version.relative_to(root).as_posix()
            result[relative] = tree_hash(version) or ""
    return result


def loose_units(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for directory_name in ("template-packs", "library-packs"):
        directory = require_safe_path(root, Path(directory_name), "workload unit root")
        if not os.path.lexists(directory):
            continue
        if not directory.is_dir() or directory.is_symlink():
            fail(f"workload unit root is not a real directory: {directory}")
        for child in directory.iterdir():
            if child.is_symlink():
                fail(f"workload unit is a symlink: {child}")
            relative = child.relative_to(root).as_posix()
            result[relative] = tree_hash(child) or ""
    return result


def current_units(root: Path) -> dict[str, str]:
    return pack_units(root) | loose_units(root)


def relaxed_json_file(path: Path, label: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        value = json.loads(re.sub(r",(?=\s*[}\]])", "", raw))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"cannot read predecessor {label}: {path}: {error}")
    if not isinstance(value, dict):
        fail(f"predecessor {label} is not an object: {path}")
    return value


def casefold_child(parent: Path, name: str, label: str) -> Path | None:
    if parent.is_symlink() or not parent.is_dir():
        return None
    matches = [item for item in parent.iterdir() if item.name.casefold() == name.casefold()]
    if len(matches) > 1:
        fail(f"predecessor {label} case-collides under {parent}")
    if not matches:
        return None
    result = matches[0]
    if result.is_symlink() or not result.is_dir():
        fail(f"predecessor {label} is not a real directory: {result}")
    return result


def installed_marker_paths(root: Path, sdk_band: str, workload_id: str) -> list[str]:
    """Return the actual direct-workload markers in every supported scope."""

    metadata = require_safe_path(root, Path("metadata/workloads"), "workload metadata")
    if not os.path.lexists(metadata):
        return []
    if metadata.is_symlink() or not metadata.is_dir():
        fail(f"workload metadata is not a real directory: {metadata}")
    matches: list[str] = []
    for directory, child_dirs, files in os.walk(metadata, topdown=True, followlinks=False):
        current = Path(directory)
        unsafe = [name for name in [*child_dirs, *files] if (current / name).is_symlink()]
        if unsafe:
            fail(f"workload metadata contains a symlink: {current / unsafe[0]}")
        if current.name.casefold() != "installedworkloads":
            continue
        relative = current.relative_to(metadata)
        scope = relative.parts[:-1]
        if scope.count(sdk_band) != 1:
            continue
        for name in files:
            if name.casefold() != workload_id.casefold():
                continue
            marker = current / name
            if not marker.is_file():
                fail(f"workload marker is not a regular file: {marker}")
            matches.append(marker.relative_to(root).as_posix())
    if len(matches) > 1:
        fail(
            f"direct workload {workload_id} has multiple markers for SDK feature band {sdk_band}: "
            + ", ".join(sorted(matches))
        )
    return matches


def observed_manifest_registration(root: Path, descriptor: dict, sdk_band: str) -> tuple[str, str]:
    """Locate the one installed manifest identity backing an existing marker."""

    registry_root = require_safe_path(
        root, Path("metadata/workloads/InstalledManifests/v1"), "installed manifest registry"
    )
    registry = casefold_child(registry_root, descriptor["manifestId"], "manifest registry")
    if registry is None:
        fail(f"existing {descriptor['workloadId']} marker lacks an installed manifest registration")
    records: list[tuple[str, str]] = []
    for version in registry.iterdir():
        if version.is_symlink() or not version.is_dir():
            fail(f"installed manifest version is unsafe: {version}")
        for manifest_band in version.iterdir():
            if manifest_band.is_symlink() or not manifest_band.is_dir():
                fail(f"installed manifest feature band is unsafe: {manifest_band}")
            leaf = manifest_band / sdk_band
            if not os.path.lexists(leaf):
                continue
            if leaf.is_symlink() or not leaf.is_file():
                fail(f"installed manifest registration is unsafe: {leaf}")
            records.append((version.name, manifest_band.name))
    if len(records) != 1:
        detail = "none" if not records else ", ".join(f"{version}/{band}" for version, band in records)
        fail(
            f"existing {descriptor['workloadId']} marker does not have one manifest identity "
            f"for SDK feature band {sdk_band}: {detail}"
        )
    return records[0]


def discover_predecessor_workloads(
    root: Path, sdk_band: str, workloads: list[dict], *, require_exact: bool
) -> list[dict]:
    """Bind marker presence to the manifest actually registered before mutation."""

    result: list[dict] = []
    for descriptor in workloads:
        markers = installed_marker_paths(root, sdk_band, descriptor["workloadId"])
        if not markers:
            if require_exact:
                fail(
                    f"requested --keep-existing workload is not registered for the selected SDK feature band: "
                    f"{descriptor['workloadId']}"
                )
            continue
        version, manifest_band = observed_manifest_registration(root, descriptor, sdk_band)
        actual = {
            **descriptor,
            "manifestVersion": version,
            "manifestFeatureBand": manifest_band,
            "markerPath": markers[0],
        }
        if require_exact and (
            version != descriptor["manifestVersion"]
            or manifest_band != descriptor["manifestFeatureBand"]
        ):
            fail(
                f"--keep-existing {descriptor['workloadId']} is not the exact locked manifest identity; "
                "refusing to reconcile an external registration"
            )
        manifest_band_root = require_safe_path(
            root,
            Path("sdk-manifests") / manifest_band,
            "installed workload manifest",
        )
        manifest_package = casefold_child(
            manifest_band_root, descriptor["manifestId"], "installed manifest package"
        )
        manifest_root = (
            casefold_child(manifest_package, version, "installed manifest version")
            if manifest_package is not None
            else None
        )
        if manifest_root is None:
            fail(f"existing {descriptor['workloadId']} has no matching extracted manifest")
        manifest = manifest_root / "WorkloadManifest.json"
        if manifest.is_symlink() or not manifest.is_file():
            fail(f"existing {descriptor['workloadId']} has no safe matching manifest: {manifest}")
        value = relaxed_json_file(manifest, "installed workload manifest")
        definitions = value.get("workloads")
        if not isinstance(definitions, dict) or not any(
            isinstance(name, str) and name.casefold() == descriptor["workloadId"].casefold()
            for name in definitions
        ):
            fail(f"existing {descriptor['workloadId']} manifest lacks its direct workload definition")
        result.append(actual)
    return result


def predecessor_package_ids(root: Path, context: dict) -> set[str]:
    """Discover the actual pre-migration package closure for replaced roots.

    The CLI reconciles old workload graphs globally.  Capturing the pre-existing
    direct/extends closure lets recovery restore only Apple predecessor payloads
    rather than replacing the complete live .NET root.
    """

    result: set[str] = set()
    for descriptor in [
        *context.get("predecessorWorkloads", []),
        *context.get("protectedWorkloads", []),
    ]:
        band = require_safe_path(
            root,
            Path("sdk-manifests") / descriptor["manifestFeatureBand"],
            "predecessor manifest",
        )
        package = casefold_child(band, descriptor["manifestId"], "manifest package")
        version = casefold_child(package, descriptor["manifestVersion"], "manifest version") if package else None
        manifest = version / "WorkloadManifest.json" if version else Path("/dev/null")
        if not manifest.is_file() or manifest.is_symlink():
            continue
        value = relaxed_json_file(manifest, "workload manifest")
        workloads = value.get("workloads")
        packs = value.get("packs")
        if not isinstance(workloads, dict) or not isinstance(packs, dict):
            fail(f"predecessor workload manifest lacks workload or pack maps: {manifest}")
        definitions: dict[str, tuple[dict, dict]] = {}
        for name, definition in workloads.items():
            if isinstance(name, str) and isinstance(definition, dict):
                definitions[name.casefold()] = (definition, packs)

        def resolve_definition(workload_id: str) -> list[tuple[dict, dict]]:
            direct = definitions.get(workload_id.casefold())
            if direct is not None:
                return [direct]
            band_root = require_safe_path(
                root,
                Path("sdk-manifests") / descriptor["manifestFeatureBand"],
                "extended predecessor manifest",
            )
            matches: list[tuple[dict, dict]] = []
            if not band_root.is_dir() or band_root.is_symlink():
                return []
            for package in band_root.iterdir():
                if package.is_symlink() or not package.is_dir():
                    continue
                for package_version in package.iterdir():
                    candidate = package_version / "WorkloadManifest.json"
                    if package_version.is_symlink() or not candidate.is_file() or candidate.is_symlink():
                        continue
                    candidate_value = relaxed_json_file(candidate, "extended workload manifest")
                    candidate_workloads = candidate_value.get("workloads")
                    candidate_packs = candidate_value.get("packs")
                    if not isinstance(candidate_workloads, dict) or not isinstance(candidate_packs, dict):
                        continue
                    for name, definition in candidate_workloads.items():
                        if isinstance(name, str) and name.casefold() == workload_id.casefold() and isinstance(definition, dict):
                            matches.append((definition, candidate_packs))
            # Workload-set manifests may deliberately repeat abstract runtime
            # definitions across compatibility packages.  Snapshot their union;
            # it is conservative and avoids choosing one order-dependent graph.
            return matches

        visited: set[str] = set()

        def visit(workload_id: str) -> None:
            key = workload_id.casefold()
            if key in visited:
                return
            visited.add(key)
            resolved = resolve_definition(workload_id)
            if not resolved:
                fail(f"predecessor manifest lacks workload {workload_id!r}: {manifest}")
            for definition, definition_packs in resolved:
                direct = definition.get("packs", [])
                extends = definition.get("extends", [])
                if not isinstance(direct, list) or not all(isinstance(item, str) for item in direct):
                    fail(f"predecessor workload packs are invalid: {workload_id!r}")
                if not isinstance(extends, list) or not all(isinstance(item, str) for item in extends):
                    fail(f"predecessor workload extends are invalid: {workload_id!r}")
                for pack_id in direct:
                    result.add(pack_id)
                    pack = definition_packs.get(pack_id)
                    if isinstance(pack, dict):
                        aliases = pack.get("alias-to", {})
                        if isinstance(aliases, dict):
                            for alias in aliases.values():
                                if isinstance(alias, str):
                                    result.add(alias)
                for extended in extends:
                    visit(extended)

        visit(descriptor["workloadId"])
    return result


def predecessor_units(root: Path, context: dict, units: dict[str, str]) -> dict[str, str]:
    package_ids = {item.casefold() for item in predecessor_package_ids(root, context)}
    result: dict[str, str] = {}
    for raw, value in units.items():
        parts = PurePosixPath(raw).parts
        package_id: str | None = None
        if len(parts) >= 3 and parts[0] == "packs":
            package_id = parts[1]
        elif len(parts) == 2 and parts[0] in {"template-packs", "library-packs"}:
            lowered = parts[1].casefold()
            if any(lowered.startswith(f"{item}.") for item in package_ids):
                result[raw] = value
                continue
        if package_id is not None and package_id.casefold() in package_ids:
            result[raw] = value
    return result


def predecessor_installed_pack_leaf(
    raw: str, package_ids: set[str], sdk_feature_band: str
) -> str | None:
    """Map a captured predecessor unit to the matching InstalledPacks record."""

    parts = PurePosixPath(raw).parts
    if len(parts) >= 3 and parts[0] == "packs":
        return f"InstalledPacks/v1/{parts[1]}/{parts[2]}/{sdk_feature_band}"
    if len(parts) != 2 or parts[0] not in {"template-packs", "library-packs"}:
        return None
    filename = parts[1]
    lowered = filename.casefold()
    suffix = ".nupkg"
    if not lowered.endswith(suffix):
        return None
    for package_id in sorted(package_ids, key=lambda item: len(item), reverse=True):
        prefix = package_id.casefold() + "."
        if not lowered.startswith(prefix):
            continue
        version = filename[len(package_id) + 1 : -len(suffix)]
        if version:
            return f"InstalledPacks/v1/{package_id}/{version}/{sdk_feature_band}"
    return None


def copy_predecessor_units(root: Path, destination: Path, units: dict[str, str]) -> None:
    for raw, expected in units.items():
        source = require_safe_path(root, safe_relative(raw, UNIT_ROOTS), "predecessor workload unit")
        target = destination / safe_relative(raw, UNIT_ROOTS)
        if source.is_symlink() or not os.path.lexists(source):
            fail(f"predecessor workload unit is unsafe or missing: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, target, follow_symlinks=False)
        else:
            fail(f"predecessor workload unit has unsupported type: {source}")
        if tree_hash(target) != expected:
            fail(f"predecessor workload snapshot did not verify: {source}")


def copy_snapshot(source: Path, destination: Path) -> None:
    def ignore_history(_directory: str, names: list[str]) -> list[str]:
        return [name for name in names if name == "history"]

    shutil.copytree(source, destination, symlinks=True, ignore=ignore_history)


def validated_expected_packages(records: object) -> list[dict[str, str]]:
    if records is None:
        return []
    if not isinstance(records, list):
        fail("expected package inventory must be a list")
    result: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            fail("expected package inventory contains an invalid record")
        package_id = record.get("id")
        version = record.get("version")
        if not isinstance(package_id, str) or not package_id or not isinstance(version, str) or not version:
            fail("expected package inventory contains an incomplete identity")
        result.append({"id": package_id, "version": version})
    return result


def expected_unit_paths(expected_packages: list[dict[str, str]]) -> set[str]:
    result: set[str] = set()
    for item in expected_packages:
        package_id = item["id"]
        version = item["version"]
        lowered = f"{package_id.lower()}.{version.lower()}.nupkg"
        result.update(
            {
                f"packs/{package_id}/{version}",
                f"template-packs/{lowered}",
                f"library-packs/{lowered}",
            }
        )
    return result


def unit_is_expected(raw: str, expected_packages: list[dict[str, str]]) -> bool:
    relative = safe_relative(raw, UNIT_ROOTS).as_posix()
    return relative.casefold() in {
        item.casefold() for item in expected_unit_paths(expected_packages)
    }


def manifest_package_id(expected_packages: list[dict[str, str]], manifest_feature_band: str) -> str:
    raw_suffix = f".Manifest-{manifest_feature_band}"
    suffix = raw_suffix.casefold()
    matches = [
        item["id"][: -len(raw_suffix)]
        for item in expected_packages
        if item["id"].casefold().endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return "Microsoft.NET.Sdk.iOS"
    fail("expected package inventory contains multiple workload manifest packages")


def manifest_snapshot_label(context: dict, feature_band: str) -> str:
    labels = expected_snapshot_paths({"context": context, "snapshots": {}})
    matches = [label for label, path in labels.items() if path == Path("sdk-manifests") / feature_band]
    if len(matches) != 1:
        fail(f"transaction cannot locate the {feature_band} manifest snapshot")
    return matches[0]


def make_ownership_plan(context: dict, expected_packages: list[dict[str, str]]) -> dict:
    sdk_band = context["sdkFeatureBand"]
    workload_version = context["workloadVersion"]
    workload_set_band = context["workloadSetFeatureBand"]
    workloads = context["workloads"]
    metadata_leaves = [f"InstalledWorkloadSets/v1/{workload_version}/{workload_set_band}/{sdk_band}"]
    tree_prefixes: dict[str, list[str]] = {}
    protected_tree_prefixes: dict[str, list[str]] = {}
    protected_metadata_leaves: list[str] = []
    predecessor_by_workload = {
        item["workloadId"].casefold(): item
        for item in context.get("predecessorWorkloads", [])
    }

    def add_manifest_tree(
        destination: dict[str, list[str]], descriptor: dict[str, str]
    ) -> None:
        destination.setdefault(
            manifest_snapshot_label(context, descriptor["manifestFeatureBand"]), []
        ).append(f"{descriptor['manifestId']}/{descriptor['manifestVersion']}")

    def manifest_leaf(descriptor: dict[str, str]) -> str:
        return (
            f"InstalledManifests/v1/{descriptor['manifestId']}/{descriptor['manifestVersion']}/"
            f"{descriptor['manifestFeatureBand']}/{sdk_band}"
        )

    def marker_leaf(descriptor: dict[str, str]) -> str:
        marker_path = descriptor.get("markerPath")
        if marker_path is None:
            return f"{sdk_band}/InstalledWorkloads/{descriptor['workloadId']}"
        if not isinstance(marker_path, str):
            fail("workload marker path is invalid")
        marker = safe_relative(marker_path)
        if marker.parts[:2] != ("metadata", "workloads"):
            fail("workload marker is outside workload metadata")
        return marker.relative_to(Path("metadata/workloads")).as_posix()

    for workload in workloads:
        workload_id = workload["workloadId"]
        manifest_id = workload["manifestId"]
        manifest_version = workload["manifestVersion"]
        manifest_band = workload["manifestFeatureBand"]
        metadata_leaves.extend(
            [
                f"{sdk_band}/InstalledWorkloads/{workload_id}",
                manifest_leaf(workload),
            ]
        )
        add_manifest_tree(tree_prefixes, workload)
        predecessor = predecessor_by_workload.get(workload_id.casefold())
        if predecessor is not None:
            metadata_leaves.extend([marker_leaf(predecessor), manifest_leaf(predecessor)])
            add_manifest_tree(tree_prefixes, predecessor)
    for workload in context.get("protectedWorkloads", []):
        workload_id = workload["workloadId"]
        manifest_id = workload["manifestId"]
        manifest_version = workload["manifestVersion"]
        manifest_band = workload["manifestFeatureBand"]
        protected_metadata_leaves.extend(
            [
                marker_leaf(workload),
                manifest_leaf(workload),
            ]
        )
        add_manifest_tree(protected_tree_prefixes, workload)
    for item in expected_packages:
        package_id = item["id"]
        if ".manifest-" in package_id.casefold() or package_id.casefold().startswith(
            "microsoft.net.workloads."
        ):
            continue
        metadata_leaves.append(
            f"InstalledPacks/v1/{package_id}/{item['version']}/{sdk_band}"
        )
    workload_set_label = manifest_snapshot_label(context, workload_set_band)
    tree_prefixes.setdefault(workload_set_label, []).append(
        f"workloadsets/{workload_version}"
    )
    return {
        "treePrefixes": tree_prefixes,
        "leafPaths": {"workloadMetadata": sorted(set(metadata_leaves))},
        "protectedTreePrefixes": protected_tree_prefixes,
        "protectedLeafPaths": {"workloadMetadata": sorted(set(protected_metadata_leaves))},
        "sharedJson": {
            "snapshot": "workloadMetadata",
            "path": f"X64/{sdk_band}/globaljsonworkloadsets.json",
            "key": context["controlGlobalJson"],
            "value": workload_version,
        },
    }


def normalise_workloads(
    raw_workloads: list[dict[str, str]], expected_packages: list[dict[str, str]]
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_workloads:
        workload_id = validate_component(raw.get("workloadId"), "workload ID")
        folded = workload_id.casefold()
        if folded in seen:
            fail(f"duplicate managed workload ID: {workload_id}")
        seen.add(folded)
        manifest_id = validate_component(raw.get("manifestId"), "manifest package ID")
        manifest_version = validate_component(raw.get("manifestVersion"), "manifest version")
        manifest_band = validate_component(raw.get("manifestFeatureBand"), "manifest feature band")
        result.append(
            {
                "workloadId": workload_id,
                "manifestId": manifest_id,
                "manifestVersion": manifest_version,
                "manifestFeatureBand": manifest_band,
            }
        )
    if not result:
        fail("transaction requires at least one managed workload")
    return sorted(result, key=lambda item: item["workloadId"].casefold())


def begin(
    root: Path,
    transaction: Path,
    expected_packages: list[dict[str, str]] | None = None,
    *,
    sdk_feature_band: str,
    workload_version: str,
    workloads: list[dict[str, str]],
    protected_workloads: list[dict[str, str]] | None = None,
    control_global_json: str,
) -> None:
    cleanup_finalized_transaction(transaction)
    if transaction.exists() or transaction.is_symlink():
        if transaction.is_symlink():
            fail(f"workload transaction must not be a symlink: {transaction}")
        state = read_state(transaction, root)
        if state.get("status") in {"prepared", "uninstalling", "restoring"}:
            restore(root, transaction, verify_committed=False)
        elif state.get("status") == "restored":
            finalize(root, transaction)
        else:
            fail(f"a committed workload transaction already exists: {transaction}")
    expected = validated_expected_packages(expected_packages)
    managed_workloads = normalise_workloads(workloads, expected)
    protected = normalise_workloads(protected_workloads or [], expected) if protected_workloads else []
    overlap = {item["workloadId"].casefold() for item in managed_workloads} & {
        item["workloadId"].casefold() for item in protected
    }
    if overlap:
        fail("managed and protected workload IDs overlap: " + ", ".join(sorted(overlap)))
    validated_sdk_band = validate_component(sdk_feature_band, "SDK feature band")
    # Discover registrations at the transaction boundary, not during early
    # installer planning. Managed workloads may replace an older manifest; kept
    # workloads must still be exactly lock-compatible at this point.
    predecessor_workloads = discover_predecessor_workloads(
        root, validated_sdk_band, managed_workloads, require_exact=False
    )
    protected = discover_predecessor_workloads(
        root, validated_sdk_band, protected, require_exact=True
    )
    context = {
        "sdkFeatureBand": validated_sdk_band,
        "workloadVersion": validate_component(workload_version, "workload version"),
        "workloadSetFeatureBand": feature_band(workload_version),
        "workloads": managed_workloads,
        "protectedWorkloads": protected,
        "predecessorWorkloads": predecessor_workloads,
        "controlGlobalJson": control_global_json,
    }
    if not Path(control_global_json).is_absolute() or "\0" in control_global_json:
        fail("control global.json path must be absolute")
    snapshots = expected_snapshot_paths({"context": context, "snapshots": {}})
    plan = make_ownership_plan(context, expected)
    mkdir_durable(transaction.parent, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=f".{transaction.name}.prepare-", dir=transaction.parent))
    try:
        before = stage / "before"
        before.mkdir()
        snapshot_state: dict[str, dict] = {}
        for label, relative in snapshots.items():
            source = require_safe_path(root, relative, "workload state")
            snapshot_state[label] = {"path": relative.as_posix(), "existed": source.exists()}
            if source.exists():
                if source.is_symlink() or not source.is_dir():
                    fail(f"workload state path is not a real directory: {source}")
                copy_snapshot(source, before / label)
        before_units = current_units(root)
        predecessor = predecessor_units(root, context, before_units)
        protected_predecessor = predecessor_units(
            root,
            {
                **context,
                "workloads": [],
                "predecessorWorkloads": [],
                "protectedWorkloads": context.get("protectedWorkloads", []),
            },
            before_units,
        )
        predecessor_root = stage / "before-units"
        predecessor_root.mkdir()
        copy_predecessor_units(root, predecessor_root, predecessor)
        # Older official graphs can contain target packs that the derivative no
        # longer selects (for example iOS simulator/net9 packs).  The CLI may
        # remove their installation records during reconciliation; authorize
        # only those records whose payload is in the durable predecessor set.
        predecessor_leaves = plan["leafPaths"]["workloadMetadata"]
        protected_leaves = plan["protectedLeafPaths"]["workloadMetadata"]
        predecessor_ids = predecessor_package_ids(root, context)
        protected_ids = predecessor_package_ids(
            root,
            {
                **context,
                "workloads": [],
                "predecessorWorkloads": [],
                "protectedWorkloads": context.get("protectedWorkloads", []),
            },
        )
        for raw in predecessor:
            leaf = predecessor_installed_pack_leaf(
                raw,
                protected_ids if raw in protected_predecessor else predecessor_ids,
                context["sdkFeatureBand"],
            )
            if leaf is not None:
                (protected_leaves if raw in protected_predecessor else predecessor_leaves).append(leaf)
        plan["leafPaths"]["workloadMetadata"] = sorted(set(predecessor_leaves))
        plan["protectedLeafPaths"]["workloadMetadata"] = sorted(set(protected_leaves))
        state = {
            "schemaVersion": SCHEMA_VERSION,
            "owner": OWNER,
            "status": "prepared",
            "dotnetRoot": str(root),
            "snapshots": snapshot_state,
            "beforeUnits": before_units,
            "predecessorUnits": predecessor,
            "protectedPredecessorUnits": protected_predecessor,
            "expectedPackages": expected,
            "context": context,
            "ownershipPlan": plan,
        }
        fsync_tree(before)
        fsync_tree(predecessor_root)
        write_state(stage, state)
        try:
            publish_directory_noreplace(stage, transaction)
        except FileExistsError:
            fail(f"workload transaction appeared during publication: {transaction}")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"workload_transaction={transaction}")


def path_relation_allowed(raw: str, candidates: list[str], *, allow_descendants: bool) -> bool:
    value = PurePosixPath(raw).parts
    for candidate in candidates:
        expected = PurePosixPath(candidate).parts
        common = min(len(value), len(expected))
        if tuple(part.casefold() for part in value[:common]) != tuple(
            part.casefold() for part in expected[:common]
        ):
            continue
        if len(value) <= len(expected) or allow_descendants:
            return True
    return False


def path_is_owned(state: dict, label: str, raw: str) -> bool:
    plan = state.get("ownershipPlan", {})
    tree_prefixes = plan.get("treePrefixes", {}).get(label, [])
    leaf_paths = list(plan.get("leafPaths", {}).get(label, []))
    shared = plan.get("sharedJson", {})
    if shared.get("snapshot") == label and isinstance(shared.get("path"), str):
        leaf_paths.append(shared["path"])
    return path_relation_allowed(raw, tree_prefixes, allow_descendants=True) or path_relation_allowed(
        raw, leaf_paths, allow_descendants=False
    )


def path_is_protected(state: dict, label: str, raw: str) -> bool:
    plan = state.get("ownershipPlan", {})
    return path_relation_allowed(
        raw, plan.get("protectedTreePrefixes", {}).get(label, []), allow_descendants=True
    ) or path_relation_allowed(
        raw, plan.get("protectedLeafPaths", {}).get(label, []), allow_descendants=False
    )


def path_is_cli_reconciliation_metadata(label: str, raw: str) -> bool:
    """Allow the CLI's documented global record refresh, never foreign payloads.

    Workload install rewrites installation records for Android/MAUI/Wasm and two
    Mono toolchain manifests while reconciling a workload set.  Their workload
    markers and pack bytes remain protected by the broader snapshot/unit checks.
    These deltas are journaled for diagnosis but deliberately are not replayed:
    replaying a global record could overwrite a later foreign CLI operation.
    """

    folded = raw.casefold()
    if label == "workloadMetadata":
        return folded.startswith("installedpacks/v1/") or folded.endswith(
            "/installstate/globaljsonworkloadsets.json"
        )
    return label.startswith("manifestBand") and folded.startswith(
        "microsoft.net.workload.mono.toolchain."
    )


def json_mapping(path: Path) -> dict[str, str]:
    if not os.path.lexists(path):
        return {}
    if path.is_symlink() or not path.is_file():
        fail(f"shared workload metadata is not a regular file: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        value = {} if not raw else json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read shared workload metadata {path}: {error}")
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        fail(f"shared workload metadata is not a string map: {path}")
    return value


def live_shared_consumers(root: Path, state: dict) -> list[str]:
    shared = state["ownershipPlan"]["sharedJson"]
    snapshot = state["snapshots"][shared["snapshot"]]
    path = require_safe_path(
        root,
        safe_relative(snapshot["path"]) / safe_relative(shared["path"]),
        "shared workload metadata",
    )
    return sorted(
        candidate
        for candidate, version in json_mapping(path).items()
        if candidate != shared["key"] and version == shared["value"]
    )


def acquire_shared_metadata_lock(root: Path, state: dict) -> tuple[int, Path, bool]:
    shared = state["ownershipPlan"]["sharedJson"]
    snapshot = state["snapshots"][shared["snapshot"]]
    path = require_safe_path(
        root,
        safe_relative(snapshot["path"]) / safe_relative(shared["path"]),
        "shared workload metadata",
    )
    mkdir_durable(path.parent, mode=0o700)
    created = not os.path.lexists(path)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        validate_shared_metadata_lock(descriptor, path)
        if created:
            os.fsync(descriptor)
            fsync_directory(path.parent)
        return descriptor, path, created
    except BaseException:
        os.close(descriptor)
        raise


def validate_shared_metadata_lock(descriptor: int, path: Path) -> None:
    opened = os.fstat(descriptor)
    try:
        current = path.lstat()
    except FileNotFoundError:
        fail(f"shared workload metadata lock path disappeared: {path}")
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or opened.st_dev != current.st_dev
        or opened.st_ino != current.st_ino
    ):
        fail(f"shared workload metadata lock path was replaced: {path}")


def cleanup_shared_metadata_placeholder(
    descriptor: int, path: Path, created: bool
) -> None:
    if not created:
        return
    validate_shared_metadata_lock(descriptor, path)
    if os.fstat(descriptor).st_size != 0:
        return
    path.unlink()
    fsync_directory(path.parent)


def snapshot_changes(
    root: Path, transaction: Path, state: dict
) -> tuple[dict[str, dict], list[dict], list[str], list[str]]:
    changes: dict[str, dict] = {}
    unexpected: list[str] = []
    shared = state["ownershipPlan"]["sharedJson"]
    for label, relative, details in snapshot_items(state):
        before = inventory(transaction / "before" / label) if details["existed"] else {}
        after = inventory(require_safe_path(root, relative, "workload state"), ignore_history=True)
        changed: dict[str, dict] = {}
        for path in sorted(before.keys() | after.keys()):
            if before.get(path) == after.get(path):
                continue
            if label == shared["snapshot"] and path.casefold() == shared["path"].casefold():
                continue
            owned = path_is_owned(state, label, path)
            protected = path_is_protected(state, label, path)
            reconciliation = path_is_cli_reconciliation_metadata(label, path) and (
                not owned or protected
            )
            if owned or protected or reconciliation:
                changed[path] = {
                    "before": before.get(path),
                    "after": after.get(path),
                    "protected": protected,
                    # Foreign records and protected pack records are never
                    # project-owned. Keep a durable audit trail, but do not
                    # overwrite a later CLI reconciliation during rollback.
                    "reconciliation": reconciliation,
                }
            else:
                unexpected.append(f"{relative.as_posix()}/{path}")
        changes[label] = {"rootCreated": not details["existed"] and (root / relative).is_dir(), "entries": changed}
    shared_label = shared["snapshot"]
    shared_relative = Path(state["snapshots"][shared_label]["path"]) / safe_relative(shared["path"])
    before_shared = transaction / "before" / shared_label / safe_relative(shared["path"])
    before_map = json_mapping(before_shared)
    after_map = json_mapping(require_safe_path(root, shared_relative, "shared workload metadata"))
    key = shared["key"]
    shared_consumers = live_shared_consumers(root, state)
    for changed_key in sorted(before_map.keys() | after_map.keys()):
        if before_map.get(changed_key) != after_map.get(changed_key) and changed_key != key:
            unexpected.append(f"{shared_relative.as_posix()}:key:{changed_key}")
    patches: list[dict] = []
    before_exists = key in before_map
    after_exists = key in after_map
    if before_exists != after_exists or before_map.get(key) != after_map.get(key):
        if not after_exists or after_map[key] != shared["value"]:
            unexpected.append(f"{shared_relative.as_posix()}:key:{key}")
        else:
            patches.append(
                {
                    "snapshot": shared_label,
                    "path": shared["path"],
                    "key": key,
                    "beforeExists": before_exists,
                    "before": before_map.get(key),
                    "after": after_map[key],
                    "fileExistedBefore": os.path.lexists(before_shared),
                    "beforeFileMode": (
                        signature(before_shared) or {"mode": 0o644}
                    )["mode"],
                }
            )
    return changes, patches, unexpected, shared_consumers


def fsync_committed_payload(root: Path, state: dict, created_units: dict[str, str]) -> None:
    for _label, relative, _details in snapshot_items(state):
        path = require_safe_path(root, relative, "committed workload state")
        if path.is_dir() and not path.is_symlink():
            fsync_tree(path)
    for raw in set(created_units) | set(state.get("predecessorUnitChanges", {})):
        path = require_safe_path(root, safe_relative(raw, UNIT_ROOTS), "committed workload pack")
        if path.is_dir() and not path.is_symlink():
            fsync_tree(path)
        elif path.is_file() and not path.is_symlink():
            fsync_file(path)
            fsync_directory(path.parent)


def commit(root: Path, transaction: Path) -> None:
    state = read_state(transaction, root)
    if state.get("status") != "prepared":
        fail("only a prepared transaction can be committed")
    lock_fd, lock_path, lock_created = acquire_shared_metadata_lock(root, state)
    try:
        after_units = current_units(root)
        before_units = state["beforeUnits"]
        changed = sorted(path for path in before_units if after_units.get(path) != before_units[path])
        predecessor = state.get("predecessorUnits", {})
        unprotected = sorted(path for path in changed if path not in predecessor)
        if unprotected:
            fail(
                "workload CLI modified pre-existing pack payloads outside the durable predecessor scope: "
                + ", ".join(unprotected)
            )
        state["predecessorUnitChanges"] = {
            path: {"before": before_units[path], "after": after_units.get(path)}
            for path in changed
        }
        protected_predecessor_changes = sorted(
            path for path in changed if path in state.get("protectedPredecessorUnits", {})
        )
        state["createdUnits"] = {
            path: value for path, value in after_units.items() if path not in before_units
        }
        unexpected = sorted(
            path
            for path in state["createdUnits"]
            if not unit_is_expected(path, state.get("expectedPackages", []))
        )
        if unexpected:
            fail("workload CLI created units outside the trusted package graph: " + ", ".join(unexpected))
        changes, patches, unexpected, shared_consumers = snapshot_changes(
            root, transaction, state
        )
        if unexpected:
            fail(
                "concurrent workload metadata changes are outside this installation: "
                + ", ".join(unexpected)
            )
        protected_metadata_changes = sorted(
            f"{label}/{raw}"
            for label, change_set in changes.items()
            for raw, change in change_set["entries"].items()
            if change.get("protected") and not change.get("reconciliation")
        )
        if protected_predecessor_changes or protected_metadata_changes:
            fail(
                "workload CLI changed a --keep-existing registration: "
                + ", ".join([*protected_predecessor_changes, *protected_metadata_changes])
            )
        state["snapshotChanges"] = changes
        state["sharedJsonPatches"] = patches
        state["sharedWorkloadSetConsumers"] = shared_consumers
        fsync_committed_payload(root, state, state["createdUnits"])
        state["status"] = "committed"
        write_state(transaction, state)
    finally:
        try:
            cleanup_shared_metadata_placeholder(lock_fd, lock_path, lock_created)
        finally:
            os.close(lock_fd)
    print("workload_transaction=committed")


def verify(root: Path, transaction: Path) -> None:
    state = read_state(transaction, root)
    if state.get("status") != "committed":
        fail("workload transaction is not committed")
    for label, relative, _details in snapshot_items(state):
        require_safe_path(root, relative, "verified workload state")
        changes = state.get("snapshotChanges", {}).get(label, {}).get("entries", {})
        for raw, change in changes.items():
            if change.get("reconciliation"):
                continue
            path = require_safe_path(root, relative / safe_relative(raw), "verified workload state")
            if signature(path) != change.get("after"):
                fail(f"installed workload state changed since setup: {path}")
    for raw, expected in state.get("createdUnits", {}).items():
        path = require_safe_path(root, safe_relative(raw, UNIT_ROOTS), "verified workload pack")
        if tree_hash(path) != expected:
            fail(f"installed workload pack changed since setup: {path}")
    for raw, change in state.get("predecessorUnitChanges", {}).items():
        path = require_safe_path(root, safe_relative(raw, UNIT_ROOTS), "verified predecessor pack")
        if tree_hash(path) != change.get("after"):
            fail(f"replaced predecessor workload pack changed since setup: {path}")
    for patch in state.get("sharedJsonPatches", []):
        snapshot = state["snapshots"].get(patch.get("snapshot"), {})
        relative = snapshot.get("path")
        if not isinstance(relative, str):
            fail("workload transaction contains an invalid shared metadata patch")
        mapping = json_mapping(
            require_safe_path(
                root,
                safe_relative(relative) / safe_relative(patch["path"]),
                "verified shared workload metadata",
            )
        )
        if mapping.get(patch["key"]) != patch.get("after"):
            fail("installed workload shared metadata changed since setup")
    print("workload_transaction=ok")


def validate_snapshots(transaction: Path, state: dict, changes: dict[str, dict]) -> None:
    conflicts: list[str] = []
    for label, _relative, _details in snapshot_items(state):
        for raw, change in changes[label]["entries"].items():
            if change.get("reconciliation"):
                continue
            before = change.get("before")
            if before is None or before.get("kind") == "directory":
                continue
            source = transaction / "before" / label / safe_relative(raw)
            if signature(source) != before:
                conflicts.append(f"{label}/{raw}")
    if conflicts:
        fail("workload snapshots changed; refusing to restore: " + ", ".join(conflicts))


def restore_tombstone(transaction: Path, target: Path, index: int) -> Path:
    token = hashlib.sha256(str(transaction).encode("utf-8")).hexdigest()[:16]
    return target.with_name(f".{target.name}.dotnet-ios-linux-restore-{token}-{index}")


def build_restore_ops(
    root: Path,
    state: dict,
    units: dict[str, str],
    changes: dict[str, dict],
    patches: list[dict],
    *,
    remove_units: bool,
    protect_shared_resources: bool = False,
) -> list[dict]:
    operations: list[dict] = []
    # A predecessor existed before our transaction.  It is never a shared
    # project-owned resource: restore its bytes even if another control
    # global.json later selects the same workload set.
    for raw, change in sorted(state.get("predecessorUnitChanges", {}).items()):
        operations.append({"kind": "restore-predecessor-unit", "path": raw, **change})
    if remove_units and not protect_shared_resources:
        for raw, expected in sorted(
            units.items(), key=lambda item: (item[0].count("/"), item[0]), reverse=True
        ):
            operations.append({"kind": "remove-unit", "path": raw, "expected": expected})

    removed: list[tuple[str, str, dict]] = []
    directories: list[tuple[str, str, dict]] = []
    nodes: list[tuple[str, str, dict]] = []
    for label, _relative, _details in snapshot_items(state):
        for raw, change in changes[label]["entries"].items():
            # Global CLI reconciliation records are intentionally diagnostic
            # only. They can be shared with unrelated workload operations, so
            # restoring them would replay foreign state.
            if change.get("reconciliation"):
                continue
            before = change.get("before")
            # Only project-created state can be retained for a late shared
            # workload-set consumer.  Predecessor payload/metadata was present
            # before this transaction and must always be restored.
            if protect_shared_resources and before is None:
                continue
            item = (label, raw, change)
            if before is None:
                removed.append(item)
            elif before.get("kind") == "directory":
                directories.append(item)
            else:
                nodes.append(item)
    for label, raw, change in sorted(
        removed, key=lambda item: (item[1].count("/"), item[1]), reverse=True
    ):
        operations.append({"kind": "restore-entry", "snapshot": label, "path": raw, **change})
    for label, raw, change in sorted(
        directories, key=lambda item: (item[1].count("/"), item[1])
    ):
        operations.append({"kind": "restore-entry", "snapshot": label, "path": raw, **change})
    for label, raw, change in sorted(nodes, key=lambda item: (item[1].count("/"), item[1])):
        operations.append({"kind": "restore-entry", "snapshot": label, "path": raw, **change})
    for label, relative, _details in snapshot_items(state):
        if changes[label].get("rootCreated") and not protect_shared_resources:
            operations.append({"kind": "remove-empty-root", "path": relative.as_posix()})
    # The shared JSON mutation is last: replacing or unlinking the locked inode
    # ends pathname exclusion, so no destructive resource operation may follow.
    for patch in patches:
        operations.append({"kind": "restore-json-key", **patch})
    return operations


def durable_write_bytes(path: Path, data: bytes, mode: int, temporary: Path) -> None:
    mkdir_durable(path.parent, mode=0o700)
    if os.path.lexists(temporary):
        if temporary.is_dir() and not temporary.is_symlink():
            fail(f"restore temporary path is an unexpected directory: {temporary}")
        temporary.unlink()
        fsync_directory(temporary.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        fsync_file(temporary)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        if os.path.lexists(temporary):
            temporary.unlink()
            fsync_directory(temporary.parent)
        raise


def remove_for_restore(
    target: Path,
    tombstone: Path,
    *,
    expected_signature: dict | None = None,
    expected_hash: str | None = None,
) -> None:
    if os.path.lexists(tombstone):
        if os.path.lexists(target):
            fail(f"restore target and tombstone both exist: {target}")
        durable_remove(target, tombstone)
        return
    if not os.path.lexists(target):
        fsync_path_state(target)
        return
    if expected_signature is not None and signature(target) != expected_signature:
        fail(f"workload state changed; refusing to restore: {target}")
    if expected_hash is not None and tree_hash(target) != expected_hash:
        fail(f"owned workload pack changed; refusing to remove: {target}")
    durable_remove(target, tombstone)


def safe_restore_target(root: Path, target: Path) -> Path:
    try:
        relative = target.relative_to(root)
    except ValueError:
        fail(f"restore target escapes the .NET root: {target}")
    return require_safe_path(root, relative, "restore target")


def execute_restore_op(
    root: Path, transaction: Path, state: dict, operation: dict, index: int
) -> None:
    kind = operation.get("kind")
    if kind == "restore-predecessor-unit":
        raw = operation.get("path")
        if not isinstance(raw, str):
            fail("predecessor restore operation lacks a path")
        target = safe_restore_target(root, root / safe_relative(raw, UNIT_ROOTS))
        before_hash = operation.get("before")
        after_hash = operation.get("after")
        if not isinstance(before_hash, str) or (after_hash is not None and not isinstance(after_hash, str)):
            fail("predecessor restore operation has invalid hashes")
        current_hash = tree_hash(target)
        if current_hash == before_hash:
            fsync_path_state(target)
            return
        if current_hash != after_hash:
            fail(f"replaced predecessor workload pack changed; refusing to restore: {target}")
        source = transaction / "before-units" / safe_relative(raw, UNIT_ROOTS)
        if tree_hash(source) != before_hash:
            fail(f"predecessor workload snapshot changed; refusing to restore: {source}")
        tombstone = restore_tombstone(transaction, target, index)
        remove_for_restore(target, tombstone, expected_hash=after_hash)
        mkdir_durable(target.parent, mode=0o700)
        temporary = target.with_name(tombstone.name + ".tmp")
        if source.is_dir() and not source.is_symlink():
            if os.path.lexists(temporary):
                durable_remove(temporary, temporary.with_name(temporary.name + ".old"))
            shutil.copytree(source, temporary, symlinks=True)
            fsync_tree(temporary)
            os.replace(temporary, target)
            fsync_directory(target.parent)
        elif source.is_file() and not source.is_symlink():
            durable_write_bytes(target, source.read_bytes(), stat.S_IMODE(source.stat().st_mode), temporary)
        else:
            fail(f"predecessor workload snapshot has an unsupported type: {source}")
        if tree_hash(target) != before_hash:
            fail(f"predecessor workload restore did not verify: {target}")
        return
    if kind == "remove-unit":
        target = safe_restore_target(root, root / safe_relative(operation["path"], UNIT_ROOTS))
        remove_for_restore(
            target,
            restore_tombstone(transaction, target, index),
            expected_hash=operation["expected"],
        )
        for parent in (target.parent, target.parent.parent):
            if parent == root:
                break
            try:
                parent.rmdir()
                fsync_directory(parent.parent)
            except FileNotFoundError:
                fsync_path_state(parent)
                continue
            except OSError:
                break
        return
    if kind == "remove-empty-root":
        target = safe_restore_target(root, root / safe_relative(operation["path"]))
        try:
            target.rmdir()
            fsync_directory(target.parent)
        except FileNotFoundError:
            fsync_path_state(target)
        except OSError:
            # A concurrent, unowned entry keeps the shared parent alive.
            pass
        return
    snapshot = operation.get("snapshot")
    details = state["snapshots"].get(snapshot)
    if not isinstance(details, dict):
        fail("restore operation references an unknown snapshot")
    target = safe_restore_target(
        root, root / safe_relative(details["path"]) / safe_relative(operation["path"])
    )
    tombstone = restore_tombstone(transaction, target, index)
    if kind == "restore-json-key":
        mapping = json_mapping(target)
        key = operation["key"]
        current_exists = key in mapping
        current = mapping.get(key)
        before_exists = operation["beforeExists"]
        before = operation.get("before")
        if current_exists == before_exists and current == before:
            if os.path.lexists(tombstone):
                if os.path.lexists(target):
                    fail(f"restore target and tombstone both exist: {target}")
                durable_remove(target, tombstone)
            else:
                fsync_path_state(target)
            return
        if not current_exists or current != operation.get("after"):
            fail(f"shared workload metadata changed; refusing to restore key: {key}")
        if before_exists:
            mapping[key] = before
        else:
            mapping.pop(key, None)
        if not operation.get("fileExistedBefore") and not mapping:
            remove_for_restore(
                target,
                tombstone,
                expected_signature=signature(target),
            )
            return
        temporary = target.with_name(tombstone.name + ".tmp")
        durable_write_bytes(
            target,
            (json.dumps(mapping, indent=2) + "\n").encode("utf-8"),
            operation.get("beforeFileMode", 0o644),
            temporary,
        )
        return
    if kind != "restore-entry":
        fail(f"unknown restore operation: {kind!r}")
    before = operation.get("before")
    after = operation.get("after")
    current = signature(target)
    if before is None:
        if after is not None and after.get("kind") == "directory":
            if current is None:
                fsync_path_state(target)
                return
            if current != after:
                fail(f"workload state changed; refusing to restore: {target}")
            try:
                target.rmdir()
                fsync_directory(target.parent)
            except OSError:
                # A preserved concurrent entry keeps this unowned parent alive.
                pass
            return
        if current is None:
            if os.path.lexists(tombstone):
                durable_remove(target, tombstone)
            else:
                fsync_path_state(target)
            return
        remove_for_restore(target, tombstone, expected_signature=after)
        return
    if current == before and not os.path.lexists(tombstone):
        fsync_path_state(target)
        return
    desired_kind = before.get("kind")
    if desired_kind == "directory":
        if current is not None and current.get("kind") != "directory":
            remove_for_restore(target, tombstone, expected_signature=after)
            current = None
        elif os.path.lexists(tombstone):
            if current is not None:
                fail(f"restore target and tombstone both exist: {target}")
            durable_remove(target, tombstone)
        if current is not None and current != before and current != after:
            fail(f"workload state changed; refusing to restore: {target}")
        mkdir_durable(target, mode=before["mode"])
        target.chmod(before["mode"])
        fsync_directory(target)
        fsync_directory(target.parent)
        return
    if current is not None and current.get("kind") == "directory":
        remove_for_restore(target, tombstone, expected_signature=after)
        current = None
    elif current is not None and current != before and current != after:
        fail(f"workload state changed; refusing to restore: {target}")
    if os.path.lexists(tombstone):
        if os.path.lexists(target):
            fail(f"restore target and tombstone both exist: {target}")
        durable_remove(target, tombstone)
    source = transaction / "before" / snapshot / safe_relative(operation["path"])
    if signature(source) != before:
        fail(f"workload snapshot changed; refusing to restore: {source}")
    mkdir_durable(target.parent, mode=0o700)
    temporary = target.with_name(tombstone.name + ".tmp")
    if desired_kind == "symlink":
        if os.path.lexists(temporary):
            temporary.unlink()
            fsync_directory(temporary.parent)
        temporary.symlink_to(before["target"])
        os.replace(temporary, target)
        fsync_directory(target.parent)
    elif desired_kind == "file":
        durable_write_bytes(target, source.read_bytes(), before["mode"], temporary)
    else:
        fail(f"unsupported restore entry kind: {desired_kind!r}")


def execute_restore_plan(
    root: Path,
    transaction: Path,
    state: dict,
    shared_lock_fd: int,
    shared_lock_path: Path,
) -> None:
    operations = state.get("restoreOps")
    index = state.get("restoreNext")
    if not isinstance(operations, list) or not isinstance(index, int) or not 0 <= index <= len(operations):
        fail("restoring transaction lacks a valid operation cursor")
    while index < len(operations):
        operation = operations[index]
        if not isinstance(operation, dict):
            fail("restoring transaction contains an invalid operation")
        if operation.get("kind") != "restore-json-key":
            validate_shared_metadata_lock(shared_lock_fd, shared_lock_path)
        execute_restore_op(root, transaction, state, operation, index)
        index += 1
        state["restoreNext"] = index
        write_state(transaction, state)


def restore(
    root: Path,
    transaction: Path,
    *,
    verify_committed: bool,
    retain: bool = False,
) -> None:
    state = read_state(transaction, root)
    status = state.get("status")
    if status == "restored":
        if not retain:
            finalize(root, transaction)
        print("workload_transaction=restored")
        return
    if verify_committed and status == "committed":
        verify(root, transaction)
    elif verify_committed and status not in {"uninstalling", "restoring"}:
        fail(f"cannot uninstall workload transaction in state {status!r}")

    lock_fd, lock_path, lock_created = acquire_shared_metadata_lock(root, state)
    try:
        if status == "restoring":
            previous_status = state.get("restorePreviousStatus")
            if previous_status not in {"prepared", "committed", "uninstalling"}:
                fail("restoring transaction lacks its previous state")
            shared_consumers = live_shared_consumers(root, state)
            if shared_consumers and not state.get("preservedSharedResources"):
                index = state.get("restoreNext")
                operations = state.get("restoreOps")
                if not isinstance(index, int) or not isinstance(operations, list):
                    fail("restoring transaction lacks a valid operation cursor")
                state["restoreOps"] = operations[:index] + [
                    operation
                    for operation in operations[index:]
                    if isinstance(operation, dict)
                    and (
                        operation.get("kind") in {"restore-json-key", "restore-predecessor-unit"}
                        or (
                            operation.get("kind") == "restore-entry"
                            and operation.get("before") is not None
                        )
                    )
                ]
                state["preservedSharedWorkloadSetConsumers"] = shared_consumers
                state["preservedSharedResources"] = True
                write_state(transaction, state)
        else:
            previous_status = status
            units = state.get("createdUnits")
            preserved_units: list[str] = []
            if units is None:
                before = state.get("beforeUnits", {})
                units = {path: value for path, value in current_units(root).items() if path not in before}
                preserved_units = sorted(
                    path
                    for path in units
                    if not unit_is_expected(path, state.get("expectedPackages", []))
                )
                units = {
                    path: value for path, value in units.items() if path not in preserved_units
                }
            if "predecessorUnitChanges" not in state:
                after_units = current_units(root)
                state["predecessorUnitChanges"] = {
                    path: {"before": expected, "after": after_units.get(path)}
                    for path, expected in state.get("predecessorUnits", {}).items()
                    if after_units.get(path) != expected
                }
            unexpected_units = sorted(
                path
                for path in units
                if not unit_is_expected(path, state.get("expectedPackages", []))
            )
            if unexpected_units:
                fail("transaction owns units outside its trusted package graph: " + ", ".join(unexpected_units))
            changes = state.get("snapshotChanges")
            patches = state.get("sharedJsonPatches", [])
            shared_consumers = sorted(
                set(state.get("sharedWorkloadSetConsumers", []))
                | set(live_shared_consumers(root, state))
            )
            unexpected: list[str] = []
            if changes is None:
                changes, patches, unexpected, shared_consumers = snapshot_changes(
                    root, transaction, state
                )
            validate_snapshots(transaction, state, changes)
            protect_shared_resources = bool(shared_consumers)
            operations = build_restore_ops(
                root,
                state,
                units,
                changes,
                patches,
                remove_units=True,
                protect_shared_resources=protect_shared_resources,
            )
            state["status"] = "restoring"
            state["restorePreviousStatus"] = previous_status
            state["restoreOps"] = operations
            state["restoreNext"] = 0
            if unexpected:
                state["preservedConcurrentChanges"] = unexpected
            if preserved_units:
                state["preservedConcurrentUnits"] = preserved_units
            if shared_consumers:
                state["preservedSharedWorkloadSetConsumers"] = shared_consumers
                state["preservedSharedResources"] = True
            write_state(transaction, state)

        execute_restore_plan(root, transaction, state, lock_fd, lock_path)
        cleanup_shared_metadata_placeholder(lock_fd, lock_path, lock_created)
        state["status"] = "restored"
        write_state(transaction, state)
    finally:
        os.close(lock_fd)
    if not retain:
        finalize(root, transaction)
    print("workload_transaction=restored")


def finalize(root: Path, transaction: Path) -> None:
    tombstone = finalized_tombstone(transaction)
    if not os.path.lexists(transaction):
        if os.path.lexists(tombstone):
            durable_remove(transaction, tombstone)
        return
    state = read_state(transaction, root)
    if state.get("status") != "restored":
        fail("only a restored workload transaction can be finalized")
    durable_remove(transaction, tombstone)


def finalized_tombstone(transaction: Path) -> Path:
    return transaction.with_name(f".{transaction.name}.dotnet-ios-linux-finalized")


def cleanup_finalized_transaction(transaction: Path) -> None:
    tombstone = finalized_tombstone(transaction)
    if os.path.lexists(tombstone):
        if os.path.lexists(transaction):
            fail("both workload transaction and finalization tombstone exist")
        durable_remove(transaction, tombstone)


def retained_units(root: Path, transaction: Path) -> dict[str, str]:
    state = read_state(transaction, root)
    if state.get("status") != "restored":
        fail("retained units can be inspected only after restoration")
    retained: dict[str, str] = {}
    conflicts: list[str] = []
    for raw, expected in state.get("createdUnits", {}).items():
        path = root / safe_relative(raw, UNIT_ROOTS)
        actual = tree_hash(path)
        if actual is None:
            continue
        if actual != expected:
            conflicts.append(raw)
        else:
            retained[raw] = expected
    if conflicts:
        fail("retained workload packs changed; preserving recovery state: " + ", ".join(sorted(conflicts)))
    return retained


def prepare_uninstall(root: Path, transaction: Path) -> None:
    verify(root, transaction)
    state = read_state(transaction, root)
    state["sharedWorkloadSetConsumers"] = sorted(
        set(state.get("sharedWorkloadSetConsumers", []))
        | set(live_shared_consumers(root, state))
    )
    state["status"] = "uninstalling"
    write_state(transaction, state)
    print("workload_transaction=uninstalling")
    print(f"shared_workload_set_consumers={len(state['sharedWorkloadSetConsumers'])}")


def shared_consumer_count(root: Path, transaction: Path) -> int:
    state = read_state(transaction, root)
    return len(
        set(state.get("sharedWorkloadSetConsumers", []))
        | set(state.get("preservedSharedWorkloadSetConsumers", []))
        | set(live_shared_consumers(root, state))
    )


def load_expected_packages(path: Path, workload_ids: set[str] | None = None) -> list[dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"cannot read expected package inventory: {error}")
    components = value.get("components")
    if not isinstance(components, dict):
        fail("package inventory lacks components")
    # Preserve the small standalone transaction fixtures used by contract tests.
    if workload_ids is None:
        try:
            return validated_expected_packages(components["linuxIosWorkload"]["packages"])
        except (KeyError, TypeError) as error:
            fail(f"cannot read expected package inventory: {error}")
    records: list[dict] = []
    if "ios" in workload_ids:
        try:
            records.extend(components["linuxIosWorkload"]["packages"])
        except (KeyError, TypeError) as error:
            fail(f"package inventory lacks the iOS graph: {error}")
    companions = components.get("appleCompanionWorkloads", {}).get("workloads", [])
    if not isinstance(companions, list):
        fail("package inventory has invalid companion workload records")
    found = {"ios"} if "ios" in workload_ids else set()
    for item in companions:
        if not isinstance(item, dict) or not isinstance(item.get("workloadId"), str):
            fail("package inventory contains an invalid companion workload")
        workload_id = item["workloadId"]
        if workload_id not in workload_ids:
            continue
        manifest = item.get("manifest")
        packages = item.get("packages")
        if not isinstance(manifest, dict) or not isinstance(packages, list):
            fail(f"package inventory has an incomplete {workload_id} graph")
        records.extend(packages)
        records.append({"id": manifest.get("id"), "version": manifest.get("version")})
        found.add(workload_id)
    missing = sorted(workload_ids - found)
    if missing:
        fail("package inventory lacks selected workload graph(s): " + ", ".join(missing))
    return validated_expected_packages(records)


def parse_workload_descriptor(raw: str) -> dict[str, str]:
    workload_id, separator, remainder = raw.partition(":")
    manifest_id, separator2, identity = remainder.partition(":")
    if separator != ":" or separator2 != ":":
        fail("--workload must be WORKLOAD_ID:MANIFEST_ID:VERSION/FEATURE-BAND")
    version, separator3, feature = identity.partition("/")
    if separator3 != "/":
        fail("--workload manifest identity must be VERSION/FEATURE-BAND")
    return {
        "workloadId": workload_id,
        "manifestId": manifest_id,
        "manifestVersion": version,
        "manifestFeatureBand": feature,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "begin",
            "commit",
            "verify",
            "prepare-uninstall",
            "restore",
            "restore-retain",
            "finalize",
            "shared-consumers",
            "status",
        ),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--transaction", type=Path, required=True)
    parser.add_argument("--package-inventory", type=Path)
    parser.add_argument("--sdk-feature-band")
    parser.add_argument("--workload-version")
    parser.add_argument("--workload-manifest")  # Legacy single-workload form.
    parser.add_argument("--workload-id")  # Legacy single-workload form.
    parser.add_argument("--workload", action="append", default=[])
    parser.add_argument("--protect-workload", action="append", default=[])
    parser.add_argument("--control-global-json")
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    transaction = args.transaction.expanduser().absolute()
    if args.command == "begin":
        required = {
            "--sdk-feature-band": args.sdk_feature_band,
            "--workload-version": args.workload_version,
            "--control-global-json": args.control_global_json,
        }
        if not args.workload:
            required["--workload-manifest"] = args.workload_manifest
            required["--workload-id"] = args.workload_id
        missing = [name for name, value in required.items() if value is None]
        if missing:
            fail("begin requires " + ", ".join(missing))
        raw_workloads = [parse_workload_descriptor(raw) for raw in args.workload]
        protected_workloads = [parse_workload_descriptor(raw) for raw in args.protect_workload]
        if not raw_workloads:
            assert args.workload_id is not None and args.workload_manifest is not None
            try:
                version, feature = args.workload_manifest.split("/", 1)
            except ValueError:
                fail("workload manifest identity must be VERSION/FEATURE-BAND")
            provisional = (
                load_expected_packages(args.package_inventory.expanduser().absolute())
                if args.package_inventory is not None
                else []
            )
            raw_workloads = [
                {
                    "workloadId": args.workload_id,
                    "manifestId": manifest_package_id(provisional, feature),
                    "manifestVersion": version,
                    "manifestFeatureBand": feature,
                }
            ]
        workload_ids = {item["workloadId"] for item in raw_workloads}
        expected = (
            load_expected_packages(args.package_inventory.expanduser().absolute(), workload_ids)
            if args.package_inventory is not None and args.workload
            else (
                load_expected_packages(args.package_inventory.expanduser().absolute())
                if args.package_inventory is not None
                else None
            )
        )
        begin(
            root,
            transaction,
            expected,
            sdk_feature_band=args.sdk_feature_band,
            workload_version=args.workload_version,
            workloads=raw_workloads,
            protected_workloads=protected_workloads,
            control_global_json=str(Path(args.control_global_json).expanduser().absolute()),
        )
    elif args.command == "commit":
        commit(root, transaction)
    elif args.command == "verify":
        verify(root, transaction)
    elif args.command == "prepare-uninstall":
        prepare_uninstall(root, transaction)
    elif args.command == "status":
        print(read_state(transaction, root).get("status", ""))
    elif args.command == "shared-consumers":
        print(shared_consumer_count(root, transaction))
    elif args.command == "finalize":
        finalize(root, transaction)
    else:
        state = read_state(transaction, root)
        restore(
            root,
            transaction,
            verify_committed=state.get("status") in {"committed", "uninstalling"},
            retain=args.command == "restore-retain",
        )


if __name__ == "__main__":
    main()
