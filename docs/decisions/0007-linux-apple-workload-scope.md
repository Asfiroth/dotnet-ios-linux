# ADR-0007: Classify Apple workloads without expanding Linux support

**Status:** Superseded by [ADR-0008](0008-register-locked-apple-companions.md)
**Date:** 2026-10-25
**Owner:** Repository maintainer
**Related:** [ADR-0005](0005-explicit-signing-and-deployment-boundary.md),
[ADR-0006](0006-use-the-active-host-dotnet-root.md)

## Context

An active .NET root can contain Apple workload markers in addition to the
project-owned `ios` path. Marker names alone do not establish an Apple target,
Linux compatibility, or an instruction to reinstall a workload. Conversely,
hard-coding companion IDs into a preflight plan fabricates absent state and can
be mistaken for an expanded Linux support claim.

The existing Linux runtime/device evidence, compiler pack, synthetic Xcode
layout, signing procedure, and runner apply only to `ios` targeting physical
`ios-arm64` devices. macOS, Mac Catalyst, tvOS, and any future Apple root
discovered by the same metadata are not qualified Linux build or runtime paths.

## Decision

`install.sh preflight` is a read-only inventory operation. It invokes no
`dotnet` command and never writes a workload marker, package registration,
manifest, pack, cache, data root, or installation receipt.

The inventory:

1. takes installed workload presence only from `metadata/workloads/**/InstalledWorkloads/*`;
2. resolves each marker's workload set from the matching `InstallState` record;
3. reads the selected workload-set mapping and its exact manifest files;
4. identifies Apple roots from macios `WorkloadDependencies.json` records that
   declare both an Xcode dependency and public workload aliases; and
5. follows manifest `extends` metadata to classify installed workloads.

The policy layer is separate from discovery:

- direct `ios` is the sole `linux-ios-runtime` workload;
- Apple workloads that reach only `ios` through another workload, such as
  `maui-ios`, are reported as unqualified rather than granted MAUI support;
- workloads whose closure reaches only non-iOS Apple roots are
  `reference-only`;
- `mixed-unqualified` means a closure with a non-Apple leaf, or with `ios` and
  at least one other Apple root; and
- malformed, unresolved, ambiguous, or case-colliding metadata fails closed.

Reference-only means inventory and preservation of existing state. It does not
permit Linux native bundles, signing, deployment, launch, runtime, Rider, Uno,
or workload-install claims. The production installer continues to invoke only
`dotnet workload install ios`; companion IDs never enter its mutation intent.

## Alternatives rejected

- Hard-code `ios`, `maui-ios`, `maccatalyst`, `tvos`, and `macos` as desired
  workloads, because it invents absent markers and conflates inventory with
  install intent.
- Classify workloads by names or package prefixes, because names are not a
  stable workload-schema contract and aggregate workloads can cross families.
- Treat a workload-set mapping or resolvable reference pack as Linux execution
  support, because neither establishes host tools, AOT, native linking,
  signing, deployment, or runtime behavior.
- Select advertised or newer workload sets during preflight, because a
  feature-band-compatible set can still change the locked iOS graph or
  unrelated workload mappings.

## Consequences

- Preflight reports only observed markers and explicit support classifications;
  it has no `desiredWorkloads` expansion.
- Existing Android, WebAssembly, and other non-Apple workloads remain outside
  the Apple transaction and are reported only as baseline state.
- The current exact iOS graph and compatible workload-set selection remain
  governed by `toolchain.lock.json`; a newer manifest/package graph requires a
  coordinated lock update and separate Linux qualification.
- Companion manifest package identities may be inspected as metadata, but no
  companion package closure, derivative, installer operation, or runtime claim
  is introduced by this decision.

## Evidence boundary and review

Repository contracts cover metadata-driven classification, trailing-comma
manifest parsing, `extends` closure, distinct SDK/workload-set/manifest feature
bands, and fail-closed unresolved or case-colliding data. They do not prove any
companion target executes on Linux.

Revisit this decision only when a target-specific host compiler, pack closure,
native-link path, signing/deployment procedure where applicable, and runtime
evidence have been independently qualified and locked.
