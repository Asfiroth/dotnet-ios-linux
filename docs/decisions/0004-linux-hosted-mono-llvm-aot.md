# ADR-0004: Use a matched Linux-hosted Mono LLVM AOT compiler

**Status:** Accepted
**Date:** 2026-10-20
**Owner:** Repository maintainer

## Context

An iOS application requires host tooling that runs on Linux while generating
code compatible with the selected iOS Mono device runtime. Copying a macOS host
compiler or substituting a Linux runtime would not establish that compatibility.

## Decision

Build and package a Linux x86_64 Mono AOT cross compiler matched to the locked
iOS runtime and LLVM inputs. The supported path is LLVM-enabled AOT for
`ios-arm64`; NativeAOT, CoreCLR, and an ignored LLVM flag are out of scope.

## Alternatives rejected

- Renaming or copying a macOS-hosted compiler, because it does not make the host
  executable runnable on Linux.
- Substituting a Linux runtime or switching to NativeAOT without a deliberate
  scope revision, because neither proves compatibility with the selected device
  runtime.

## Consequences

- Compiler, runtime, LLVM, and task provenance must remain lock-coupled.
- A build is insufficient proof: device probes are required to establish runtime
  compatibility.
- A future compiler or runtime upgrade is a new compatibility evaluation, not a
  transparent package refresh.

## Evidence boundary and review

Compiler inputs and the current device-path scope are recorded in
[`toolchain.lock.json`](../../toolchain.lock.json) and the
[compatibility matrix](../status/compatibility-matrix.md). Revisit if a matched
compiler cannot be built, emits incompatible output, or a deliberate runtime
strategy change is approved.
