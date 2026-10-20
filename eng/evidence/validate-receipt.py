#!/usr/bin/env python3
"""Validate the repository's dependency-free evidence-receipt contract."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


REQUIRED = {
    "schemaVersion",
    "id",
    "milestone",
    "status",
    "timestampUtc",
    "repositoryRevision",
    "toolchainLockSha256",
    "host",
    "expectedBehavior",
    "observedBehavior",
    "commands",
    "inputHashes",
    "outputHashes",
    "limitations",
    "nextDecision",
    "redaction",
}
STATUSES = {"passed", "failed", "blocked", "superseded"}
ALLOWED = REQUIRED | {"example", "localLogReferences"}
IDENTIFIER = re.compile(r"[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*")
MILESTONE = re.compile(r"M(?:0|[1-9]|10)")
HEX_SHA256 = re.compile(r"[a-f0-9]{64}")
REVISION = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?")
TIMESTAMP_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z")


def fail(message: str) -> None:
    print(f"receipt validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} must be a non-empty string")
    return value


def require_string_list(value: object, label: str, *, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        qualifier = " non-empty" if non_empty else ""
        fail(f"{label} must be a{qualifier} array")
    for index, item in enumerate(value):
        require_string(item, f"{label}[{index}]")
    return value


def reject_unknown_properties(value: dict[object, object], label: str, allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        fail(f"{label} contains unknown properties: {', '.join(sorted(unknown))}")


def validate_hash_map(value: object, label: str) -> None:
    if not isinstance(value, dict) or not value:
        fail(f"{label} must be a non-empty object")
    for name, digest in value.items():
        require_string(name, f"{label} key")
        if not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest):
            fail(f"{label}[{name!r}] must be a lowercase SHA-256")


def validate_host(value: object) -> None:
    if not isinstance(value, dict):
        fail("host must be an object")
    allowed = {"os", "architecture", "distribution", "kernel", "notes"}
    reject_unknown_properties(value, "host", allowed)
    for name in ("os", "architecture"):
        require_string(value.get(name), f"host.{name}")
    for name in allowed - {"os", "architecture"}:
        if name in value:
            require_string(value[name], f"host.{name}")


def validate_commands(value: object) -> None:
    if not isinstance(value, list) or not value:
        fail("commands must be a non-empty array")
    for index, command in enumerate(value):
        label = f"commands[{index}]"
        if not isinstance(command, dict):
            fail(f"{label} must be an object")
        reject_unknown_properties(command, label, {"command", "exitCode", "purpose"})
        require_string(command.get("command"), f"{label}.command")
        exit_code = command.get("exitCode")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            fail(f"{label}.exitCode must be an integer")
        if "purpose" in command:
            require_string(command["purpose"], f"{label}.purpose")


def validate_redaction(value: object) -> None:
    if not isinstance(value, dict):
        fail("redaction must be an object")
    required = {"performed", "excluded", "notes"}
    reject_unknown_properties(value, "redaction", required)
    missing = required - set(value)
    if missing:
        fail(f"redaction is missing properties: {', '.join(sorted(missing))}")
    if not isinstance(value["performed"], bool):
        fail("redaction.performed must be a boolean")
    require_string_list(value["excluded"], "redaction.excluded")
    require_string(value["notes"], "redaction.notes")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: validate-receipt.py <receipt.json>")

    path = Path(sys.argv[1])
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(str(error))

    if not isinstance(receipt, dict):
        fail("root must be an object")
    reject_unknown_properties(receipt, "receipt", ALLOWED)
    missing = REQUIRED - receipt.keys()
    if missing:
        fail(f"missing required properties: {', '.join(sorted(missing))}")
    if not isinstance(receipt["schemaVersion"], int) or isinstance(receipt["schemaVersion"], bool) or receipt["schemaVersion"] != 1:
        fail("schemaVersion must be 1")
    if not isinstance(receipt["id"], str) or not IDENTIFIER.fullmatch(receipt["id"]):
        fail("id must use uppercase letters, digits, hyphens, and underscores")
    if not isinstance(receipt["milestone"], str) or not MILESTONE.fullmatch(receipt["milestone"]):
        fail("milestone must be M0 through M10")
    if not isinstance(receipt["status"], str) or receipt["status"] not in STATUSES:
        fail("invalid status")
    timestamp = require_string(receipt["timestampUtc"], "timestampUtc")
    if not TIMESTAMP_UTC.fullmatch(timestamp):
        fail("timestampUtc must be an ISO-8601 UTC timestamp with seconds")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except ValueError:
        fail("timestampUtc must be a valid ISO-8601 UTC timestamp")
    if parsed_timestamp.tzinfo is None:
        fail("timestampUtc must include a UTC offset")
    revision = receipt["repositoryRevision"]
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        fail("repositoryRevision must be a 40- or 64-character lowercase revision")
    if not isinstance(receipt["toolchainLockSha256"], str) or not HEX_SHA256.fullmatch(receipt["toolchainLockSha256"]):
        fail("toolchainLockSha256 must be a lowercase SHA-256")
    validate_host(receipt["host"])
    validate_commands(receipt["commands"])
    validate_hash_map(receipt["inputHashes"], "inputHashes")
    validate_hash_map(receipt["outputHashes"], "outputHashes")
    require_string(receipt["expectedBehavior"], "expectedBehavior")
    require_string(receipt["observedBehavior"], "observedBehavior")
    require_string_list(receipt["limitations"], "limitations", non_empty=True)
    require_string(receipt["nextDecision"], "nextDecision")
    validate_redaction(receipt["redaction"])
    if "example" in receipt and not isinstance(receipt["example"], bool):
        fail("example must be a boolean")
    if "localLogReferences" in receipt:
        require_string_list(receipt["localLogReferences"], "localLogReferences")

    print(f"valid receipt: {path}")


if __name__ == "__main__":
    main()
