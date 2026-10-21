# ADR-0001: Use one canonical user-local .NET root

**Status:** Superseded by [ADR-0006](0006-use-the-active-host-dotnet-root.md)
**Date:** 2026-10-20
**Owner:** Repository maintainer

## Context

The Linux iOS workload must coexist with other .NET workloads while preserving
ordinary `dotnet`, MSBuild, and application-project contracts. A second SDK root
or a modified system installation would make workload resolution, IDE use, and
uninstall behavior ambiguous.

## Decision

Use `~/.dotnet` as the sole participating SDK and workload root. Keep the
package-manager-owned `/usr/share/dotnet` untouched. The bootstrapper owns only
its recorded additions, activates the root explicitly, and preserves unrelated
SDKs, packs, workloads, and history.

## Alternatives rejected

- A separate `~/.dotnet-ios-linux` root, because it breaks the one-root
  consumer/workload contract.
- Mutating `/usr/share/dotnet`, because it expands the ownership and rollback
  boundary beyond project-managed assets.

## Consequences

- Application projects remain ordinary .NET projects and do not name a
  Linux-specific SDK.
- Setup, doctor, and uninstall must detect conflicting roots and stale MSBuild
  overrides rather than silently choosing another installation.
- Rider and other consumers must select `~/.dotnet/dotnet` and inherit the same
  activation environment.

## Evidence boundary and review

This historical user-local-root decision was superseded when the installer began
using the active host `dotnet` root with a constrained privileged helper. The
current lifecycle and coexistence boundary is summarized in the
[compatibility matrix](../status/compatibility-matrix.md) and locked in
[`toolchain.lock.json`](../../toolchain.lock.json). Rider and uno-check remain
separate consumer gates; revisit this decision only if the standard workload
model cannot satisfy those consumers without breaking coexistence.
