#!/usr/bin/env python3
"""Contracts for independent installer and toolchain provenance."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
LOCK = REPOSITORY / "toolchain.lock.json"
FORMER_PROJECT = "omar" + "chy-apple-dev"
FORMER_LOCK_KEY = "omar" + "chyAppleDev"
FORMER_URL = f"github.com/joshuaswarren/{FORMER_PROJECT}"
EXPECTED_HOST_TOOLS = {
    "swift",
    "binutils",
    "xtool",
    "rcodesign",
    "portablePython",
    "pymobiledevice3",
}


def provenance_surfaces() -> list[Path]:
    paths = [REPOSITORY / "install.sh", LOCK, REPOSITORY / "README.md"]
    for root in (REPOSITORY / "eng" / "install", REPOSITORY / "eng" / "toolchain", REPOSITORY / "docs"):
        paths.extend(path for path in root.rglob("*") if path.is_file())
    return paths


class ProvenanceBoundaryContracts(unittest.TestCase):
    def test_inspiration_project_is_absent_from_operational_provenance(self) -> None:
        forbidden = (FORMER_PROJECT.casefold(), FORMER_LOCK_KEY.casefold(), FORMER_URL.casefold())
        matches: list[str] = []
        for path in provenance_surfaces():
            try:
                content = path.read_text(encoding="utf-8").casefold()
            except UnicodeDecodeError:
                continue
            if any(value in content for value in forbidden):
                matches.append(str(path.relative_to(REPOSITORY)))

        self.assertEqual([], matches)

    def test_host_toolchain_contains_only_independently_pinned_tools(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        components = lock["components"]

        self.assertNotIn(FORMER_LOCK_KEY, components)
        self.assertEqual(EXPECTED_HOST_TOOLS, set(components["hostToolchain"]))

    def test_inspiration_acquisition_helper_is_absent(self) -> None:
        helper = REPOSITORY / "eng" / "toolchain" / f"acquire-{FORMER_PROJECT}-backend.sh"

        self.assertFalse(helper.exists())


if __name__ == "__main__":
    unittest.main()
