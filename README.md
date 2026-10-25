# dotnet-ios-linux

An experimental Linux host compatibility layer for building, signing, installing,
and launching an ordinary `net10.0-ios` application on a physical iPhone with
standard .NET and MSBuild contracts.

> **Current status — 2026-10-26:** the pinned physical-device path has private
> local M2–M9 observations, while the top-level root bootstrap remains a
> candidate. Repository validation currently passes 124 contract tests, and all
> four receipt JSON files pass schema validation. The historical device
> observations are not published receipts; see the
> [compatibility matrix](docs/status/compatibility-matrix.md) and
> [receipt index](docs/evidence/receipts/index.md) for the evidence boundary.

## What is supported

| Area | Current boundary |
| --- | --- |
| Host | glibc Linux x86_64 |
| Build target | `net10.0-ios`, `ios-arm64`, Mono LLVM AOT |
| Device path | One paired physical iPhone over USB, with operator-supplied signing material and a mounted personalized Developer Disk Image |
| Apple workloads | `ios` alone has private Linux runtime/device observations; writable roots also register locked `macos`, `maccatalyst`, and `tvos` workloads for coexistence only |
| Consumers | Terminal and ordinary .NET/MSBuild projects; Rider-managed deployment/debugging and Uno validation are not qualified |

The companion Apple registrations are **registration-only**. They do not claim
Linux compilation, native bundling, signing, deployment, launch, runtime,
Rider, Uno, or broad MAUI support. Derived workloads are unqualified when their
closure reaches a non-Apple leaf, or combines `ios` with another Apple root.

The complete top-level bootstrap has not yet been qualified with an original
Xcode XIP archive. A validated extracted `Xcode.app` is not equivalent evidence
for that import gate. Non-baseline compatible XIPs may be accepted after their
bounded metadata and extracted SDK structure pass validation, but that records
installer compatibility—not baseline provenance or additional device evidence.

## Quick start

The installer supports glibc Linux x86_64. Have Bash, Python 3.10+, Git, curl,
CMake, Ninja, Make, Clang, binutils, `file`, `flock`, standard archive/checksum
tools, and a compatible host `dotnet` already on `PATH`. It never invokes a
distro package manager.

Supply an original, operator-provided Xcode XIP compatible with the selected
iOS workload:

```sh
./install.sh preflight
./install.sh install --xcode-archive /private/path/Xcode_27.xip
source "${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux/activate"
./install.sh doctor
```

`preflight` inventories the active root without invoking `dotnet` or creating
project data/cache roots. It reports installed workload markers and derives
Apple-family classifications from the selected workload set and manifest
`extends` metadata; it does not invent or install workloads.

Useful options:

```sh
# Reuse a validated LLVM-enabled AOT pack.
./install.sh install --xcode-archive /private/path/Xcode_27.xip \
  --aot-pack /private/path/aot-pack

# Inspect the plan without creating data or cache roots.
./install.sh install --xcode-archive /private/path/Xcode_27.xip \
  --offline --dry-run

# Use only complete, hash-verified cached inputs.
./install.sh install --xcode-archive /private/path/Xcode_27.xip --offline
```

`--offline` is fail-closed: every required archive, source checkout, SwiftPM
dependency, Python wheel, LLVM package, and workload package must already be in
the project cache. `--jobs N` controls the Mono compiler build. `--yes` skips
installer confirmations but not a `sudo` password prompt. Do not run
`install.sh` with `sudo`; the complete installer refuses effective UID 0.

## Workload and host-root behavior

Installation targets the root containing `realpath "$(command -v dotnet)"`.

- **Writable root:** by default, one standard CLI transaction registers the
  exact locked direct set `ios macos maccatalyst tvos`. Existing Android,
  `maui-android`, `wasm-tools`, foreign workloads, predecessor Apple closures,
  later workload history, and unknown files remain part of the host state and
  are preserved by the receipt-backed lifecycle.
- **System/non-writable root:** the constrained helper supports the established
  iOS-only plan. It records only `ios`; companion registration is deferred until
  equivalent recovery coverage exists. `--keep-existing` is rejected.
