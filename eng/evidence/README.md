# Evidence and validation tools

- `capture-linux-inventory.sh` records a private Linux host inventory.
- `capture-mac-reference.sh` records a private reference-Mac report and optional
  build binlog.
- `validate-receipt.py` enforces the dependency-free receipt contract mirrored
  by [`docs/evidence/receipt.schema.json`](../../docs/evidence/receipt.schema.json).
- `validate-repository.sh` runs the canonical SDK-free repository checks.

Capture outputs may contain paths, signing metadata, or device-adjacent
information. Always write them to an operator-controlled directory outside the
repository, retain mode-600/700 permissions, and commit only reviewed sanitized
receipts. The publication process and the current index are documented in
[`docs/evidence/receipts/`](../../docs/evidence/receipts/).
