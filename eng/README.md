# Engineering tools

This directory contains maintainer and private-gate tooling:

- `runtime/` — acquire, patch, build, verify, and package the matched Mono AOT
  compiler and LLVM tools;
- `toolchain/` — import a user-supplied Apple SDK, construct synthetic Xcode,
  stage pinned dependencies, sign copied bundles, and perform guarded device
  operations; and
- `evidence/` — inventory, reference-capture, receipt-validation, and repository
  validation helpers.

Generated toolchains, Apple assets, credentials, signed applications, raw
binlogs, and private device evidence must remain outside the repository.
