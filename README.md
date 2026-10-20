# dotnet-ios-linux

An experimental Linux host compatibility layer for building, signing, installing,
and launching an ordinary `net10.0-ios` application on a physical iPhone with
standard .NET and MSBuild contracts.

**Status: private/local M2–M9 physical-device evidence is recorded; sanitized
receipt publication is pending. The root bootstrap remains a candidate under
validation.** In private local runs, the unchanged
`tests/HelloIos/HelloIos.csproj` completed trimming, LLVM AOT, native
compilation/linking, unsigned bundling, development signing, USB replacement
installation, manual launch, and cold relaunch on the pinned stack. Runtime
probes covered generics, managed exceptions, P/Invoke, and a native-to-managed
callback. These observations are not independently auditable until receipts are
published. Automated CoreDevice launch, Rider, stock `uno-check`, minimal Uno,
and negative deployment coverage remain separate unpassed gates; see the
[compatibility matrix](docs/status/compatibility-matrix.md).

Private/local isolated exercises report install, update, list, restore, repair,
uninstall, clean, reinstall, rollback, and real Android/Wasm coexistence. A
complete top-level bootstrap run is still pending because validation has the
extracted `Xcode.app`, not the authentic `Xcode_27.xip` that the installer
intentionally requires.

## Install

The installer supports glibc Linux x86_64 only. It requires ordinary host build
commands including Bash, Python 3.10+, Git, curl, CMake, Ninja, Make, Clang,
binutils, `flock`, and the standard checksum/archive tools. It never invokes a
distro package manager or writes to `/usr`.

Supply an original Xcode XIP compatible with the selected iOS workload. The
current workload recommends Xcode `27.0`; patch releases in that major/minor are
accepted, while Xcode `27.1` is not:

```sh
./install.sh install --xcode-archive /private/path/Xcode_27.xip
source "${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux/activate"
./install.sh doctor
```

The validated baseline archive hash remains recorded in `toolchain.lock.json`.
Other compatible builds are structurally inspected and their observed hash and
metadata are written to the installation receipt. Apple files are imported
locally without metadata changes and are never part of this repository or its
releases.

Useful install options:

```sh
# Reuse an already validated, LLVM-enabled local AOT pack.
./install.sh install --xcode-archive /private/path/Xcode_27.xip \
  --aot-pack /private/path/aot-pack

# Print the plan without creating the data or cache roots.
./install.sh install --xcode-archive /private/path/Xcode_27.xip \
  --aot-pack /private/path/aot-pack --offline --dry-run

# Reuse complete, hash-verified caches without downloads.
./install.sh install --xcode-archive /private/path/Xcode_27.xip --offline
```

`--offline` is fail-closed: every required archive, source checkout, SwiftPM
dependency, Python wheel, LLVM package, and workload package must already be in
the project cache. `--jobs N` controls the Mono compiler build. `--yes` suppresses
the canonical-root confirmation.

Installation locations are fixed by policy:

- .NET SDK and workloads: `~/.dotnet`
- durable project data: `${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux`
- disposable downloads/builds: `${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux`

The installer detects the newest SDK patch already present in `~/.dotnet` for
the supported feature band. It falls back to the lock's hash-verified SDK only
when no compatible SDK is installed. It likewise reuses an installed workload
set only when that set maps `Microsoft.NET.Sdk.iOS` to the locked iOS manifest;
otherwise it installs the lock's fallback workload set. A private `global.json`
pins the resolved SDK and workload-set versions for the transaction.

The supplied Xcode archive is inspected rather than matched to a hard-coded
build. Its Xcode major/minor must match `RecommendedXcodeVersion` from the
selected iOS SDK pack (for example, a workload requiring `27.0` rejects Xcode
`27.1`). The imported iPhoneOS SDK must also match the locked workload family.
The archive's observed metadata and SHA-256 are recorded in the receipt.

The installer verifies every archive in the 12-package direct graph against
`toolchain.lock.json`. Official NuGet archives must also pass author/repository
signature verification; the deterministic local derivative and project packs
are explicitly unsigned and hash-trusted. Downloads and staged directories use
randomized reservations and atomic no-clobber publication. It does not edit
shell startup files. Source `activate` explicitly in each shell or configure the
same environment in your IDE.

