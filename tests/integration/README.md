# Private integration gate

`build-hello-ios.sh` is the M2+M4+M5+M6 integration entry point for a private
runner with a user-supplied Apple SDK. It requires an LLVM-enabled AOT pack,
uses the ordinary `net10.0-ios`/`ios-arm64` application project unchanged,
disables signing through the macios `EnableCodeSigning` property, disables the
optional dSYM and native-strip steps with standard `NoDSymUtil` and
`NoSymbolStrip` properties, and verifies:

- the selected Linux `mono-aot-cross` actually executes;
- `AotProbe.dll` passes through that AOT stage;
- both AotProbe Mono and LLVM outputs are Mach-O arm64 objects consumed by the
  native-link inputs;
- the AotProbe native object and final executable contain the required
  native-to-managed `Compare` callback wrapper;
- an unsigned `HelloIos.app/HelloIos` exists; and
- the executable inspects as Mach-O arm64 without an `LC_CODE_SIGNATURE` load
  command.

This gate intentionally fails rather than skipping when its SDK, LLVM pack, or
inspection tools are absent. macios's final Spotlight notification is accepted
through the synthetic `mdimport` entry point, which validates the output
directory and explicitly reports that indexing is unavailable on Linux; it does
not transform the app.

Invoke it from the repository root with the reorganized shim path:

```sh
PATH="$PWD/src/shims:$PATH" \
DEVELOPER_DIR=/path/to/Xcode.app/Contents/Developer \
  tests/integration/build-hello-ios.sh \
  --aot-pack /path/to/llvm-enabled-aot-pack \
  --output /private/empty/output-directory
```

`verify-macios-discovery.sh` is the narrower M2 gate. It runs the actual pinned
macios `_DetectSdkLocations` target and proves that the compatibility SDK maps
`DEVELOPER_DIR` to the synthetic bundle's `XcodeLocation` without an app edit.

`verify-aot-selection.sh` is the narrow host-pack gate. It restores into an
isolated package directory, runs the actual macios `_FindAotCompiler` target,
and requires its `MonoAotCrossCompiler` item to resolve to the staged
`ios-arm64` Linux compiler.

`verify-workload-lifecycle.sh` creates an isolated SDK root from the locked SDK
archive, generates the local derivatives feed, and exercises all-four direct
Apple workload update, install, list, restore, repair, uninstall, clean, and
reinstall. With a
`--developer-dir`, it also builds unchanged copies of `HelloIos` and `AotProbe`
through LLVM AOT. Each installed official package archive is hash-checked and
must pass NuGet author/repository signature verification; local derivative
packages must remain unsigned. It never operates on `~/.dotnet`.
The update/uninstall commands here are isolated SDK behavior gates; the
production writable-root installer invokes one pinned all-four install command
and performs receipt-backed teardown itself. Companion registrations remain
unqualified for Linux execution.
