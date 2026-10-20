# Evidence Receipt Protocol

Receipts are immutable, sanitized records of an observed experiment. The
dependency-free contract below is enforced by
`eng/evidence/validate-receipt.py`. A committed JSON Schema and sanitized
receipt publication directory remain future work; raw captures stay outside the
repository.

## Required properties

- Identity: receipt ID, milestone, UTC timestamp, and status.
- Reproducibility: repository revision, toolchain-lock hash, host facts,
  commands, exit codes, and input/output hashes.
- Meaning: expected and observed behavior, limitations, and next decision.
- Safety: redaction record and only sanitized local-log references.

Statuses are `passed`, `failed`, `blocked`, or `superseded`. A failed attempt
remains a failed receipt; a later success receives a new receipt.

Raw binlogs, Apple SDK files, credentials, provisioning-profile files or
contents, device identifiers, pairing records, and private logs are local
artifacts and must not be committed. A `CodesignProvision` profile-name or UUID
selector is permitted only as a redacted standard build-property reference; it
must never be recorded as a profile path, content, or credential. The receipt
records a sanitized reference and, where possible, the local artifact hash.
