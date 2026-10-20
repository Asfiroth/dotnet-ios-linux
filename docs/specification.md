# dotnet-ios-linux project specification

Status: experimental M2–M9 positive path; minimal UIKit device MVP passed.
Date: 2026-10-17; last updated 2026-10-20.
Primary host: EndeavourOS/Arch Linux x86_64.  
Decision basis: the final decisions in “Linux Mac Development Setup,” conversation `6a68da62-2fbc-83eb-ad21-0f2dd79c7ce6`.

## 1. Outcome and architectural rule

Build an ordinary .NET iOS application on Linux, produce a development-signed application bundle, install it over USB, and launch it on a physical iPhone. After initial setup, developers use `dotnet`, MSBuild, Rider, and stock `uno-check` through their existing interfaces.

**Compatibility before customization:** when upstream expects a workload, SDK pack, file, executable, environment variable, or build target, reproduce that interface before changing upstream code. Fork only where a host dependency cannot be satisfied by configuration, a compatible implementation, or a narrowly scoped shim.

This is an experimental Linux host implementation for the macios build pipeline. A synthetic Xcode tree does not make Linux macOS, and a successfully resolved workload does not prove that its tasks or native tools execute on Linux. Each boundary below requires evidence.

The first technical success is an unsigned minimal UIKit application. The first device MVP is that application signed, installed, and visibly running on an iPhone without a Mac participating in the build, signing, or deployment. A Mac may supply the initial SDK extraction and reference-build evidence.

## 2. Fixed decisions and open hypotheses

| Area | Decision | Evidence still required |
| --- | --- | --- |
| SDK root | Canonical development installation at `~/.dotnet` | CLI, Rider, and uno-check resolve the same root |
| Workload identity | Public workload ID remains `ios` | Resolver accepts exactly one effective definition on Linux |
| Manifest | Project-owned Linux-capable manifest; preferred ID `asfiroth.net.sdk.ios` | Conflict handling, dependency IDs, workload-set behavior, update and repair |
| Microsoft packs | Reuse official SDK, reference, and device runtime packs | Inventory executable formats and host-specific dependencies |
| AOT | Build/package a Linux-hosted Mono cross compiler for iOS arm64 | ABI compatibility, complete compilation, link, and runtime behavior |
| Xcode | Synthetic layout selected by `DEVELOPER_DIR` | Discovery, version parsing, and tool lookups at the pinned macios revision |
| Darwin backend | Reuse pinned omarchy-apple-dev components | Exact compatibility with the chosen 27.0 toolchain |
| Uno environment check | Stock binary with a custom manifest | Linux iOS check runs rather than being silently filtered out |
| IDE | Stock Rider with canonical CLI | Evaluation and build; run/debug are separate gates |

Earlier proposals for workload ID `ios-linux` and a separate `~/.dotnet-ios-linux` SDK installation are superseded. `dotnet-ios-linux` remains the project/bootstrap name. No global operating-system spoofing is permitted.

## 3. Scope and version policy

### MVP matrix

| Dimension | Initial scope |
| --- | --- |
| Host | Linux x86_64, glibc, EndeavourOS/Arch first |
| Framework and RID | `net10.0-ios`, `ios-arm64` |
| Runtime | Mono with device AOT; no JIT dependency |
| Configuration | Release first; Debug compatibility investigated separately |
| Application | Minimal UIKit app before Uno |
| Apple toolchain | Xcode 27.0 / iPhoneOS 27.0 SDK family |
| Device | One recorded physical iPhone model and OS build, connected by USB |
| Outputs | Unsigned `.app`, then development-signed `.app`; `.ipa` when needed by deployment |
| Build host independence | No remote Mac build service after SDK acquisition |

The active candidate baseline was rebaselined on 2026-10-17 to the supplied Mac reference: .NET SDK `10.0.402`, workload set `10.0.401.1`, macios package version `27.0.10722`, Xcode `27.0 (27A266a)`, and iPhoneOS SDK `27.0 (24A430)`. Linux now verifies SDK discovery, matched LLVM AOT execution, native compilation/linking, unsigned bundling, direct development signing, USB installation, clean and incremental manual launches, cold relaunch, visible UI, and deterministic runtime probes for the minimal UIKit application. The complete package graph, supported workload lifecycle, automated launch, negative deployment paths, and consumer tooling remain unverified. Preserve the superseded `10.0.302` / `26.5.10301` evidence as historical receipts; do not mix the two graphs.

