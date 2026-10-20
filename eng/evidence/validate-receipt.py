#!/usr/bin/env python3
"""Validate the repository's dependency-free evidence-receipt contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REQUIRED = {
    "schemaVersion",
    "id",
    "milestone",
    "status",
    "timestampUtc",
    "repositoryRevision",
    "toolchainLockSha256",
    "expectedBehavior",
    "observedBehavior",
    "commands",
    "redaction",
}
STATUSES = {"passed", "failed", "blocked", "superseded"}


def fail(message: str) -> None:
    print(f"receipt validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


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
    missing = REQUIRED - receipt.keys()
    if missing:
        fail(f"missing required properties: {', '.join(sorted(missing))}")
    if receipt["schemaVersion"] != 1:
        fail("schemaVersion must be 1")
    if not isinstance(receipt["milestone"], str) or not re.fullmatch(r"M(?:0|[1-9]|10)", receipt["milestone"]):
        fail("milestone must be M0 through M10")
    if receipt["status"] not in STATUSES:
        fail("invalid status")
    if not isinstance(receipt["toolchainLockSha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", receipt["toolchainLockSha256"]):
        fail("toolchainLockSha256 must be a lowercase SHA-256")
    if not isinstance(receipt["commands"], list) or not receipt["commands"]:
        fail("commands must be a non-empty array")
    for index, command in enumerate(receipt["commands"]):
        if not isinstance(command, dict) or not isinstance(command.get("command"), str):
            fail(f"commands[{index}] requires a command string")
        if not isinstance(command.get("exitCode"), int):
            fail(f"commands[{index}] requires an integer exitCode")
    redaction = receipt["redaction"]
    if not isinstance(redaction, dict) or not isinstance(redaction.get("performed"), bool):
        fail("redaction.performed must be a boolean")
    if not isinstance(redaction.get("excluded"), list):
        fail("redaction.excluded must be an array")

    print(f"valid receipt: {path}")


if __name__ == "__main__":
    main()
