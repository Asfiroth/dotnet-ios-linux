# ADR-0005: Keep signing, deployment, and launch explicit

**Status:** Accepted
**Date:** 2026-10-20
**Owner:** Repository maintainer

## Context

Signing and device operations consume credentials, provisioning material, a
trusted device, and Apple-controlled developer services. Treating them as an
implicit build side effect risks accidental secret exposure, pairing, deployment,
or unsupported launch behavior.

## Decision

Keep setup, signing, installation, and launch as explicit operations. Setup
never loads credentials, pairs a device, signs an application, installs an app,
or launches one. Private signing configuration remains outside the repository;
the first supported workflow uses direct, validated backend actions before any
MSBuild or IDE Run integration is claimed.

## Alternatives rejected

- Implicit signing or deployment during setup/build, because it could consume
  secrets or change a device without a deliberate operator action.
- Claiming IDE Run/debug support from a CLI device result, because consumer host
  checks and debugger requirements are independent gates.

## Consequences

- Build success does not imply signing, installation, or launch success.
- Device actions require deliberate confirmations and redact device identifiers.
- IDE Run/debug support is a separate consumer and debugging gate, not implied
  by a CLI device proof.

## Evidence boundary and review

The supported action boundaries are documented in
[the toolchain guide](../../eng/toolchain/README.md) and summarized in the
[compatibility matrix](../status/compatibility-matrix.md). Revisit after the
direct path is reproducible and a standard MSBuild integration can preserve the
same safety properties.
