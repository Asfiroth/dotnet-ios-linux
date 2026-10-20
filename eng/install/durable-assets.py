#!/usr/bin/env python3
"""Record, verify, and remove only project-owned durable installation assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath


OWNER = "dotnet-ios-linux"
EXCLUDED_PREFIXES = {".dotnet-ios-linux.json", "receipts"}


def fail(message: str) -> "None":
    raise SystemExit(f"durable asset lifecycle failed: {message}")


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
    fail(f"unsupported durable asset type: {path}")


def safe_relative(raw: str) -> Path:
    value = PurePosixPath(raw)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        fail(f"unsafe durable asset path in receipt: {raw!r}")
    if value.parts[0] in EXCLUDED_PREFIXES:
        fail(f"excluded lifecycle path appears in durable inventory: {raw!r}")
    return Path(*value.parts)


def load_receipt(receipt: Path, root: Path) -> dict:
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read installation receipt {receipt}: {error}")
    if value.get("owner") != OWNER or value.get("dataRoot") != str(root):
        fail(f"installation receipt has different provenance: {receipt}")
    return value


def write_receipt(receipt: Path, value: dict) -> None:
    temporary = receipt.with_name(receipt.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, receipt)


def inventory(root: Path) -> list[dict]:
    if root.is_symlink() or not root.is_dir():
        fail(f"durable data root is not a real directory: {root}")
    entries: list[dict] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in EXCLUDED_PREFIXES:
            continue
        value = signature(path)
        if value is None:
            continue
        entries.append({"path": relative.as_posix(), **value})
    return entries


def record(root: Path, receipt: Path) -> None:
    value = load_receipt(receipt, root)
    if "durableAssets" in value:
        fail("installation receipt already contains a durable asset inventory")
    entries = inventory(root)
    value["durableAssets"] = {
        "excludedPrefixes": sorted(EXCLUDED_PREFIXES),
        "entries": entries,
    }
    write_receipt(receipt, value)
    print(f"durable_assets={len(entries)}")


def expected_signature(item: dict) -> dict:
    result = {"kind": item.get("kind"), "mode": item.get("mode")}
    if result["kind"] == "file":
        result["sha256"] = item.get("sha256")
    elif result["kind"] == "symlink":
        result["target"] = item.get("target")
    elif result["kind"] != "directory":
        fail(f"invalid durable asset kind: {result['kind']!r}")
    return result


def verified_entries(root: Path, receipt: Path) -> list[tuple[Path, dict]]:
    value = load_receipt(receipt, root)
    assets = value.get("durableAssets")
    if not isinstance(assets, dict) or assets.get("excludedPrefixes") != sorted(EXCLUDED_PREFIXES):
        fail("installation receipt lacks the expected durable asset inventory")
    raw_entries = assets.get("entries")
    if not isinstance(raw_entries, list):
        fail("durable asset inventory is not a list")
    entries: list[tuple[Path, dict]] = []
    conflicts: list[str] = []
    seen: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            fail("durable asset inventory contains an invalid entry")
        raw = item["path"]
        if raw in seen:
            fail(f"durable asset inventory contains a duplicate path: {raw}")
        seen.add(raw)
        path = root / safe_relative(raw)
        expected = expected_signature(item)
        if signature(path) != expected:
            conflicts.append(raw)
        entries.append((path, expected))
    if conflicts:
        preview = ", ".join(conflicts[:20])
        suffix = "" if len(conflicts) <= 20 else f" (and {len(conflicts) - 20} more)"
        fail(f"owned durable assets changed; refusing lifecycle mutation: {preview}{suffix}")
    return entries


def verify(root: Path, receipt: Path) -> None:
    entries = verified_entries(root, receipt)
    print(f"durable_assets_verified={len(entries)}")


def uninstall(root: Path, receipt: Path) -> None:
    entries = verified_entries(root, receipt)
    for path, expected in sorted(entries, key=lambda item: (len(item[0].parts), str(item[0])), reverse=True):
        if expected["kind"] == "directory":
            try:
                path.rmdir()
            except OSError:
                # Unknown later files are not owned and keep their parents.
                pass
        else:
            path.unlink()
    remaining = inventory(root)
    print(f"durable_assets_removed={len(entries)}")
    print(f"unowned_assets_preserved={len(remaining)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("record", "verify", "uninstall"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    receipt = args.receipt.expanduser().absolute()
    if args.command == "record":
        record(root, receipt)
    elif args.command == "verify":
        verify(root, receipt)
    else:
        uninstall(root, receipt)


if __name__ == "__main__":
    main()
