# dotnet-ios-linux

A proposed Linux host compatibility layer for .NET for iOS. The goal is to build, sign, install, and launch an ordinary `net10.0-ios` application on a physical iPhone using standard .NET tooling.

**Status: specification only.** No installer, workload, compiler, or working build pipeline is included yet. Feasibility depends primarily on a Linux-hosted Mono AOT cross compiler that matches the selected iOS runtime.

Read [SPEC.md](SPEC.md) for architecture, implementation requirements, milestones M0–M10, validation, and release criteria.

## Design commitments

- Preserve the contracts of `dotnet`, MSBuild, Rider, and stock `uno-check`.
- Use `~/.dotnet` as the canonical development SDK root, including all participating workloads.
- Keep workload ID `ios`; provide a Linux-capable workload manifest and project-owned host tooling.
- Reuse official `Microsoft.iOS.*` packs and the device runtime wherever possible.
- Present a synthetic Xcode layout through `DEVELOPER_DIR`; prefer compatible tools and shims over forks.
- Reuse pinned components from [omarchy-apple-dev](https://github.com/joshuaswarren/omarchy-apple-dev) for Darwin tooling, signing, and device operations.
- Keep application projects portable between Linux and macOS without custom SDK names or Linux-only build switches.

## First target

Linux x86_64, initially EndeavourOS/Arch; .NET 10; `net10.0-ios`; `ios-arm64`; Mono; physical iPhone over USB; Xcode 27.0 / iOS 27.0 SDK family. The active candidate baseline is .NET SDK `10.0.402`, workload set `10.0.401.1`, and macios `27.0.10722`, selected from the supplied Mac reference. Linux compatibility remains unverified.

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

M0–M6 establish an unsigned build, M7–M9 establish the physical-device MVP, and M10 explores native debugging. Every milestone requires an evidence receipt; none is currently marked complete.
