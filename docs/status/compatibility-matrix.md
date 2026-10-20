# Compatibility Status

**Last reviewed:** 2026-10-20

**Active baseline:** [toolchain.lock.json](../../toolchain.lock.json)

**Lock SHA-256:** `0601c269996b6681987bcfd8171390cc2a5ddb9ff76b5abd7bb4590fc65a68b5`

**Published evidence:** [receipt index](../evidence/receipts/index.md)

Status values describe the current project boundary; they are not marketing or
release claims. `candidate` means an input is selected, not that it works on
Linux. `Private summary` means the project has recorded local evidence but has
not yet published a sanitized receipt, so the claim is not independently
auditable from this repository.

## Current matrix

| Area | Current boundary | Evidence state | Traceability |
| --- | --- | --- | --- |
| Linux host | glibc Linux x86_64; observed on EndeavourOS/Arch | SDK-free contracts, lifecycle, and device path observed | [Lock](../../toolchain.lock.json) + [private historical summary](../evidence/historical/m0-to-m9-observations.md); receipt pending |
| .NET SDK | 10.0.402 / feature band 10.0.400 | Archive hash verified; conflict-safe merge and Android/Wasm coexistence observed | [Lock](../../toolchain.lock.json); receipt pending |
| macios | 27.0.10722 / Xcode 27.0 | Linux discovery through physical-device launch observed for the minimal UIKit sample | [Historical summary](../evidence/historical/m0-to-m9-observations.md); M2–M9 receipts pending |
| Workload set | CLI 10.0.401.1 / package 10.401.1 | Private `global.json` pin and isolated lifecycle observed | [Lock](../../toolchain.lock.json); receipt pending |
| iOS workload | Public `ios` ID | Deterministic unsigned official-ID derivative with a direct Linux graph | [ADR-0002](../decisions/0002-standard-ios-workload-identity.md) + [lock](../../toolchain.lock.json) |
| Mono device AOT | Linux x64 host to iOS arm64 | LLVM AOT, native link, and repeated minimal-app device probes observed | [ADR-0004](../decisions/0004-linux-hosted-mono-llvm-aot.md) + historical summary; receipt pending |
| Apple SDK | Xcode 27.0 / iPhoneOS 27.0, operator supplied | Archive/SDK metadata verified; SDK is never distributed | [ADR-0003](../decisions/0003-synthetic-xcode-through-developer-dir.md) + [provenance policy](../governance/provenance-and-redistribution.md) |
| Darwin backend | Minimal Linux Clang/LLD adapter | Required discovery, compilation, link, and minimal app path observed | Historical summary; broader tool coverage pending |
| Signing and USB deployment | Development identity and paired iPhone | Private sign/install/manual-launch path observed | [ADR-0005](../decisions/0005-explicit-signing-and-deployment-boundary.md) + historical summary; receipt pending |
| Root bootstrap | `install.sh` candidate | Component and lifecycle paths exercised; full archive-backed top-level run pending | [Specification](../specification.md#5-bootstrap-lifecycle-and-repository-layout) |
| Uno and Rider | Stock consumers | Not started | Separate consumer-gate receipts required |
| Debugging | Native research only | Not started | M10 evidence required |

## Claim boundaries

- M0/M1 evidence does not prove a build pipeline.
- M4 AOT compilation does not prove device runtime compatibility.
- M6 proves an unsigned technical bundle only.
- M9 is required for a device MVP claim.
- Signing, installation, launch, Uno, Rider, and debugging each require
  independent evidence.
- A lock entry, ADR, or historical summary never substitutes for a published
  receipt when independent review is required.

## Evidence publication state

No sanitized milestone receipt has been published yet. The existing lock and
historical summary explain the source of the current status, but they do not
permit an independent reviewer to reproduce or audit a private observation.
The first publication work is to publish bounded, sanitized records for the
workload lifecycle, minimal build, signing/install/manual launch, and root
bootstrap gates, then replace the corresponding `receipt pending` labels above
with record IDs.
