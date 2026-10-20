# Evidence

Evidence is separate from architecture and support claims:

- [`receipt-schema.md`](receipt-schema.md) defines the required content of a
  sanitized, immutable receipt.
- [`receipt.schema.json`](receipt.schema.json) is the machine-readable contract
  reference mirrored by the enforced Python validator.
- [`receipts/index.md`](receipts/index.md) is the publication index. A receipt
  becomes independently reviewable only when it is listed there.
- [`historical/m0-to-m9-observations.md`](historical/m0-to-m9-observations.md)
  preserves the previous narrative summary. It provides context, not auditable
  proof.

Raw captures, SDK files, signing material, pairings, device identifiers, and
unsanitized logs remain outside the repository. A status page may summarize
private evidence, but it must label that summary as unpublished rather than
present it as a published receipt.
