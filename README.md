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
extracted `Xcode.app`, not an original XIP archive that exercises the complete
import path required by the installer.

## Install

The installer supports glibc Linux x86_64 only. It requires ordinary host build
commands including Bash, Python 3.10+, Git, curl, CMake, Ninja, Make, Clang,
binutils, `file`, `flock`, and the standard checksum/archive tools. A compatible
host `dotnet` command must already be on `PATH`. The installer never invokes a
distro package manager.

`install.sh` is intentionally the single Bash orchestration entry point. Bash
owns argument parsing, environment boundaries, lifecycle locking, traps,
rollback, and calls to the normal .NET workload CLI. Python helpers handle
structured and byte-sensitive operations—lock and receipt parsing, XIP metadata
inspection, archive extraction and inventory, conflict-safe SDK publication,
hashing, and activation-file generation—where shell is less appropriate. The
helpers are implementation details, not separate installers.

Supply an original Xcode XIP compatible with the selected iOS workload:

```sh
./install.sh install --xcode-archive /private/path/Xcode_27.xip
source "${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux/activate"
./install.sh doctor
```

The lock records the known SHA-256 of the validated baseline archive. That
known-hash validation applies only to the baseline. A non-baseline compatible
XIP is not matched to a known repository hash: its bounded XIP metadata and
extracted SDK structure are inspected, then its observed SHA-256 and metadata
are written to the installation receipt. This records the bytes and metadata
used, but does not establish baseline-equivalent provenance or device evidence.
Apple SDK files are imported locally and are never part of this repository or
its releases.

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
the installation and privilege-boundary confirmations; it does not suppress a
password prompt from `sudo`.

Installation locations are selected by policy:

- .NET SDK and workloads: the root containing
  `realpath "$(command -v dotnet)"`
- durable project data: `${XDG_DATA_HOME:-$HOME/.local/share}/dotnet-ios-linux`
- disposable downloads/builds: `${XDG_CACHE_HOME:-$HOME/.cache}/dotnet-ios-linux`

Do not run `install.sh` with `sudo`. A writable host root uses the unprivileged
lifecycle. If the selected root is not writable (for example,
`/usr/share/dotnet`), the installer requires a compatible SDK to be present,
explains the boundary, and asks permission to bootstrap and invoke a constrained
root-owned workload helper. It does not install a fallback SDK into a
package-manager root.

Before elevation, every package is downloaded and verified and a complete
local-only workload bundle is created. Privileged `dotnet` processes receive an
allowlisted environment (private home, CLI home, package cache and temp paths;
the selected `DOTNET_ROOT`; a fixed system `PATH` and locale; telemetry/first-run
controls; and fail-closed loopback proxy values) and run in a network namespace
without external interfaces. Root-owned helper code and the trust lock are installed in matching
content-addressed version directories under `/usr/local/lib/dotnet-ios-linux`
and `/usr/local/share/dotnet-ios-linux`;
recovery state and locks live under `/var/lib/dotnet-ios-linux` and
`/run/lock/dotnet-ios-linux`. A receipt-bound random token prevents another
installation from verifying, recovering, or uninstalling that transaction.
Live package-manager-root qualification remains a separate compatibility gate.
The helper validates every root ancestor immediately before privileged execution,
and its workload children inherit the root lifecycle lock so a terminated helper
cannot release recovery while an orphaned mutator still runs.

Within the selected host root, the resolver selects the newest installed stable
SDK version in the locked feature band. This is version and feature-band
compatibility selection, not hash validation of the pre-existing SDK. In a
writable root only, it installs the lock's hash-verified fallback if no eligible
SDK is present.

In a writable root, it reuses an installed workload set only when its
`microsoft.net.workloads.workloadset.json` maps `Microsoft.NET.Sdk.iOS` to the
locked iOS manifest. The current installed set is preferred; otherwise the
newest matching installed set is selected. If no matching set exists, the
lock's hash-verified fallback workload set is installed. A private `global.json`
pins the selected SDK and workload-set versions for the transaction, and the
resulting workload graph is still verified against the lock.

A non-writable root is stricter: it must already contain a compatible SDK, and
the selected workload-set version must equal the lock. The installer never
merges an SDK fallback there; when the exact workload set is absent, its locked
package is included in the verified local-only bundle installed by the
constrained helper.

Xcode compatibility is governed by `RecommendedXcodeVersion` in the selected
installed `Microsoft.iOS.Sdk` pack. Compatibility compares major/minor families:
a workload recommending `27.0` accepts Xcode `27.0.x` and rejects Xcode `27.1`.
The pre-install archive check uses the lock's corresponding compatibility family;
the post-install check reads the recommendation from the selected workload pack.
The imported iPhoneOS SDK must also match the locked workload family.

The recorded M2–M9 device evidence covers the baseline stack only. Accepting a
different compatible XIP or installed SDK patch is an installer compatibility
result, not additional physical-device validation.

