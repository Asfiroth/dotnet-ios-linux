# dotnet-ios-linux

A proposed Linux host compatibility layer for .NET for iOS. The goal is to build, sign, install, and launch an ordinary `net10.0-ios` application on a physical iPhone using standard .NET tooling.

**Status: experimental M2–M9 positive-path proof.** The repository includes a
transactional importer for a user-supplied Xcode 27 archive, a validated
synthetic-Xcode builder, fail-closed `xcrun`/`xcodebuild` contracts, and a pinned
Linux-hosted Mono iOS AOT cross compiler. The unchanged `HelloIos` project now
passes the macios trim, LLVM AOT, native compile, native link, and unsigned app
bundle pipeline entirely on Linux. Its executable is Mach-O arm64 for iOS 15.0,
records SDK 27.0, and starts without a code-signature load command. A validated
Apple development identity/profile then signs a copy with pinned `rcodesign`,
and pinned `pymobiledevice3` installs the exact bundle over USB without
auto-pairing or identifier output. A manual icon launch now displays stable UI
after executing generic AOT, managed-exception, P/Invoke, and native-callback
probes. A subsequent incremental LLVM AOT/native-link rebuild was freshly
signed, installed as a replacement, manually launched, force-quit, and cold
relaunched with the same results and no observed crash activity. This completes
the minimal UIKit device MVP. Automated CoreDevice launch remains blocked on an
operator-approved personalized Developer Disk Image; negative deployment,
lifecycle, Rider, and Uno remain unverified, so this is not yet a working iOS
development distribution.

Read the [project specification](docs/specification.md) for architecture,
implementation requirements, milestones M0–M10, validation, and release
criteria.

Implementation entry points:

- [`eng/toolchain/README.md`](eng/toolchain/README.md) — import an iPhoneOS SDK and create the synthetic Xcode tree.
- [`eng/runtime/README.md`](eng/runtime/README.md) — acquire, patch, build, smoke-test, and package the matched AOT compiler.
- [`src/README.md`](src/README.md) — package templates, MSBuild integration, and Linux host shims.
- [`tests/contracts/README.md`](tests/contracts/README.md) — run SDK-free host contract tests.
- [`tests/integration/README.md`](tests/integration/README.md) — run macios discovery/compiler-selection gates and the private-SDK app build gate.
- [`docs/README.md`](docs/README.md) — specification, evidence policy, compatibility status, and governance.

## Repository layout

- `src/` contains product-facing MSBuild integration, package templates, and
  host shims.
- `eng/` contains maintainer tooling for runtime construction, SDK import,
  signing/deployment, and evidence validation.
- `tests/` contains unchanged application fixtures plus public contract and
  private integration gates.
- `docs/` contains durable architecture, status, evidence, and governance
  material.

Run the SDK-free repository checks with:

```sh
eng/evidence/validate-repository.sh
```

## Design commitments

- Preserve the contracts of `dotnet`, MSBuild, Rider, and stock `uno-check`.
- Use `~/.dotnet` as the canonical development SDK root, including all participating workloads.
- Keep workload ID `ios`; provide a Linux-capable workload manifest and project-owned host tooling.
- Reuse official `Microsoft.iOS.*` packs and the device runtime wherever possible.
- Present a synthetic Xcode layout through `DEVELOPER_DIR`; prefer compatible tools and shims over forks.
- Reuse pinned components from [omarchy-apple-dev](https://github.com/joshuaswarren/omarchy-apple-dev) for Darwin tooling, signing, and device operations.
- Keep application projects portable between Linux and macOS without custom SDK names or Linux-only build switches.

## First target

Linux x86_64, initially EndeavourOS/Arch; .NET 10; `net10.0-ios`; `ios-arm64`; Mono; physical iPhone over USB; Xcode `27.0 (27A266a)` and iPhoneOS `27.0 (24A430)`. The active candidate baseline is .NET SDK `10.0.402`, workload set `10.0.401.1`, and macios `27.0.10722`, selected from the supplied Mac reference. Linux app linking, development signing, USB installation, manual launch, visible UI, and deterministic AOT probes are verified for the minimal UIKit sample.

Simulator support, NativeAOT, CoreCLR, wireless deployment, managed debugging, hot reload, and App Store distribution are outside the first MVP.

## Intended workflow

The following is a target user experience, **not runnable installation instructions**:

```sh
# One-time bootstrap will install the compatibility distribution.
dotnet-ios-linux setup

# Shell and Rider must use this same SDK.
export DOTNET_ROOT="$HOME/.dotnet"
export PATH="$DOTNET_ROOT:$DOTNET_ROOT/tools:$PATH"
export DEVELOPER_DIR="$HOME/.local/share/dotnet-ios-linux/Xcode.app/Contents/Developer"

dotnet workload install ios
dotnet workload list
dotnet new ios -n HelloIos
cd HelloIos
dotnet build -f net10.0-ios -r ios-arm64 -c Release
```

Setup will also provide the shim environment and package-source configuration. Signing requires a configured development identity and provisioning profile. For a private Mac build, standard `CodesignKey` and `CodesignProvision` selectors may be set in local project configuration or passed as MSBuild properties; selectors must be redacted from shared evidence, and profiles or credentials must never be passed as arguments. Stock `uno-check` will use the distribution's custom manifest. Normal MSBuild `Run` integration is planned after separate signing, installation, and launch have been proven.

## Implementation starts here

1. Capture the exact working Mac toolchain and package graph; record checksums and source revisions.
2. Prove one unambiguous Linux manifest can expose workload `ios` and evaluate the minimal application in the canonical SDK root.
3. Restore the required official packs and record the next host dependency.
4. Start the Mono AOT compiler feasibility experiment early, before polishing the installer or IDE workflow.

M0–M6 establish an unsigned build, M7–M9 establish the physical-device MVP, and M10 explores native debugging. The private M2–M6 technical gate and full M7–M9 positive path pass for the pinned inputs, including clean and incremental bundle launches plus cold relaunch. Negative deployment coverage and workload lifecycle remain open.

## License

Original project content is available under the [MIT License](LICENSE).
Third-party tools, SDKs, sources, and generated artifacts remain subject to
their own terms; see the
[provenance policy](docs/governance/provenance-and-redistribution.md).
