# Evidence Receipt Index

Published receipts are the auditable source for an experimental result. A
compatibility-matrix row may reference a lock entry or a historical summary, but
neither replaces a published receipt.

## Published records

| Receipt ID | Scope | Status | Lock SHA-256 | Record |
| --- | --- | --- | --- | --- |
| _None yet_ | Sanitized milestone receipt publication is pending. | — | — | — |

[`example-receipt.json`](example-receipt.json) is intentionally excluded from
this table because it is a format example, not an observed result.

## Historical baseline

The active lock supersedes historical lock SHA-256
`e647355a38ec868caca79f764ff38f13e944df53f47026689b4c0bec06c4ee4f`.
The historical lock itself is not committed: it referred to private evidence
locations and has not been independently republished in sanitized form. Its hash
is retained for continuity only; it is not a reviewable evidence record.

## Publication procedure

1. Start from the [receipt protocol](../receipt-schema.md). The Python validator
   is the repository's enforced implementation of `receipt.schema.json`; run
   `validate-receipt.py` before publication.
2. Remove sensitive material and replace any local artifact location with a
   neutral label plus its content hash where publication is appropriate.
3. Add the record to this directory and one row to the table above.
4. Link the corresponding current-status claim to the receipt ID. Do not change
   a claim merely because a record was published; the record must demonstrate
   the stated boundary.