## Uninstall and cache policy

```sh
./install.sh uninstall
./install.sh uninstall --yes --purge-cache
```

Uninstall preflights hashes and removes only receipt-owned workload and durable
entries, plus the fallback SDK when this installer had to add it. A compatible
SDK that predated installation is never owned or removed. Uninstall preserves
unrelated SDKs, later workloads, workload history, and unknown files. A changed
owned file blocks removal rather than being erased.
The cache is preserved by default; deleting it does not break an installed
toolchain. `--purge-cache` is valid only with uninstall.

## Safety boundaries

- Xcode must be operator-supplied, structurally valid, and compatible with the
  selected iOS workload's declared recommended Xcode major/minor; Apple SDKs are
  neither downloaded nor redistributed by the project.
- Swift 6.4.0, ncurses 6.6, libxml2 2.13.9, portable CPython 3.14.8, uv 0.9.10,
  xtool, rcodesign, pymobiledevice3, LLVM, and every workload package are pinned
  and hash checked.
- LLVM is mandatory in the installed AOT pack. A non-LLVM feasibility artifact
  cannot enter the workload feed.
- Signing, installation, and launch are explicit separate actions. Setup never
  loads credentials, pairs a device, signs an app, installs an app, or launches
  one.
- Credentials, profiles, pairing records, Apple archives, signed apps, raw
  binlogs, and device identifiers remain outside managed project data.
- `tests/HelloIos/HelloIos.csproj` and `tests/AotProbe/AotProbe.csproj` remain
  ordinary, unchanged .NET projects.

## Baseline and scope

The active baseline is SDK `10.0.402`, feature band `10.0.400`, workload set
`10.0.401.1`, macios `27.0.10722`, runtime `10.0.12`, matched Mono LLVM
`19.1.0-alpha.1.25461.1`, Xcode `27.0 (27A266a)`, and iPhoneOS
`27.0 (24A430)`. The first target is glibc Linux x86_64, `net10.0-ios`,
`ios-arm64`, Mono, and a physical iPhone over USB.

The baseline SDK is a verified fallback, not an exact-version ceiling. Later SDK
patches in feature band `10.0.400` are selected automatically when installed;
new feature bands or iOS workload graphs still require validation and a lock
update because they can change runtime/AOT pack inputs.

Simulator support, Linux arm64/musl, NativeAOT, CoreCLR, wireless deployment,
managed debugging, hot reload, and App Store distribution are outside this MVP.

## Validation and documentation

Run all public SDK-free repository checks with:

```sh
eng/evidence/validate-repository.sh
```

Key references:

- [`docs/README.md`](docs/README.md) — audience-oriented documentation map.
- [`docs/specification.md`](docs/specification.md) — architecture, lifecycle,
  milestones, validation, and claim boundaries.
- [`docs/status/compatibility-matrix.md`](docs/status/compatibility-matrix.md)
  — current scope and evidence-publication state.
- [`docs/decisions/README.md`](docs/decisions/README.md) — accepted
  architecture decisions.
- [`docs/runbooks/installation-and-device-troubleshooting.md`](docs/runbooks/installation-and-device-troubleshooting.md)
  — installation and device failure triage.
- [`docs/evidence/receipts/index.md`](docs/evidence/receipts/index.md) —
  published evidence receipt index.
- [`eng/toolchain/README.md`](eng/toolchain/README.md) — Apple SDK import,
  portable host tools, signing, and device actions.
- [`eng/runtime/README.md`](eng/runtime/README.md) — matched Mono/LLVM compiler.
- [`tests/contracts/README.md`](tests/contracts/README.md) — SDK-free contracts.
- [`tests/integration/README.md`](tests/integration/README.md) — private SDK,
  workload, AOT, and native-link gates.
- [`toolchain.lock.json`](toolchain.lock.json) — canonical pins and hashes.

## License

Project-owned source is available under the [MIT License](LICENSE):
`Copyright (c) 2026 Asfiroth`.
Third-party software and Apple materials remain subject to their own terms; see
the [provenance policy](docs/governance/provenance-and-redistribution.md).