- **Existing direct registrations:** `--keep-existing` may be repeated for
  `ios`, `macos`, `maccatalyst`, or `tvos` only when the exact locked manifest
  version and feature band already exist. Such registrations remain
  external/unqualified; a different registration fails before CLI mutation.

In a writable root, the resolver selects the newest stable SDK in the locked
feature band. It reuses an installed workload set only when all four direct
Apple manifest identities map to the locked versions; otherwise it installs the
hash-verified fallback. A non-writable root must already contain a compatible
SDK and use the exact locked workload-set version; it never receives a merged
SDK fallback. All selected archives are hash checked. Official NuGet archives
also require author/repository signature verification; deterministic local
derivatives and project packs are unsigned and hash-trusted.

The installer does not edit shell startup files. It prints the appropriate
`PATH` entry for `run-ios-app.sh`; source `activate` for direct CLI work, while
the runner verifies and loads it automatically.

## Pinned inputs and reproducible xtool

The active lock is [`toolchain.lock.json`](toolchain.lock.json), SHA-256:

```text
3f33fc154ef95f5b15881bac9baa6a423ddac6cd2c050d14d678536d55f419e3
```

Exactly six independently pinned entries exist under
`components.hostToolchain`:

1. Swift `6.4.0` (with private, source-built `ncurses` `6.6` and `libxml2`
   `2.13.9` compatibility libraries)
2. GNU Binutils `2.47`
3. xtool revision `f0a1f90efdbb0dc023e276ff529da92618da7a87`
4. rcodesign `0.29.0`
5. portable CPython `3.14.8` (with uv `0.9.10`)
6. pymobiledevice3 `11.26.0`

These inputs are acquired and verified through this repository's lock and setup
scripts. External host-setup or similar projects are never installer inputs,
compatibility authorities, audit baselines, provenance records, or reference
implementations.

xtool builds use the canonical logical scratch path
`/tmp/dotnet-ios-linux/.xtool-build-f0a1f90efdbb0dc023e276ff529da92618da7a87`
inside a rootless user/mount namespace. The namespace privately overlays only
the neutral `/tmp/dotnet-ios-linux` mountpoint, so physical SwiftPM paths cannot
enter Swift `#file` strings. The accepted output SHA-256 values are:

```text
xtool:  c13e2270e6e9d9cce21ba7daae1b51016fcbd50c1bfd2c6747b1ebd208cb168d
XADI:   eb68bd3dad62f68eac166aad7dc4e8c931013f42a529a8f5d4c520e671375350
```

The pinned Binutils normalization removes the non-runtime build-ID note, zeros
`.swift_modhash`, and strips unneeded symbols. It does **not** rewrite `.rodata`
or mask hashes. Candidate artifacts are never executed before their complete
hashes are accepted; reuse rechecks the hashes and runs xtool with
`LD_LIBRARY_PATH` unset. See [the toolchain guide](eng/toolchain/README.md) and
[ADR-0009](docs/decisions/0009-hermetic-xtool-normalization.md) for the full
reproducibility and provenance record.

Apple SDK files are imported locally from the operator's Xcode archive and are
never bundled in this repository or its releases.

## Run an app on a device

`run-ios-app.sh` is an explicit sign/install/launch workflow, not a debugger.
Use it only when exactly one paired iPhone is connected and an operator-approved
personalized Developer Disk Image is mounted. Prepare a current-user-owned,
mode-`600` signing configuration:

```sh
install -m 600 eng/toolchain/signing-config.example.json "$PWD/ios-signing.json"
# Edit ios-signing.json with private signing paths and identifiers.
./run-ios-app.sh \
  --project /absolute/path/to/App.csproj \
  --confirm-install --confirm-launch
```

Without `--signing-config`, the runner reads exactly `$PWD/ios-signing.json`;
it does not search parent directories. Both confirmations are required. The
runner validates the configuration, builds a `Release` `net10.0-ios`/
`ios-arm64` target unsigned, signs a private copy, validates pairing and profile
authorization, installs, and launches without emitting a device identifier.
Keep signing configuration, credentials, profiles, pairing records, signed
apps, and raw binlogs outside managed project data and source control.
Installation setup itself never loads credentials, pairs a device, signs an app,
installs an app, or launches one.

