# ADR-0008: Register locked Apple companions without claiming Linux execution

**Status:** Accepted
**Date:** 2026-10-25
**Owner:** Repository maintainer
**Supersedes:** [ADR-0007](0007-linux-apple-workload-scope.md)
**Related:** [ADR-0002](0002-standard-ios-workload-identity.md), [ADR-0006](0006-use-the-active-host-dotnet-root.md)

## Context

The .NET workload-set mapping for the selected SDK contains four direct Apple workloads: `ios`, `macos`, `maccatalyst`, and `tvos`. The earlier policy correctly separated read-only inventory from installation intent, but prohibited companion registration entirely. That left the selected workload-set topology incomplete and made a direct, reversible registration path impossible to test.

The existing Linux compiler, Xcode shim, signing, deployment, device, and runtime evidence still applies only to `ios` targeting physical `ios-arm64` devices. A successful CLI registration is not execution evidence.

## Decision

For a writable host root, the installer manages all four direct Apple workload registrations by default in one `dotnet workload install` transaction. It uses verified, deterministic unsigned derivatives of the exact official manifest packages:

- `ios` remains the only `linux-ios-runtime` workload and retains its direct Linux physical-device graph and project-owned AOT packs.
- `macos`, `maccatalyst`, and `tvos` derivatives remove unsupported `extends`, declare `linux-x64`, and are explicitly `installed-unqualified`.
- The normal .NET CLI owns every registration record. The installer never fabricates `InstalledWorkloads`, `InstalledPacks`, or manifest metadata.

`--keep-existing ios|macos|maccatalyst|tvos` is repeatable. It preserves an already registered direct workload as external and unqualified instead of replacing it, but only when its manifest version/band exactly matches the lock at the transaction boundary. Any payload or protected registration change fails closed. The CLI's unavoidable global `InstalledPacks` reconciliation records are narrow, journaled metadata changes, not ownership of those workloads and are never replayed during restoration.

One aggregate transaction discovers the selected Apple predecessor's actual manifest identity and payload closure before CLI mutation. Rollback and uninstall restore those predecessor bytes and records while preserving Android, `maui-android`, `wasm-tools`, unrelated workload history, and unknown files. The system-root helper remains intentionally limited to its existing iOS-only path; it reports the reduced plan and companion registration there is deferred until equivalent privileged recovery coverage exists.

## Alternatives rejected

- Hand-write companion markers, because that bypasses the CLI's registration semantics and cannot establish a valid package closure.
- Treat all Apple registrations as Linux execution support, because the required compiler, native-link, signing, deployment, and runtime evidence is absent.
- Use a full-root rollback, because it would erase unrelated workload changes.
- Preserve a requested existing workload without snapshotting it, because CLI reconciliation can alter global metadata even when its payload is unchanged.

## Consequences

- The locked companion package closures and derivative hashes are part of `toolchain.lock.json` and are verified during setup and doctor.
- Receipts distinguish managed registrations, external kept registrations, and their support classifications.
- Preflight stays read-only and remains the source of observed-marker inventory; it does not invent workload presence.
- Documentation and status output must say **registration-only** for `macos`, `maccatalyst`, and `tvos`, never build/runtime support.

### Existing installations and operators

Existing ADR-0007/iOS-only receipts remain valid as iOS-only installations and
are not silently expanded. Re-run installation only after choosing an explicit
ownership policy: default writable-root setup replaces the four selected direct
registrations, while `--keep-existing` preserves an **exact lock-compatible**
existing direct registration. A different manifest version/band fails before
mutation; it must be reconciled by an explicit uninstall or a separately
qualified migration. Operators must retain the transaction/receipt until
uninstall completes; maintainers must update the lock, package closures,
derivative hashes, transaction tests, and this support boundary together.

## Evidence boundary and review

Contract and disposable-root probes cover all-four registration, archive and signature verification, predecessor restoration, and a protected existing iOS registration. They do not qualify companion builds, native bundles, signing, deployment, launch, Rider, Uno, or runtime execution on Linux.

Revisit this decision only with independent target-specific execution evidence, or before extending the privileged helper beyond iOS.
