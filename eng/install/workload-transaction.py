#!/usr/bin/env python3
"""Snapshot and restore only .NET workload paths changed by this project."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
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
SCHEMA_VERSION = 2
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
    return value


def safe_relative(raw: str, allowed_roots: set[str] | None = None) -> Path:
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"unsafe receipt path: {raw!r}")
    if allowed_roots is not None and value.parts[0] not in allowed_roots:
        fail(f"receipt path is outside the workload unit roots: {raw!r}")
    return Path(*value.parts)


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
    manifest_band = validate_component(context.get("manifestFeatureBand"), "manifest feature band")
    workload_set_band = validate_component(
        context.get("workloadSetFeatureBand"), "workload-set feature band"
    )
    result = {
        "workloadMetadata": Path("metadata/workloads"),
        "manifestBand": Path("sdk-manifests") / manifest_band,
        "workloadSetBand": Path("sdk-manifests") / workload_set_band,
    }
    if manifest_band == workload_set_band:
        result.pop("workloadSetBand")
    if sdk_band != workload_set_band:
        fail("selected SDK and workload set use different feature bands")
    return result


def snapshot_items(state: dict):
    for label, relative in expected_snapshot_paths(state).items():
        yield label, relative, state["snapshots"][label]


def pack_units(root: Path) -> dict[str, str]:
    packs = root / "packs"
    result: dict[str, str] = {}
    if not packs.is_dir() or packs.is_symlink():
        return result
    for package in packs.iterdir():
        if package.is_symlink() or not package.is_dir():
            continue
        for version in package.iterdir():
            if version.is_symlink() or not version.is_dir():
                continue
            relative = version.relative_to(root).as_posix()
            result[relative] = tree_hash(version) or ""
    return result


def loose_units(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for directory_name in ("template-packs", "library-packs"):
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            continue
        for child in directory.iterdir():
            relative = child.relative_to(root).as_posix()
            result[relative] = tree_hash(child) or ""
    return result


def current_units(root: Path) -> dict[str, str]:
    return pack_units(root) | loose_units(root)


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


def manifest_package_id(
    expected_packages: list[dict[str, str]], manifest_feature_band: str
) -> str:
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


def make_ownership_plan(context: dict, expected_packages: list[dict[str, str]]) -> dict:
    sdk_band = context["sdkFeatureBand"]
    workload_version = context["workloadVersion"]
    workload_set_band = context["workloadSetFeatureBand"]
    manifest_version = context["manifestVersion"]
    manifest_band = context["manifestFeatureBand"]
    manifest_id = context["manifestId"]
    workload_id = context["workloadId"]
    metadata_leaves = [
        f"{sdk_band}/InstalledWorkloads/{workload_id}",
        f"InstalledWorkloadSets/v1/{workload_version}/{workload_set_band}/{sdk_band}",
        f"InstalledManifests/v1/{manifest_id}/{manifest_version}/{manifest_band}/{sdk_band}",
    ]
    for item in expected_packages:
        package_id = item["id"]
        if ".manifest-" in package_id.casefold() or package_id.casefold().startswith(
            "microsoft.net.workloads."
        ):
            continue
        metadata_leaves.append(
            f"InstalledPacks/v1/{package_id}/{item['version']}/{sdk_band}"
        )
    tree_prefixes = {"manifestBand": [f"{manifest_id}/{manifest_version}"]}
    workload_set_label = (
        "manifestBand" if manifest_band == workload_set_band else "workloadSetBand"
    )
    tree_prefixes.setdefault(workload_set_label, []).append(
        f"workloadsets/{workload_version}"
    )
    return {
        "treePrefixes": tree_prefixes,
        "leafPaths": {"workloadMetadata": sorted(set(metadata_leaves))},
        "sharedJson": {
            "snapshot": "workloadMetadata",
            "path": f"X64/{sdk_band}/globaljsonworkloadsets.json",
            "key": context["controlGlobalJson"],
            "value": workload_version,
        },
    }


def begin(
    root: Path,
    transaction: Path,
    expected_packages: list[dict[str, str]] | None = None,
    *,
    sdk_feature_band: str,
    workload_version: str,
    workload_manifest: str,
    workload_id: str,
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
    try:
        manifest_version, manifest_band = workload_manifest.split("/", 1)
    except ValueError:
        fail("workload manifest identity must be VERSION/FEATURE-BAND")
    context = {
        "sdkFeatureBand": validate_component(sdk_feature_band, "SDK feature band"),
        "workloadVersion": validate_component(workload_version, "workload version"),
        "workloadSetFeatureBand": feature_band(workload_version),
        "manifestVersion": validate_component(manifest_version, "manifest version"),
        "manifestFeatureBand": validate_component(manifest_band, "manifest feature band"),
        "manifestId": manifest_package_id(expected, manifest_band),
        "workloadId": validate_component(workload_id, "workload ID"),
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
            source = root / relative
            snapshot_state[label] = {"path": relative.as_posix(), "existed": source.exists()}
            if source.exists():
                if source.is_symlink() or not source.is_dir():
                    fail(f"workload state path is not a real directory: {source}")
                copy_snapshot(source, before / label)
        state = {
            "schemaVersion": SCHEMA_VERSION,
            "owner": OWNER,
            "status": "prepared",
            "dotnetRoot": str(root),
            "snapshots": snapshot_state,
            "beforeUnits": current_units(root),
            "expectedPackages": expected,
            "context": context,
            "ownershipPlan": plan,
        }
        fsync_tree(before)
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
    path = root / safe_relative(snapshot["path"]) / safe_relative(shared["path"])
    return sorted(
        candidate
        for candidate, version in json_mapping(path).items()
        if candidate != shared["key"] and version == shared["value"]
    )


def acquire_shared_metadata_lock(root: Path, state: dict) -> tuple[int, Path, bool]:
    shared = state["ownershipPlan"]["sharedJson"]
    snapshot = state["snapshots"][shared["snapshot"]]
    path = root / safe_relative(snapshot["path"]) / safe_relative(shared["path"])
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
        after = inventory(root / relative, ignore_history=True)
        changed: dict[str, dict] = {}
        for path in sorted(before.keys() | after.keys()):
            if before.get(path) == after.get(path):
                continue
            if label == shared["snapshot"] and path.casefold() == shared["path"].casefold():
                continue
            if path_is_owned(state, label, path):
                changed[path] = {"before": before.get(path), "after": after.get(path)}
            else:
                unexpected.append(f"{relative.as_posix()}/{path}")
        changes[label] = {"rootCreated": not details["existed"] and (root / relative).is_dir(), "entries": changed}
    shared_label = shared["snapshot"]
    shared_relative = Path(state["snapshots"][shared_label]["path"]) / safe_relative(shared["path"])
    before_shared = transaction / "before" / shared_label / safe_relative(shared["path"])
    before_map = json_mapping(before_shared)
    after_map = json_mapping(root / shared_relative)
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
        path = root / relative
        if path.is_dir() and not path.is_symlink():
            fsync_tree(path)
    for raw in created_units:
        path = root / safe_relative(raw, UNIT_ROOTS)
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
        if changed:
            fail("workload CLI modified pre-existing pack payloads: " + ", ".join(changed))
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
        changes = state.get("snapshotChanges", {}).get(label, {}).get("entries", {})
        for raw, change in changes.items():
            path = root / relative / safe_relative(raw)
            if signature(path) != change.get("after"):
                fail(f"installed workload state changed since setup: {path}")
    for raw, expected in state.get("createdUnits", {}).items():
        path = root / safe_relative(raw, UNIT_ROOTS)
        if tree_hash(path) != expected:
            fail(f"installed workload pack changed since setup: {path}")
    for patch in state.get("sharedJsonPatches", []):
        snapshot = state["snapshots"].get(patch.get("snapshot"), {})
        relative = snapshot.get("path")
        if not isinstance(relative, str):
            fail("workload transaction contains an invalid shared metadata patch")
        mapping = json_mapping(root / safe_relative(relative) / safe_relative(patch["path"]))
        if mapping.get(patch["key"]) != patch.get("after"):
            fail("installed workload shared metadata changed since setup")
    print("workload_transaction=ok")


def validate_snapshots(transaction: Path, state: dict, changes: dict[str, dict]) -> None:
    conflicts: list[str] = []
    for label, _relative, _details in snapshot_items(state):
        for raw, change in changes[label]["entries"].items():
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
            if protect_shared_resources:
                continue
            before = change.get("before")
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


def execute_restore_op(
    root: Path, transaction: Path, state: dict, operation: dict, index: int
) -> None:
    kind = operation.get("kind")
    if kind == "remove-unit":
        target = root / safe_relative(operation["path"], UNIT_ROOTS)
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
        target = root / safe_relative(operation["path"])
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
    target = root / safe_relative(details["path"]) / safe_relative(operation["path"])
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
                    and operation.get("kind") == "restore-json-key"
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


def load_expected_packages(path: Path) -> list[dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        records = value["components"]["linuxIosWorkload"]["packages"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"cannot read expected package inventory: {error}")
    return validated_expected_packages(records)


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
    parser.add_argument("--workload-manifest")
    parser.add_argument("--workload-id")
    parser.add_argument("--control-global-json")
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    transaction = args.transaction.expanduser().absolute()
    if args.command == "begin":
        required = {
            "--sdk-feature-band": args.sdk_feature_band,
            "--workload-version": args.workload_version,
            "--workload-manifest": args.workload_manifest,
            "--workload-id": args.workload_id,
            "--control-global-json": args.control_global_json,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            fail("begin requires " + ", ".join(missing))
        expected = (
            load_expected_packages(args.package_inventory.expanduser().absolute())
            if args.package_inventory is not None
            else None
        )
        begin(
            root,
            transaction,
            expected,
            sdk_feature_band=args.sdk_feature_band,
            workload_version=args.workload_version,
            workload_manifest=args.workload_manifest,
            workload_id=args.workload_id,
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