`toolchain.lock.json` will record the SDK version and hash, SDK feature band, workload installation mode/set, manifest IDs and versions, every package ID/version/hash, runtime source commit, compiler configuration, LLVM revision, Apple SDK version/build/hash, Xcode metadata, omarchy revision, downstream patches, signing/deployment tool versions, uno-check version, Rider version, and host dependencies. The runtime commit must be traced to the selected device runtime, not inferred solely from the SDK version.

Keep SDK version, SDK feature band, manifest package suffix, manifest version, workload-set version, iOS SDK version, and deployment target distinct. For example, SDK `10.0.402` belongs to feature band `10.0.400`; this alone does not establish which manifest package suffix or fallback directory the distribution consumes. Inspect the selected SDK's behavior rather than copying a path blindly.

### Non-goals for the first MVP

- iOS simulator, Linux arm64 or musl hosts, macOS/Catalyst/tvOS/watchOS targets.
- NativeAOT, CoreCLR, managed breakpoints, hot reload, or a full Xcode replacement.
- Wireless deployment, automatic Apple-account enrollment, or App Store submission.
- General compatibility with all asset catalogs, storyboards, native plugins, or Swift packages.
- A new application SDK, replacement .NET CLI, custom Uno project format, or modified Rider.
- Broad MAUI support. Minimal Uno validation follows the UIKit device proof; the production ChambaXtra application is a later integration target.

## 4. Architecture and responsibilities

```text
Rider / terminal / stock uno-check + project manifest
                         |
               canonical ~/.dotnet
                         |
          standard workload ios + MSBuild
                         |
          official Microsoft.iOS.* packs
                         |
       managed compilation / trim / registrar
                         |
      Linux Mono AOT cross compiler -> arm64 objects
                         |
 synthetic Xcode + omarchy Darwin tools + Apple SDK
                         |
             native link -> unsigned .app
                         |
       provisioning + signing -> signed .app/.ipa
                         |
          USB install -> launch -> physical iPhone
```

| Component | Owned behavior | Boundary |
| --- | --- | --- |
| Bootstrapper | Install manifests and project packs, configure environment, acquire toolchain, validate and roll back | Custom setup is allowed; normal builds remain standard |
| Workload manifest | Expose `ios`, resolve official packs and Linux host tools | Actual schema and resolver behavior verified against pinned SDK |
| Compatibility SDK pack | Import Linux integration targets and locate host tools | Automatic workload import, limited to Linux/iOS; no app edits |
| AOT pack | Linux executable and dependencies with recorded runtime provenance | Targets the selected device runtime; never substitutes Linux runtime libraries into the app |
| Synthetic Xcode | Required directory structure, metadata, SDK, tool entry points | Actual SDK version represented truthfully |
| Shims/adapters | Translate supported command contracts to reusable tools | Unknown operations fail clearly |
| macios patch set | Only irreducible host assumptions | Separate versioned patches; original packages retained |
| Validation harness | Stage tests, negative cases, device receipts | Environment checks never stand in for running an app |

### 4.1 Canonical .NET environment

All participating Android, WebAssembly, and iOS workloads live under the canonical `~/.dotnet` installation. Keep the package-manager-owned `/usr/share/dotnet` untouched. Existing canonical-root contents must be inventoried and preserved.

The generated activation file sets `DOTNET_ROOT`, puts its `dotnet` and tool directory on `PATH`, exports `DEVELOPER_DIR`, and adds the project shim directory for that development environment. Rider must explicitly select `~/.dotnet/dotnet` and receive the same environment when started through the desktop. Detect conflicting architecture-specific root variables and stale MSBuild SDK overrides.

Doctor must compare resolved executable paths, `dotnet --info`, selected SDK, workload graph, and IDE configuration. A mismatch is an error with a concrete correction, not a reason to continue against another installation. Temporary test homes may isolate destructive lifecycle tests; they do not become a second production SDK root.

### 4.2 Workload resolution and installation

