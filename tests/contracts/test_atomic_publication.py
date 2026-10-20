#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
MODULE_PATH = REPOSITORY / "eng" / "common" / "atomic.py"
SPEC = importlib.util.spec_from_file_location("atomic_publication", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
atomic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atomic)


class AtomicPublicationContracts(unittest.TestCase):
    def test_directory_publication_is_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atomic-publication-") as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "value").write_text("new\n", encoding="utf-8")
            (destination / "value").write_text("existing\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                atomic.publish_directory_noreplace(source, destination)

            self.assertEqual("new\n", (source / "value").read_text(encoding="utf-8"))
            self.assertEqual("existing\n", (destination / "value").read_text(encoding="utf-8"))

    def test_directory_publication_moves_complete_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="atomic-publication-") as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "value").write_text("complete\n", encoding="utf-8")

            atomic.publish_directory_noreplace(source, destination)

            self.assertFalse(source.exists())
            self.assertEqual("complete\n", (destination / "value").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