## Lifecycle, rollback, and safety

Persistent project data is under `${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux`;
downloads and builds are under `${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux`.
The cache is preserved by default and is not required to run an installed
toolchain. Use `--purge-cache` only with uninstall:

```sh
./install.sh uninstall
./install.sh uninstall --yes --purge-cache
```

Writable roots use the unprivileged lifecycle. A non-writable root uses a
verified local-only bundle and a constrained helper. Before elevation, inputs
are downloaded, verified, and sealed. Privileged processes receive an
allowlisted environment and a network namespace with no external interfaces;
the helper, lock, journal, and recovery state are content-addressed or
receipt-bound. Root ancestors are revalidated immediately before privileged
execution, and workload children inherit the lifecycle lock. A random ownership
token prevents another installation from verifying, recovering, or uninstalling
the transaction.

Uninstall and rollback are ownership-aware:

- only receipt-owned workload and durable entries are removed;
- selected predecessor Apple bytes and metadata are restored exactly;
- Android, `maui-android`, `wasm-tools`, foreign workloads, later history,
  unrelated SDKs, and unknown files are preserved;
- foreign `InstalledPacks` reconciliation deltas are journaled for diagnosis,
  never restored or replayed;
- changed owned files block removal instead of being erased; and
- shared packs, manifests, workload-set data, installation records, or a
  fallback SDK are retained when another recorded consumer still needs them.

The lifecycle uses exact path/key restoration rather than broad workload
uninstall commands. Interruptions, rollback, repair, restore, reinstall, and
ownership-token mismatches fail closed. See the
[installation and device troubleshooting runbook](docs/runbooks/installation-and-device-troubleshooting.md)
for recovery procedures.

## Baseline and non-goals

The active baseline is SDK `10.0.402`, feature band `10.0.400`, workload set
`10.0.401.1`, macios `27.0.10722`, Mono iOS runtime/AOT packs `10.0.12`, matched
Mono LLVM `19.1.0-alpha.1.25461.1`, Xcode `27.0 (27A266a)`, and iPhoneOS
`27.0 (24A430)`. Later SDK patches in feature band `10.0.400` may be selected
when installed; new feature bands or workload graphs require a lock update and
validation.

Outside this MVP: simulator support; Linux arm64/musl; NativeAOT; CoreCLR;
wireless deployment; managed debugging; hot reload; App Store distribution;
macOS, Mac Catalyst, tvOS, or watchOS runtime targets; broad MAUI; and
unqualified Rider/Uno consumer paths. A manifest or reference pack is not
execution evidence.

## Validation and further reading

Run the public SDK-free repository validation with:

```sh
eng/evidence/validate-repository.sh
```

The command validates repository structure, JSON and receipt files, shell syntax,
contract tests, hashes, and local Markdown links. Detailed references:

- [`docs/README.md`](docs/README.md) — documentation map.
- [`docs/specification.md`](docs/specification.md) — architecture, lifecycle,
  validation, and claim boundaries.
- [`docs/status/compatibility-matrix.md`](docs/status/compatibility-matrix.md) —
  current support and evidence state.
- [`docs/decisions/README.md`](docs/decisions/README.md) — accepted decisions.
- [`docs/evidence/receipts/index.md`](docs/evidence/receipts/index.md) —
  published receipt index and publication rules.
- [`eng/toolchain/README.md`](eng/toolchain/README.md) — host tools, Apple SDK
  import, signing, and device actions.
- [`eng/runtime/README.md`](eng/runtime/README.md) — matched Mono/LLVM compiler.
- [`tests/contracts/README.md`](tests/contracts/README.md) — SDK-free contracts.
- [`tests/integration/README.md`](tests/integration/README.md) — private SDK,
  workload, AOT, and native-link gates.

## License

Project-owned source is available under the [MIT License](LICENSE):
`Copyright (c) 2026 Asfiroth`. Third-party software and Apple materials remain
subject to their own terms; see the
[provenance policy](docs/governance/provenance-and-redistribution.md).