The public command remains `dotnet workload install ios`. The preferred manifest ID is `asfiroth.net.sdk.ios`, with a Linux x64 platform mapping and official logical SDK pack identities preserved. The [.NET workload manifest design](https://github.com/dotnet/designs/blob/main/accepted/2020/workloads/workload-manifest.md) describes pack mappings, platform constraints, and aliases; the selected SDK implementation is the final compatibility authority.

Do not assume two manifests can both define `ios` merely because their platform lists differ. M0/M1 must test duplicate workloads, duplicate logical packs, official manifests already bundled with the SDK, dependencies referring to `microsoft.net.sdk.ios`, and manifests introduced by Android/MAUI installation.

Implementation selection order:

1. Use a distinct project manifest if the effective graph has no competing definitions and all dependency/import paths resolve.
2. If that fails, test a managed replacement of the effective official iOS manifest within the user-local root, retaining any required identity and backed-up original. Record this deviation from the preferred ID in an architecture decision.
3. If neither survives normal lifecycle operations, stop the packaging gate and design a compatible distribution mechanism. Do not invent a new workload ID or silently modify the CLI.

The bootstrapper must stage and validate the complete effective graph before activation. Project-owned package names must not impersonate official Microsoft packages. Where supported, logical pack aliases select a custom Linux AOT package; otherwise use the smallest automatically imported tool-path override. Verify the physical path consumed by the AOT task rather than assuming an alias relocates files.

Manifest-generated imports must load the Linux compatibility pack before the relevant upstream targets consume its values. Preserve official reference and device-runtime contents byte-for-byte. An official SDK pack may contain unusable Mach-O host executables; inventory those separately from its reusable managed assemblies.

The installation graph must include transitive Mono/runtime/toolchain packs and templates, not just the four visible iOS pack names. Candidate families from the discussion are `Microsoft.iOS.Sdk.net10.0_26.5`, `Microsoft.iOS.Ref.net10.0_26.5`, `Microsoft.iOS.Runtime.ios.net10.0_26.5`, and `Microsoft.iOS.Runtime.ios-arm64.net10.0_26.5`; verify actual IDs and versions before generating a manifest.

The standard CLI owns workload installation records. Hand-unpacking packs is allowed as an explicitly labeled discovery experiment, never as evidence that installation or repair works. Support list, install, restore, repair, and uninstall for the pinned distribution. Qualify update separately because it may replace the customized graph. [.NET workload installation documentation](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-workload-install) explains manifest updates and workload-set selection; M1 must record the selected mode and its interaction with custom manifests.

Normal application `global.json` stays standard. Pin the SDK with a documented roll-forward policy. Add a workload-set pin only after proving that set resolves the custom manifest graph. Configure package sources so official IDs come from trusted official feeds and project IDs from the project feed or local package source. An offline cache must reproduce the same graph.

### 4.3 Stock uno-check

Ship a custom manifest derived from the pinned upstream schema, retaining relevant existing checks and adding Linux iOS coverage. Its workload entry must retain `workloadId: ios`, reference the selected effective manifest ID and package, and use the exact Linux platform token recognized by that uno-check release. Do not assume .NET RIDs and uno-check platform strings are interchangeable.

Target invocation after installation:

```sh
uno-check --manifest "$HOME/.local/share/dotnet-ios-linux/manifests/uno.ui.linux-ios.manifest.json"
```

Uno documents [custom manifest input](https://platform.uno/docs/articles/uno-check.html) and its [manifest format](https://platform.uno/docs/articles/external/uno.check/manifests/README.html). Verify SDK-root selection and remediation behavior against the pinned binary. A green run caused by an excluded iOS check is failure. Negative tests remove the required pack or change its version and require a failed check. Repair must not reinstall a conflicting official manifest. Compiler/toolchain tests that the existing schema cannot express belong in doctor and integration tests; do not fabricate unsupported check types or fork uno-check to hide that gap.

### 4.4 Synthetic Xcode and Darwin backend

Proposed installed layout; files are added only when required by observed consumers:

```text
~/.local/share/dotnet-ios-linux/
├── Xcode.app/Contents/
│   ├── Info.plist
│   ├── version.plist
│   └── Developer/
│       ├── Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk/
│       ├── Toolchains/XcodeDefault.xctoolchain/usr/bin/
│       └── usr/bin/
├── bin/                  # activation-scoped command shims
├── manifests/            # stock uno-check input
├── toolchains/<lock-id>/ # pinned backend and host tools
└── receipts/             # installed paths, hashes, provenance
```

Expose the SDK through `DEVELOPER_DIR=$HOME/.local/share/dotnet-ios-linux/Xcode.app/Contents/Developer`. Preserve the Apple SDK's own metadata and record its build identifier. Tool discovery must never claim Xcode features that the backend does not implement.

Reuse the full SDK/tooling path from [omarchy-apple-dev](https://github.com/joshuaswarren/omarchy-apple-dev), pinned to a compatible revision. Its current README describes a newer default stack than the requested baseline and separately records older device evidence; neither proves .NET compatibility. Select and test components instead of running an unpinned installer. Extract the SDK from the user's Xcode installation, optionally through `mac-build.asfiroth.home` when access is configured. Do not bundle Apple SDK files in project releases. Retain dependency licenses and audit redistribution eligibility before publishing binaries.

| Interface | Minimum required behavior | Validation |
| --- | --- | --- |
| `xcrun` | SDK queries, tool lookup, supported tool execution; resolve through `DEVELOPER_DIR` | Compare captured argument/output fixtures and exit codes |
| `xcodebuild` | Observed version/SDK queries | Accurate metadata; unsupported build invocations fail |
| `clang`, `clang++`, linker | Native compilation and Darwin linking for arm64 iOS | Mach-O architecture, platform load commands, symbols, deployment target |
| `actool`, `ibtool` | Only the fixture formats needed by supported sample apps | Inspect generated resources and exercise them on device |
| `codesign` | Translate observed signing/inspection operations into backend semantics | Nested signing, entitlements, signature inspection, device acceptance |
| `lipo`, `strip`, `otool`, `install_name_tool` | Observed native pipeline operations | Golden object/bundle tests; explicit rejection of unsupported modes |
| SDK discovery tasks | Consume synthetic filesystem and environment | Try unchanged first; patch only demonstrated host dependencies |

Wrappers must preserve argument boundaries, paths containing spaces, response files, standard streams, exit codes, and cancellation. Never use shell `eval` to forward arguments. Avoid recursion when resolving the real tool. Do not blindly inject conflicting target/sysroot flags; validate explicit caller values and apply defaults only when absent. Record sanitized invocations for diagnostics. Absolute paths such as `/usr/bin/xcrun` may require a narrow task adaptation; do not replace system executables.

### 4.5 Mono AOT: primary feasibility blocker

The required component is a **Linux x86_64 host executable generating code for the selected iOS arm64 Mono runtime**. Renaming a macOS compiler or merely copying a pack does not provide it. The [Mono build source](https://github.com/dotnet/runtime/blob/main/src/mono/CMakeLists.txt) is the investigation entry point for host/target configuration; its existence does not establish that this specific combination builds or runs correctly.

Actionable experiment:

1. Extract the runtime revision, AOT options, LLVM dependencies, runtime libraries, and registrar/link requirements from a reference Mac build and the selected packages.
2. Build the matching Mono AOT cross compiler on Linux. Treat the previously discussed `aarch64-apple-darwin10` triple as a source-level candidate to verify, not a complete build recipe.
3. Compile a small managed fixture with the same AOT mode and options as the iOS pipeline. Verify the actual output format and architecture; where the compiler emits intermediate assembly/IR, include the required assembler/LLVM steps.
4. Cover generics, exceptions, P/Invoke, callbacks, and representative runtime dependencies. Confirm all selected host executables are Linux-runnable and do not load Darwin host libraries.
5. Package the compiler and necessary host dependencies under a project-owned ID such as `DotnetIosLinux.Runtime.AOT.linux-x64.Cross.ios-arm64`. Record ABI/runtime/LLVM provenance and hashes.
6. Integrate tool selection beneath the normal macios AOT task, then compile the complete minimal application and link against the unchanged device runtime.

AOT completion alone is insufficient: run the result on the device to detect ABI, unwind, trampoline, GC, or runtime mismatches. Both default and optional LLVM paths must be distinguished; the MVP supports only the path actually tested. Missing LLVM support cannot be hidden by accepting and ignoring flags.

Start this experiment after M0/M1 while other integration details are still inexpensive to change. If the compiler cannot be built or produces incompatible objects, record the failing revision, minimal reproduction, and estimated patch scope. Pause downstream packaging polish. A switch to NativeAOT would require an explicit scope revision.

### 4.6 MSBuild, managed tasks, and application portability

Preserve ordinary `TargetFramework`, `RuntimeIdentifier`, SDK imports, signing properties, and CLI commands. No `UseLinuxIosHack`, custom application SDK, or checked-in Linux-only import is allowed in acceptance samples. Normal app metadata and signing configuration remain legitimate application inputs.

Trace the actual task graph: SDK discovery, C# compilation, trimming/linking, registrar generation, native compilation, AOT, native linking, resources, bundle creation, signing, and deployment. The diagram is conceptual; task ordering and names are version-specific.

Managed assemblies may still call native macOS libraries or reject the host. Classify each failure as file discovery, executable lookup, host-native dependency, runtime incompatibility, or unsupported behavior. Prefer filesystem/environment configuration, then shims, then supported MSBuild properties, then minimal task patches. Each patch records the upstream revision, reproduction, rationale, tests, and removal/upstreaming condition. Do not mutate downloaded official packs in place; build and identify patched artifacts separately.

Early unsigned-build experiments may use a version-specific signing bypass. Verify its actual effect from the target graph; `_RequireCodeSigning=false` from the earlier discussion is not a promised public interface. The packaged workflow must hide such implementation details beneath normal targets and must not silently omit required signing in the device path.

### 4.7 Signing, deployment, Rider, and Uno

First use backend signing and device utilities directly against the produced bundle. Reuse omarchy provisioning helpers, `rcodesign`/selected signing implementation, and `pymobiledevice3` or its validated deployment wrapper. Pin all versions, including Python dependencies.

Require a development identity with its private key, matching provisioning profile, bundle ID, permitted device, team, capabilities, and unexpired validity. Start with user-provided credentials/profiles; account automation is optional later. Sign embedded frameworks/libraries in the required order and verify the final bundle. Do not edit resources after signing. Keep private keys, passwords, tokens, profiles, and pairing records out of source control and public receipts.

USB tests must record pairing/trust, Developer Mode where required, developer-image/tunnel requirements for that OS, installation result, launch result, and application logs. Never infer successful launch from successful installation. Expose actionable diagnostics for a locked/untrusted device, unavailable service, rejected profile, or expired signature.

After M9, integrate proven operations behind ordinary MSBuild run targets where feasible. Building from Rider is a separate test from device discovery, Run, native debugging, and managed debugging. An IDE may enforce host checks before invoking MSBuild; if stock Rider blocks the workflow, document the exact limit rather than claiming automatic compatibility.

Validate a minimal Uno application after the UIKit proof using the same TFM/RID and canonical SDK. Exercise the resource/native-library paths it adds. Successful `uno-check` alone is not Uno application support. Port ChambaXtra only after that minimal Uno device test passes.

## 5. Bootstrap, lifecycle, and repository layout

Bootstrap is the one custom entry point. Planned commands are setup, doctor, and uninstall; exact CLI implementation is deferred. Setup must be idempotent, detect existing state, stage downloads with verified hashes, validate the lock and effective manifest graph, activate atomically where possible, and leave a transaction receipt. On failure restore prior project-managed state without deleting shared workloads or unrelated SDK versions.

Do not hand-edit workload metadata to fabricate installation success. Coordinate mutations with the CLI and prevent concurrent setup/repair operations. Back up any manifest replaced by the selected strategy, detect user changes before restoring it, and preserve original package hashes. Uninstall removes only owned assets and restores only known originals; it does not delete `~/.dotnet`. Environment activation must be reversible and must not globally intercept unrelated Apple tooling.

Updates are explicit distribution upgrades. Test them in a temporary home before changing the canonical installation. Check unknown SDK/manifest/pack combinations before build and fail with the incompatible component identified. Keep diagnostic logs local by default and redact signing secrets and device identifiers before sharing.

Repository structure:

```text
dotnet-ios-linux/
├── .editorconfig
├── .gitignore
├── LICENSE
├── README.md
├── toolchain.lock.json
├── docs/                      # specification, status, evidence, and governance
├── src/
│   ├── msbuild/               # automatically imported Linux integration
│   ├── packs/                 # package templates without generated binaries
│   └── shims/                 # compatibility command entry points
├── eng/
│   ├── runtime/               # pinned Mono/LLVM acquisition and build recipe
│   │   └── patches/           # minimal versioned upstream runtime patches
│   ├── toolchain/             # SDK import, signing, deployment, dependency setup
│   └── evidence/              # inventory, receipt, and repository validation
├── tests/
│   ├── contracts/             # tool and manifest fixtures
│   ├── integration/           # private SDK/AOT/native-link gates
│   ├── HelloIos/              # minimal UIKit app
│   └── AotProbe/              # generics, exceptions, native interop
└── artifacts/                 # ignored downloads/build outputs when explicitly used
```

Original project content is licensed under the MIT License. Retain third-party
notices and do not apply the project license to external components on their
authors' behalf. SDKs, credentials, local configuration, private binlogs,
extracted runtimes, and generated binaries are excluded from source control.

## 6. Implementation phases and milestones M0–M10

Milestone numbering preserves the earlier discussion. M0/M1 may iterate together because evaluation and manifest acquisition interact. Start the M4 compiler feasibility spike as soon as the runtime graph is known; completion still requires its integration evidence.

| Milestone | Work and deliverable | Exit evidence |
| --- | --- | --- |
| **M0 — Evaluate** | Inventory the reference Mac and Linux host; create baseline lock draft; select conflict-free `ios` manifest strategy; evaluate minimal `net10.0-ios` project | Canonical Linux SDK loads intended iOS targets without missing-SDK failure; binlog shows TFM/RID and effective manifest identities; next blocker classified |
| **M1 — Restore** | Generate manifest/package graph and feeds; install official and custom packs through CLI; resolve templates; validate stock uno-check input | Clean restore succeeds; `workload list` reports `ios`; no fabricated records; missing-pack negative test fails; lifecycle graph documented |
| **M2 — Discover Xcode** | Import pinned SDK; construct layout; provide version/query wrappers | macios resolves correct SDK and tool paths through `DEVELOPER_DIR`; wrong/missing SDK fails clearly |
| **M3 — Managed pipeline** | Run managed compilation, trimming/linking, registrar preparation as required; classify managed host assumptions | Required managed outputs exist and references resolve; binlog reaches AOT/native work without unexplained host bypasses |
| **M4 — Mono AOT** | Build matched Linux host compiler, package it, integrate normal AOT task | Entire minimal app AOT stage succeeds; outputs inspect as correct target artifacts; probes pass compilation; no macOS host executable invoked |
| **M5 — Native link** | Compile generated/native sources; link AOT output, device runtime, and Apple frameworks | Valid arm64 iOS Mach-O; expected deployment target/load commands; no unresolved symbols or Linux libraries |
| **M6 — Bundle** | Assemble metadata/resources/native dependencies into `.app`; prove declared asset subset | Valid unsigned bundle; required files/plists present; clean and incremental builds succeed; artifact manifest and hashes captured |
| **M7 — Sign** | Validate identity/profile; sign nested code and final bundle; optionally package IPA | Signature and entitlements inspect correctly; profile matches app/device; negative mismatch and expired-profile cases fail |
| **M8 — Install** | Pair and prepare USB device; deploy signed bundle | Device reports installed expected bundle/version; rejected/locked-device cases produce useful errors |
| **M9 — Launch** | Launch app, collect logs, exercise UI and AOT probes | Visible Hello UI and deterministic runtime checks pass; repeat installation/launch after rebuild; no Mac build/sign/deploy service involved |
| **M10 — Native debug research** | Validate backend LLDB/debugserver path and symbols; document integration limits | Attach, native breakpoint, backtrace, and symbol evidence; managed debugging remains a distinct future project |

Phase A (M0–M1) resolves distribution feasibility. Phase B (M2–M4) resolves host execution and compiler feasibility. Phase C (M5–M6) delivers the unsigned technical proof. Phase D (M7–M9) delivers the device MVP. Phase E qualifies stock tooling, lifecycle behavior, and minimal Uno integration for a usable preview. M10 is post-MVP and does not delay declaring a correctly scoped device proof.

Each receipt includes date, lock hash, exact commands, environment, exit codes, sanitized logs/binlog reference, input/output hashes, observed behavior, and remaining limitations. Device receipts include model/OS build and visual/log evidence, with identifying values redacted. Failed attempts remain research evidence, never passed milestones.

## 7. Validation strategy

### Test layers

| Layer | Required cases | Execution environment |
| --- | --- | --- |
| Static/package | Manifest schema and duplicate identities; import order; package provenance; no secrets/SDK payload in releases | Linux CI, no Apple SDK |
| Shim contracts | Query/execution forms, quoting, response files, stderr, exit status, cancellation, unsupported arguments | Linux CI with fixtures and fake backend |
| Compiler | Rebuild matched AOT host; compile generic/exception/interop probes; inspect generated code | Linux toolchain runner |
| Build integration | Restore/evaluate/trim/AOT/link/bundle, clean/incremental builds, paths containing spaces | Private runner with user-supplied Apple SDK |
| Lifecycle | Fresh install, repeated setup, interruption/rollback, repair, restore, uninstall, supported upgrade | Disposable homes; preserve unrelated workloads |
| Coexistence | Android and WebAssembly builds before/after iOS install; non-iOS imports unaffected; conflicting official iOS manifest rejected | Canonical-root-equivalent test environment |
| Signing | Matching and mismatched identities/profiles, nested code, tampered resources, expiration | Private signing environment |
| Device | USB install, launch, relaunch, runtime probes, resource rendering, logs | Recorded physical iPhone |
| Consumer | Stock uno-check positive/negative cases; Rider evaluation/build; minimal Uno device run | Pinned consumer versions |

Use a reference Mac build of the same sample and baseline to compare task inputs, command contracts, bundle structure, entitlements, architectures, and runtime behavior. Do not demand byte-identical signed artifacts: timestamps, paths, UUIDs, and signatures may differ. Normalize known differences and investigate semantic ones. Reproducibility means the same locked inputs produce equivalent validated outputs; deterministic host-tool binaries are an additional goal where attainable.

Public CI runs SDK-free tests. Private SDK/device tests are explicit gates with receipts, not silently skipped passes. Keep ordinary untrusted PR jobs away from signing identities and pairing material. Establish duration and disk-use baselines during M4/M6, then track regressions; no unmeasured performance claim belongs in the release matrix.

### Acceptance command contracts

These are verification targets after bootstrap, not commands proven by this specification:

```sh
dotnet --info
dotnet workload list
dotnet workload install ios
dotnet workload restore tests/HelloIos/HelloIos.csproj
dotnet restore tests/HelloIos/HelloIos.csproj -p:TargetFramework=net10.0-ios -r ios-arm64
dotnet build tests/HelloIos/HelloIos.csproj -f net10.0-ios -r ios-arm64 -c Release
```

Run `dotnet workload repair` only in an isolated lifecycle test initially because it affects the installed workload set. Test workload uninstall and reinstallation there as well. Record the exact `Run` command and device-selection properties only after the selected macios targets and adapter are proven. CLI flags in examples must be checked against the pinned SDK before being published as setup instructions.

## 8. Risks and decision gates

| Risk | Impact | Mitigation / stop condition |
| --- | --- | --- |
| Mono cross compiler cannot target matched iOS runtime from Linux | Core feasibility failure | Early M4 spike; preserve reproduction; reassess before investing in installer polish |
| AOT output compiles but fails on device | Invalid apparent success | Runtime probes and real-device M9 gate |
| Duplicate workload definitions or workload-set overwrite | Broken SDK or inconsistent repairs | Resolve graph in M0/M1, lifecycle tests, reversible activation |
| macios uses native macOS APIs or absolute tool paths | Shims insufficient | Inventory tasks; patch narrowly; track ongoing maintenance cost |
| Backend assets/storyboards support only a subset | Broken UI or missing resources | Explicit capability matrix and device resource fixtures |
| SDK/Swift/LLVM versions drift | Compile/link incompatibility | Lock entire backend; verify candidate older toolchain; no rolling dependencies |
| Signing or device protocols change | Installation/launch failure | Pin tools and device OS evidence; separate errors from compilation |
| Rider blocks Linux iOS before MSBuild | IDE promise unmet despite CLI success | Early evaluation test; report precise limitation; no automatic support claim |
| uno-check filters out iOS or repairs wrong manifest | False green or broken installation | Positive/negative check tests and remediation capture |
| Installer damages shared canonical root | Other development workloads fail | Inventory, staging, transaction receipts, rollback/coexistence tests |
| Redistribution rights differ by component | Unshippable binary bundle | User-supplied Apple SDK; per-component provenance/license review |
| Minimal UIKit success overgeneralized to Uno/MAUI | Misleading support scope | Separate application gates and release matrix entries |

No arbitrary calendar estimates are assigned before the compiler and manifest gates pass. Every unresolved hypothesis must have an owner when implementation is staffed, a minimal experiment, and a recorded result.

## 9. Success criteria and release claims

**Unsigned technical proof (M6):** Linux alone restores and builds the minimal UIKit app through normal `dotnet build`, produces the expected arm64 iOS bundle, and records the complete AOT/native pipeline. No claim of device usability yet.

**Device MVP (M9):** the same bundle is signed, installed over USB, and launched on the recorded iPhone. Hello UI and runtime probes succeed across a clean rebuild and a subsequent incremental rebuild. Direct backend sign/deploy commands are acceptable at this research stage; document them explicitly.

**Usable compatibility preview:** M9 plus reproducible bootstrap, validated workload install/list/restore/repair/uninstall, safe rollback, canonical-root agreement, stock uno-check checking iOS correctly, stock Rider evaluation/build, and an unchanged minimal Uno project running on the device. Standard run-target integration is reported separately until passed. Any missing consumer gate means the release is labeled a narrower CLI/device proof.

**Debugging claim (M10):** limited to the native operations actually demonstrated. LLDB attachment does not imply C# debugging, hot reload, or Rider debugger integration.

Every release publishes the exact tested host, SDK/runtime/toolchain, device OS, application scope, resource limitations, and passed gates. No status is inferred from an upstream project's Flutter or Swift success.

## 10. First implementation backlog

1. Capture the known-working Mac's `dotnet --info`, workload list, effective manifests, pack versions, Xcode/iOS SDK metadata, and minimal UIKit build binlog.
2. Resolve candidate version availability and create the first complete lock file with source provenance.
3. Create minimal UIKit and AOT probe samples; confirm reference behavior on Mac.
4. Inventory official pack contents into managed host code, native host executables, target code, and metadata.
5. Prototype the effective Linux `ios` manifest graph in a disposable test home; test conflicts before canonical-root activation.
6. Prove evaluation and restore; record exact SDK/feature-band and workload-set behavior.
7. Run the Linux Mono AOT build experiment against the matched runtime revision.
8. Extract only required Apple SDK/toolchain assets and construct the synthetic layout.
9. Implement only command forms observed in captured build traces, adding negative contract tests with each adapter.
10. Advance M2–M9 with receipts, then qualify lifecycle, Rider, stock uno-check, and minimal Uno support.

## 11. Source and evidence notes

The conversation establishes product intent and candidate versions. It does not establish experimental results for this repository. Public sources were consulted on 2026-10-17; mutable branches and release pages must be replaced with pinned revision references during M0.

- [omarchy-apple-dev](https://github.com/joshuaswarren/omarchy-apple-dev): backend reuse candidate and its own reported test matrix; not evidence of .NET support.
- [.NET workload manifest design](https://github.com/dotnet/designs/blob/main/accepted/2020/workloads/workload-manifest.md): manifest concepts; validate implementation details in the pinned SDK.
- [dotnet workload install](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-workload-install): standard installation and version-selection behavior.
- [Mono CMake source](https://github.com/dotnet/runtime/blob/main/src/mono/CMakeLists.txt): compiler host/target investigation starting point.
- [macios releases](https://github.com/dotnet/macios/releases): resolve the requested toolchain against actual release/package evidence before locking it.
- [Uno environment checking](https://platform.uno/docs/articles/uno-check.html) and [manifest documentation](https://platform.uno/docs/articles/external/uno.check/manifests/README.html): custom-manifest integration entry points.

The original planning baseline predated implementation. Current repository evidence now covers the pinned compiler build, project-owned SDK import, synthetic-Xcode discovery, the private unsigned `HelloIos` M2–M6 gate, development signing, clean and incremental replacement installation, repeated manual launch/runtime probes, and cold relaunch. Negative deployment gates, automated CoreDevice launch, Rider, Uno, and production lifecycle tests remain outstanding.
