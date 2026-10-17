# Provenance and Redistribution Control

No executable, package, SDK, or third-party source is accepted merely because
its version is known. Before execution in a canonical SDK root or a signing
environment, record its immutable source, integrity hash, license/notice, and
redistribution classification in `toolchain.lock.json` and the relevant
receipt.

## Required classifications

- `redistributable`: rights and required notices have been confirmed.
- `user-supplied-only`: acquired and retained by the developer; never bundled.
- `build-only`: used privately to create another artifact; distribution rights
  for that artifact must still be assessed.
- `blocked`: cannot be acquired, executed, or redistributed under the current
  policy.
- `unknown`: not eligible for execution in privileged contexts.

## Apple SDK boundary

Apple SDK/Xcode assets are `user-supplied-only` and remain blocked from M2
extraction/use until applicable terms cover the intended copy, host, cache,
backup, CI, and output scenarios. They are never committed or released by this
repository.

## Sensitive material

Private keys, profiles, tokens, pairing records, raw UDIDs, unsanitized
binlogs, and diagnostic dumps are secrets or sensitive operational records.
They must not be committed, passed as command-line arguments, or emitted by
capture scripts.

## Project license

The project license is intentionally unselected. Add `LICENSE` only after the
operator chooses it. This policy does not assign licenses to third-party work.
