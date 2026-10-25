# Compatibility Status

**Last reviewed:** 2026-10-25

**Active baseline:** [toolchain.lock.json](../../toolchain.lock.json)

**Lock SHA-256:** `3f33fc154ef95f5b15881bac9baa6a423ddac6cd2c050d14d678536d55f419e3`

**Published evidence:** [receipt index](../evidence/receipts/index.md)

Status values describe the current project boundary; they are not marketing or
release claims. `candidate` means an input is selected, not that it works on
Linux. `Private summary` means the project has recorded local evidence but has
not yet published a sanitized receipt, so the claim is not independently
auditable from this repository.

## Current matrix

| Area | Current boundary | Evidence state | Traceability |
| --- | --- | --- | --- |
| Linux host | glibc Linux x86_64; device path observed on EndeavourOS/Arch | General-Linux installer contracts pass; cross-distribution system-root qualification pending | [Lock](../../toolchain.lock.json) + [private historical summary](../evidence/historical/m0-to-m9-observations.md); receipt pending |
| .NET SDK | Active host root; 10.0.402 / feature band 10.0.400 baseline | Host-root discovery and writable-root merge contracts pass; live package-manager root pending | [ADR-0006](../decisions/0006-use-the-active-host-dotnet-root.md) + [lock](../../toolchain.lock.json); receipt pending |
| macios | 27.0.10722 / Xcode 27.0 | Linux discovery through physical-device launch observed for the minimal UIKit sample | [Historical summary](../evidence/historical/m0-to-m9-observations.md); M2–M9 receipts pending |
| Workload set | CLI 10.0.401.1 / package 10.401.1 | Private `global.json` pin and isolated lifecycle observed | [Lock](../../toolchain.lock.json); receipt pending |
| iOS workload | Public `ios` ID | Deterministic unsigned official-ID derivative with a direct Linux graph | [ADR-0002](../decisions/0002-standard-ios-workload-identity.md) + [lock](../../toolchain.lock.json) |
| Apple companion workloads | Direct `macos`, `maccatalyst`, and `tvos` registrations; derived aggregates remain reference-only, iOS-derived-unqualified, or mixed-unqualified by their closure | **Private summary:** locked deterministic derivatives register the three direct companion IDs in a writable root. This is registration-only: no native bundle, signing, deployment, launch, runtime, Rider, or Uno claim. `mixed-unqualified` means a non-Apple leaf or `ios` plus another Apple root | [ADR-0008](../decisions/0008-register-locked-apple-companions.md); receipt pending |
| Mono device AOT | Linux x64 host to iOS arm64 | LLVM AOT, native link, and repeated minimal-app device probes observed | [ADR-0004](../decisions/0004-linux-hosted-mono-llvm-aot.md) + historical summary; receipt pending |
| Apple SDK | Xcode 27.0 / iPhoneOS 27.0, operator supplied | Archive/SDK metadata verified; SDK is never distributed | [ADR-0003](../decisions/0003-synthetic-xcode-through-developer-dir.md) + [provenance policy](../governance/provenance-and-redistribution.md) |
| Darwin backend | Minimal Linux Clang/LLD adapter | Required discovery, compilation, link, and minimal app path observed | Historical summary; broader tool coverage pending |
| xtool build reproducibility | Host-neutral fixed logical SwiftPM path in a rootless mount namespace; source-built GNU Binutils 2.47 | Two clean offline builds from distinct physical and Swift prefixes produced byte-identical xtool/XADI outputs without an OpenCode host path. Prior OpenCode-path and staged-LLVM evidence remains preserved | [ADR-0009](../decisions/0009-hermetic-xtool-normalization.md); [portable-path qualification](../evidence/receipts/XTOOL-PORTABLE-PATH-QUALIFICATION-20261025.json); [earlier hermetic qualification](../evidence/receipts/XTOOL-HERMETIC-QUALIFICATION-20261025.json); [failed LLVM attempt](../evidence/receipts/XTOOL-NORMALIZER-QUALIFICATION-20261025.json) |
| Signing and USB deployment | Development identity and paired iPhone | Private sign/install/manual-launch path observed | [ADR-0005](../decisions/0005-explicit-signing-and-deployment-boundary.md) + historical summary; receipt pending |
| Root bootstrap | `install.sh` host-root candidate | Writable lifecycle plus sealed-bundle/helper contracts pass; no live `/usr/share/dotnet` mutation has been qualified | [ADR-0006](../decisions/0006-use-the-active-host-dotnet-root.md) + [specification](../specification.md#5-bootstrap-lifecycle-and-repository-layout) |
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

Sanitized xtool normalizer-failure and hermetic-build receipts are published.
The remaining lock and historical-summary entries are still private evidence
and do not permit an independent reviewer to reproduce or audit those wider
claims. The next publication work is to publish bounded records for the
workload lifecycle, minimal app build, signing/install/manual launch, and root
bootstrap gates, then replace their `receipt pending` labels with record IDs.
