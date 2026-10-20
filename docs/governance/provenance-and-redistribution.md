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

Apple SDK/Xcode assets are `user-supplied-only`. The private M2–M6 gate may use
an archive supplied by the operator, but that local use does not establish any
redistribution right. Applicable terms must cover the operator's intended host,
cache, backup, CI, and output scenarios. Apple payloads are never committed or
released by this repository.

## Sensitive material

Provisioning-profile files, contents, encoded representations, private keys,
certificates, passwords, tokens, pairing records, raw UDIDs, unsanitized
binlogs, and diagnostic dumps are secrets or sensitive operational records.
They must not be committed, passed as command-line arguments, or emitted by
capture scripts.

The standard macios `CodesignProvision` property is an identifier selector, not
profile material. A profile name or UUID selector may be supplied in private
project configuration or with `-p:CodesignProvision=<selector>` for a local
build. It must never contain a profile path, file content, encoded profile, or
credential. `CodesignKey` identity selectors follow the same local-only rule.
Capture reports and committed receipts must redact selector values; raw logs
and binlogs remain private local artifacts.

## rcodesign 0.29.0

The Linux x86-64 release archive is locked to SHA-256
`dbe85cedd8ee4217b64e9a0e4c2aef92ab8bcaaa41f20bde99781ff02e600002`
and release commit `2312b1e5bd77322c019613e0ca624e222c6c6e08`. The archive's
`COPYING` inventory has SHA-256
`856867e127c2349ea11dff0930a488e7a1e386d975bcb9088a3c6e2bf1b3d990`.
It reports 335 components across 13 SPDX license families and identifies one
component, `ring`, without scanner-derived licensing. The release lock selects
`ring 0.17.8` with crates.io checksum
`c17fa4cb658e3583423e915b9f3acc01cceaee1860e33d59ebae66adc3a2dc0d`;
that exact crate contains an ISC/BoringSSL-derived license notice. The tool is
classified `build-only`: private execution is accepted with `COPYING` retained,
but redistribution is not claimed by this project.

## Project license

Original project content is licensed under the MIT License; see
[`LICENSE`](../../LICENSE). Copyright is held by Asfiroth for the 2026 project
work.

The project license does not assign or replace licenses for Apple SDK/Xcode
assets, downloaded tools, generated binaries, upstream source, or other
third-party components. Their original terms and the classifications above
continue to control acquisition, use, and redistribution.
