# Linux-hosted Mono iOS AOT compiler

The runtime source is pinned to
`4271d88e0aebf3d04f188f1334c2220d80555ef6`, the dotnet/runtime commit
corresponding to runtime pack `10.0.12` from VMR commit
`95017c711e6afc1085133d440e42b4bd78155701`.

```sh
eng/runtime/acquire-runtime.sh --destination artifacts/runtime-10.0.12
eng/runtime/apply-patches.sh --source artifacts/runtime-10.0.12
eng/runtime/acquire-llvm.py \
  --packages artifacts/llvm-packages \
  --output artifacts/llvm-19.1.0
eng/runtime/build-mono-aot-cross.sh \
  --source artifacts/runtime-10.0.12 \
  --build artifacts/mono-aot-build \
  --install artifacts/mono-aot-install \
  --llvm-root artifacts/llvm-19.1.0

eng/runtime/verify-compiler-smoke.py \
  --compiler artifacts/mono-aot-install/bin/mono-aot-cross \
  --assembly tests/AotProbe/bin/Release/net10.0-ios/ios-arm64/AotProbe.dll \
  --runtime-pack /path/to/Microsoft.NETCore.App.Runtime.Mono.ios-arm64/10.0.12 \
  --output artifacts/aot-smoke \
  --llvm-root artifacts/llvm-19.1.0

eng/runtime/package-aot-pack.py \
  --compiler artifacts/mono-aot-install/bin/mono-aot-cross \
  --runtime-commit 4271d88e0aebf3d04f188f1334c2220d80555ef6 \
  --output artifacts/aot-pack \
  --llvm-root artifacts/llvm-19.1.0
```

The source revision uses Microsoft Mono LLVM packages at version
`19.1.0-alpha.1.25461.1`. A host LLVM with another major or package revision is
not assumed compatible. `acquire-llvm.py` restores the exact Linux-x64 SDK and
Tools packages into an isolated cache, verifies their locked SHA-256 hashes and
source commit, and overlays their `tools/linux-x64` payloads into one
`bin`/`include`/`lib` root. It records a file/mode/symlink inventory and rechecks
the complete assembled root before reuse, even when the package cache has been
deleted. New roots and AOT pack directories are published atomically without
replacing a concurrently created path; explicit replacement of a verified owned
LLVM root uses an atomic directory exchange. The Mono compiler cache follows the
same protocol: it is built in a temporary sibling directory and published only
after the compiler payload, matched LLVM dependencies, and provenance record
verify. An executable without `aot-build-provenance.json` is incomplete and is
discarded rather than reused. `--without-llvm` is available only as an explicit feasibility mode;
the installer never accepts a non-LLVM pack and no script accepts and ignores
`--llvm`.

The direct CMake build writes truthful standalone version metadata
(`10.0.12.0` plus the dotnet/runtime source commit) before configuration. It
does not reuse the VMR's official build number or claim to be an official
Microsoft-produced binary. LLVM-enabled installs place the two matched C++
runtime libraries beside `mono-aot-cross`; its `$ORIGIN` runtime path therefore
does not depend on an ambient `LD_LIBRARY_PATH`. Pack staging requires the
adjacent compiler-build provenance file and rejects a copied or mismatched
binary/root combination.

The build applies canonical `-ffile-prefix-map` values for the runtime source,
build directory, install directory, and LLVM root, and sets a deterministic
locale/time environment. This makes the compiler artifact independent of the
operator's cache path. The current reproducible compiler SHA-256 is recorded in
`toolchain.lock.json` under
`components.monoRuntime.compilerConfiguration.reproducibleCompilerSha256`.
The historical smoke/device hashes in that lock remain observations of the
original evidence runs.

`verify-source.sh` permits only the exact pinned runtime commit in either a
pristine state or with the byte-for-byte locked three-file patch. Staged,
untracked, or unrelated tracked source changes fail before configuration. The
LLVM-enabled compiler and its copied `llc`, `opt`, libc++, and libc++abi payloads
must match the output hashes in `toolchain.lock.json`; a merely plausible
version string is insufficient.

`verify-compiler-smoke.py` compiles every discovered method in one managed
assembly. It checks both the compiler's LLVM object and the Clang-assembled Mono
output as Mach-O arm64 relocatable objects. This is useful compiler evidence,
but it is deliberately labeled **not M4 acceptance**: M4 still requires the
executable `HelloIos` pipeline, and runtime acceptance requires device execution.
Each evidence file records Mono's per-run AOTID; LLVM object hashes are therefore
observations for that invocation, not reproducible lock values.
