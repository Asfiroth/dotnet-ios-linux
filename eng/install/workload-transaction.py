#!/usr/bin/env python3
"""Snapshot and restore only .NET workload paths changed by this project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from atomic import publish_directory_noreplace


OWNER = "dotnet-ios-linux"
STATE_NAME = "transaction.json"
SNAPSHOTS = {
    "sdkManifests": Path("sdk-manifests/10.0.100"),
    "workloadMetadata": Path("metadata/workloads"),
}
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
    transaction.mkdir(parents=True, exist_ok=True)
    path = transaction / STATE_NAME
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_state(transaction: Path, root: Path) -> dict:
    try:
        value = json.loads((transaction / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read transaction {transaction}: {error}")
    if value.get("owner") != OWNER:
        fail(f"transaction is not owned by {OWNER}: {transaction}")
    if value.get("dotnetRoot") != str(root):
        fail(f"transaction belongs to a different .NET root: {transaction}")
    snapshots = value.get("snapshots")
    if not isinstance(snapshots, dict):
        fail("transaction lacks workload snapshots")
    for label, relative in SNAPSHOTS.items():
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


def begin(root: Path, transaction: Path) -> None:
    if transaction.exists() or transaction.is_symlink():
        if transaction.is_symlink():
            fail(f"workload transaction must not be a symlink: {transaction}")
        state = read_state(transaction, root)
        if state.get("status") in {"prepared", "uninstalling"}:
            restore(root, transaction, verify_committed=False)
        else:
            fail(f"a committed workload transaction already exists: {transaction}")
    transaction.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{transaction.name}.prepare-", dir=transaction.parent))
    try:
        before = stage / "before"
        before.mkdir()
        snapshot_state: dict[str, dict] = {}
        for label, relative in SNAPSHOTS.items():
            source = root / relative
            snapshot_state[label] = {"path": relative.as_posix(), "existed": source.exists()}
            if source.exists():
                if source.is_symlink() or not source.is_dir():
                    fail(f"workload state path is not a real directory: {source}")
                copy_snapshot(source, before / label)
        state = {
            "schemaVersion": 1,
            "owner": OWNER,
            "status": "prepared",
            "dotnetRoot": str(root),
            "snapshots": snapshot_state,
            "beforeUnits": current_units(root),
        }
        write_state(stage, state)
        try:
            publish_directory_noreplace(stage, transaction)
        except FileExistsError:
            fail(f"workload transaction appeared during publication: {transaction}")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"workload_transaction={transaction}")


def snapshot_changes(root: Path, transaction: Path, state: dict) -> dict[str, dict]:
    changes: dict[str, dict] = {}
    for label, relative in SNAPSHOTS.items():
        details = state["snapshots"][label]
        before = inventory(transaction / "before" / label) if details["existed"] else {}
        after = inventory(root / relative, ignore_history=True)
        changed = {
            path: {"before": before.get(path), "after": after.get(path)}
            for path in sorted(before.keys() | after.keys())
            if before.get(path) != after.get(path)
        }
        changes[label] = {"rootCreated": not details["existed"] and (root / relative).is_dir(), "entries": changed}
    return changes


def commit(root: Path, transaction: Path) -> None:
    state = read_state(transaction, root)
    if state.get("status") != "prepared":
        fail("only a prepared transaction can be committed")
    after_units = current_units(root)
    before_units = state["beforeUnits"]
    changed = sorted(path for path in before_units if after_units.get(path) != before_units[path])
    if changed:
        fail("workload CLI modified pre-existing pack payloads: " + ", ".join(changed))
    state["createdUnits"] = {
        path: value for path, value in after_units.items() if path not in before_units
    }
    state["snapshotChanges"] = snapshot_changes(root, transaction, state)
    state["status"] = "committed"
    write_state(transaction, state)
    print("workload_transaction=committed")


def verify(root: Path, transaction: Path) -> None:
    state = read_state(transaction, root)
    if state.get("status") != "committed":
        fail("workload transaction is not committed")
    for label, relative in SNAPSHOTS.items():
        changes = state.get("snapshotChanges", {}).get(label, {}).get("entries", {})
        for raw, change in changes.items():
            path = root / relative / safe_relative(raw)
            if signature(path) != change.get("after"):
                fail(f"installed workload state changed since setup: {path}")
    for raw, expected in state.get("createdUnits", {}).items():
        path = root / safe_relative(raw, UNIT_ROOTS)
        if tree_hash(path) != expected:
            fail(f"installed workload pack changed since setup: {path}")
    print("workload_transaction=ok")


def remove_units(root: Path, units: dict[str, str], *, require_hash: bool) -> None:
    conflicts: list[str] = []
    paths: list[Path] = []
    for raw, expected in units.items():
        path = root / safe_relative(raw, UNIT_ROOTS)
        if not os.path.lexists(path):
            continue
        if require_hash and tree_hash(path) != expected:
            conflicts.append(raw)
        paths.append(path)
    if conflicts:
        fail("owned workload packs changed; refusing to remove: " + ", ".join(sorted(conflicts)))
    for path in sorted(paths, key=lambda item: (len(item.parts), str(item)), reverse=True):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        for parent in (path.parent, path.parent.parent):
            if parent == root:
                break
            try:
                parent.rmdir()
            except OSError:
                break


def validate_changes(root: Path, state: dict, changes: dict[str, dict]) -> None:
    if state.get("status") == "uninstalling":
        return
    conflicts: list[str] = []
    for label, relative in SNAPSHOTS.items():
        for raw, change in changes[label]["entries"].items():
            if signature(root / relative / safe_relative(raw)) != change.get("after"):
                conflicts.append(f"{relative.as_posix()}/{raw}")
    if conflicts:
        fail("workload state changed; refusing to restore: " + ", ".join(conflicts))


def validate_snapshots(transaction: Path, changes: dict[str, dict]) -> None:
    conflicts: list[str] = []
    for label in SNAPSHOTS:
        for raw, change in changes[label]["entries"].items():
            before = change.get("before")
            if before is None or before.get("kind") == "directory":
                continue
            source = transaction / "before" / label / safe_relative(raw)
            if signature(source) != before:
                conflicts.append(f"{label}/{raw}")
    if conflicts:
        fail("workload snapshots changed; refusing to restore: " + ", ".join(conflicts))


def restore_changes(root: Path, transaction: Path, state: dict, changes: dict[str, dict]) -> None:
    validate_changes(root, state, changes)
    validate_snapshots(transaction, changes)
    for label, relative in SNAPSHOTS.items():
        destination = root / relative
        entries = changes[label]["entries"]

        # Remove changed non-directories first, then directories that need to
        # become files/symlinks or disappear. rmdir preserves later unrelated
        # children instead of recursively deleting them.
        for raw, change in sorted(entries.items(), key=lambda item: (item[0].count("/"), item[0]), reverse=True):
            path = destination / safe_relative(raw)
            current = signature(path)
            before = change.get("before")
            if current is None or current == before:
                continue
            if current["kind"] == "directory":
                if before is None or before.get("kind") != "directory":
                    try:
                        path.rmdir()
                    except OSError:
                        if before is not None:
                            fail(f"workload directory contains unrelated files: {path}")
                continue
            path.unlink()

        # Recreate the original directories before restoring original files.
        for raw, change in sorted(entries.items(), key=lambda item: (item[0].count("/"), item[0])):
            before = change.get("before")
            if before is None or before["kind"] != "directory":
                continue
            path = destination / safe_relative(raw)
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(before["mode"])
        for raw, change in sorted(entries.items()):
            before = change.get("before")
            if before is None or before["kind"] == "directory":
                continue
            path = destination / safe_relative(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            source = transaction / "before" / label / safe_relative(raw)
            if before["kind"] == "symlink":
                path.symlink_to(before["target"])
            else:
                shutil.copy2(source, path, follow_symlinks=False)
                path.chmod(before["mode"])

        if changes[label].get("rootCreated"):
            try:
                destination.rmdir()
            except OSError:
                pass


def restore(root: Path, transaction: Path, *, verify_committed: bool) -> None:
    state = read_state(transaction, root)
    status = state.get("status")
    if verify_committed and status == "committed":
        verify(root, transaction)
    elif verify_committed and status != "uninstalling":
        fail(f"cannot uninstall workload transaction in state {status!r}")

    units = state.get("createdUnits")
    if units is None:
        before = state.get("beforeUnits", {})
        units = {path: value for path, value in current_units(root).items() if path not in before}
    if status != "uninstalling":
        remove_units(root, units, require_hash=status == "committed")

    changes = state.get("snapshotChanges")
    if changes is None:
        changes = snapshot_changes(root, transaction, state)
    restore_changes(root, transaction, state, changes)
    shutil.rmtree(transaction)
    print("workload_transaction=restored")


def prepare_uninstall(root: Path, transaction: Path) -> None:
    verify(root, transaction)
    state = read_state(transaction, root)
    state["status"] = "uninstalling"
    write_state(transaction, state)
    print("workload_transaction=uninstalling")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("begin", "commit", "verify", "prepare-uninstall", "restore", "status"),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--transaction", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    transaction = args.transaction.expanduser().absolute()
    if args.command == "begin":
        begin(root, transaction)
    elif args.command == "commit":
        commit(root, transaction)
    elif args.command == "verify":
        verify(root, transaction)
    elif args.command == "prepare-uninstall":
        prepare_uninstall(root, transaction)
    elif args.command == "status":
        print(read_state(transaction, root).get("status", ""))
    else:
        state = read_state(transaction, root)
        restore(root, transaction, verify_committed=state.get("status") in {"committed", "uninstalling"})


if __name__ == "__main__":
    main()
