# ADR-0003: Select the synthetic Xcode layout through `DEVELOPER_DIR`

**Status:** Accepted
**Date:** 2026-10-20
**Owner:** Repository maintainer

## Context

The macios targets discover SDK paths and selected command entry points through
Xcode-shaped filesystem and environment contracts. Linux cannot provide a real
Xcode installation, and replacing system Apple tools would affect unrelated
workflows.

## Decision

Construct the minimum truthful synthetic `Xcode.app` layout required by the
pinned macios release and select it with `DEVELOPER_DIR`. Implement only
observed query and tool contracts; unsupported operations fail clearly. The
operator-supplied Apple SDK remains unmodified and is never bundled in releases.

## Alternatives rejected

- Global operating-system or tool-path spoofing, because it affects unrelated
  Apple tooling and makes failure boundaries unclear.
- Treating the layout as a full Xcode replacement, because only observed macios
  contracts are in scope.

## Consequences

- The compatibility layer is scoped to MSBuild/tool discovery, not a claim that
  Linux provides Xcode or all Xcode capabilities.
- Activation must supply `DEVELOPER_DIR` and the project-owned shims without
  globally intercepting unrelated Apple tooling.
- Broader resource, simulator, and Xcode IDE support need independent evidence.

## Evidence boundary and review

The pinned layout and backend are recorded in
[`toolchain.lock.json`](../../toolchain.lock.json); the current boundary is in
the [compatibility matrix](../status/compatibility-matrix.md). Revisit when a
pinned macios consumer requires an interface that cannot be implemented safely
through the scoped layout and shims.
