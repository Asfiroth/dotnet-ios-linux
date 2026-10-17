# Evidence Receipts

This directory stores sanitized, machine-readable milestone evidence. Validate
receipts with:

```sh
python3 scripts/validate-receipt.py receipts/<milestone>/<receipt-id>.json
```

`receipts/private/` is ignored and may contain local redacted-log references
only. Never place Apple SDK assets, credentials, provisioning-profile files or
contents, pairing records, raw UDIDs, or raw private binlogs in this repository.
Use `CodesignProvision` only as a redacted local profile-name or UUID selector;
never use it for a path, profile content, or credential.
