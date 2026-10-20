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

## xtool and SwiftPM closure

The enforced xtool resolution contains 55 exact source revisions. Every checkout
at those revisions contains a top-level license file; the reviewed closure is 32
Apache-2.0, 19 MIT, two BSD-2-Clause, one LGPL-3.0-only (`unxip`), and one
LGPL-2.1-only (`xadi`). Eighteen upstream notice files are also present. The
XADI binary-artifact checksum remains separately enforced by SwiftPM and the
setup script. The private stage retains all 55 license files and 18 notices in
`share/licenses/xtool`; a locked 74-entry SHA-256 inventory is verified during
build and reuse.

This review does **not** authorize the project to publish the compiled xtool
stage. xtool and all SwiftPM inputs are `build-only`: the bootstrap acquires
their exact sources/artifact into the operator's cache and creates a private
local executable. A future binary release would need to carry every applicable
notice and satisfy the LGPL source, relinking, and modification requirements;
until that release-specific work is done, binary redistribution is blocked.

## Portable Python and pymobiledevice3 closure

The hash-enforced Python lock resolves 101 packages for CPython 3.14 on glibc
Linux x86-64. The review inspected every installed distribution's exact version,
`METADATA`, classifiers, and embedded license/notice files. The closure includes
permissive, PSF, public-domain, MPL-2.0, LGPL, and GPL packages. In particular,
`pymobiledevice3`, `developer-disk-image`, the `pmd-*` packages, and several
firmware/parser helpers are GPL-3.0-or-later; `pylzss` is LGPLv3. Metadata for
`pytun-pmd3` and `remotezip2` is internally inconsistent, so it is treated
conservatively rather than used to justify redistribution.

CPython, uv, wheels, and the resulting virtual environment are therefore
`build-only`/private local acquisitions. The project does not ship the archives,
wheels, or populated environment. The wheel hashes in
`pymobiledevice3-requirements.lock`, the portable interpreter/archive hashes,
and the durable installation inventory identify the exact reviewed bytes. A
redistributed Python environment remains blocked unless a release-specific
notice/source/compliance bundle resolves the copyleft and metadata-conflict
requirements.

The durable portable-Python stage retains CPython's license,
python-build-standalone's MPL-2.0 license and consolidated bundled-dependency
license inventory, and uv's Apache-2.0/MIT license pair. These four upstream
license inputs are fetched from immutable release tags, hash-verified before
staging, and rechecked independently of the disposable cache. This retention is
required for the private stage but does not change its `build-only`
classification or authorize redistribution.

## NuGet signatures

SDK 10.0.402 `dotnet nuget verify --all` validates author and NuGet.org
repository signatures for the workload-set package, the authentic iOS manifest,
and all nine official archives installed by the direct Linux iOS graph. The
exact package hashes remain mandatory. Cache-only verification sets
`NUGET_CERT_REVOCATION_MODE=offline`; it performs cryptographic chain/timestamp
verification without certificate-network access.

The deterministic iOS manifest derivative and two project packages are
intentionally unsigned and are never represented as Microsoft-signed outputs.
Their absence of `.signature.p7s` is checked, and their locked SHA-256/SHA-512
values are the local trust anchor. The authentic signed manifest is retained as
the input and is verified before derivation.

## Project license

Original project content is licensed under the MIT License; see
[`LICENSE`](../../LICENSE). Copyright is held by Asfiroth for the 2026 project
work.

The project license does not assign or replace licenses for Apple SDK/Xcode
assets, downloaded tools, generated binaries, upstream source, or other
third-party components. Their original terms and the classifications above
continue to control acquisition, use, and redistribution.