The installer verifies every archive in the 12-package direct graph against
`toolchain.lock.json`. Official NuGet archives must also pass author/repository
signature verification; the deterministic local derivative and project packs
are explicitly unsigned and hash-trusted. Downloads and staged directories use
randomized reservations and atomic no-clobber publication. It does not edit
shell startup files. After installation it prints the appropriate `~/.bashrc`
or `~/.zshrc` `PATH` entry for `run-ios-app.sh` and the command that reloads the
file. Source `activate` explicitly for direct CLI work; `run-ios-app.sh` verifies
and loads it automatically.

## Rider

Rider discovers the installed iOS workload through normal .NET/MSBuild workload
resolution. If the application loads with its iOS target available, no
Rider-specific SDK, MSBuild, environment, solution, or project configuration is
required for editing the project. Use the runner below for the supported Linux
iOS build and device workflow.

Stock Rider on Linux cannot use a locally connected iPhone in its built-in iOS
device selector. JetBrains supports Apple-device run/debug through a local or
remote macOS host; the Linux iOS session is not extensible through a supported
public plugin API. This project therefore does not claim Rider-managed iOS
deployment or debugging.

Use the explicit Linux device runner when an operator-approved personalized
Developer Disk Image is already mounted and exactly one paired iPhone is
connected over USB:

```sh
./run-ios-app.sh \
  --project /absolute/path/to/App.csproj \
  --signing-config /private/path/signing.json \
  --confirm-install --confirm-launch
```

The signing configuration follows
[`eng/toolchain/signing-config.example.json`](eng/toolchain/signing-config.example.json)
and must remain outside the repository with mode `600`. The runner builds the
`Release` `net10.0-ios`/`ios-arm64` target without code signing, signs a private
copy, validates the existing pairing and profile authorization, installs, and
launches the app without emitting a device identifier. It does not attach a
debugger.

To expose this operation through Rider's Run button, create a local **Shell
Script** run configuration that executes the absolute path to this repository's
`run-ios-app.sh` with the arguments above. Keep it local because it contains the
private signing-configuration path. This is a command wrapper, not Rider iOS
device or managed-debugger integration.

## Uninstall and cache policy

```sh
./install.sh uninstall
./install.sh uninstall --yes --purge-cache
```

Uninstall preflights hashes and restores only receipt-owned workload and durable
entries, plus the fallback SDK when this installer had to add it. A compatible
SDK that predated installation is never owned or removed. Uninstall preserves
unrelated SDKs, later workloads, workload history, and unknown files. A changed
owned file blocks removal rather than being erased. Workload teardown uses the
transaction's exact path/key restoration rather than a broad `dotnet workload
uninstall`. It holds the SDK-interoperable lock on
`globaljsonworkloadsets.json` while checking same-set consumers and publishing
the owned key change last. If another recorded global JSON selects the same
workload set, exact shared packs, manifests, workload-set data, and installation
records are retained.
If that shared state depends on a fallback SDK added by this installer, its
intact files are also retained and their project ownership receipt is
relinquished rather than deleting an SDK another recorded consumer may use.
The cache is preserved by default; deleting it does not break an installed
toolchain. `--purge-cache` is valid only with uninstall.

For a system-root installation, uninstall elevates only the installed helper and
requires the ownership token in the local receipt. Shared helper binaries and a
minimal root-owned lifecycle tombstone may remain for future verified recovery;
the owned workload mutation is removed unless a recorded same-set consumer
requires shared resources to remain.

## Safety boundaries

- Xcode and Apple SDKs must be operator-supplied; they are neither downloaded
  nor redistributed by the project. Compatibility and non-baseline trust
  boundaries are defined in the installation section above.
- Swift 6.4.0, ncurses 6.6, libxml2 2.13.9, portable CPython 3.14.8, uv 0.9.10,
  xtool, rcodesign, pymobiledevice3, LLVM, and every workload package are pinned
  and hash checked.
- LLVM is mandatory in the installed AOT pack. A non-LLVM feasibility artifact
  cannot enter the workload feed.
- The complete installer refuses effective UID 0. Privileged workload commands
  use only verified local inputs and fail if network namespace isolation is not
  available.
- Signing, installation, and launch are explicit separate actions. Setup never
  loads credentials, pairs a device, signs an app, installs an app, or launches
  one.
- Credentials, profiles, pairing records, Apple archives, signed apps, raw
  binlogs, and device identifiers remain outside managed project data.
- `tests/HelloIos/HelloIos.csproj` and `tests/AotProbe/AotProbe.csproj` remain
  ordinary, unchanged .NET projects.

## Baseline and scope

The active baseline is SDK `10.0.402`, feature band `10.0.400`, workload set
`10.0.401.1`, macios `27.0.10722`, Mono iOS device runtime/AOT packs `10.0.12`,
matched Mono LLVM
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
