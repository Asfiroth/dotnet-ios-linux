#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
VALIDATOR = REPOSITORY / "eng" / "evidence" / "validate-receipt.py"
EXAMPLE = REPOSITORY / "docs" / "evidence" / "receipts" / "example-receipt.json"
SCHEMA = REPOSITORY / "docs" / "evidence" / "receipt.schema.json"


class ReceiptValidationContracts(unittest.TestCase):
    def validate(self, receipt: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(receipt)],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_receipt(self, directory: Path, payload: dict[str, object]) -> Path:
        path = directory / "receipt.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_sanitized_example_is_valid(self) -> None:
        result = self.validate(EXAMPLE)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("valid receipt", result.stdout)

    def test_validator_top_level_contract_matches_schema(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        validator = runpy.run_path(str(VALIDATOR))

        self.assertEqual(set(schema["required"]), validator["REQUIRED"])
        self.assertEqual(set(schema["properties"]), validator["ALLOWED"])

    def test_example_hashes_match_its_documented_inputs_and_output(self) -> None:
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        lock_hash = hashlib.sha256((REPOSITORY / "toolchain.lock.json").read_bytes()).hexdigest()
        output_hash = hashlib.sha256(b"repository_validation=passed\n").hexdigest()

        self.assertTrue(example["example"])
        self.assertEqual(lock_hash, example["toolchainLockSha256"])
        self.assertEqual(lock_hash, example["inputHashes"]["toolchain.lock.json"])
        self.assertEqual(
            output_hash,
            example["outputHashes"]["repository_validation=passed\n"],
        )

    def test_missing_reproducibility_data_fails(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        del payload["inputHashes"]

        with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-receipt-") as directory:
            result = self.validate(self.write_receipt(Path(directory), payload))

        self.assertNotEqual(0, result.returncode)
        self.assertIn("inputHashes", result.stderr)

    def test_unknown_properties_fail_closed(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        payload["privatePath"] = "/do-not-publish"

        with tempfile.TemporaryDirectory(prefix="dotnet-ios-linux-receipt-") as directory:
            result = self.validate(self.write_receipt(Path(directory), payload))

        self.assertNotEqual(0, result.returncode)
        self.assertIn("unknown properties", result.stderr)


if __name__ == "__main__":
    unittest.main()
