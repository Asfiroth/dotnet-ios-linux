# ADR-0002: Preserve the public `ios` workload identity

**Status:** Accepted
**Date:** 2026-10-20
**Owner:** Repository maintainer

## Context

Developers and upstream tooling expect `dotnet workload install ios` and the
ordinary `net10.0-ios` target framework. A new workload name would require
application or consumer-tool changes and would no longer test the expected
macios interfaces.

## Decision

Keep `ios` as the public workload ID. For the pinned SDK, derive the effective
Linux graph from the official iOS manifest identity and preserve official pack
identities where they remain applicable. Project-owned packs use distinct names
and never impersonate Microsoft-signed artifacts.

## Alternatives rejected

- A public `ios-linux` workload, because consumers and projects expect `ios`.
- A parallel effective `ios` definition, because manifest precedence and
  lifecycle behavior would be ambiguous.

## Consequences

- Users retain standard CLI and project syntax.
- The bootstrapper must prove that exactly one effective `ios` definition is
  present and that lifecycle operations preserve the intended graph.
- A competing official or third-party `ios` manifest is a compatibility error,
  not a reason to introduce `ios-linux`.

## Evidence boundary and review

The direct graph and package identities are recorded in
[`toolchain.lock.json`](../../toolchain.lock.json); support status is maintained
in the [compatibility matrix](../status/compatibility-matrix.md). Revisit only
if the pinned SDK's workload resolver makes a safe standard-ID graph impossible.
