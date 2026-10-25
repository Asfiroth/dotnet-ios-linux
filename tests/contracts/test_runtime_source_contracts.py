#!/usr/bin/env python3

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
VERIFIER = REPOSITORY / "eng" / "runtime" / "verify-source.sh"
PATCH = REPOSITORY / "eng" / "runtime" / "patches" / "0001-linux-host-apple-mobile-aot-cross.patch"


class RuntimeSourceContracts(unittest.TestCase):
    def test_patched_source_verifier_uses_full_blob_identity(self) -> None:
        verifier = VERIFIER.read_text(encoding="utf-8")
        patch = PATCH.read_text(encoding="utf-8")

        expected = dict(
            re.findall(r"(src/[A-Za-z0-9_./-]+)\\t([0-9a-f]{40})", verifier)
        )
        patched_paths = re.findall(r"^diff --git a/(.+) b/\1$", patch, flags=re.MULTILINE)
        patched_prefixes = re.findall(
            r"^index [0-9a-f]+\.\.([0-9a-f]+) ", patch, flags=re.MULTILINE
        )

        self.assertEqual(set(patched_paths), set(expected))
        self.assertEqual(len(patched_paths), len(patched_prefixes))
        for path, prefix in zip(patched_paths, patched_prefixes, strict=True):
            self.assertTrue(expected[path].startswith(prefix))
        self.assertIn('git -C "$source_root" hash-object "$path"', verifier)
        self.assertNotIn("diff --binary --no-ext-diff", verifier)


if __name__ == "__main__":
    unittest.main()
