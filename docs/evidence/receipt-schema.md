# Evidence Receipt Protocol

Receipts are immutable, sanitized records of an observed experiment. The
machine-readable reference is [`receipt.schema.json`](receipt.schema.json); the
dependency-free `eng/evidence/validate-receipt.py` mirrors and enforces that
version-one contract in this repository.
Published records belong in [`receipts/`](receipts/); raw captures stay outside
the repository.

Validate a record before publication:

```sh
python3 eng/evidence/validate-receipt.py <receipt.json>
```

## Required properties

| Property | Requirement |
| --- | --- |
| `schemaVersion` | Exactly `1`. A format change requires a new schema version. |
| `id`, `milestone`, `status`, `timestampUtc` | Uppercase stable ID, `M0`–`M10`, one of `passed`/`failed`/`blocked`/`superseded`, and an ISO-8601 UTC timestamp. |
| `repositoryRevision`, `toolchainLockSha256` | Full repository revision and the exact 64-character lock hash used for the observation. |
| `host` | At minimum the sanitized operating-system and architecture facts; distribution, kernel, and non-sensitive notes are optional. |
| `commands` | At least one exact sanitized command and its integer exit code. A purpose may be added. |
| `inputHashes`, `outputHashes` | Non-empty label-to-SHA-256 maps. Labels must not disclose private paths. |
| `expectedBehavior`, `observedBehavior` | What the experiment was intended to show and what actually happened. |
| `limitations`, `nextDecision` | At least one remaining limitation and the concrete follow-up or decision. |
| `redaction` | Whether redaction occurred, what was excluded, and a non-sensitive note about the review. |

`localLogReferences` is optional and may contain only neutral references to
operator-controlled local logs. `example: true` marks a format example that
must never be listed as experimental evidence.

Statuses are `passed`, `failed`, `blocked`, or `superseded`. A failed attempt
remains a failed receipt; a later success receives a new receipt. A receipt is
not a support claim by itself: the claim must state the tested boundary and link
to its record from the compatibility matrix.

## Publication rules

1. Validate the JSON with the enforced Python validator, which mirrors the
   version-one schema, then add it to [`receipts/index.md`](receipts/index.md).
2. Keep the record immutable after publication. A correction or a later result
   is a new receipt that references the earlier ID in its narrative.
3. Link only demonstrated boundaries. A successful build receipt does not prove
   signing, installation, launch, Rider, Uno, or debugging.
4. Mark a status claim as unpublished when its underlying receipt is still
   private. A historical summary or lock entry is context, not a replacement.

Raw binlogs, Apple SDK files, credentials, provisioning-profile files or
contents, device identifiers, pairing records, and private logs are local
artifacts and must not be committed. A `CodesignProvision` profile-name or UUID
selector is permitted only as a redacted standard build-property reference; it
must never be recorded as a profile path, content, or credential. The receipt
records a sanitized reference and, where possible, the local artifact hash.
