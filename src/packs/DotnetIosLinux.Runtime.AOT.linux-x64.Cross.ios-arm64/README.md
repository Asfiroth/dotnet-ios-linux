# Linux to iOS Mono AOT pack

This is the source layout for the project-owned host pack. Generated binaries
are intentionally not committed. `Sdk/Sdk.props` supplies the standard
`MonoAotCrossCompiler` item for `ios-arm64`; the workload/package graph must
import this SDK rather than requiring an application-project import.

Use `eng/runtime/package-aot-pack.py` to stage `tools/mono-aot-cross`, matched LLVM
tools and their `$ORIGIN` C++ runtime libraries when enabled, and
`provenance.json`. LLVM packaging accepts only a root carrying the locked
`acquire-llvm.py` provenance marker.
