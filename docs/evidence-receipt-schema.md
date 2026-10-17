# Evidence Receipt Protocol

Receipts are immutable, sanitized records of an observed experiment. They are
stored at `receipts/<milestone>/<receipt-id>.json` and validated against
`receipts/receipt.schema.json`.

## Required properties

- Identity: receipt ID, milestone, UTC timestamp, and status.
- Reproducibility: repository revision, toolchain-lock hash, host facts,
  commands, exit codes, and input/output hashes.
- Meaning: expected and observed behavior, limitations, and next decision.
- Safety: redaction record and only sanitized local-log references.

Statuses are `passed`, `failed`, `blocked`, or `superseded`. A failed attempt
remains a failed receipt; a later success receives a new receipt.

Raw binlogs, Apple SDK files, credentials, provisioning profiles, device
identifiers, pairing records, and private logs are local artifacts and must not
be committed. The receipt records a sanitized reference and, where possible,
the local artifact hash.
