# Published Receipts

This directory contains reviewed, sanitized receipt records and one non-evidence
format example. Every JSON record must validate with:

```sh
python3 eng/evidence/validate-receipt.py <receipt.json>
```

The receipt must then be added to [`index.md`](index.md). Do not publish a
receipt that contains an Apple SDK path, provisioning data, certificate or key
material, profile selector, UDID, pairing record, raw binlog, or private log.

`example-receipt.json` demonstrates the required shape only. It is explicitly
marked as an example and is not evidence for any project claim.
