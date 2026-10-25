# ADR-0009: Qualify xtool through a hermetic logical build path

**Status:** Accepted  
**Date:** 2026-10-25  
**Owner:** Repository maintainer  
**Related:** [ADR-0003](0003-synthetic-xcode-through-developer-dir.md)

## Context

The active xtool output hash rejected a current installer build. The current
ambient GNU Binutils 2.47 tools reproduced the locked `libXADI.so` output but
not xtool. Two clean offline builds using Swift's staged LLVM 21
`llvm-objcopy` also failed: LLVM changed `libXADI.so`, and the two xtool files
retained 59 differing bytes in `.rodata`.

Every remaining xtool difference was a Swift `#file` literal containing the
physical SwiftPM scratch path. Swift's `-debug-prefix-map` and
`-file-prefix-map` options do not rewrite those runtime string literals. A
generic post-link `.rodata` rewrite would therefore modify runtime-consumed
data and could mask an unexpected source or dependency change.

## Decision

- Build each private physical scratch tree in a rootless user and mount
  namespace that exposes it to Swift at the fixed historical logical path
  `/tmp/dotnet-ios-linux/.xtool-build-f0a1f90efdbb0dc023e276ff529da92618da7a87`.
  The installer creates the neutral host mountpoint when absent; the namespace
  overlays only that directory with private tmpfs before creating the logical
  child, so no OpenCode path or pre-existing child directory is required.
- Build and stage GNU Binutils 2.47 from its hash-locked GNU source release with
  the locked Swift Clang/LLD stage. Invoke its hash-locked `objcopy`, `strip`,
  and `readelf` executables by absolute path. Never fall back to ambient tools.
- Keep the full-file xtool and XADI SHA-256 gates. Do not use masked hashes,
  ignored sections, automatic rebaselining, or an `accept-current` mode.
- Collect only static metadata before the full hashes pass. Execute xtool and
  use the dynamic loader only after complete artifact acceptance.
- Treat unavailable unprivileged user/mount namespaces as an explicit host
  incompatibility rather than selecting a weaker path.

A post-link `.rodata` canonicalizer is outside this decision and requires a
separate ADR and operator authorization.

## Alternatives rejected

- Adopt staged LLVM normalization: it changed XADI and did not produce
  byte-identical xtool files.
- Continue using ambient GNU tools: their executable identity is not controlled.
- Rewrite all matching `.rodata` paths: that scope could hide semantic changes.
- Rebaseline either failed build: neither result has two-build reproducibility
  evidence.

## Consequences

GNU Binutils becomes a private, build-only toolchain component. Linux hosts
must permit unprivileged user and mount namespaces. The build is independent
of its physical destination while preserving the existing full-file trust
anchor. Any change to xtool, Swift, Binutils, or the logical path requires
requalification.

## Evidence boundary and review

The initially qualified path used an OpenCode-specific parent. It was rejected
as a portable installer contract and superseded without weakening verification.
Two new clean offline builds from distinct physical and Swift prefixes produced
xtool SHA-256
`c13e2270e6e9d9cce21ba7daae1b51016fcbd50c1bfd2c6747b1ebd208cb168d`
and unchanged XADI SHA-256
`eb68bd3dad62f68eac166aad7dc4e8c931013f42a529a8f5d4c520e671375350`.
Their final bytes and static ELF metadata matched, neither physical prefix was
present, and `--version`/`--help` succeeded with `LD_LIBRARY_PATH` unset after
candidate-hash agreement. Pinned Binutils itself was reproduced from two
distinct source/build/Swift prefixes before use.

The receipt records this build-only boundary. It does not independently qualify
signing, installation, launch, Rider, Uno, or device runtime behavior. Review
this decision whenever xtool, Swift, Binutils, the fixed logical path, or Linux
namespace requirements change.
