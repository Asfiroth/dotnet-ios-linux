# Evidence Receipts

This directory stores sanitized, machine-readable milestone evidence. Validate
receipts with:

```sh
python3 scripts/validate-receipt.py receipts/<milestone>/<receipt-id>.json
```

`receipts/private/` is ignored and may contain local redacted-log references
only. Never place Apple SDK assets, credentials, provisioning profiles, pairing
records, raw UDIDs, or raw private binlogs in this repository.
