# Evidence Receipt Index

Published receipts are the auditable source for an experimental result. A
compatibility-matrix row may reference a lock entry or a historical summary, but
neither replaces a published receipt.

## Published records

| Receipt ID | Scope | Status | Lock SHA-256 | Record |
| --- | --- | --- | --- | --- |
| `XTOOL-NORMALIZER-QUALIFICATION-20261025` | Staged LLVM normalizer qualification | Failed | `ec7cf5d3dec83286663233461391592cebe461f5214baf23b8f1d87bd24f36ef` | [Receipt](XTOOL-NORMALIZER-QUALIFICATION-20261025.json) |
| `XTOOL-HERMETIC-QUALIFICATION-20261025` | Fixed logical-path and pinned GNU xtool build | Passed | `b18e5fbfdd9d5424a06799adea06b51150020afb060bcec1de8c714bf4fa5b6f` | [Receipt](XTOOL-HERMETIC-QUALIFICATION-20261025.json) |
| `XTOOL-PORTABLE-PATH-QUALIFICATION-20261025` | Host-neutral namespace-private xtool path | Passed | `9f0b25897d695f66c1539f31d2a6fce3ecfe750d13ae7bc561a69fdfdd582398` | [Receipt](XTOOL-PORTABLE-PATH-QUALIFICATION-20261025.json) |

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
